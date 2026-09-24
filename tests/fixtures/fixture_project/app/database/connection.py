"""Minimal database connection abstraction."""

from __future__ import annotations

from typing import Any

from config.settings import get_settings

_connection_pool: Database | None = None


class Database:
    """Thin async wrapper around a connection pool."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._rows: list[dict[str, Any]] = []

    async def fetch_one(self, query: str, params: dict[str, Any]) -> dict[str, Any] | None:
        for row in self._rows:
            if all(row.get(key) == value for key, value in params.items() if key in row):
                return row
        return None

    async def execute(self, query: str, params: dict[str, Any]) -> None:
        self._rows.append(dict(params))


def get_database() -> Database:
    global _connection_pool
    if _connection_pool is None:
        settings = get_settings()
        _connection_pool = Database(dsn=settings.database_url)
    return _connection_pool
