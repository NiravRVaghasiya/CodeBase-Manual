"""The interface AI-backed features are built on.

Deterministic layers (facts, retrieval, graph traversal) never depend on
this -- only synthesis/interpretation does. Swapping providers, or running
with none configured, must not affect what facts are available.
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class AIProviderNotConfiguredError(RuntimeError):
    """Raised when AI synthesis is requested but no provider is configured."""


class AISynthesisError(RuntimeError):
    """Raised when a provider's response can't be parsed/validated as expected."""


class AIProvider(Protocol):
    def complete(self, *, system: str, prompt: str) -> str:
        """Return a text completion for `prompt` under `system` instructions."""
        ...


def complete_json(provider: AIProvider, *, system: str, prompt: str) -> dict[str, Any]:
    """Call `provider.complete` and parse the response as a single JSON object."""
    raw = provider.complete(system=system, prompt=prompt)
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AISynthesisError(f"Provider did not return valid JSON: {raw!r}") from exc

    if not isinstance(parsed, dict):
        raise AISynthesisError(f"Provider JSON was not an object: {raw!r}")

    return parsed
