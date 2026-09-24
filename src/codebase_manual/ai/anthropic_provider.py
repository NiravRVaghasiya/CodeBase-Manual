"""An `AIProvider` backed by the Anthropic API.

Requires `ANTHROPIC_API_KEY` in the environment. The check happens lazily,
on first `complete()` call, so importing this module (and everything that
depends on the `AIProvider` protocol) never requires network access or an
API key.
"""

from __future__ import annotations

import os
from typing import Any

from codebase_manual.ai.provider import AIProviderNotConfiguredError

_MODEL_ENV_VAR = "CODEBASE_MANUAL_AI_MODEL"
_DEFAULT_MODEL = "claude-sonnet-5"
_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"


class AnthropicProvider:
    """Calls the Anthropic Messages API to synthesize interpretation from facts."""

    def __init__(self, *, model: str | None = None, max_tokens: int = 2048) -> None:
        self._model = model or os.environ.get(_MODEL_ENV_VAR, _DEFAULT_MODEL)
        self._max_tokens = max_tokens
        self._client: Any | None = None

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
        client = self._get_client()
        response = client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
