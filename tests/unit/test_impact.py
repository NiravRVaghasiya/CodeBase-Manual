"""Tests for impact analysis."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from codebase_manual.ai.impact import analyze_impact, compute_impact_facts
from codebase_manual.domain.models import EntityKind, EntityRef, Relationship, RelationshipKind
from codebase_manual.persistence.snapshot import RepositorySnapshot


class _StubProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt: str | None = None

    def complete(self, *, system: str, prompt: str) -> str:
        self.last_prompt = prompt
        return self.response


def _ref(kind: EntityKind, identifier: str) -> EntityRef:
    return EntityRef(kind=kind, identifier=identifier)


MOD_A = _ref(EntityKind.MODULE, "pkg.a")
MOD_B = _ref(EntityKind.MODULE, "pkg.b")
MOD_C = _ref(EntityKind.MODULE, "pkg.c")
TEST_MOD = _ref(EntityKind.MODULE, "tests.test_a")


def _snapshot() -> RepositorySnapshot:
    relationships = [
        Relationship(
            kind=RelationshipKind.IMPORTS, source=MOD_B, target=MOD_A, evidence="b imports a"
        ),
        Relationship(
            kind=RelationshipKind.IMPORTS, source=MOD_C, target=MOD_B, evidence="c imports b"
        ),
        Relationship(
            kind=RelationshipKind.TESTS, source=TEST_MOD, target=MOD_A, evidence="test imports a"
        ),
    ]
    return RepositorySnapshot(
        repository_identity="repo",
        repository_root="/repo",
        commit_sha="abc",
        branch="main",
        remote_url=None,
        indexed_at=datetime.now(UTC),
        files=[],
        modules=[],
        relationships=relationships,
    )


def test_compute_impact_facts_separates_direct_and_indirect_dependents() -> None:
    facts = compute_impact_facts(MOD_A, _snapshot())

    assert [ref.identifier for ref in facts.direct_dependents] == ["pkg.b"]
    assert [ref.identifier for ref in facts.indirect_dependents] == ["pkg.c"]
    assert [ref.identifier for ref in facts.affected_tests] == ["tests.test_a"]


def test_analyze_impact_grounds_explanation_in_computed_facts() -> None:
    response = json.dumps(
        {"explanation": "Changing pkg.a affects pkg.b directly and pkg.c indirectly."}
    )
    provider = _StubProvider(response)

    report = analyze_impact(MOD_A, _snapshot(), provider)

    assert report.direct_dependents == ["pkg.b"]
    assert report.indirect_dependents == ["pkg.c"]
    assert report.affected_tests == ["tests.test_a"]
    assert "pkg.b" in (provider.last_prompt or "")
    # IMPORTS and TESTS edges are both RESOLVED evidence -> HIGH, never
    # taken from the model's own response.
    assert report.confidence.value == "high"


def _chain(length: int) -> tuple[list[EntityRef], RepositorySnapshot]:
    """A straight-line dependency chain: chain[i] imports chain[i - 1]."""
    chain = [_ref(EntityKind.MODULE, f"pkg.chain{i}") for i in range(length)]
    relationships = [
        Relationship(
            kind=RelationshipKind.IMPORTS,
            source=chain[i],
            target=chain[i - 1],
            evidence="chain",
        )
        for i in range(1, len(chain))
    ]
    snapshot = RepositorySnapshot(
        repository_identity="repo",
        repository_root="/repo",
        commit_sha="abc",
        branch="main",
        remote_url=None,
        indexed_at=datetime.now(UTC),
        files=[],
        modules=[],
        relationships=relationships,
    )
    return chain, snapshot


def test_compute_impact_facts_reports_truncation_beyond_the_depth_limit() -> None:
    chain, snapshot = _chain(7)

    facts = compute_impact_facts(chain[0], snapshot)

    assert facts.truncated is True
    assert chain[6].identifier not in {ref.identifier for ref in facts.indirect_dependents}


def test_analyze_impact_caps_confidence_at_medium_when_truncated() -> None:
    chain, snapshot = _chain(7)
    provider = _StubProvider(json.dumps({"explanation": "Deep dependency chain."}))

    report = analyze_impact(chain[0], snapshot, provider)

    assert report.truncated is True
    assert report.confidence.value == "medium"


def test_analyze_impact_is_high_confidence_when_nothing_depends_on_target() -> None:
    response = json.dumps({"explanation": "Nothing else in the repository depends on pkg.c."})
    provider = _StubProvider(response)

    report = analyze_impact(MOD_C, _snapshot(), provider)

    assert report.direct_dependents == []
    assert report.confidence.value == "high"
