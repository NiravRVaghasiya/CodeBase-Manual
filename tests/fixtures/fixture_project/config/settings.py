"""Environment-backed application settings."""

from __future__ import annotations

import os as operating_system
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_OAUTH_TIMEOUT_SECONDS = 30


@dataclass
class Settings:
    database_url: str
    smtp_host: str
    github_client_id: str
    github_client_secret: str
    oauth_timeout: int = DEFAULT_OAUTH_TIMEOUT_SECONDS


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=operating_system.getenv("DATABASE_URL", "sqlite:///app.db"),
        smtp_host=operating_system.getenv("SMTP_HOST", "localhost"),
        github_client_id=operating_system.getenv("GITHUB_CLIENT_ID", ""),
        github_client_secret=operating_system.getenv("GITHUB_CLIENT_SECRET", ""),
    )
