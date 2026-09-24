"""Tests for retrieval-grounded question answering."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from codebase_manual.ai.provider import AISynthesisError
from codebase_manual.ai.qa import answer_question
from codebase_manual.domain.models import FunctionSymbol, PythonModule, SourceLocation
from codebase_manual.persistence.snapshot import RepositorySnapshot

_LOCATION = SourceLocation(line_start=1, line_end=2)


class _StubProvider:
    def __init__(self, response: str | None = None) -> None:
        self.response = response
        self.called = False

    def complete(self, *, system: str, prompt: str) -> str:
        self.called = True
        if self.response is None:
            raise AssertionError("provider should not have been called")
        return self.response


def _snapshot() -> RepositorySnapshot:
    module = PythonModule(
        path="app/auth/service.py",
        module_name="app.auth.service",
        docstring="Authentication service coordinating OAuth providers.",
        functions=[
            FunctionSymbol(
                name="login_with_provider",
                qualified_name="app.auth.service.login_with_provider",
                docstring="Authenticate via a provider.",
                location=_LOCATION,
            )
        ],
    )
    return RepositorySnapshot(
        repository_identity="repo",
        repository_root="/repo",
        commit_sha="abc",
        branch="main",
        remote_url=None,
        indexed_at=datetime.now(UTC),
        files=[],
        modules=[module],
        relationships=[],
    )


def test_answer_question_skips_provider_when_no_evidence_found() -> None:
    provider = _StubProvider(response=None)
    answer = answer_question("what does kubernetes do here", _snapshot(), provider)

    assert provider.called is False
    assert answer.confidence.value == "low"
    assert answer.evidence == []


def test_answer_question_synthesizes_from_retrieved_context() -> None:
    response = json.dumps(
        {"answer": "Authentication is handled by AuthService.", "confidence": "high"}
    )
    provider = _StubProvider(response=response)

    answer = answer_question("how does authentication work", _snapshot(), provider)

    assert provider.called is True
    assert answer.text == "Authentication is handled by AuthService."
    assert answer.confidence.value == "high"
    assert answer.evidence


def test_answer_question_raises_on_malformed_response() -> None:
    provider = _StubProvider(response="not json")
    with pytest.raises(AISynthesisError):
        answer_question("how does authentication work", _snapshot(), provider)
