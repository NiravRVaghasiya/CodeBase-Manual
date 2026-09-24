"""Tests for the structured evidence model."""

from __future__ import annotations

from codebase_manual.domain.evidence import (
    EvidenceStrength,
    EvidenceType,
    evidence_from_relationship,
    strength_for_relationship_kind,
)
from codebase_manual.domain.models import (
    EntityKind,
    EntityRef,
    Relationship,
    RelationshipKind,
    SourceLocation,
)

_LOCATION = SourceLocation(line_start=10, line_end=12)


def _relationship(kind: RelationshipKind) -> Relationship:
    return Relationship(
        kind=kind,
        source=EntityRef(kind=EntityKind.FUNCTION, identifier="pkg.a.foo"),
        target=EntityRef(kind=EntityKind.FUNCTION, identifier="pkg.b.bar"),
        evidence="pkg.a.foo calls pkg.b.bar at pkg/a.py:10",
        location=_LOCATION,
    )


def test_evidence_from_relationship_references_the_relationship_endpoints() -> None:
    evidence = evidence_from_relationship(_relationship(RelationshipKind.CALLS), id="EV_001")

    assert evidence.source_entity_id == "pkg.a.foo"
    assert evidence.target_entity_id == "pkg.b.bar"
    assert evidence.line_start == 10
    assert evidence.line_end == 12
    assert evidence.type is EvidenceType.CALL
    assert evidence.strength is EvidenceStrength.RESOLVED


def test_evidence_from_relationship_derives_file_path_from_file_source() -> None:
    relationship = Relationship(
        kind=RelationshipKind.CONTAINS,
        source=EntityRef(kind=EntityKind.FILE, identifier="pkg/a.py"),
        target=EntityRef(kind=EntityKind.MODULE, identifier="pkg.a"),
        evidence="pkg/a.py defines module `pkg.a`",
    )

    evidence = evidence_from_relationship(relationship, id="EV_002")

    assert evidence.file_path == "pkg/a.py"
    assert evidence.strength is EvidenceStrength.DIRECT


def test_evidence_from_relationship_accepts_an_explicit_file_path() -> None:
    evidence = evidence_from_relationship(
        _relationship(RelationshipKind.IMPORTS), id="EV_003", file_path="pkg/a.py"
    )

    assert evidence.file_path == "pkg/a.py"
    assert evidence.strength is EvidenceStrength.RESOLVED


def test_tests_relationship_is_resolved_strength() -> None:
    # TESTS is asserted from a resolved call/construction made by a test
    # function -- the same resolution mechanism as CALLS -- not a bare
    # import, so it carries the same strength.
    assert strength_for_relationship_kind(RelationshipKind.TESTS) is EvidenceStrength.RESOLVED
    assert strength_for_relationship_kind(RelationshipKind.IMPORTS) is EvidenceStrength.RESOLVED
