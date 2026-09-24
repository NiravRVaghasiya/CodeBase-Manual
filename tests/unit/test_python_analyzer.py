"""Tests for the Python AST analyzer."""

from __future__ import annotations

from pathlib import Path

from codebase_manual.analyzer.python_analyzer import analyze_module, infer_module_name
from codebase_manual.domain.models import ParameterKind, PythonModule


def _analyze(tmp_path: Path, source: str, *, filename: str = "module.py") -> PythonModule:
    file_path = tmp_path / filename
    file_path.write_text(source, encoding="utf-8")
    return analyze_module(file_path=file_path, repo_relative_path=filename, module_name="module")


def test_infer_module_name_for_regular_file() -> None:
    assert infer_module_name("app/users/models.py") == "app.users.models"


def test_infer_module_name_for_package_init() -> None:
    assert infer_module_name("app/users/__init__.py") == "app.users"


def test_infer_module_name_ignores_non_python_files() -> None:
    assert infer_module_name("app/config.yaml") is None


def test_analyze_module_extracts_docstring_and_module_name(tmp_path: Path) -> None:
    module = _analyze(tmp_path, '"""Module docstring."""\n')

    assert module.docstring == "Module docstring."
    assert module.module_name == "module"
    assert module.parse_error is None


def test_analyze_module_reports_syntax_error(tmp_path: Path) -> None:
    module = _analyze(tmp_path, "def broken(:\n")

    assert module.parse_error is not None
    assert module.functions == []


def test_analyze_module_reports_unreadable_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.py"

    module = analyze_module(
        file_path=missing, repo_relative_path="missing.py", module_name="missing"
    )

    assert module.parse_error is not None


def test_analyze_module_extracts_plain_import(tmp_path: Path) -> None:
    module = _analyze(tmp_path, "import os\nimport os.path\n")

    assert [(i.module, i.name, i.alias) for i in module.imports] == [
        ("os", None, None),
        ("os.path", None, None),
    ]


def test_analyze_module_extracts_from_import_with_alias(tmp_path: Path) -> None:
    module = _analyze(tmp_path, "from foo import bar as baz\n")

    imported = module.imports[0]
    assert imported.module == "foo"
    assert imported.name == "bar"
    assert imported.alias == "baz"
    assert imported.is_relative is False


def test_analyze_module_extracts_relative_import(tmp_path: Path) -> None:
    module = _analyze(tmp_path, "from . import sibling\nfrom ..pkg import other\n")

    first, second = module.imports
    assert first.is_relative is True
    assert first.relative_level == 1
    assert second.relative_level == 2


def test_analyze_module_extracts_function_signature(tmp_path: Path) -> None:
    source = (
        "def greet(name: str, *, loud: bool = False, **extra: str) -> str:\n"
        '    """Greet someone."""\n'
        "    return name\n"
    )
    module = _analyze(tmp_path, source)
    function = module.functions[0]

    assert function.name == "greet"
    assert function.qualified_name == "module.greet"
    assert function.return_annotation == "str"
    assert function.docstring == "Greet someone."
    assert function.is_async is False

    kinds = [p.kind for p in function.parameters]
    assert kinds == [
        ParameterKind.POSITIONAL,
        ParameterKind.KEYWORD_ONLY,
        ParameterKind.VAR_KEYWORD,
    ]

    loud_param = function.parameters[1]
    assert loud_param.name == "loud"
    assert loud_param.default == "False"
    assert loud_param.annotation == "bool"


def test_analyze_module_extracts_async_function_and_decorators(tmp_path: Path) -> None:
    source = "@staticmethod\nasync def fetch() -> None:\n    pass\n"
    module = _analyze(tmp_path, source)
    function = module.functions[0]

    assert function.is_async is True
    assert [d.expression for d in function.decorators] == ["staticmethod"]


def test_analyze_module_extracts_class_with_inheritance_and_methods(tmp_path: Path) -> None:
    source = (
        "class Base:\n"
        "    pass\n"
        "\n"
        "@dataclass\n"
        "class Child(Base):\n"
        '    """A child class."""\n'
        "    count: int = 0\n"
        "\n"
        "    def method(self) -> int:\n"
        "        return self.count\n"
    )
    module = _analyze(tmp_path, source)
    base, child = module.classes

    assert base.name == "Base"
    assert child.bases == ["Base"]
    assert [d.expression for d in child.decorators] == ["dataclass"]
    assert child.docstring == "A child class."
    assert [m.name for m in child.methods] == ["method"]
    assert child.methods[0].is_method is True
    assert child.methods[0].qualified_name == "module.Child.method"
    assert [v.name for v in child.class_variables] == ["count"]


def _call_expressions(function_name: str, module: PythonModule) -> list[str]:
    by_name = {f.name: f for f in module.functions}
    for klass in module.classes:
        by_name.update({m.name: m for m in klass.methods})
    return [c.expression for c in by_name[function_name].calls]


def test_infer_module_name_strips_a_configured_source_root() -> None:
    assert infer_module_name("src/app/service.py", ["src"]) == "app.service"


def test_infer_module_name_leaves_path_untouched_without_a_matching_root() -> None:
    assert infer_module_name("app/service.py", ["src"]) == "app.service"


def test_infer_module_name_handles_package_init_under_a_source_root() -> None:
    assert infer_module_name("src/app/__init__.py", ["src"]) == "app"


def test_calls_do_not_leak_from_a_nested_function_into_the_outer_one(tmp_path: Path) -> None:
    source = (
        "def outer():\n"
        "    prepare()\n"
        "\n"
        "    def inner():\n"
        "        execute()\n"
    )
    module = _analyze(tmp_path, source)

    assert _call_expressions("outer", module) == ["prepare"]
    assert _call_expressions("inner", module) == ["execute"]


def test_calls_do_not_leak_from_a_lambda_body(tmp_path: Path) -> None:
    source = "def outer():\n    prepare()\n    f = lambda: execute()\n"
    module = _analyze(tmp_path, source)

    assert _call_expressions("outer", module) == ["prepare"]


def test_calls_inside_a_comprehension_belong_to_the_enclosing_function(tmp_path: Path) -> None:
    source = "def outer():\n    return [transform(x) for x in items()]\n"
    module = _analyze(tmp_path, source)

    assert set(_call_expressions("outer", module)) == {"transform", "items"}


def test_calls_do_not_leak_from_a_nested_class_method(tmp_path: Path) -> None:
    source = (
        "def outer():\n"
        "    prepare()\n"
        "\n"
        "    class Inner:\n"
        "        def method(self):\n"
        "            execute()\n"
    )
    module = _analyze(tmp_path, source)

    assert _call_expressions("outer", module) == ["prepare"]
    assert _call_expressions("method", module) == ["execute"]


def test_a_nested_functions_decorator_call_belongs_to_the_outer_scope(tmp_path: Path) -> None:
    source = "def outer():\n    @some_decorator()\n    def inner():\n        pass\n"
    module = _analyze(tmp_path, source)

    assert _call_expressions("outer", module) == ["some_decorator"]
    assert _call_expressions("inner", module) == []


def test_calls_do_not_leak_from_a_nested_async_function(tmp_path: Path) -> None:
    source = (
        "def outer():\n"
        "    prepare()\n"
        "\n"
        "    async def inner():\n"
        "        await execute()\n"
    )
    module = _analyze(tmp_path, source)

    assert _call_expressions("outer", module) == ["prepare"]
    assert _call_expressions("inner", module) == ["execute"]


def test_call_site_records_its_own_line_not_the_functions(tmp_path: Path) -> None:
    source = "def outer():\n    x = 1\n    do_something()\n"
    module = _analyze(tmp_path, source)

    call_site = module.functions[0].calls[0]
    assert call_site.line == 3
    assert call_site.containing_symbol_id == "module.outer"


def test_analyze_module_extracts_module_level_variables(tmp_path: Path) -> None:
    source = "MAX_RETRIES: int = 3\nname = 'app'\n"
    module = _analyze(tmp_path, source)
    by_name = {v.name: v for v in module.variables}

    assert by_name["MAX_RETRIES"].is_constant is True
    assert by_name["MAX_RETRIES"].annotation == "int"
    assert by_name["name"].is_constant is False
