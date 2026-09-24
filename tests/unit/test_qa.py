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
        {"answer": "Authentication is handled by AuthService.", "cited_ids": ["FILE_001"]}
    )
    provider = _StubProvider(response=response)

    answer = answer_question("how does authentication work", _snapshot(), provider)

    assert provider.called is True
    assert answer.text == "Authentication is handled by AuthService."
    # Grounded in a single INFERRED (lexical retrieval) citation -> MEDIUM,
    # never taken from the model's own response.
    assert answer.confidence.value == "medium"
    assert answer.grounding.value == "valid"
    assert answer.evidence


def test_answer_question_quarantines_a_cited_id_the_model_invented() -> None:
    response = json.dumps(
        {
            "answer": "Authentication is handled by AuthService.",
            "cited_ids": ["FILE_001", "FILE_999"],
        }
    )
    provider = _StubProvider(response=response)

    answer = answer_question("how does authentication work", _snapshot(), provider)

    assert answer.grounding.value == "partially_valid"
    assert all(item.file_path != "FILE_999" for item in answer.evidence)


def test_answer_question_rejects_when_every_cited_id_is_invented() -> None:
    response = json.dumps(
        {"answer": "Authentication is handled by AuthService.", "cited_ids": ["FILE_999"]}
    )
    provider = _StubProvider(response=response)

    answer = answer_question("how does authentication work", _snapshot(), provider)

    assert answer.grounding.value == "invalid"
    assert answer.evidence == []
    assert answer.confidence.value == "low"


def test_answer_question_rejects_a_plausible_looking_id_the_same_as_any_other() -> None:
    """Widening the hallucination net (Phase 7): an off-by-one guess like `FILE_002`
    (only `FILE_001` actually exists) is exactly as invented as an obviously wrong ID
    -- the validator does no fuzzy/nearest-match leniency, so it must be rejected too.
    """
    response = json.dumps(
        {"answer": "Authentication is handled by AuthService.", "cited_ids": ["FILE_002"]}
    )
    provider = _StubProvider(response=response)

    answer = answer_question("how does authentication work", _snapshot(), provider)

    assert answer.grounding.value == "invalid"
    assert answer.evidence == []
    assert answer.confidence.value == "low"


def test_answer_question_raises_on_malformed_response() -> None:
    provider = _StubProvider(response="not json")
    with pytest.raises(AISynthesisError):
        answer_question("how does authentication work", _snapshot(), provider)
