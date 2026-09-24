"""Tests for candidate ID tables built from retrieval results."""

from __future__ import annotations

from codebase_manual.domain.models import EntityKind, PythonModule
from codebase_manual.query.candidates import build_candidate_set
from codebase_manual.query.retrieval import (
    MatchReason,
    MatchSignal,
    RetrievalResult,
    RetrievedFile,
    RetrievedSymbol,
)

_AUTH_SIGNAL = [MatchSignal(MatchReason.DOCSTRING, "docstring matches ['auth']")]


def _retrieval() -> RetrievalResult:
    files = [
        RetrievedFile(
            module=PythonModule(path="app/auth/service.py", module_name="app.auth.service"),
            signals=_AUTH_SIGNAL,
        ),
        RetrievedFile(
            module=PythonModule(
                path="tests/test_auth_service.py", module_name="tests.test_auth_service"
            ),
            signals=_AUTH_SIGNAL,
        ),
    ]
    symbols = [
        RetrievedSymbol(
            identifier="app.auth.service.AuthService",
            kind="class",
            module_path="app/auth/service.py",
            signals=_AUTH_SIGNAL,
        )
    ]
    return RetrievalResult(query_terms={"auth"}, files=files, symbols=symbols)


def test_build_candidate_set_separates_files_tests_and_symbols() -> None:
    candidates = build_candidate_set(_retrieval())

    assert [c.id for c in candidates.files] == ["FILE_001"]
    assert [c.id for c in candidates.tests] == ["TEST_001"]
    assert [c.id for c in candidates.symbols] == ["SYMBOL_001"]
    assert candidates.files[0].ref.identifier == "app/auth/service.py"
    assert candidates.tests[0].ref.identifier == "tests/test_auth_service.py"
    assert candidates.symbols[0].ref.kind is EntityKind.CLASS


def test_resolve_returns_none_for_an_unknown_id() -> None:
    candidates = build_candidate_set(_retrieval())

    assert candidates.resolve("FILE_999") is None
    assert candidates.resolve("FILE_001") is not None


def test_resolve_many_splits_known_and_unknown_ids() -> None:
    candidates = build_candidate_set(_retrieval())

    resolved, unknown = candidates.resolve_many(["FILE_001", "FILE_999", "SYMBOL_001"])

    assert [ref.identifier for ref in resolved] == [
        "app/auth/service.py",
        "app.auth.service.AuthService",
    ]
    assert unknown == ["FILE_999"]


def test_prompt_block_lists_every_candidate_id_and_label() -> None:
    block = build_candidate_set(_retrieval()).prompt_block()

    assert "FILE_001 = app/auth/service.py" in block
    assert "TEST_001 = tests/test_auth_service.py" in block
    assert "SYMBOL_001" in block and "AuthService" in block
