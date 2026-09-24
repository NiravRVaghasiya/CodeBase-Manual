"""Database engine setup.

Defaults to a SQLite file under `<repository>/.codebase_manual/index.db` so
indexing works with no external services. Set `CODEBASE_MANUAL_DATABASE_URL`
to point at PostgreSQL (or any other SQLAlchemy-supported database) instead
-- the schema and queries are engine-agnostic.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine

from codebase_manual.persistence.orm import Base

_DATABASE_URL_ENV_VAR = "CODEBASE_MANUAL_DATABASE_URL"


def default_database_url(repository_root: Path) -> str:
    override = os.environ.get(_DATABASE_URL_ENV_VAR)
    if override:
        return override

    db_dir = repository_root / ".codebase_manual"
    db_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(db_dir / 'index.db').as_posix()}"


def create_database_engine(database_url: str) -> Engine:
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    return engine
