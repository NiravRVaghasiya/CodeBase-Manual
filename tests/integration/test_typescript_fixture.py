"""Integration test: the TypeScript analyzer through the real scan/analyze/
relationship pipeline -- the same `domain.relationships.build_relationships_with_unresolved`
Python's fixtures exercise, with zero TypeScript-specific code in that function.

This is the architecture proof `docs/analyzers.md` describes: same-file
`CONTAINS`/`INHERITS`/`CALLS` resolution works identically to Python (see
`analyzer.typescript_analyzer`'s module docstring for the one addition this
needed elsewhere: `domain.relationships._resolve_call` recognizing `this`
as a self-reference alongside Python's `self`/`cls`). Cross-file relative
import resolution is a disclosed, real gap -- `_resolve_import_module`'s
relative-import logic assumes Python package semantics -- so the fixture
also proves that gap fails closed (an `UnresolvedCall`, not a fabricated
relationship) rather than silently working by accident.
"""

from __future__ import annotations

from pathlib import Path

from codebase_manual.analyzer.registry import analyze_repository
from codebase_manual.domain.models import RelationshipKind
from codebase_manual.domain.relationships import build_relationships_with_unresolved
from codebase_manual.repository.scanner import RepositoryScanner

FIXTURE_PROJECT = Path(__file__).parents[1] / "fixtures" / "typescript_project"


def _analyze_fixture():
    scanner = RepositoryScanner(FIXTURE_PROJECT)
    scan_result = scanner.scan()
    modules = analyze_repository(scanner.root, scan_result)
    return modules, build_relationships_with_unresolved(modules)


def test_typescript_files_are_scanned_and_analyzed() -> None:
    modules, _result = _analyze_fixture()
    module_names = {m.module_name for m in modules}

    assert "src.repository" in module_names
    assert "src.service" in module_names
    assert all(m.parse_error is None for m in modules)


def test_same_file_inheritance_resolves(tmp_path: Path) -> None:
    _modules, result = _analyze_fixture()

    inherits = [r for r in result.relationships if r.kind is RelationshipKind.INHERITS]
    assert any(
        r.source.identifier == "src.service.AuthService"
        and r.target.identifier == "src.service.BaseService"
        for r in inherits
    )


def test_this_reference_resolves_same_class_and_inherited_methods() -> None:
    """`this.lookup()` (defined on `AuthService` itself) and `this.describe()`
    (defined on its base `BaseService`) both resolve -- proving `this` is
    recognized as a self-reference and that inheritance-aware method
    resolution works for TypeScript through the unmodified Python resolver."""
    _modules, result = _analyze_fixture()

    calls = [r for r in result.relationships if r.kind is RelationshipKind.CALLS]
    login_calls = {
        r.target.identifier for r in calls if r.source.identifier == "src.service.AuthService.login"
    }
    assert "src.service.AuthService.lookup" in login_calls
    assert "src.service.BaseService.describe" in login_calls


def test_cross_file_relative_import_fails_closed_not_fabricated() -> None:
    """`buildRepository()`'s `new UserRepository()` construction cannot resolve
    cross-file (a disclosed, real limitation -- see this file's module
    docstring) -- it must show up as an `UnresolvedCall`, never as a
    fabricated `CALLS` relationship to something in another file."""
    _modules, result = _analyze_fixture()

    calls = [r for r in result.relationships if r.kind is RelationshipKind.CALLS]
    assert not any(r.target.identifier == "src.repository.UserRepository" for r in calls)
    assert any(
        u.source.identifier == "src.service.buildRepository" and u.expression == "UserRepository"
        for u in result.unresolved_calls
    )
