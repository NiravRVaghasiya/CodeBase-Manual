"""GitHub OAuth provider."""

from __future__ import annotations

from functools import lru_cache

from config.settings import get_settings

from app.auth.providers.base import OAuthProvider, OAuthToken


class GithubProvider(OAuthProvider):
    """OAuth provider for GitHub."""

    name = "github"

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret

    async def exchange_code(self, code: str) -> OAuthToken:
        settings = get_settings()
        return OAuthToken(access_token=f"github-token-for-{code}-{settings.oauth_timeout}")

    async def fetch_profile(self, token: OAuthToken) -> dict[str, str]:
        return {"provider": self.name, "access_token": token.access_token}


@lru_cache(maxsize=1)
def default_github_provider() -> GithubProvider:
    settings = get_settings()
    return GithubProvider(settings.github_client_id, settings.github_client_secret)
