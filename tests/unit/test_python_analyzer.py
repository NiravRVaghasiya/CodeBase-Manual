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


def test_analyze_module_extracts_module_level_variables(tmp_path: Path) -> None:
    source = "MAX_RETRIES: int = 3\nname = 'app'\n"
    module = _analyze(tmp_path, source)
    by_name = {v.name: v for v in module.variables}

    assert by_name["MAX_RETRIES"].is_constant is True
    assert by_name["MAX_RETRIES"].annotation == "int"
    assert by_name["name"].is_constant is False
