"""User domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

DEFAULT_ROLE = "member"


@dataclass
class User:
    """A registered user of the application."""

    id: int
    email: str
    role: str = DEFAULT_ROLE
    created_at: datetime = field(default_factory=datetime.utcnow)

    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass
class AdminUser(User):
    """A user with elevated permissions."""

    role: str = "admin"
    granted_by: str | None = None
