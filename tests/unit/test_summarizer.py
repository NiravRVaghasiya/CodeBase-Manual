"""Tests for grounded AI file/function summaries."""

from __future__ import annotations

import json

import pytest

from codebase_manual.ai.provider import AISynthesisError
from codebase_manual.ai.summarizer import summarize_file, summarize_function
from codebase_manual.domain.models import (
    FunctionSymbol,
    Parameter,
    ParameterKind,
    PythonModule,
    SourceLocation,
)

_LOCATION = SourceLocation(line_start=1, line_end=5)


class _StubProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt: str | None = None

    def complete(self, *, system: str, prompt: str) -> str:
        self.last_prompt = prompt
        return self.response


def test_summarize_file_parses_valid_response_and_attaches_evidence() -> None:
    module = PythonModule(
        path="app/auth/service.py",
        module_name="app.auth.service",
        docstring="Coordinates OAuth providers.",
    )
    response = json.dumps(
        {
            "purpose": "Coordinates OAuth providers.",
            "responsibilities": ["Registers providers", "Issues sessions"],
            "important_symbols": ["AuthService"],
            "side_effects": [],
        }
    )
    summary = summarize_file(module, _StubProvider(response))

    assert summary.file_path == "app/auth/service.py"
    assert summary.purpose == "Coordinates OAuth providers."
    # A docstring is direct, author-stated evidence -> HIGH, computed
    # deterministically rather than trusted from the model's own response.
    assert summary.confidence.value == "high"
    assert summary.evidence[0].file_path == "app/auth/service.py"


def test_summarize_file_is_medium_confidence_without_a_docstring() -> None:
    module = PythonModule(
        path="app/auth/service.py",
        module_name="app.auth.service",
        functions=[
            FunctionSymbol(
                name="login", qualified_name="app.auth.service.login", location=_LOCATION
            )
        ],
    )
    response = json.dumps(
        {
            "purpose": "Coordinates OAuth providers.",
            "responsibilities": [],
            "important_symbols": [],
            "side_effects": [],
        }
    )
    summary = summarize_file(module, _StubProvider(response))

    assert summary.confidence.value == "medium"


def test_summarize_file_is_low_confidence_with_no_facts_at_all() -> None:
    module = PythonModule(path="app/auth/__init__.py", module_name="app.auth")
    response = json.dumps(
        {
            "purpose": "Package marker.",
            "responsibilities": [],
            "important_symbols": [],
            "side_effects": [],
        }
    )
    summary = summarize_file(module, _StubProvider(response))

    assert summary.confidence.value == "low"


def test_summarize_file_raises_on_malformed_json() -> None:
    module = PythonModule(path="app/auth/service.py", module_name="app.auth.service")
    with pytest.raises(AISynthesisError):
        summarize_file(module, _StubProvider("not json"))


def test_summarize_file_raises_when_required_field_missing() -> None:
    module = PythonModule(path="app/auth/service.py", module_name="app.auth.service")
    response = json.dumps({"responsibilities": []})
    with pytest.raises(AISynthesisError):
        summarize_file(module, _StubProvider(response))


def test_summarize_function_parses_valid_response() -> None:
    function = FunctionSymbol(
        name="login_with_provider",
        qualified_name="app.auth.service.login_with_provider",
        docstring="Authenticate a user via an OAuth provider.",
        parameters=[Parameter(name="provider_name", kind=ParameterKind.POSITIONAL)],
        location=_LOCATION,
    )
    response = json.dumps(
        {
            "purpose": "Authenticates a user via an OAuth provider.",
            "inputs": ["provider_name", "code"],
            "outputs": ["User"],
            "side_effects": ["Creates a user record"],
        }
    )
    summary = summarize_function(function, "app/auth/service.py", _StubProvider(response))

    assert summary.qualified_name == "app.auth.service.login_with_provider"
    # A docstring is direct evidence -> HIGH, computed deterministically.
    assert summary.confidence.value == "high"
    assert summary.evidence[0].line == 1


def test_summarize_function_is_medium_confidence_without_a_docstring() -> None:
    function = FunctionSymbol(
        name="login_with_provider",
        qualified_name="app.auth.service.login_with_provider",
        parameters=[Parameter(name="provider_name", kind=ParameterKind.POSITIONAL)],
        location=_LOCATION,
    )
    response = json.dumps(
        {
            "purpose": "Authenticates a user via an OAuth provider.",
            "inputs": ["provider_name"],
            "outputs": [],
            "side_effects": [],
        }
    )
    summary = summarize_function(function, "app/auth/service.py", _StubProvider(response))

    assert summary.confidence.value == "medium"


def test_summarize_file_strips_markdown_code_fences() -> None:
    module = PythonModule(path="app/auth/service.py", module_name="app.auth.service")
    payload = {
        "purpose": "Coordinates OAuth providers.",
        "responsibilities": [],
        "important_symbols": [],
        "side_effects": [],
    }
    response = f"```json\n{json.dumps(payload)}\n```"
    summary = summarize_file(module, _StubProvider(response))
    assert summary.confidence.value == "low"
