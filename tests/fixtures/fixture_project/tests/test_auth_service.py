"""Tests for the authentication service."""

from __future__ import annotations

import pytest
from app.auth.providers.github import GithubProvider
from app.auth.service import AuthService
from app.database.connection import Database
from app.users.repository import UserRepository


@pytest.fixture
def auth_service() -> AuthService:
    database = Database(dsn="sqlite:///:memory:")
    service = AuthService(UserRepository(database))
    service.register_provider(GithubProvider(client_id="id", client_secret="secret"))
    return service


@pytest.mark.asyncio
async def test_login_with_provider_creates_user(auth_service: AuthService) -> None:
    user = await auth_service.login_with_provider("github", "auth-code")
    assert user.email == "github"
