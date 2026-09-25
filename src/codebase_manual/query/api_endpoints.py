"""Detects HTTP API endpoints from route-decorator source text.

This is a deterministic regex read of decorator expressions the analyzer
already captured (e.g. `router.post("/login/{provider}")`) -- not framework
introspection. It recognizes two FastAPI/Starlette-style decorator shapes:

1. `<name>.<http_method>(<path>)` -- `router.post("/login")`.
2. `<name>.api_route(<path>, methods=[...])` -- `app.api_route("/health",
   methods=["GET", "HEAD"])`, which can register more than one method per
   decorator; each becomes its own `ApiEndpoint`. `methods` defaults to
   `["GET"]` when omitted, matching Starlette's own default.

`ApiEndpoint.detection` records *which shape* matched, not which framework
is in use -- FastAPI and Starlette (and code that merely imitates their
decorator shape) are syntactically indistinguishable without inspecting
imports, and guessing a framework name from syntax alone would be exactly
the kind of fabrication this codebase's evidence model exists to prevent.

3. `<name>.add_api_route(<path>, <handler>, methods=[...])` -- a *call
   statement* registering a separately-defined handler, not a decorator on
   it (`router.add_api_route("/health", health_check)`). Unlike the two
   decorator shapes, the handler here is an argument, not the function
   the decorator sits on -- it is resolved to a qualified name only when
   it's a bare name matching a function/method defined in the *same
   module* (the common case: `def health_check(): ...` then later
   `router.add_api_route("/health", health_check)`); a handler imported
   from elsewhere, or any non-bare-name expression, produces an
   `ApiEndpoint` with `function_qualified_name=None` rather than a guess --
   the path/method registration is still real evidence even when the
   handler can't be resolved.

Anything else is left alone rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from codebase_manual.domain.models import CallSite, FunctionSymbol, PythonModule

_METHOD_DECORATOR = re.compile(
    r"^[\w.]+\.(?P<method>get|post|put|patch|delete|options|head)\("
    r"\s*(?P<path>\"[^\"]*\"|'[^']*')?"
)

_API_ROUTE_DECORATOR = re.compile(r"^[\w.]+\.api_route\(\s*(?P<path>\"[^\"]*\"|'[^']*')?")

_METHODS_KWARG = re.compile(r"methods\s*=\s*\[(?P<methods>[^\]]*)\]")
_QUOTED = re.compile(r"[\"']([^\"']+)[\"']")

_DEFAULT_API_ROUTE_METHODS = ("GET",)


class EndpointDetection(StrEnum):
    METHOD_DECORATOR = "method_decorator"
    API_ROUTE_DECORATOR = "api_route_decorator"
    ADD_API_ROUTE_CALL = "add_api_route_call"


@dataclass
class ApiEndpoint:
    http_method: str
    path: str | None
    function_qualified_name: str | None
    file_path: str
    detection: EndpointDetection = EndpointDetection.METHOD_DECORATOR


def _methods_from_api_route(expression: str) -> tuple[str, ...]:
    match = _METHODS_KWARG.search(expression)
    if not match:
        return _DEFAULT_API_ROUTE_METHODS
    methods = tuple(m.upper() for m in _QUOTED.findall(match.group("methods")))
    return methods or _DEFAULT_API_ROUTE_METHODS


def _endpoints_for(function: FunctionSymbol, file_path: str) -> list[ApiEndpoint]:
    endpoints: list[ApiEndpoint] = []
    for decorator in function.decorators:
        expression = decorator.expression

        method_match = _METHOD_DECORATOR.match(expression)
        if method_match:
            raw_path = method_match.group("path")
            endpoints.append(
                ApiEndpoint(
                    http_method=method_match.group("method").upper(),
                    path=raw_path.strip("\"'") if raw_path else None,
                    function_qualified_name=function.qualified_name,
                    file_path=file_path,
                    detection=EndpointDetection.METHOD_DECORATOR,
                )
            )
            continue

        api_route_match = _API_ROUTE_DECORATOR.match(expression)
        if api_route_match:
            raw_path = api_route_match.group("path")
            path = raw_path.strip("\"'") if raw_path else None
            for method in _methods_from_api_route(expression):
                endpoints.append(
                    ApiEndpoint(
                        http_method=method,
                        path=path,
                        function_qualified_name=function.qualified_name,
                        file_path=file_path,
                        detection=EndpointDetection.API_ROUTE_DECORATOR,
                    )
                )
    return endpoints


_ADD_API_ROUTE_CALL = re.compile(r"\.add_api_route$")


def _is_string_literal(text: str) -> bool:
    return len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'"


def _all_call_sites(module: PythonModule) -> list[CallSite]:
    """Every call site in `module`, at module scope or inside any function/method --
    `add_api_route(...)` is a call statement, not a decorator, so it can appear
    anywhere a `CallSite` is recorded, not just inside route-handler functions."""
    sites = list(module.calls)
    for function in module.functions:
        sites.extend(function.calls)
    for klass in module.classes:
        for method in klass.methods:
            sites.extend(method.calls)
    return sites


def _local_function_names(module: PythonModule) -> dict[str, str]:
    """Bare name -> qualified name, for functions/methods defined in `module`.

    Only a same-module lookup -- resolving a handler imported from elsewhere
    would need the same cross-module resolution `domain.relationships`
    already does, which this deliberately regex-based module doesn't
    duplicate (see the module docstring).
    """
    names = {function.name: function.qualified_name for function in module.functions}
    for klass in module.classes:
        for method in klass.methods:
            names.setdefault(method.name, method.qualified_name)
    return names


def _add_api_route_endpoints(module: PythonModule) -> list[ApiEndpoint]:
    endpoints: list[ApiEndpoint] = []
    handler_by_name = _local_function_names(module)

    for call_site in _all_call_sites(module):
        if not _ADD_API_ROUTE_CALL.search(call_site.expression):
            continue
        positional = [arg for arg in call_site.arguments if arg.keyword is None]
        if not positional:
            continue

        path = positional[0].value.strip("\"'") if _is_string_literal(positional[0].value) else None
        handler_expression = positional[1].value if len(positional) > 1 else None
        handler_qualified_name = (
            handler_by_name.get(handler_expression) if handler_expression else None
        )
        methods_kwarg = next(
            (arg.value for arg in call_site.arguments if arg.keyword == "methods"), None
        )
        methods = (
            tuple(m.upper() for m in _QUOTED.findall(methods_kwarg)) or _DEFAULT_API_ROUTE_METHODS
            if methods_kwarg
            else _DEFAULT_API_ROUTE_METHODS
        )

        for method in methods:
            endpoints.append(
                ApiEndpoint(
                    http_method=method,
                    path=path,
                    function_qualified_name=handler_qualified_name,
                    file_path=module.path,
                    detection=EndpointDetection.ADD_API_ROUTE_CALL,
                )
            )
    return endpoints


def detect_api_endpoints(modules: list[PythonModule]) -> list[ApiEndpoint]:
    endpoints: list[ApiEndpoint] = []
    for module in modules:
        for function in module.functions:
            endpoints.extend(_endpoints_for(function, module.path))
        for klass in module.classes:
            for method in klass.methods:
                endpoints.extend(_endpoints_for(method, module.path))
        endpoints.extend(_add_api_route_endpoints(module))
    return endpoints
