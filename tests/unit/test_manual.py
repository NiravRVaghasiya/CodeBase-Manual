"""Tests for living manual generation."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from codebase_manual.ai.manual import generate_manual
from codebase_manual.domain.models import (
    Decorator,
    FileHashStrategy,
    FileLanguage,
    FileRecord,
    FunctionSymbol,
    PythonModule,
    SourceLocation,
)
from codebase_manual.persistence.snapshot import RepositorySnapshot

_LOCATION = SourceLocation(line_start=1, line_end=2)


class _StubProvider:
    def __init__(self, response: str, *, model_identifier: str = "stub-model") -> None:
        self.response = response
        self.model_identifier = model_identifier
        self.calls = 0

    def complete(self, *, system: str, prompt: str) -> str:
        self.calls += 1
        return self.response


class _FakeCacheStore:
    """An in-memory stand-in for `IndexStore`'s key-value cache methods."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get_cached_value(self, cache_key: str) -> str | None:
        return self._values.get(cache_key)

    def set_cached_value(self, cache_key: str, payload: str) -> None:
        self._values[cache_key] = payload


def _snapshot() -> RepositorySnapshot:
    api_module = PythonModule(
        path="app/api/routes.py",
        module_name="app.api.routes",
        docstring="HTTP endpoints.",
        functions=[
            FunctionSymbol(
                name="login",
                qualified_name="app.api.routes.login",
                decorators=[Decorator(expression='router.post("/login")', location=_LOCATION)],
                location=_LOCATION,
            )
        ],
    )
    db_module = PythonModule(
        path="app/database/connection.py", module_name="app.database.connection"
    )
    files = [
        FileRecord(
            path="app/api/routes.py",
            size_bytes=10,
            extension=".py",
            language=FileLanguage.PYTHON,
            content_hash="hash-routes",
            hash_strategy=FileHashStrategy.FULL_HASH,
        ),
        FileRecord(path=".env.example", size_bytes=5, extension="", language=FileLanguage.ENV),
    ]
    return RepositorySnapshot(
        repository_identity="repo",
        repository_root="/repo",
        commit_sha="abc",
        branch="main",
        remote_url="https://github.com/example/repo.git",
        indexed_at=datetime.now(UTC),
        files=files,
        modules=[api_module, db_module],
        relationships=[],
    )


def test_generate_manual_without_provider_includes_facts_only_sections() -> None:
    manual = generate_manual(_snapshot(), provider=None)

    assert "# Codebase Manual" in manual
    assert "app/api/routes.py" in manual
    assert "AI summary unavailable: no provider configured." in manual
    assert "POST /login" in manual
    assert ".env.example" in manual
    assert "app/database/connection.py" in manual


def test_generate_manual_with_provider_includes_ai_summary() -> None:
    response = json.dumps(
        {
            "purpose": "Exposes login endpoints.",
            "responsibilities": [],
            "important_symbols": [],
            "side_effects": [],
            "confidence": "high",
        }
    )
    manual = generate_manual(_snapshot(), provider=_StubProvider(response))
    assert "Exposes login endpoints." in manual


def test_generate_manual_reports_ai_failure_honestly() -> None:
    manual = generate_manual(_snapshot(), provider=_StubProvider("not json"))
    assert "AI summary unavailable:" in manual


_SUMMARY_PAYLOAD = json.dumps(
    {
        "purpose": "Exposes login endpoints.",
        "responsibilities": [],
        "important_symbols": [],
        "side_effects": [],
    }
)


def test_generate_manual_caches_a_summary_and_reuses_it_without_calling_the_provider() -> None:
    # `_snapshot()` has two modules: "app/api/routes.py" (has a content
    # hash, cacheable) and "app/database/connection.py" (no FileRecord, so
    # never cacheable -- it always calls the provider, see the dedicated
    # test below).
    cache = _FakeCacheStore()
    provider = _StubProvider(_SUMMARY_PAYLOAD)

    generate_manual(_snapshot(), provider=provider, cache=cache)
    assert provider.calls == 2

    second_provider = _StubProvider(_SUMMARY_PAYLOAD)
    manual = generate_manual(_snapshot(), provider=second_provider, cache=cache)

    # Only the uncacheable module calls the provider the second time.
    assert second_provider.calls == 1
    assert "cached" in manual
    assert "Exposes login endpoints." in manual


def test_generate_manual_regenerates_when_the_model_identifier_changes() -> None:
    cache = _FakeCacheStore()
    generate_manual(_snapshot(), provider=_StubProvider(_SUMMARY_PAYLOAD), cache=cache)

    different_model_provider = _StubProvider(_SUMMARY_PAYLOAD, model_identifier="other-model")
    generate_manual(_snapshot(), provider=different_model_provider, cache=cache)

    # Both modules regenerate: the cacheable one because the model
    # identifier changed, the other because it's never cacheable.
    assert different_model_provider.calls == 2


def test_generate_manual_never_caches_a_file_with_no_content_hash() -> None:
    # `_snapshot()`'s "app/database/connection.py" module has no FileRecord
    # (and so no content hash) -- it must never be served from cache.
    cache = _FakeCacheStore()
    generate_manual(_snapshot(), provider=_StubProvider(_SUMMARY_PAYLOAD), cache=cache)

    second_provider = _StubProvider(_SUMMARY_PAYLOAD)
    generate_manual(_snapshot(), provider=second_provider, cache=cache)

    # The routes module (hashed) was served from cache on the second call;
    # the unhashed database module was re-requested both times.
    assert second_provider.calls == 1
