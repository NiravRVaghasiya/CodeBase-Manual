"""Database access for users."""

from __future__ import annotations

from app.database.connection import Database
from app.users.models import User


class UserRepository:
    """Persists and retrieves `User` records."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self._next_id = 1

    async def get_by_email(self, email: str) -> User | None:
        row = await self._database.fetch_one(
            "SELECT * FROM users WHERE email = :email", {"email": email}
        )
        if row is None:
            return None
        return User(id=row["id"], email=row["email"], role=row["role"])

    async def get_or_create(self, email: str) -> User:
        existing = await self.get_by_email(email)
        if existing is not None:
            return existing

        user = User(id=self._next_id, email=email)
        self._next_id += 1
        await self._database.execute(
            "INSERT INTO users (id, email, role) VALUES (:id, :email, :role)",
            {"id": user.id, "email": user.email, "role": user.role},
        )
        return user
