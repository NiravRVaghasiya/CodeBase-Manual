"""GitLab OAuth provider."""

from __future__ import annotations

from app.auth.providers.base import OAuthProvider, OAuthToken


class GitlabProvider(OAuthProvider):
    """OAuth provider for GitLab."""

    name = "gitlab"

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret

    async def exchange_code(self, code: str) -> OAuthToken:
        return OAuthToken(access_token=f"gitlab-token-for-{code}")

    async def fetch_profile(self, token: OAuthToken) -> dict[str, str]:
        return {"provider": self.name, "access_token": token.access_token}
