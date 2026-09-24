"""Detects HTTP API endpoints from route-decorator source text.

This is a deterministic regex read of decorator expressions the analyzer
already captured (e.g. `router.post("/login/{provider}")`) -- not framework
introspection. It only recognizes the common `<name>.<http_method>(<path>)`
shape used by FastAPI/Starlette-style routers; anything else is left alone
rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from codebase_manual.domain.models import FunctionSymbol, PythonModule

_ROUTE_DECORATOR = re.compile(
    r"^[\w.]+\.(?P<method>get|post|put|patch|delete|options|head)\("
    r"\s*(?P<path>\"[^\"]*\"|'[^']*')?"
)


@dataclass
class ApiEndpoint:
    http_method: str
    path: str | None
    function_qualified_name: str
    file_path: str


def _endpoints_for(function: FunctionSymbol, file_path: str) -> list[ApiEndpoint]:
    endpoints: list[ApiEndpoint] = []
    for decorator in function.decorators:
        match = _ROUTE_DECORATOR.match(decorator.expression)
        if not match:
            continue
        raw_path = match.group("path")
        endpoints.append(
            ApiEndpoint(
                http_method=match.group("method").upper(),
                path=raw_path.strip("\"'") if raw_path else None,
                function_qualified_name=function.qualified_name,
                file_path=file_path,
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
