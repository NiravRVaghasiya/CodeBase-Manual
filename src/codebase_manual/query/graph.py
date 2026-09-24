"""Deterministic traversal over relationship facts.

This only ever reads edges that `domain.relationships` already asserted
with evidence -- it never invents a dependency.
"""

from __future__ import annotations

from collections import defaultdict

from codebase_manual.domain.models import EntityRef, Relationship, RelationshipKind

# Kinds that represent "A depends on B" when followed forward from A.
DEPENDENCY_KINDS = (RelationshipKind.IMPORTS, RelationshipKind.CALLS, RelationshipKind.INHERITS)


class RelationshipGraph:
    """An adjacency index over a snapshot's relationships, for traversal queries."""

    def __init__(self, relationships: list[Relationship]) -> None:
        self.relationships = relationships
        self._outgoing: dict[EntityRef, list[Relationship]] = defaultdict(list)
        self._incoming: dict[EntityRef, list[Relationship]] = defaultdict(list)
        for rel in relationships:
            self._outgoing[rel.source].append(rel)
            self._incoming[rel.target].append(rel)

    def outgoing(
        self, ref: EntityRef, kinds: tuple[RelationshipKind, ...] | None = None
    ) -> list[Relationship]:
        edges = self._outgoing.get(ref, [])
        return edges if kinds is None else [e for e in edges if e.kind in kinds]

    def incoming(
        self, ref: EntityRef, kinds: tuple[RelationshipKind, ...] | None = None
    ) -> list[Relationship]:
        edges = self._incoming.get(ref, [])
        return edges if kinds is None else [e for e in edges if e.kind in kinds]

    def dependencies_of(self, ref: EntityRef) -> list[Relationship]:
        """What `ref` depends on: its outgoing imports/calls/inherits edges."""
        return self.outgoing(ref, DEPENDENCY_KINDS)

    def dependents_of(self, ref: EntityRef) -> list[Relationship]:
        """What directly depends on `ref`: its incoming imports/calls/inherits edges."""
        return self.incoming(ref, DEPENDENCY_KINDS)

    def transitive_dependents(self, ref: EntityRef, max_depth: int = 5) -> list[EntityRef]:
        """All entities that depend on `ref`, directly or through a chain, in BFS order."""
        visited: set[EntityRef] = {ref}
        frontier = [ref]
        collected: list[EntityRef] = []

        for _ in range(max_depth):
            next_frontier: list[EntityRef] = []
            for node in frontier:
                for edge in self.dependents_of(node):
                    if edge.source in visited:
                        continue
                    visited.add(edge.source)
                    collected.append(edge.source)
                    next_frontier.append(edge.source)
            if not next_frontier:
                break
            frontier = next_frontier

        return collected

    def tests_for(self, ref: EntityRef) -> list[Relationship]:
        """Test modules with a direct TESTS edge to `ref`."""
        return self.incoming(ref, (RelationshipKind.TESTS,))

    def members_of(self, ref: EntityRef) -> list[Relationship]:
        """Direct CONTAINS children of `ref` (a module's functions/classes, a class's methods)."""
        return self.outgoing(ref, (RelationshipKind.CONTAINS,))
