"""Tests for the AI provider interface and JSON-completion helper."""

from __future__ import annotations

import pytest

from codebase_manual.ai.anthropic_provider import AnthropicProvider
from codebase_manual.ai.provider import (
    AIProviderNotConfiguredError,
    AISynthesisError,
    complete_json,
)


class _StubProvider:
    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, *, system: str, prompt: str) -> str:
        return self.response


def test_complete_json_parses_plain_json_object() -> None:
    result = complete_json(_StubProvider('{"a": 1}'), system="sys", prompt="p")
    assert result == {"a": 1}


def test_complete_json_strips_markdown_code_fences() -> None:
    result = complete_json(_StubProvider('```json\n{"a": 1}\n```'), system="sys", prompt="p")
    assert result == {"a": 1}


def test_complete_json_raises_on_invalid_json() -> None:
    with pytest.raises(AISynthesisError):
        complete_json(_StubProvider("not json"), system="sys", prompt="p")


def test_complete_json_raises_when_response_is_not_an_object() -> None:
    with pytest.raises(AISynthesisError):
        complete_json(_StubProvider("[1, 2, 3]"), system="sys", prompt="p")


def test_anthropic_provider_raises_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicProvider()
    with pytest.raises(AIProviderNotConfiguredError):
        provider.complete(system="sys", prompt="p")
