"""First-class evidence: the only thing an AI-facing claim may point to.

`Evidence` always references deterministic entities (an `EntityRef`, a file
path, a source location) that already exist in the repository snapshot. It
is never constructed from an LLM-generated string -- callers build it from a
`Relationship` that `domain.relationships` already derived from concrete
syntax, or from another deterministic fact (a retrieval match, a graph
traversal step). `strength` is assigned here, deterministically, from how
the fact was derived; it is never read from a model's self-report.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from codebase_manual.domain.models import EntityKind, Relationship, RelationshipKind


class EvidenceType(StrEnum):
    FILE = "file"
    SYMBOL = "symbol"
    IMPORT = "import"
    CALL = "call"
    INHERITANCE = "inheritance"
    TEST = "test"
    API_ENDPOINT = "api_endpoint"
    SOURCE_LOCATION = "source_location"
    CONFIGURATION = "configuration"
    GRAPH_PATH = "graph_path"
    RETRIEVAL_MATCH = "retrieval_match"


class EvidenceStrength(StrEnum):
    """How directly an `Evidence` record was derived from source facts.

    DIRECT   -- read straight off the AST/filesystem, no resolution step
                (a docstring's text, a decorator's source, a file's existence).
    RESOLVED -- required a deterministic resolution step that can fail
                (an import resolved to a module, a call resolved to a callee,
                a base class resolved to a class).
    INFERRED -- a deterministic heuristic with acknowledged false-positive
                risk (keyword-overlap retrieval, directory proximity).
    UNKNOWN  -- the fact could not be determined. This record's presence is
                the evidence of "unknown" -- it must never be treated as a
                negative fact or silently dropped.
    """

    DIRECT = "direct"
    RESOLVED = "resolved"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


# Relationship kinds asserted by `domain.relationships` map to a fixed
# EvidenceType/EvidenceStrength -- this is a property of how each kind is
# derived, not a per-instance judgment call.
_RELATIONSHIP_EVIDENCE_TYPE: dict[RelationshipKind, EvidenceType] = {
    RelationshipKind.CONTAINS: EvidenceType.SYMBOL,
    RelationshipKind.IMPORTS: EvidenceType.IMPORT,
    RelationshipKind.CALLS: EvidenceType.CALL,
    RelationshipKind.INHERITS: EvidenceType.INHERITANCE,
    RelationshipKind.TESTS: EvidenceType.TEST,
}

_RELATIONSHIP_STRENGTH: dict[RelationshipKind, EvidenceStrength] = {
    RelationshipKind.CONTAINS: EvidenceStrength.DIRECT,
    RelationshipKind.IMPORTS: EvidenceStrength.RESOLVED,
    RelationshipKind.CALLS: EvidenceStrength.RESOLVED,
    RelationshipKind.INHERITS: EvidenceStrength.RESOLVED,
    # TESTS is asserted from a resolved call/construction made by a test
    # function (see domain.relationships._tests_relationships) -- the same
    # resolution mechanism as CALLS, not a bare import -- so it carries the
    # same strength.
    RelationshipKind.TESTS: EvidenceStrength.RESOLVED,
}


def strength_for_relationship_kind(kind: RelationshipKind) -> EvidenceStrength:
    """The deterministic evidence strength for a relationship kind, independent of any instance."""
    return _RELATIONSHIP_STRENGTH[kind]


class Evidence(BaseModel):
    """A single, typed, resolvable piece of evidence behind an AI-facing claim."""

    model_config = {"frozen": True}

    id: str
    type: EvidenceType
    source_entity_id: str | None
    target_entity_id: str | None
    file_path: str | None
    line_start: int | None
    line_end: int | None
    description: str
    strength: EvidenceStrength


def evidence_from_relationship(
    relationship: Relationship, *, id: str, file_path: str | None = None
) -> Evidence:
    """Build a structured `Evidence` record from an already-derived `Relationship`.

    `relationship` must come from `domain.relationships.build_relationships`,
    which only asserts facts backed by concrete syntax -- this never accepts
    an LLM claim as input.
    """
    location = relationship.location
    resolved_file_path = file_path
    if resolved_file_path is None and relationship.source.kind is EntityKind.FILE:
        resolved_file_path = relationship.source.identifier
    return Evidence(
        id=id,
        type=_RELATIONSHIP_EVIDENCE_TYPE[relationship.kind],
        source_entity_id=relationship.source.identifier,
        target_entity_id=relationship.target.identifier,
        file_path=resolved_file_path,
        line_start=location.line_start if location else None,
        line_end=location.line_end if location else None,
        description=relationship.evidence,
        strength=_RELATIONSHIP_STRENGTH[relationship.kind],
    )
