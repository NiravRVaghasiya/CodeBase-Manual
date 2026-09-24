"""Deterministic structural analysis of Python source files using the
standard-library `ast` module.

This module only extracts facts (names, signatures, imports, locations). It
performs no semantic interpretation.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from codebase_manual.domain.models import (
    CallSite,
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


def infer_module_name(relative_path: str, source_roots: Sequence[str] = ()) -> str | None:
    """Infer a dotted module name from a repository-relative path.

    This is a path-based heuristic, not a `sys.path`/package resolution --
    it is only meaningful when the path already lies within a Python
    package. `source_roots` (e.g. `("src",)`) are stripped from the front of
    the path first, so `src/pkg/mod.py` resolves to `pkg.mod`, not
    `src.pkg.mod` -- see `analyzer.config.detect_source_roots`.
    """
    path = PurePosixPath(relative_path)
    if path.suffix != ".py":
        return None

    parts = list(path.parts)
    for root in source_roots:
        root_parts = PurePosixPath(root).parts
        if tuple(parts[: len(root_parts)]) == root_parts:
            parts = parts[len(root_parts) :]
            break

    if not parts:
        return None
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


class _ScopedCallVisitor(ast.NodeVisitor):
    """Collects calls belonging directly to one function's own scope.

    Calls made inside a nested function/lambda/class defined within this
    function's body are excluded -- they belong to that nested scope, not
    this one (that nested function's own `FunctionSymbol.calls` covers
    them, or -- for a lambda, which has no symbol to own it -- they are not
    tracked at all, rather than misattributed here). Decorator expressions
    and parameter defaults/annotations of a nested `def`/`class` execute in
    *this* scope at definition time, so those are still visited.
    """

    def __init__(self, containing_symbol_id: str) -> None:
        self._containing_symbol_id = containing_symbol_id
        self.calls: list[CallSite] = []

    def visit_Call(self, node: ast.Call) -> None:
        callee = _unparse(node.func)
        if callee:
            self.calls.append(
                CallSite(
                    expression=callee,
                    line=node.lineno,
                    column=node.col_offset,
                    containing_symbol_id=self._containing_symbol_id,
                )
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_def_time_expressions(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_def_time_expressions(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Defaults evaluate eagerly in this scope; the lambda body only
        # executes when called, so it does not belong to this scope.
        self._visit_arg_defaults(node.args)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Bases/keywords/decorators evaluate in this scope; the class body
        # is its own scope (its methods are extracted, and scoped, separately).
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for decorator in node.decorator_list:
            self.visit(decorator)

    def _visit_def_time_expressions(self, node: _FunctionNode) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        if node.returns is not None:
            self.visit(node.returns)
        self._visit_arg_defaults(node.args)

    def _visit_arg_defaults(self, args: ast.arguments) -> None:
        for default in (*args.defaults, *args.kw_defaults):
            if default is not None:
                self.visit(default)
        all_args = (*args.posonlyargs, *args.args, *args.kwonlyargs)
        for arg in (*all_args, args.vararg, args.kwarg):
            if arg is not None and arg.annotation is not None:
                self.visit(arg.annotation)


def _extract_calls(node: _FunctionNode, containing_symbol_id: str) -> list[CallSite]:
    visitor = _ScopedCallVisitor(containing_symbol_id)
    for statement in node.body:
        visitor.visit(statement)
    return visitor.calls


_ScopeDef = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
# Fields that hold nested statement lists on compound statements that do
# NOT introduce a new scope (if/for/while/with/try) -- a def/class inside
# one of these is still a direct child of the enclosing function/module
# scope, unlike a def/class inside a FunctionDef/AsyncFunctionDef/ClassDef.
_NON_SCOPE_BODY_FIELDS = ("body", "orelse", "finalbody")


def _iter_scope_defs(statements: Sequence[ast.stmt]) -> list[_ScopeDef]:
    """Find every function/class def directly in this scope, at any statement depth.

    Descends into non-scope-creating compound statements (if/for/while/
    with/try and their branches) but stops at the boundary of any nested
    def/class -- those are separate scopes, extracted (and recursed into)
    by the caller instead.
    """
    found: list[_ScopeDef] = []
    for statement in statements:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.append(statement)
            continue
        for field_name in _NON_SCOPE_BODY_FIELDS:
            nested = getattr(statement, field_name, None)
            if isinstance(nested, list):
                found.extend(_iter_scope_defs(nested))
        if isinstance(statement, ast.Try):
            for handler in statement.handlers:
                found.extend(_iter_scope_defs(handler.body))
    return found


@dataclass
class _NestedSymbols:
    functions: list[FunctionSymbol] = field(default_factory=list)
    classes: list[ClassSymbol] = field(default_factory=list)


def _extract_nested_defs(statements: Sequence[ast.stmt], qualified_prefix: str) -> _NestedSymbols:
    nested = _NestedSymbols()
    for child in _iter_scope_defs(statements):
        if isinstance(child, ast.ClassDef):
            class_symbol, grandchildren = _extract_class(child, qualified_prefix)
            nested.classes.append(class_symbol)
        else:
            function_symbol, grandchildren = _extract_function(
                child, qualified_prefix, is_method=False
            )
            nested.functions.append(function_symbol)
        nested.functions.extend(grandchildren.functions)
        nested.classes.extend(grandchildren.classes)
    return nested


def _extract_function(
    node: _FunctionNode, qualified_prefix: str, *, is_method: bool
) -> tuple[FunctionSymbol, _NestedSymbols]:
    qualified_name = _qualify(qualified_prefix, node.name)
    symbol = FunctionSymbol(
        name=node.name,
        qualified_name=qualified_name,
        parameters=_extract_parameters(node.args),
        return_annotation=_unparse(node.returns),
        decorators=_extract_decorators(node.decorator_list),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        is_method=is_method,
        docstring=ast.get_docstring(node),
        location=_location(node),
        calls=_extract_calls(node, qualified_name),
    )
    return symbol, _extract_nested_defs(node.body, qualified_name)


def _simple_assign_targets(node: ast.Assign) -> list[ast.Name]:
    return [target for target in node.targets if isinstance(target, ast.Name)]


def _extract_class(
    node: ast.ClassDef, qualified_prefix: str
) -> tuple[ClassSymbol, _NestedSymbols]:
    qualified_name = _qualify(qualified_prefix, node.name)
    methods: list[FunctionSymbol] = []
    class_variables: list[Variable] = []
    nested = _NestedSymbols()

    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            method, grandchildren = _extract_function(item, qualified_name, is_method=True)
            methods.append(method)
            nested.functions.extend(grandchildren.functions)
            nested.classes.extend(grandchildren.classes)
        elif isinstance(item, ast.ClassDef):
            nested_class, grandchildren = _extract_class(item, qualified_name)
            nested.classes.append(nested_class)
            nested.functions.extend(grandchildren.functions)
            nested.classes.extend(grandchildren.classes)
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

    symbol = ClassSymbol(
        name=node.name,
        qualified_name=qualified_name,
        bases=[_unparse(base) or "" for base in node.bases],
        decorators=_extract_decorators(node.decorator_list),
        docstring=ast.get_docstring(node),
        methods=methods,
        class_variables=class_variables,
        location=_location(node),
    )
    return symbol, nested


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
            function_symbol, nested_functions = _extract_function(node, prefix, is_method=False)
            functions.append(function_symbol)
            functions.extend(nested_functions.functions)
            classes.extend(nested_functions.classes)
        elif isinstance(node, ast.ClassDef):
            class_symbol, nested_classes = _extract_class(node, prefix)
            classes.append(class_symbol)
            functions.extend(nested_classes.functions)
            classes.extend(nested_classes.classes)
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
