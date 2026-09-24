"""Tests for route-decorator based API endpoint detection."""

from __future__ import annotations

from codebase_manual.domain.models import (
    ClassSymbol,
    Decorator,
    FunctionSymbol,
    PythonModule,
    SourceLocation,
)
from codebase_manual.query.api_endpoints import detect_api_endpoints

_LOCATION = SourceLocation(line_start=1, line_end=2)


def _function(name: str, decorator_expression: str) -> FunctionSymbol:
    return FunctionSymbol(
        name=name,
        qualified_name=f"app.api.routes.{name}",
        decorators=[Decorator(expression=decorator_expression, location=_LOCATION)],
        location=_LOCATION,
    )


def test_detects_a_route_decorator_with_a_literal_path() -> None:
    module = PythonModule(
        path="app/api/routes.py",
        module_name="app.api.routes",
        functions=[_function("login", 'router.post("/login/{provider_name}")')],
    )

    endpoints = detect_api_endpoints([module])

    assert len(endpoints) == 1
    endpoint = endpoints[0]
    assert endpoint.http_method == "POST"
    assert endpoint.path == "/login/{provider_name}"
    assert endpoint.function_qualified_name == "app.api.routes.login"
    assert endpoint.file_path == "app/api/routes.py"


def test_detects_endpoints_declared_as_class_methods() -> None:
    module = PythonModule(
        path="app/api/routes.py",
        module_name="app.api.routes",
        classes=[
            ClassSymbol(
                name="UserView",
                qualified_name="app.api.routes.UserView",
                methods=[_function("get", 'router.get("/users/{id}")')],
                location=_LOCATION,
            )
        ],
    )

    endpoints = detect_api_endpoints([module])

    assert endpoints[0].http_method == "GET"
    assert endpoints[0].path == "/users/{id}"


def test_ignores_non_route_decorators() -> None:
    module = PythonModule(
        path="app/api/routes.py",
        module_name="app.api.routes",
        functions=[_function("helper", "staticmethod")],
    )

    assert detect_api_endpoints([module]) == []


def test_handles_route_decorator_without_a_literal_path() -> None:
    module = PythonModule(
        path="app/api/routes.py",
        module_name="app.api.routes",
        functions=[_function("dynamic", "router.delete(build_path())")],
    )

    endpoints = detect_api_endpoints([module])

    assert endpoints[0].http_method == "DELETE"
    assert endpoints[0].path is None
