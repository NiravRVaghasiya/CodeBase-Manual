"""Validates structured AI output against deterministic repository facts.

Every candidate ID an LLM response references is resolved here, against the
same `CandidateSet` the model was given -- an invented ID simply fails to
resolve. Nothing the model writes becomes part of a returned result without
passing through this validator first. A result touched by any rejected
reference is `PARTIALLY_VALID`, never silently `VALID`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from codebase_manual.domain.models import EntityRef
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.candidates import CandidateSet


class ValidationVerdict(StrEnum):
    VALID = "valid"
    PARTIALLY_VALID = "partially_valid"
    INVALID = "invalid"


@dataclass
class ValidationResult:
    verdict: ValidationVerdict
    resolved_refs: list[EntityRef] = field(default_factory=list)
    rejected_ids: list[str] = field(default_factory=list)


def combine_verdicts(verdicts: list[ValidationVerdict]) -> ValidationVerdict:
    """Combine several per-field verdicts into one overall verdict for a result.

    Any rejection anywhere downgrades the whole result: VALID only if every
    field was VALID; INVALID only if every field was INVALID (or there were
    no fields at all); otherwise PARTIALLY_VALID.
    """
    if not verdicts:
        return ValidationVerdict.VALID
    if all(v is ValidationVerdict.VALID for v in verdicts):
        return ValidationVerdict.VALID
    if all(v is ValidationVerdict.INVALID for v in verdicts):
        return ValidationVerdict.INVALID
    return ValidationVerdict.PARTIALLY_VALID


class GroundingValidator:
    """Resolves LLM-referenced candidate IDs and proposed paths against deterministic facts."""

    def __init__(self, candidates: CandidateSet, snapshot: RepositorySnapshot) -> None:
        self._candidates = candidates
        self._existing_paths = {f.path for f in snapshot.files} | {
            m.path for m in snapshot.modules
        }

    def resolve_candidate_ids(self, candidate_ids: list[str]) -> ValidationResult:
        """Resolve a list of candidate IDs the model returned for one field.

        An empty input list is VALID-and-empty (the model simply didn't
        reference anything for this field) -- that is not the same as
        every ID failing to resolve.
        """
        if not candidate_ids:
            return ValidationResult(verdict=ValidationVerdict.VALID)
        resolved, unknown = self._candidates.resolve_many(candidate_ids)
        if not unknown:
            return ValidationResult(verdict=ValidationVerdict.VALID, resolved_refs=resolved)
        if not resolved:
            return ValidationResult(verdict=ValidationVerdict.INVALID, rejected_ids=unknown)
        return ValidationResult(
            verdict=ValidationVerdict.PARTIALLY_VALID,
            resolved_refs=resolved,
            rejected_ids=unknown,
        )

    def resolve_candidate_id(self, candidate_id: str) -> EntityRef | None:
        return self._candidates.resolve(candidate_id)

    def path_exists(self, path: str) -> bool:
        return path in self._existing_paths

    def validate_proposed_path(self, path: str) -> ValidationVerdict:
        """A CREATE target is only legitimate if it does not already exist.

        A path that already exists was mislabeled -- it should have been a
        MODIFY candidate ID, not a proposed new file.
        """
        return ValidationVerdict.INVALID if self.path_exists(path) else ValidationVerdict.VALID
