"""Tests for the grounding validator -- hallucination prevention at the trust boundary.

Each test corresponds to a scenario in the project's AI-grounding
requirements: an invented path/symbol/ID must never silently pass through
as if it were a real repository fact.
"""

from __future__ import annotations

from datetime import UTC, datetime

from codebase_manual.ai.grounding import GroundingValidator, ValidationVerdict, combine_verdicts
from codebase_manual.domain.models import PythonModule
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.candidates import build_candidate_set
from codebase_manual.query.retrieval import MatchReason, MatchSignal, RetrievalResult, RetrievedFile


def _snapshot(modules: list[PythonModule]) -> RepositorySnapshot:
    return RepositorySnapshot(
        repository_identity="repo",
        repository_root="/repo",
        commit_sha="abc",
        branch="main",
        remote_url=None,
        indexed_at=datetime.now(UTC),
        files=[],
        modules=modules,
        relationships=[],
    )


def _validator() -> GroundingValidator:
    module = PythonModule(path="app/auth/service.py", module_name="app.auth.service")
    retrieval = RetrievalResult(
        query_terms={"auth"},
        files=[
            RetrievedFile(
                module=module,
                signals=[MatchSignal(MatchReason.DOCSTRING, "docstring matches ['auth']")],
            )
        ],
    )
    candidates = build_candidate_set(retrieval)
    return GroundingValidator(candidates, _snapshot([module]))


def test_valid_candidate_ids_are_accepted() -> None:
    result = _validator().resolve_candidate_ids(["FILE_001"])

    assert result.verdict is ValidationVerdict.VALID
    assert [ref.identifier for ref in result.resolved_refs] == ["app/auth/service.py"]
    assert result.rejected_ids == []


def test_a_nonexistent_id_is_rejected_not_silently_dropped() -> None:
    result = _validator().resolve_candidate_ids(["FILE_999"])

    assert result.verdict is ValidationVerdict.INVALID
    assert result.resolved_refs == []
    assert result.rejected_ids == ["FILE_999"]


def test_mixed_ids_are_partially_valid_and_quarantine_only_the_invalid_one() -> None:
    result = _validator().resolve_candidate_ids(["FILE_001", "FILE_999"])

    assert result.verdict is ValidationVerdict.PARTIALLY_VALID
    assert [ref.identifier for ref in result.resolved_refs] == ["app/auth/service.py"]
    assert result.rejected_ids == ["FILE_999"]


def test_no_ids_referenced_is_trivially_valid() -> None:
    result = _validator().resolve_candidate_ids([])

    assert result.verdict is ValidationVerdict.VALID
    assert result.resolved_refs == []


def test_a_proposed_path_that_already_exists_is_invalid() -> None:
    assert _validator().validate_proposed_path("app/auth/service.py") is ValidationVerdict.INVALID


def test_a_genuinely_new_proposed_path_is_valid() -> None:
    assert _validator().validate_proposed_path("app/auth/oauth.py") is ValidationVerdict.VALID


def test_combine_verdicts_is_valid_only_if_every_verdict_is_valid() -> None:
    assert combine_verdicts([ValidationVerdict.VALID, ValidationVerdict.VALID]) is (
        ValidationVerdict.VALID
    )
    assert combine_verdicts([]) is ValidationVerdict.VALID


def test_combine_verdicts_is_invalid_only_if_every_verdict_is_invalid() -> None:
    assert combine_verdicts([ValidationVerdict.INVALID, ValidationVerdict.INVALID]) is (
        ValidationVerdict.INVALID
    )


def test_combine_verdicts_is_partially_valid_for_a_mix() -> None:
    assert combine_verdicts([ValidationVerdict.VALID, ValidationVerdict.INVALID]) is (
        ValidationVerdict.PARTIALLY_VALID
    )
