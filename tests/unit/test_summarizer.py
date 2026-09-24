"""Tests for grounded AI file/function summaries."""

from __future__ import annotations

import json

import pytest

from codebase_manual.ai.provider import AISynthesisError
from codebase_manual.ai.summarizer import summarize_file, summarize_function
from codebase_manual.domain.models import FunctionSymbol, PythonModule, SourceLocation

_LOCATION = SourceLocation(line_start=1, line_end=5)


class _StubProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt: str | None = None

    def complete(self, *, system: str, prompt: str) -> str:
        self.last_prompt = prompt
        return self.response


def test_summarize_file_parses_valid_response_and_attaches_evidence() -> None:
    module = PythonModule(path="app/auth/service.py", module_name="app.auth.service")
    response = json.dumps(
        {
            "purpose": "Coordinates OAuth providers.",
            "responsibilities": ["Registers providers", "Issues sessions"],
            "important_symbols": ["AuthService"],
            "side_effects": [],
            "confidence": "high",
        }
    )
    summary = summarize_file(module, _StubProvider(response))

    assert summary.file_path == "app/auth/service.py"
    assert summary.purpose == "Coordinates OAuth providers."
    assert summary.confidence.value == "high"
    assert summary.evidence[0].file_path == "app/auth/service.py"


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
        location=_LOCATION,
    )
    response = json.dumps(
        {
            "purpose": "Authenticates a user via an OAuth provider.",
            "inputs": ["provider_name", "code"],
            "outputs": ["User"],
            "side_effects": ["Creates a user record"],
            "confidence": "medium",
        }
    )
    summary = summarize_function(function, "app/auth/service.py", _StubProvider(response))

    assert summary.qualified_name == "app.auth.service.login_with_provider"
    assert summary.confidence.value == "medium"
    assert summary.evidence[0].line == 1


def test_summarize_file_strips_markdown_code_fences() -> None:
    module = PythonModule(path="app/auth/service.py", module_name="app.auth.service")
    payload = {
        "purpose": "Coordinates OAuth providers.",
        "responsibilities": [],
        "important_symbols": [],
        "side_effects": [],
        "confidence": "low",
    }
    response = f"```json\n{json.dumps(payload)}\n```"
    summary = summarize_file(module, _StubProvider(response))
    assert summary.confidence.value == "low"
