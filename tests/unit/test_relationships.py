"""Tests for deterministic relationship derivation."""

from __future__ import annotations

from pathlib import Path

from codebase_manual.analyzer.python_analyzer import analyze_module
from codebase_manual.domain.models import PythonModule, RelationshipKind
from codebase_manual.domain.relationships import build_relationships


def _module(tmp_path: Path, name: str, source: str) -> PythonModule:
    relative_path = f"{name.replace('.', '/')}.py"
    file_path = tmp_path / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(source, encoding="utf-8")
    return analyze_module(file_path=file_path, repo_relative_path=relative_path, module_name=name)


def test_contains_relationships_link_file_module_and_symbols(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "pkg.mod",
        "def greet():\n    pass\n\nclass Foo:\n    def bar(self):\n        pass\n",
    )
    relationships = build_relationships([module])
    edges = {(r.kind, r.source.identifier, r.target.identifier) for r in relationships}

    assert (RelationshipKind.CONTAINS, "pkg/mod.py", "pkg.mod") in edges
    assert (RelationshipKind.CONTAINS, "pkg.mod", "pkg.mod.greet") in edges
    assert (RelationshipKind.CONTAINS, "pkg.mod", "pkg.mod.Foo") in edges
    assert (RelationshipKind.CONTAINS, "pkg.mod.Foo", "pkg.mod.Foo.bar") in edges


def test_imports_relationship_links_known_modules(tmp_path: Path) -> None:
    base = _module(tmp_path, "pkg.base", "class Base:\n    pass\n")
    user = _module(tmp_path, "pkg.user", "from pkg.base import Base\n")
    relationships = build_relationships([base, user])

    imports = [r for r in relationships if r.kind is RelationshipKind.IMPORTS]
    assert any(
        r.source.identifier == "pkg.user" and r.target.identifier == "pkg.base" for r in imports
    )


def test_imports_relationship_ignores_external_modules(tmp_path: Path) -> None:
    module = _module(tmp_path, "pkg.mod", "import os\nimport requests\n")
    relationships = build_relationships([module])

    assert [r for r in relationships if r.kind is RelationshipKind.IMPORTS] == []


def test_inherits_relationship_resolves_imported_base(tmp_path: Path) -> None:
    base = _module(tmp_path, "pkg.base", "class Base:\n    pass\n")
    child = _module(
        tmp_path,
        "pkg.child",
        "from pkg.base import Base\n\nclass Child(Base):\n    pass\n",
    )
    relationships = build_relationships([base, child])

    inherits = [r for r in relationships if r.kind is RelationshipKind.INHERITS]
    assert any(
        r.source.identifier == "pkg.child.Child" and r.target.identifier == "pkg.base.Base"
        for r in inherits
    )


def test_inherits_relationship_skips_unresolved_bases(tmp_path: Path) -> None:
    module = _module(tmp_path, "pkg.mod", "class Foo(SomeExternalBase):\n    pass\n")
    relationships = build_relationships([module])

    assert [r for r in relationships if r.kind is RelationshipKind.INHERITS] == []


def test_calls_relationship_resolves_local_function_call(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "pkg.mod",
        "def helper():\n    pass\n\ndef main():\n    helper()\n",
    )
    relationships = build_relationships([module])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    assert any(
        r.source.identifier == "pkg.mod.main" and r.target.identifier == "pkg.mod.helper"
        for r in calls
    )


def test_calls_relationship_resolves_self_method_call(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "pkg.mod",
        (
            "class Service:\n"
            "    def run(self):\n"
            "        self.helper()\n"
            "\n"
            "    def helper(self):\n"
            "        pass\n"
        ),
    )
    relationships = build_relationships([module])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    assert any(
        r.source.identifier == "pkg.mod.Service.run"
        and r.target.identifier == "pkg.mod.Service.helper"
        for r in calls
    )


def test_calls_relationship_skips_unresolvable_expressions(tmp_path: Path) -> None:
    module = _module(tmp_path, "pkg.mod", "def main(service):\n    service.run()\n")
    relationships = build_relationships([module])

    assert [r for r in relationships if r.kind is RelationshipKind.CALLS] == []


def test_calls_relationship_location_points_to_the_call_site_not_the_function(
    tmp_path: Path,
) -> None:
    module = _module(
        tmp_path,
        "pkg.mod",
        "def helper():\n    pass\n\n\ndef main():\n    x = 1\n    helper()\n",
    )
    relationships = build_relationships([module])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    call = next(r for r in calls if r.target.identifier == "pkg.mod.helper")
    assert call.location is not None
    # `helper()` is on line 7, not line 5 where `def main():` starts.
    assert call.location.line_start == 7


def test_tests_relationship_requires_a_resolved_call_not_just_an_import(tmp_path: Path) -> None:
    target = _module(tmp_path, "pkg.mod", "def greet():\n    pass\n")
    test_module = _module(
        tmp_path,
        "tests.test_mod",
        "from pkg.mod import greet\n\ndef test_greet():\n    greet()\n",
    )
    relationships = build_relationships([target, test_module])

    tests = [r for r in relationships if r.kind is RelationshipKind.TESTS]
    assert any(
        r.source.identifier == "tests.test_mod" and r.target.identifier == "pkg.mod" for r in tests
    )


def test_tests_relationship_is_not_asserted_from_import_alone(tmp_path: Path) -> None:
    target = _module(tmp_path, "pkg.mod", "def greet():\n    pass\n")
    test_module = _module(
        tmp_path,
        "tests.test_mod",
        "from pkg.mod import greet\n\ndef test_something_unrelated():\n    assert 1 == 1\n",
    )
    relationships = build_relationships([target, test_module])

    assert [r for r in relationships if r.kind is RelationshipKind.TESTS] == []


def test_tests_relationship_is_asserted_from_constructing_an_imported_class(tmp_path: Path) -> None:
    target = _module(
        tmp_path,
        "pkg.service",
        "class Service:\n    def __init__(self):\n        pass\n",
    )
    test_module = _module(
        tmp_path,
        "tests.test_service",
        "from pkg.service import Service\n\ndef test_construct():\n    Service()\n",
    )
    relationships = build_relationships([target, test_module])

    tests = [r for r in relationships if r.kind is RelationshipKind.TESTS]
    assert any(
        r.source.identifier == "tests.test_service" and r.target.identifier == "pkg.service"
        for r in tests
    )
