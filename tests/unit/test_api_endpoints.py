"""Tests for route-decorator based API endpoint detection."""

from __future__ import annotations

from codebase_manual.domain.models import (
    CallArgument,
    CallSite,
    ClassSymbol,
    Decorator,
    FunctionSymbol,
    PythonModule,
    SourceLocation,
)
from codebase_manual.query.api_endpoints import EndpointDetection, detect_api_endpoints

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
    assert endpoints[0].detection is EndpointDetection.METHOD_DECORATOR


def test_detects_api_route_decorator_with_multiple_methods() -> None:
    module = PythonModule(
        path="app/api/routes.py",
        module_name="app.api.routes",
        functions=[_function("health", 'app.api_route("/health", methods=["GET", "HEAD"])')],
    )

    endpoints = detect_api_endpoints([module])

    assert {e.http_method for e in endpoints} == {"GET", "HEAD"}
    assert all(e.path == "/health" for e in endpoints)
    assert all(e.detection is EndpointDetection.API_ROUTE_DECORATOR for e in endpoints)


def test_api_route_decorator_defaults_to_get_without_methods_kwarg() -> None:
    module = PythonModule(
        path="app/api/routes.py",
        module_name="app.api.routes",
        functions=[_function("ping", 'router.api_route("/ping")')],
    )

    endpoints = detect_api_endpoints([module])

    assert len(endpoints) == 1
    assert endpoints[0].http_method == "GET"
    assert endpoints[0].detection is EndpointDetection.API_ROUTE_DECORATOR


def _add_api_route_call_site(path: str, handler: str, *, methods: str | None = None) -> CallSite:
    arguments = [CallArgument(value=f'"{path}"'), CallArgument(value=handler)]
    if methods is not None:
        arguments.append(CallArgument(value=methods, keyword="methods"))
    return CallSite(
        expression="router.add_api_route",
        line=10,
        containing_symbol_id="app.api.routes",
        arguments=arguments,
    )


def test_detects_add_api_route_call_with_a_locally_defined_handler() -> None:
    module = PythonModule(
        path="app/api/routes.py",
        module_name="app.api.routes",
        functions=[
            FunctionSymbol(
                name="health_check",
                qualified_name="app.api.routes.health_check",
                location=_LOCATION,
            )
        ],
        calls=[_add_api_route_call_site("/health", "health_check")],
    )

    endpoints = detect_api_endpoints([module])

    assert len(endpoints) == 1
    endpoint = endpoints[0]
    assert endpoint.http_method == "GET"
    assert endpoint.path == "/health"
    assert endpoint.function_qualified_name == "app.api.routes.health_check"
    assert endpoint.detection is EndpointDetection.ADD_API_ROUTE_CALL


def test_add_api_route_call_with_methods_kwarg_registers_each_method() -> None:
    module = PythonModule(
        path="app/api/routes.py",
        module_name="app.api.routes",
        functions=[
            FunctionSymbol(
                name="health_check",
                qualified_name="app.api.routes.health_check",
                location=_LOCATION,
            )
        ],
        calls=[_add_api_route_call_site("/health", "health_check", methods='["GET", "HEAD"]')],
    )

    endpoints = detect_api_endpoints([module])

    assert {e.http_method for e in endpoints} == {"GET", "HEAD"}
    assert all(e.path == "/health" for e in endpoints)


def test_add_api_route_call_with_an_unresolvable_handler_still_records_the_endpoint() -> None:
    """The handler is imported from elsewhere (not defined in this module) -- the
    path/method registration is still real evidence even though the handler
    can't be resolved without cross-module resolution this module doesn't do."""
    module = PythonModule(
        path="app/api/routes.py",
        module_name="app.api.routes",
        calls=[_add_api_route_call_site("/health", "imported_health_check")],
    )

    endpoints = detect_api_endpoints([module])

    assert len(endpoints) == 1
    assert endpoints[0].path == "/health"
    assert endpoints[0].function_qualified_name is None


def test_add_api_route_call_inside_a_function_is_still_detected() -> None:
    module = PythonModule(
        path="app/api/routes.py",
        module_name="app.api.routes",
        functions=[
            FunctionSymbol(
                name="setup_routes",
                qualified_name="app.api.routes.setup_routes",
                location=_LOCATION,
                calls=[
                    CallSite(
                        expression="router.add_api_route",
                        line=5,
                        containing_symbol_id="app.api.routes.setup_routes",
                        arguments=[CallArgument(value='"/ping"'), CallArgument(value="ping")],
                    )
                ],
            )
        ],
    )

    endpoints = detect_api_endpoints([module])

    assert len(endpoints) == 1
    assert endpoints[0].path == "/ping"


def test_ignores_a_call_that_is_not_add_api_route() -> None:
    module = PythonModule(
        path="app/api/routes.py",
        module_name="app.api.routes",
        calls=[
            CallSite(
                expression="router.include_router",
                line=1,
                containing_symbol_id="app.api.routes",
                arguments=[CallArgument(value="other_router")],
            )
        ],
    )

    assert detect_api_endpoints([module]) == []
