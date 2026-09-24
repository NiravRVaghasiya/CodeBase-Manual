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

`router.add_api_route("/path", handler, methods=[...])` -- a *call
statement* registering a separately-defined handler, not a decorator on it
-- is deliberately not detected here: that requires capturing a call
expression's arguments, which `domain.models.CallSite` doesn't do yet.
Anything else is left alone rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from codebase_manual.domain.models import FunctionSymbol, PythonModule

_METHOD_DECORATOR = re.compile(
    r"^[\w.]+\.(?P<method>get|post|put|patch|delete|options|head)\("
    r"\s*(?P<path>\"[^\"]*\"|'[^']*')?"
)

_API_ROUTE_DECORATOR = re.compile(
    r"^[\w.]+\.api_route\(\s*(?P<path>\"[^\"]*\"|'[^']*')?"
)

_METHODS_KWARG = re.compile(r"methods\s*=\s*\[(?P<methods>[^\]]*)\]")
_QUOTED = re.compile(r"[\"']([^\"']+)[\"']")

_DEFAULT_API_ROUTE_METHODS = ("GET",)


class EndpointDetection(StrEnum):
    METHOD_DECORATOR = "method_decorator"
    API_ROUTE_DECORATOR = "api_route_decorator"


@dataclass
class ApiEndpoint:
    http_method: str
    path: str | None
    function_qualified_name: str
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


def detect_api_endpoints(modules: list[PythonModule]) -> list[ApiEndpoint]:
    endpoints: list[ApiEndpoint] = []
    for module in modules:
        for function in module.functions:
            endpoints.extend(_endpoints_for(function, module.path))
        for klass in module.classes:
            for method in klass.methods:
                endpoints.extend(_endpoints_for(method, module.path))
    return endpoints
