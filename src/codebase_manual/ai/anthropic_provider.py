"""An `AIProvider` backed by the Anthropic API.

Requires `ANTHROPIC_API_KEY` in the environment. The check happens lazily,
on first `complete()` call, so importing this module (and everything that
depends on the `AIProvider` protocol) never requires network access or an
API key.

Every `anthropic` SDK exception raised during a request is mapped to a
domain-level error before it escapes this module -- callers (the CLI, the
web API) only ever need to know about `ai.provider`'s error types, never
about the SDK's. An authentication failure maps to
`AIProviderNotConfiguredError` (the credential itself is the problem);
everything else (timeouts, rate limits, network failures, server errors)
maps to `AIProviderError` (the request failed, the configuration is fine).
"""

from __future__ import annotations

import os
import time
from typing import Any

from codebase_manual.ai.provider import AIProviderError, AIProviderNotConfiguredError
from codebase_manual.logging_config import get_logger

_logger = get_logger("ai.anthropic_provider")

_MODEL_ENV_VAR = "CODEBASE_MANUAL_AI_MODEL"
_DEFAULT_MODEL = "claude-sonnet-5"
_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"


class AnthropicProvider:
    """Calls the Anthropic Messages API to synthesize interpretation from facts."""

    def __init__(self, *, model: str | None = None, max_tokens: int = 2048) -> None:
        self._model = model or os.environ.get(_MODEL_ENV_VAR, _DEFAULT_MODEL)
        self._max_tokens = max_tokens
        self._client: Any | None = None

    @property
    def model_identifier(self) -> str:
        return self._model

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        api_key = os.environ.get(_API_KEY_ENV_VAR)
        if not api_key:
            raise AIProviderNotConfiguredError(
                f"{_API_KEY_ENV_VAR} is not set; AI-backed features are unavailable."
            )

        try:
            import anthropic
        except ImportError as exc:
            raise AIProviderNotConfiguredError("The `anthropic` package is not installed.") from exc

        self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def complete(self, *, system: str, prompt: str) -> str:
        import anthropic

        client = self._get_client()
        started = time.perf_counter()
        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError as exc:
            _logger.warning(
                "ai request failed model=%s duration=%.2fs reason=authentication",
                self._model,
                time.perf_counter() - started,
            )
            raise AIProviderNotConfiguredError(
                f"{_API_KEY_ENV_VAR} was rejected by the Anthropic API: {exc}"
            ) from exc
        except anthropic.APIError as exc:
            _logger.warning(
                "ai request failed model=%s duration=%.2fs reason=%s",
                self._model,
                time.perf_counter() - started,
                type(exc).__name__,
            )
            raise AIProviderError(f"The Anthropic API request failed: {exc}") from exc

        _logger.info(
            "ai request succeeded model=%s duration=%.2fs",
            self._model,
            time.perf_counter() - started,
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
