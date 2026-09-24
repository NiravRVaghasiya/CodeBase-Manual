"""Tests for deterministic, evidence-derived confidence."""

from __future__ import annotations

from codebase_manual.ai.confidence import confidence_for_summary, confidence_from_strengths
from codebase_manual.ai.models import Confidence
from codebase_manual.domain.evidence import EvidenceStrength


def test_no_evidence_is_low_confidence() -> None:
    assert confidence_from_strengths([]) is Confidence.LOW


def test_only_unknown_evidence_is_low_confidence() -> None:
    assert confidence_from_strengths([EvidenceStrength.UNKNOWN]) is Confidence.LOW


def test_all_direct_or_resolved_evidence_is_high_confidence() -> None:
    assert (
        confidence_from_strengths([EvidenceStrength.DIRECT, EvidenceStrength.RESOLVED])
        is Confidence.HIGH
    )


def test_mixed_evidence_is_medium_confidence() -> None:
    assert (
        confidence_from_strengths([EvidenceStrength.DIRECT, EvidenceStrength.INFERRED])
        is Confidence.MEDIUM
    )


def test_purely_inferred_evidence_is_medium_confidence() -> None:
    assert confidence_from_strengths([EvidenceStrength.INFERRED]) is Confidence.MEDIUM


def test_summary_confidence_prefers_docstring_over_structure() -> None:
    assert (
        confidence_for_summary(has_docstring=True, has_structural_facts=False)
        is Confidence.HIGH
    )
    assert (
        confidence_for_summary(has_docstring=False, has_structural_facts=True)
        is Confidence.MEDIUM
    )
    assert (
        confidence_for_summary(has_docstring=False, has_structural_facts=False)
        is Confidence.LOW
    )
