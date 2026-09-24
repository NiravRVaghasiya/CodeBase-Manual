"""Coordinates OAuth providers and issues sessions for authenticated users."""

from __future__ import annotations

from app.auth.providers.base import OAuthProvider
from app.auth.providers.github import GithubProvider as GitHub
from app.auth.providers.gitlab import GitlabProvider
from app.users.models import User
from app.users.repository import UserRepository

DEFAULT_SESSION_TTL_SECONDS = 86400


class AuthService:
    """Authenticates users against a registry of OAuth providers."""

    def __init__(self, user_repository: UserRepository) -> None:
        self._user_repository = user_repository
        self._providers: dict[str, OAuthProvider] = {}

    def register_provider(self, provider: OAuthProvider) -> None:
        self._providers[provider.name] = provider

    def get_provider(self, name: str) -> OAuthProvider:
        return self._providers[name]

    async def login_with_provider(self, provider_name: str, code: str) -> User:
        provider = self.get_provider(provider_name)
        token = await provider.exchange_code(code)
        profile = await provider.fetch_profile(token)
        return await self._user_repository.get_or_create(
            email=profile.get("provider", provider_name),
        )


def build_default_auth_service(user_repository: UserRepository) -> AuthService:
    service = AuthService(user_repository)
    service.register_provider(GitHub(client_id="", client_secret=""))
    service.register_provider(GitlabProvider(client_id="", client_secret=""))
    return service
