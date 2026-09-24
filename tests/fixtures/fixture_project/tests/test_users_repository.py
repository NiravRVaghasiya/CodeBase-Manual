"""Tests for user persistence."""

from __future__ import annotations

import pytest
from app.database.connection import Database
from app.users.repository import UserRepository


@pytest.mark.asyncio
async def test_get_or_create_persists_new_user() -> None:
    repository = UserRepository(Database(dsn="sqlite:///:memory:"))

    created = await repository.get_or_create(email="new@example.com")
    fetched = await repository.get_by_email("new@example.com")

    assert fetched is not None
    assert fetched.id == created.id
