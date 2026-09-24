"""Tests for mapping the Anthropic SDK's exceptions to domain-level errors."""

from __future__ import annotations

from typing import Any

import anthropic
import httpx
import pytest

from codebase_manual.ai.anthropic_provider import AnthropicProvider
from codebase_manual.ai.provider import AIProviderError, AIProviderNotConfiguredError

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class _FailingMessages:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def create(self, **kwargs: Any) -> Any:
        raise self._error


class _FailingClient:
    def __init__(self, error: Exception) -> None:
        self.messages = _FailingMessages(error)


def _provider_with_failing_client(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> AnthropicProvider:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    provider = AnthropicProvider()
    provider._client = _FailingClient(error)  # type: ignore[attr-defined]
    return provider


def test_authentication_error_maps_to_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(401, request=_REQUEST)
    error = anthropic.AuthenticationError("invalid x-api-key", response=response, body=None)
    provider = _provider_with_failing_client(error, monkeypatch)

    with pytest.raises(AIProviderNotConfiguredError):
        provider.complete(system="sys", prompt="hi")


def test_rate_limit_error_maps_to_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(429, request=_REQUEST)
    error = anthropic.RateLimitError("rate limited", response=response, body=None)
    provider = _provider_with_failing_client(error, monkeypatch)

    with pytest.raises(AIProviderError):
        provider.complete(system="sys", prompt="hi")


def test_timeout_error_maps_to_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    error = anthropic.APITimeoutError(request=_REQUEST)
    provider = _provider_with_failing_client(error, monkeypatch)

    with pytest.raises(AIProviderError):
        provider.complete(system="sys", prompt="hi")


def test_connection_error_maps_to_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    error = anthropic.APIConnectionError(request=_REQUEST)
    provider = _provider_with_failing_client(error, monkeypatch)

    with pytest.raises(AIProviderError):
        provider.complete(system="sys", prompt="hi")


def test_provider_error_preserves_the_original_exception_as_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.Response(500, request=_REQUEST)
    error = anthropic.InternalServerError("server error", response=response, body=None)
    provider = _provider_with_failing_client(error, monkeypatch)

    with pytest.raises(AIProviderError) as exc_info:
        provider.complete(system="sys", prompt="hi")
    assert exc_info.value.__cause__ is error
