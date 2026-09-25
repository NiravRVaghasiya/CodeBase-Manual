"""Tests for deterministic relationship derivation."""

from __future__ import annotations

from pathlib import Path

from codebase_manual.analyzer.python_analyzer import analyze_module
from codebase_manual.domain.models import EntityKind, PythonModule, RelationshipKind
from codebase_manual.domain.relationships import (
    build_relationships,
    build_relationships_with_unresolved,
)


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


# -- Type-aware call resolution (constructor-injected dependencies, direct
# construction, dataclass-style attributes, factories, inheritance) --


def test_calls_relationship_resolves_constructor_injected_attribute_call(
    tmp_path: Path,
) -> None:
    """The flagship case: `self._repo.find_user()` resolves via the type of the
    constructor parameter it was assigned from, not dropped as unresolvable."""
    repo = _module(
        tmp_path,
        "pkg.repository",
        "class UserRepository:\n    def find_user(self):\n        pass\n",
    )
    service = _module(
        tmp_path,
        "pkg.service",
        (
            "from pkg.repository import UserRepository\n\n"
            "class AuthService:\n"
            "    def __init__(self, repository: UserRepository):\n"
            "        self._repo = repository\n\n"
            "    def login(self):\n"
            "        return self._repo.find_user()\n"
        ),
    )
    relationships = build_relationships([repo, service])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    assert any(
        r.source.identifier == "pkg.service.AuthService.login"
        and r.target.identifier == "pkg.repository.UserRepository.find_user"
        for r in calls
    )


def test_calls_relationship_resolves_attribute_set_by_direct_construction(
    tmp_path: Path,
) -> None:
    repo = _module(
        tmp_path,
        "pkg.repository",
        "class UserRepository:\n    def find_user(self):\n        pass\n",
    )
    service = _module(
        tmp_path,
        "pkg.service",
        (
            "from pkg.repository import UserRepository\n\n"
            "class AuthService:\n"
            "    def __init__(self):\n"
            "        self._repo = UserRepository()\n\n"
            "    def login(self):\n"
            "        return self._repo.find_user()\n"
        ),
    )
    relationships = build_relationships([repo, service])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    assert any(
        r.source.identifier == "pkg.service.AuthService.login"
        and r.target.identifier == "pkg.repository.UserRepository.find_user"
        for r in calls
    )


def test_calls_relationship_resolves_dataclass_style_class_annotated_attribute(
    tmp_path: Path,
) -> None:
    """No `__init__` at all -- the attribute's type comes from a class-level annotation."""
    repo = _module(
        tmp_path,
        "pkg.repository",
        "class UserRepository:\n    def find_user(self):\n        pass\n",
    )
    service = _module(
        tmp_path,
        "pkg.service",
        (
            "from pkg.repository import UserRepository\n\n"
            "class AuthService:\n"
            "    repo: UserRepository\n\n"
            "    def login(self):\n"
            "        return self.repo.find_user()\n"
        ),
    )
    relationships = build_relationships([repo, service])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    assert any(
        r.source.identifier == "pkg.service.AuthService.login"
        and r.target.identifier == "pkg.repository.UserRepository.find_user"
        for r in calls
    )


def test_calls_relationship_resolves_local_variable_direct_construction(tmp_path: Path) -> None:
    module = _module(
        tmp_path,
        "pkg.mod",
        (
            "class Repo:\n"
            "    def find(self):\n"
            "        pass\n\n"
            "def main():\n"
            "    repo = Repo()\n"
            "    repo.find()\n"
        ),
    )
    relationships = build_relationships([module])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    assert any(
        r.source.identifier == "pkg.mod.main" and r.target.identifier == "pkg.mod.Repo.find"
        for r in calls
    )


def test_calls_relationship_resolves_local_variable_via_typed_parameter_propagation(
    tmp_path: Path,
) -> None:
    """Simple assignment-based type propagation: `x = repo` for a typed parameter `repo`."""
    module = _module(
        tmp_path,
        "pkg.mod",
        (
            "class Repo:\n"
            "    def find(self):\n"
            "        pass\n\n"
            "def main(repo: Repo):\n"
            "    x = repo\n"
            "    x.find()\n"
        ),
    )
    relationships = build_relationships([module])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    assert any(
        r.source.identifier == "pkg.mod.main" and r.target.identifier == "pkg.mod.Repo.find"
        for r in calls
    )


def test_calls_relationship_resolves_local_variable_via_factory_return_annotation(
    tmp_path: Path,
) -> None:
    module = _module(
        tmp_path,
        "pkg.mod",
        (
            "class Repo:\n"
            "    def find(self):\n"
            "        pass\n\n"
            "def get_repo() -> Repo:\n"
            "    return Repo()\n\n"
            "def main():\n"
            "    repo = get_repo()\n"
            "    repo.find()\n"
        ),
    )
    relationships = build_relationships([module])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    assert any(
        r.source.identifier == "pkg.mod.main" and r.target.identifier == "pkg.mod.Repo.find"
        for r in calls
    )


def test_calls_relationship_resolves_self_method_defined_on_a_base_class(
    tmp_path: Path,
) -> None:
    """Inheritance-aware method resolution: `self.helper()` where `helper` is only
    defined on the (locally resolvable) base class, not the class itself."""
    module = _module(
        tmp_path,
        "pkg.mod",
        (
            "class Base:\n"
            "    def helper(self):\n"
            "        pass\n\n"
            "class Child(Base):\n"
            "    def run(self):\n"
            "        self.helper()\n"
        ),
    )
    relationships = build_relationships([module])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    assert any(
        r.source.identifier == "pkg.mod.Child.run" and r.target.identifier == "pkg.mod.Base.helper"
        for r in calls
    )


def test_calls_relationship_resolves_attribute_set_by_a_base_class_constructor(
    tmp_path: Path,
) -> None:
    """Inheritance-aware attribute resolution: `self._repo` is assigned in the base
    class's `__init__`; a subclass method reads it through the same attribute name."""
    repo = _module(
        tmp_path,
        "pkg.repository",
        "class UserRepository:\n    def find_user(self):\n        pass\n",
    )
    module = _module(
        tmp_path,
        "pkg.service",
        (
            "from pkg.repository import UserRepository\n\n"
            "class BaseService:\n"
            "    def __init__(self, repository: UserRepository):\n"
            "        self._repo = repository\n\n"
            "class AuthService(BaseService):\n"
            "    def login(self):\n"
            "        return self._repo.find_user()\n"
        ),
    )
    relationships = build_relationships([repo, module])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    assert any(
        r.source.identifier == "pkg.service.AuthService.login"
        and r.target.identifier == "pkg.repository.UserRepository.find_user"
        for r in calls
    )


def test_calls_relationship_does_not_resolve_a_reassigned_ambiguous_local_variable(
    tmp_path: Path,
) -> None:
    """A local variable assigned two different resolvable types is ambiguous -- dropped,
    not guessed as whichever assignment happened to be seen first or last."""
    module = _module(
        tmp_path,
        "pkg.mod",
        (
            "class Foo:\n"
            "    def method(self):\n"
            "        pass\n\n"
            "class Bar:\n"
            "    def method(self):\n"
            "        pass\n\n"
            "def main(flag):\n"
            "    if flag:\n"
            "        x = Foo()\n"
            "    else:\n"
            "        x = Bar()\n"
            "    x.method()\n"
        ),
    )
    relationships = build_relationships([module])

    # The constructor calls `Foo()`/`Bar()` themselves still resolve (unrelated to `x`'s
    # ambiguity) -- only a `.method()` call *through* the ambiguous variable `x` must not.
    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    method_calls = [c for c in calls if c.target.identifier.endswith(".method")]
    assert method_calls == []


def test_calls_relationship_still_skips_an_untyped_attribute_chain(tmp_path: Path) -> None:
    """No annotation and no resolvable assignment for `self._repo` -- stays unresolved."""
    module = _module(
        tmp_path,
        "pkg.mod",
        (
            "class Service:\n"
            "    def __init__(self, repository):\n"
            "        self._repo = repository\n\n"
            "    def run(self):\n"
            "        self._repo.find()\n"
        ),
    )
    relationships = build_relationships([module])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    assert [c for c in calls if c.source.identifier == "pkg.mod.Service.run"] == []


def test_tests_relationship_reaches_the_specific_method_via_a_typed_fixture(
    tmp_path: Path,
) -> None:
    """Test-to-code precision: a test function whose fixture parameter is typed as the
    class under test gets a TESTS edge straight to the specific method it calls, not
    only to the target module."""
    target = _module(
        tmp_path,
        "pkg.service",
        "class Service:\n    def do_work(self):\n        pass\n",
    )
    test_module = _module(
        tmp_path,
        "tests.test_service",
        (
            "from pkg.service import Service\n\n"
            "def test_do_work(service: Service):\n"
            "    service.do_work()\n"
        ),
    )
    relationships = build_relationships([target, test_module])

    tests = [r for r in relationships if r.kind is RelationshipKind.TESTS]
    assert any(
        r.source.identifier == "tests.test_service.test_do_work"
        and r.target.identifier == "pkg.service.Service.do_work"
        for r in tests
    )
    # The coarser module-level edge still exists alongside the finer one.
    assert any(
        r.source.identifier == "tests.test_service" and r.target.identifier == "pkg.service"
        for r in tests
    )


# -- CALL_UNKNOWN: unresolved calls recorded explicitly, not silently dropped --


def test_unresolved_call_is_recorded_for_a_call_through_an_untyped_parameter(
    tmp_path: Path,
) -> None:
    module = _module(tmp_path, "pkg.mod", "def main(service):\n    service.run()\n")
    result = build_relationships_with_unresolved([module])

    assert [r for r in result.relationships if r.kind is RelationshipKind.CALLS] == []
    assert len(result.unresolved_calls) == 1
    unresolved = result.unresolved_calls[0]
    assert unresolved.source.kind is EntityKind.FUNCTION
    assert unresolved.source.identifier == "pkg.mod.main"
    assert unresolved.expression == "service.run"
    assert unresolved.location.line_start == 2


def test_resolved_calls_are_not_also_recorded_as_unresolved(tmp_path: Path) -> None:
    module = _module(tmp_path, "pkg.mod", "def helper():\n    pass\n\ndef main():\n    helper()\n")
    result = build_relationships_with_unresolved([module])

    calls = [r for r in result.relationships if r.kind is RelationshipKind.CALLS]
    assert len(calls) == 1
    assert result.unresolved_calls == []


def test_module_level_call_resolves_to_a_calls_relationship_sourced_from_the_module(
    tmp_path: Path,
) -> None:
    module = _module(tmp_path, "pkg.mod", "def helper():\n    pass\n\nhelper()\n")
    relationships = build_relationships([module])

    calls = [r for r in relationships if r.kind is RelationshipKind.CALLS]
    assert any(
        r.source.kind is EntityKind.MODULE
        and r.source.identifier == "pkg.mod"
        and r.target.identifier == "pkg.mod.helper"
        for r in calls
    )


def test_unresolved_module_level_call_is_recorded(tmp_path: Path) -> None:
    module = _module(tmp_path, "pkg.mod", "router.add_api_route('/health', health_check)\n")
    result = build_relationships_with_unresolved([module])

    assert [r for r in result.relationships if r.kind is RelationshipKind.CALLS] == []
    assert len(result.unresolved_calls) == 1
    unresolved = result.unresolved_calls[0]
    assert unresolved.source.kind is EntityKind.MODULE
    assert unresolved.source.identifier == "pkg.mod"
    assert unresolved.expression == "router.add_api_route"
