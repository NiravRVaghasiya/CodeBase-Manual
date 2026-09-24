"""Tests for relationship graph traversal."""

from __future__ import annotations

from codebase_manual.domain.models import EntityKind, EntityRef, Relationship, RelationshipKind
from codebase_manual.query.graph import RelationshipGraph


def _ref(kind: EntityKind, identifier: str) -> EntityRef:
    return EntityRef(kind=kind, identifier=identifier)


def _rel(kind: RelationshipKind, source: EntityRef, target: EntityRef) -> Relationship:
    return Relationship(kind=kind, source=source, target=target, evidence="test evidence")


MOD_A = _ref(EntityKind.MODULE, "pkg.a")
MOD_B = _ref(EntityKind.MODULE, "pkg.b")
MOD_C = _ref(EntityKind.MODULE, "pkg.c")
TEST_MOD = _ref(EntityKind.MODULE, "tests.test_a")
FUNC_A = _ref(EntityKind.FUNCTION, "pkg.a.run")


def _graph() -> RelationshipGraph:
    return RelationshipGraph(
        [
            _rel(RelationshipKind.IMPORTS, MOD_B, MOD_A),  # b imports a
            _rel(RelationshipKind.IMPORTS, MOD_C, MOD_B),  # c imports b
            _rel(RelationshipKind.TESTS, TEST_MOD, MOD_A),
            _rel(RelationshipKind.CONTAINS, MOD_A, FUNC_A),
        ]
    )


def test_dependencies_of_returns_outgoing_dependency_edges() -> None:
    graph = _graph()
    deps = graph.dependencies_of(MOD_B)
    assert [d.target for d in deps] == [MOD_A]


def test_dependents_of_returns_direct_incoming_dependency_edges() -> None:
    graph = _graph()
    dependents = graph.dependents_of(MOD_A)
    assert [d.source for d in dependents] == [MOD_B]


def test_transitive_dependents_follows_chain() -> None:
    graph = _graph()
    transitive = graph.transitive_dependents(MOD_A)
    assert set(transitive) == {MOD_B, MOD_C}


def test_transitive_dependents_of_leaf_is_empty() -> None:
    graph = _graph()
    assert graph.transitive_dependents(MOD_C) == []


def test_tests_for_returns_test_modules() -> None:
    graph = _graph()
    tests = graph.tests_for(MOD_A)
    assert [t.source for t in tests] == [TEST_MOD]


def test_members_of_returns_contains_children() -> None:
    graph = _graph()
    members = graph.members_of(MOD_A)
    assert [m.target for m in members] == [FUNC_A]


def test_transitive_dependents_traversal_reports_no_truncation_within_depth() -> None:
    graph = _graph()
    traversal = graph.transitive_dependents_traversal(MOD_A)

    assert set(traversal.entities) == {MOD_B, MOD_C}
    assert traversal.truncated is False


def test_transitive_dependents_traversal_reports_truncation_at_depth_limit() -> None:
    graph = _graph()
    traversal = graph.transitive_dependents_traversal(MOD_A, max_depth=1)

    # Only MOD_B is reachable within one hop; MOD_C is one hop further and
    # was never explored -- this must be reported, not silently omitted.
    assert traversal.entities == [MOD_B]
    assert traversal.truncated is True


def test_unknown_entity_has_no_edges() -> None:
    graph = _graph()
    unknown = _ref(EntityKind.MODULE, "pkg.unknown")
    assert graph.dependents_of(unknown) == []
    assert graph.dependencies_of(unknown) == []
