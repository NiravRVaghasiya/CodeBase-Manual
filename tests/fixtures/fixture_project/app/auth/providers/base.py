"""Base interface that every OAuth provider implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str | None = None
    expires_in: int = 3600


class OAuthProvider(ABC):
    """Common interface for third-party OAuth providers."""

    name: str = "unknown"

    @abstractmethod
    async def exchange_code(self, code: str) -> OAuthToken:
        """Exchange an authorization code for an access token."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_profile(self, token: OAuthToken) -> dict[str, str]:
        """Fetch the authenticated user's profile from the provider."""
        raise NotImplementedError
