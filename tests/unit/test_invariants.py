"""Property-based invariant tests over randomly generated repositories.

No `hypothesis` dependency: a small hand-rolled generator (seeded
`random.Random`, deterministic per seed) produces real Python source for
several repository shapes, each run through the *real* scan/analyze/
build_relationships pipeline -- not hand-built domain objects -- so these
invariants hold against the same code path production traffic uses.
Deliberately includes unresolvable calls/bases/imports (dangling names)
in every generated repository: the invariant is that they're dropped, not
fabricated into a relationship pointing at a nonexistent entity.

Each invariant is checked against *every* relationship/entity the
generator produced across several seeds and sizes, not a handful of
hand-picked examples -- that breadth is what distinguishes this from the
existing example-based unit tests in `test_relationships.py`/`test_graph.py`.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

from codebase_manual.analyzer.registry import analyze_repository
from codebase_manual.domain.models import (
    EntityKind,
    EntityRef,
    PythonModule,
    Relationship,
    RelationshipKind,
    UnresolvedCall,
)
from codebase_manual.domain.relationships import build_relationships_with_unresolved
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.candidates import build_candidate_set
from codebase_manual.query.graph import RelationshipGraph
from codebase_manual.query.retrieval import retrieve_relevant
from codebase_manual.repository.scanner import RepositoryScanner

_SEEDS = (1, 2, 3, 4, 5)
_SIZES = (8, 16)


def _generate_repo(root: Path, rng: random.Random, module_count: int) -> None:
    """Modules with real (and some deliberately dangling) imports/calls/bases."""
    for i in range(module_count):
        earlier = list(range(i))
        import_lines = []
        for j in rng.sample(earlier, k=min(2, len(earlier))):
            import_lines.append(f"from mod_{j} import helper_{j}, Thing{j}")

        base = f"Thing{rng.choice(earlier)}" if earlier and rng.random() < 0.5 else "NoSuchBase"
        call_target = (
            f"helper_{rng.choice(earlier)}" if earlier and rng.random() < 0.6 else "no_such_helper"
        )

        lines = [
            f'"""Module {i}."""',
            "",
            *import_lines,
            "",
            f"{call_target}(0)",  # a module-level call, not inside any function
            "",
            "",
            f"def helper_{i}(x):",
            f'    """Helper {i}."""',
            f"    return {call_target}(x) if x else x",
            "",
            "",
            f"class Thing{i}({base}):",
            f'    """Thing {i}."""',
            "",
            "    def method(self):",
            f"        return helper_{i}(1)",
            "",
        ]
        (root / f"mod_{i}.py").write_text("\n".join(lines), encoding="utf-8")

    test_lines = [
        '"""A test module exercising the last real module."""',
        "",
        f"from mod_{module_count - 1} import Thing{module_count - 1}",
        "",
        "",
        "def test_thing():",
        f"    thing = Thing{module_count - 1}()",
        "    assert thing.method() is not None",
        "",
    ]
    (root / "test_generated.py").write_text("\n".join(test_lines), encoding="utf-8")


def _real_entities(modules: list[PythonModule]) -> set[EntityRef]:
    entities: set[EntityRef] = set()
    for module in modules:
        entities.add(EntityRef(kind=EntityKind.FILE, identifier=module.path))
        if module.module_name:
            entities.add(EntityRef(kind=EntityKind.MODULE, identifier=module.module_name))
        for function in module.functions:
            entities.add(EntityRef(kind=EntityKind.FUNCTION, identifier=function.qualified_name))
        for klass in module.classes:
            entities.add(EntityRef(kind=EntityKind.CLASS, identifier=klass.qualified_name))
            for method in klass.methods:
                entities.add(EntityRef(kind=EntityKind.FUNCTION, identifier=method.qualified_name))
    return entities


_GeneratedRepo = tuple[list[PythonModule], list[Relationship], list[UnresolvedCall], set[EntityRef]]


@pytest.fixture(scope="session", params=[(seed, size) for seed in _SEEDS for size in _SIZES])
def generated_repo(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> _GeneratedRepo:
    """Session-scoped: the same (seed, size) repo is reused across every test
    function below rather than re-generated/re-analyzed per test."""
    seed, size = request.param
    rng = random.Random(seed)
    root = tmp_path_factory.mktemp(f"repo_{seed}_{size}")
    _generate_repo(root, rng, size)

    scanner = RepositoryScanner(root)
    scan_result = scanner.scan()
    modules = analyze_repository(scanner.root, scan_result)
    assert all(m.parse_error is None for m in modules), "generator produced invalid Python"
    result = build_relationships_with_unresolved(modules)
    return modules, result.relationships, result.unresolved_calls, _real_entities(modules)


def test_every_relationship_endpoint_resolves_to_a_real_entity(
    generated_repo: _GeneratedRepo,
) -> None:
    _modules, relationships, _unresolved_calls, real_entities = generated_repo
    assert relationships, "generator should always produce at least one relationship"

    for rel in relationships:
        assert rel.source in real_entities, f"dangling relationship source: {rel}"
        assert rel.target in real_entities, f"dangling relationship target: {rel}"


def test_every_tests_relationship_targets_a_real_entity(
    generated_repo: _GeneratedRepo,
) -> None:
    """TESTS is asserted at two granularities from the same resolved call: a
    module-level edge (MODULE source -> MODULE target) and a symbol-level
    edge (FUNCTION source -> the specific FUNCTION/CLASS resolved). Neither
    granularity may reference an entity that wasn't actually derived.
    """
    _modules, relationships, _unresolved_calls, real_entities = generated_repo

    for rel in relationships:
        if rel.kind is not RelationshipKind.TESTS:
            continue
        if rel.source.kind is EntityKind.MODULE:
            assert rel.target.kind is EntityKind.MODULE
        else:
            assert rel.source.kind is EntityKind.FUNCTION
            assert rel.target.kind in (EntityKind.FUNCTION, EntityKind.CLASS)
        assert rel.source in real_entities
        assert rel.target in real_entities


def test_every_calls_source_symbol_is_a_real_function_or_module(
    generated_repo: _GeneratedRepo,
) -> None:
    """A `CALLS` edge's source is almost always a function/method, but a call written
    directly at module scope (not inside any function) sources from the module itself
    -- see `domain.relationships._calls_relationships`'s module-level pass."""
    _modules, relationships, _unresolved_calls, real_entities = generated_repo

    for rel in relationships:
        if rel.kind is not RelationshipKind.CALLS:
            continue
        assert rel.source.kind in (EntityKind.FUNCTION, EntityKind.MODULE)
        assert rel.source in real_entities


def test_dangling_calls_and_bases_are_dropped_not_fabricated(
    generated_repo: _GeneratedRepo,
) -> None:
    """The generator always emits at least one `no_such_helper`/`NoSuchBase`
    reference; this asserts none of them ever became a relationship target --
    the resolver dropped them, per `domain.relationships`'s "never fabricate" rule.
    """
    _modules, relationships, _unresolved_calls, _real_entities = generated_repo

    assert all("NoSuchBase" not in rel.target.identifier for rel in relationships)
    assert all("no_such_helper" not in rel.target.identifier for rel in relationships)


def test_every_unresolved_call_source_is_a_real_function_or_module(
    generated_repo: _GeneratedRepo,
) -> None:
    """Every `UnresolvedCall` is recorded, never silently discarded past the
    point of derivation -- and its `source` (the containing function/method,
    or the module itself for a module-level call) must be a real entity, the
    same bar a `Relationship` endpoint is held to."""
    _modules, _relationships, unresolved_calls, real_entities = generated_repo

    for call in unresolved_calls:
        assert call.source.kind in (EntityKind.FUNCTION, EntityKind.MODULE)
        assert call.source in real_entities


def test_unresolved_calls_are_produced_on_realistic_generated_code(
    generated_repo: _GeneratedRepo,
) -> None:
    """The generator's `no_such_helper`/`NoSuchBase`-style dangling references (and,
    per the module-level statement `_generate_repo` now adds, dangling module-level
    calls too) should yield at least one `UnresolvedCall` per generated repository --
    confirming the mechanism actually fires on realistic generated code, not only
    the hand-crafted examples in `test_relationships.py`."""
    _modules, _relationships, unresolved_calls, _real_entities = generated_repo

    assert unresolved_calls, "generator's dangling references should yield unresolved calls"


def test_graph_traversal_never_yields_an_entity_outside_the_snapshot(
    generated_repo: _GeneratedRepo,
) -> None:
    _modules, relationships, _unresolved_calls, real_entities = generated_repo
    graph = RelationshipGraph(relationships)

    for entity in real_entities:
        for edge in (*graph.dependents_of(entity), *graph.dependencies_of(entity)):
            assert edge.source in real_entities
            assert edge.target in real_entities

        traversal = graph.transitive_dependents_traversal(entity, max_depth=len(real_entities))
        for found in traversal.entities:
            assert found in real_entities


def test_every_ai_referenced_candidate_id_resolves_or_is_quarantined(
    generated_repo: _GeneratedRepo,
) -> None:
    modules, relationships, _unresolved_calls, real_entities = generated_repo

    snapshot = RepositorySnapshot(
        repository_identity="invariant-test",
        repository_root="/repo",
        commit_sha=None,
        branch=None,
        remote_url=None,
        indexed_at=datetime.now(UTC),
        files=[],
        modules=modules,
        relationships=relationships,
    )
    retrieval = retrieve_relevant("helper thing", snapshot)
    if retrieval.is_empty:
        pytest.skip("no lexical match for this generated repository's names")

    candidates = build_candidate_set(retrieval)
    real_ids = [c.id for c in (*candidates.files, *candidates.symbols, *candidates.tests)]
    rng = random.Random(0)
    fake_ids = [f"FILE_{rng.randint(900, 999)}", f"SYMBOL_{rng.randint(900, 999)}"]
    mixed_ids = [*real_ids, *fake_ids]

    resolved, unknown = candidates.resolve_many(mixed_ids)

    assert set(unknown) == set(fake_ids) - set(real_ids)
    for ref in resolved:
        assert ref in real_entities
    assert len(resolved) + len(unknown) == len(mixed_ids)
