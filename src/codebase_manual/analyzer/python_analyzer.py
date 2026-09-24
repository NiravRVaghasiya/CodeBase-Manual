"""Deterministic structural analysis of Python source files using the
standard-library `ast` module.

This module only extracts facts (names, signatures, imports, locations). It
performs no semantic interpretation.
"""

from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath

from codebase_manual.domain.models import (
    ClassSymbol,
    Decorator,
    FunctionSymbol,
    ImportedName,
    Parameter,
    ParameterKind,
    PythonModule,
    SourceLocation,
    Variable,
)

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def infer_module_name(relative_path: str) -> str | None:
    """Infer a dotted module name from a repository-relative path.

    This is a path-based heuristic, not a `sys.path`/package resolution --
    it is only meaningful when the path already lies within a Python package.
    """
    path = PurePosixPath(relative_path)
    if path.suffix != ".py":
        return None

    parts = list(path.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = path.stem

    return ".".join(parts) if parts else None


def _location(node: ast.expr | ast.stmt) -> SourceLocation:
    return SourceLocation(
        line_start=node.lineno,
        line_end=node.end_lineno if node.end_lineno is not None else node.lineno,
        col_start=node.col_offset,
        col_end=node.end_col_offset,
    )


def _unparse(node: ast.AST | None) -> str | None:
    return ast.unparse(node) if node is not None else None


def _qualify(prefix: str, name: str) -> str:
    return f"{prefix}.{name}" if prefix else name


def _extract_parameters(args: ast.arguments) -> list[Parameter]:
    parameters: list[Parameter] = []

    positional = [*args.posonlyargs, *args.args]
    default_offset = len(positional) - len(args.defaults)
    for index, arg in enumerate(positional):
        default = None
        if index >= default_offset:
            default = _unparse(args.defaults[index - default_offset])
        parameters.append(
            Parameter(
                name=arg.arg,
                annotation=_unparse(arg.annotation),
                default=default,
                kind=ParameterKind.POSITIONAL,
            )
        )

    if args.vararg is not None:
        parameters.append(
            Parameter(
                name=args.vararg.arg,
                annotation=_unparse(args.vararg.annotation),
                kind=ParameterKind.VAR_POSITIONAL,
            )
        )

    for arg, kw_default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        parameters.append(
            Parameter(
                name=arg.arg,
                annotation=_unparse(arg.annotation),
                default=_unparse(kw_default),
                kind=ParameterKind.KEYWORD_ONLY,
            )
        )

    if args.kwarg is not None:
        parameters.append(
            Parameter(
                name=args.kwarg.arg,
                annotation=_unparse(args.kwarg.annotation),
                kind=ParameterKind.VAR_KEYWORD,
            )
        )

    return parameters


def _extract_decorators(decorator_list: list[ast.expr]) -> list[Decorator]:
    return [
        Decorator(expression=_unparse(node) or "", location=_location(node))
        for node in decorator_list
    ]


def _extract_imports(node: ast.stmt) -> list[ImportedName]:
    location = _location(node)

    if isinstance(node, ast.Import):
        return [
            ImportedName(module=alias.name, alias=alias.asname, location=location)
            for alias in node.names
        ]

    if isinstance(node, ast.ImportFrom):
        return [
            ImportedName(
                module=node.module or "",
                name=alias.name,
                alias=alias.asname,
                is_relative=node.level > 0,
                relative_level=node.level,
                location=location,
            )
            for alias in node.names
        ]

    return []


def _extract_calls(node: _FunctionNode) -> list[str]:
    calls: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            callee = _unparse(child.func)
            if callee:
                calls.append(callee)
    return calls


def _extract_function(
    node: _FunctionNode, qualified_prefix: str, *, is_method: bool
) -> FunctionSymbol:
    return FunctionSymbol(
        name=node.name,
        qualified_name=_qualify(qualified_prefix, node.name),
        parameters=_extract_parameters(node.args),
        return_annotation=_unparse(node.returns),
        decorators=_extract_decorators(node.decorator_list),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        is_method=is_method,
        docstring=ast.get_docstring(node),
        location=_location(node),
        calls=_extract_calls(node),
    )


def _simple_assign_targets(node: ast.Assign) -> list[ast.Name]:
    return [target for target in node.targets if isinstance(target, ast.Name)]


def _extract_class(node: ast.ClassDef, qualified_prefix: str) -> ClassSymbol:
    qualified_name = _qualify(qualified_prefix, node.name)
    methods: list[FunctionSymbol] = []
    class_variables: list[Variable] = []

    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            methods.append(_extract_function(item, qualified_name, is_method=True))
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            class_variables.append(
                Variable(
                    name=item.target.id,
                    annotation=_unparse(item.annotation),
                    scope="class",
                    is_constant=item.target.id.isupper(),
                    location=_location(item),
                )
            )
        elif isinstance(item, ast.Assign):
            for target in _simple_assign_targets(item):
                class_variables.append(
                    Variable(
                        name=target.id,
                        scope="class",
                        is_constant=target.id.isupper(),
                        location=_location(item),
                    )
                )

    return ClassSymbol(
        name=node.name,
        qualified_name=qualified_name,
        bases=[_unparse(base) or "" for base in node.bases],
        decorators=_extract_decorators(node.decorator_list),
        docstring=ast.get_docstring(node),
        methods=methods,
        class_variables=class_variables,
        location=_location(node),
    )


def analyze_module(
    *,
    file_path: Path,
    repo_relative_path: str,
    module_name: str | None,
) -> PythonModule:
    """Parse a single Python file and extract its structural facts.

    Unreadable or unparseable files are reported via `parse_error` rather
    than raising, so that indexing a whole repository can proceed.
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return PythonModule(path=repo_relative_path, module_name=module_name, parse_error=str(exc))

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        return PythonModule(path=repo_relative_path, module_name=module_name, parse_error=str(exc))

    imports: list[ImportedName] = []
    functions: list[FunctionSymbol] = []
    classes: list[ClassSymbol] = []
    variables: list[Variable] = []
    prefix = module_name or ""

    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            imports.extend(_extract_imports(node))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.append(_extract_function(node, prefix, is_method=False))
        elif isinstance(node, ast.ClassDef):
            classes.append(_extract_class(node, prefix))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            variables.append(
                Variable(
                    name=node.target.id,
                    annotation=_unparse(node.annotation),
                    scope="module",
                    is_constant=node.target.id.isupper(),
                    location=_location(node),
                )
            )
        elif isinstance(node, ast.Assign):
            for target in _simple_assign_targets(node):
                variables.append(
                    Variable(
                        name=target.id,
                        scope="module",
                        is_constant=target.id.isupper(),
                        location=_location(node),
                    )
                )

    return PythonModule(
        path=repo_relative_path,
        module_name=module_name,
        docstring=ast.get_docstring(tree),
        imports=imports,
        functions=functions,
        classes=classes,
        variables=variables,
    )
