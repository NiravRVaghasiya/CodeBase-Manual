"""Tests for the TypeScript analyzer.

See `analyzer.typescript_analyzer`'s module docstring for the exact,
deliberately narrow set of shapes this covers -- these tests exercise
precisely that set, not general TypeScript syntax.
"""

from __future__ import annotations

from pathlib import Path

from codebase_manual.analyzer.config import AnalysisContext
from codebase_manual.analyzer.typescript_analyzer import analyze_module, infer_module_name
from codebase_manual.domain.models import PythonModule


def _analyze(tmp_path: Path, source: str, *, filename: str = "module.ts") -> PythonModule:
    file_path = tmp_path / filename
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(source, encoding="utf-8")
    return analyze_module(
        file_path=file_path, repo_relative_path=filename, context=AnalysisContext()
    )


def test_infer_module_name_strips_extension_and_dots_the_path() -> None:
    assert infer_module_name("app/services/auth.ts") == "app.services.auth"


def test_infer_module_name_accepts_tsx() -> None:
    assert infer_module_name("app/Widget.tsx") == "app.Widget"


def test_infer_module_name_rejects_non_typescript_files() -> None:
    assert infer_module_name("app/service.py") is None


def test_analyze_module_reports_unreadable_file(tmp_path: Path) -> None:
    module = analyze_module(
        file_path=tmp_path / "missing.ts",
        repo_relative_path="missing.ts",
        context=AnalysisContext(),
    )
    assert module.parse_error is not None


def test_extracts_named_default_namespace_and_side_effect_imports(tmp_path: Path) -> None:
    source = (
        'import { UserRepository, Other as Alias } from "./repository";\n'
        'import express from "express";\n'
        'import * as path from "path";\n'
        'import "./setup";\n'
    )
    module = _analyze(tmp_path, source)

    by_module = {(i.module, i.name, i.alias) for i in module.imports}
    assert ("./repository", "UserRepository", None) in by_module
    assert ("./repository", "Other", "Alias") in by_module
    assert ("express", None, "express") in by_module
    assert ("path", None, "path") in by_module
    assert ("./setup", None, None) in by_module
    assert next(i for i in module.imports if i.module == "./repository").is_relative is True
    assert next(i for i in module.imports if i.module == "express").is_relative is False


def test_extracts_a_top_level_function_and_its_scoped_calls(tmp_path: Path) -> None:
    source = "function healthCheck() {\n  return doWork();\n}\n"
    module = _analyze(tmp_path, source, filename="app/routes.ts")

    (function,) = module.functions
    assert function.qualified_name == "app.routes.healthCheck"
    assert [c.expression for c in function.calls] == ["doWork"]


def test_extracts_a_class_with_extends_and_methods(tmp_path: Path) -> None:
    source = (
        "export class AuthService extends BaseService {\n"
        "  login(name: string) {\n"
        "    return this.repo.find(name);\n"
        "  }\n"
        "}\n"
    )
    module = _analyze(tmp_path, source, filename="app/auth.ts")

    (klass,) = module.classes
    assert klass.qualified_name == "app.auth.AuthService"
    assert klass.bases == ["BaseService"]
    (method,) = klass.methods
    assert method.qualified_name == "app.auth.AuthService.login"
    assert method.is_method is True
    assert [c.expression for c in method.calls] == ["this.repo.find"]


def test_calls_do_not_leak_from_a_method_into_the_class_body(tmp_path: Path) -> None:
    """A call directly in a class body (not inside any method) is not attributed
    to anything -- there is no confident scope for it, mirroring the Python
    analyzer's treatment of a lambda body."""
    source = (
        "class Config {\n  static base = computeBase();\n  method() {\n    doInner();\n  }\n}\n"
    )
    module = _analyze(tmp_path, source)

    (klass,) = module.classes
    (method,) = klass.methods
    assert [c.expression for c in method.calls] == ["doInner"]


def test_if_for_blocks_do_not_create_new_scopes(tmp_path: Path) -> None:
    source = (
        "function run(flag: boolean) {\n"
        "  if (flag) {\n"
        "    helperA();\n"
        "  } else {\n"
        "    for (let i = 0; i < 3; i++) {\n"
        "      helperB();\n"
        "    }\n"
        "  }\n"
        "}\n"
    )
    module = _analyze(tmp_path, source)

    (function,) = module.functions
    assert {c.expression for c in function.calls} == {"helperA", "helperB"}


def test_control_flow_keywords_are_not_mistaken_for_calls(tmp_path: Path) -> None:
    source = (
        "function run(x: number) {\n"
        "  if (x) {\n"
        "    return x;\n"
        "  }\n"
        "  switch (x) {\n"
        "    case 1:\n"
        "      break;\n"
        "  }\n"
        "}\n"
    )
    module = _analyze(tmp_path, source)

    (function,) = module.functions
    assert function.calls == []


def test_dotted_calls_named_like_keywords_are_still_detected(tmp_path: Path) -> None:
    """`.get`/`.set`/`.of`/`.from` etc. are common, legitimate method names --
    only the *first* segment of a dotted call is checked against the small
    control-flow keyword-exclusion set."""
    source = "function run(app: any, arr: any) {\n  app.get('/x', handler);\n  Array.of(1, 2);\n}\n"
    module = _analyze(tmp_path, source)

    (function,) = module.functions
    assert {c.expression for c in function.calls} == {"app.get", "Array.of"}


def test_this_resolves_as_a_self_reference_like_python_self(tmp_path: Path) -> None:
    """`this.method()`/`this.attr` are captured as call-site facts exactly like
    Python's `self.method()` -- `domain.relationships` (not this analyzer)
    is what actually resolves `this` as a self-reference; this test only
    proves the fact shape is right for that resolution to work."""
    source = "class Service {\n  run() {\n    this.helper();\n  }\n  helper() {}\n}\n"
    module = _analyze(tmp_path, source)

    (klass,) = module.classes
    run_method = next(m for m in klass.methods if m.name == "run")
    assert [c.expression for c in run_method.calls] == ["this.helper"]


def test_construction_call_is_captured_without_the_new_keyword(tmp_path: Path) -> None:
    source = "function build() {\n  return new Repository();\n}\n"
    module = _analyze(tmp_path, source)

    (function,) = module.functions
    assert [c.expression for c in function.calls] == ["Repository"]


def test_module_level_call_is_attributed_to_the_module(tmp_path: Path) -> None:
    source = "function setup() {}\nsetup();\n"
    module = _analyze(tmp_path, source, filename="app/main.ts")

    assert [c.expression for c in module.calls] == ["setup"]
    assert module.calls[0].containing_symbol_id == "app.main"


def test_nested_function_declarations_are_flattened_with_a_qualified_name(
    tmp_path: Path,
) -> None:
    source = "function outer() {\n  function inner() {\n    innerCall();\n  }\n  return inner;\n}\n"
    module = _analyze(tmp_path, source)

    names = {f.qualified_name: [c.expression for c in f.calls] for f in module.functions}
    assert names["module.outer"] == []
    assert names["module.outer.inner"] == ["innerCall"]


def test_template_literal_interpolation_is_not_scanned_for_calls(tmp_path: Path) -> None:
    source = "function run() {\n  return `value: ${computeValue()}`;\n}\n"
    module = _analyze(tmp_path, source)

    (function,) = module.functions
    assert function.calls == []


def test_call_arguments_are_captured_as_a_shallow_split(tmp_path: Path) -> None:
    source = "function run() {\n  handler(a, nested(b, c), 3);\n}\n"
    module = _analyze(tmp_path, source)

    (function,) = module.functions
    handler_call = next(c for c in function.calls if c.expression == "handler")
    assert [arg.value for arg in handler_call.arguments] == ["a", "nested(b, c)", "3"]
