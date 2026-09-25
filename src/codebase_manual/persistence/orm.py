"""SQLAlchemy schema for persisted repository intelligence.

Deliberately small: `repositories`, `working_copies`, `index_runs`,
`files`, `symbols`, `relationships`, `unresolved_calls`, `ai_cache`.
Symbol- and relationship-specific detail is kept in a JSON payload rather
than further normalized tables, per the plan's guidance to add tables only
when justified.

`IndexRunORM.analyzer_version` stamps each run with `domain.models.
PYTHON_MODULE_SCHEMA_VERSION` at the time it was written -- `analyzer.
registry.analyze_repository_incremental` refuses to reuse a previous run's
`PythonModule`s if this doesn't match the current version, rather than
silently reusing facts an older analyzer produced. No migration tooling
exists for schema changes (this column included) -- an existing local
SQLite index created before this column existed needs re-indexing.

`RepositoryORM` identifies a *logical* repository (its Git remote, or its
root path when there is none). `WorkingCopyORM` identifies one checkout of
that repository on disk -- there can be more than one (different machines,
different local paths) producing index runs for the same logical
repository. Keeping them distinct lets `latest_snapshot` prefer the run
from the working copy actually being queried, rather than whichever
working copy happened to index most recently.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class RepositoryORM(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    identity: Mapped[str] = mapped_column(unique=True, index=True)
    root: Mapped[str]
    created_at: Mapped[datetime]

    working_copies: Mapped[list[WorkingCopyORM]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    index_runs: Mapped[list[IndexRunORM]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class WorkingCopyORM(Base):
    """One checkout, on disk, of a logical `RepositoryORM`."""

    __tablename__ = "working_copies"
    __table_args__ = (UniqueConstraint("repository_id", "root", name="uq_repo_root"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    root: Mapped[str]
    created_at: Mapped[datetime]

    repository: Mapped[RepositoryORM] = relationship(back_populates="working_copies")
    index_runs: Mapped[list[IndexRunORM]] = relationship(back_populates="working_copy")


class IndexRunORM(Base):
    __tablename__ = "index_runs"
    __table_args__ = (UniqueConstraint("repository_id", "commit_sha", name="uq_repo_commit"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    working_copy_id: Mapped[int] = mapped_column(ForeignKey("working_copies.id"))
    commit_sha: Mapped[str | None]
    branch: Mapped[str | None]
    is_dirty: Mapped[bool | None]
    remote_url: Mapped[str | None]
    indexed_at: Mapped[datetime]
    analyzer_version: Mapped[str | None]

    repository: Mapped[RepositoryORM] = relationship(back_populates="index_runs")
    working_copy: Mapped[WorkingCopyORM] = relationship(back_populates="index_runs")
    files: Mapped[list[FileORM]] = relationship(
        back_populates="index_run", cascade="all, delete-orphan"
    )
    symbols: Mapped[list[SymbolORM]] = relationship(
        back_populates="index_run", cascade="all, delete-orphan"
    )
    relationships_: Mapped[list[RelationshipORM]] = relationship(
        back_populates="index_run", cascade="all, delete-orphan"
    )
    unresolved_calls: Mapped[list[UnresolvedCallORM]] = relationship(
        back_populates="index_run", cascade="all, delete-orphan"
    )


class FileORM(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True)
    index_run_id: Mapped[int] = mapped_column(ForeignKey("index_runs.id"))
    path: Mapped[str] = mapped_column(index=True)
    size_bytes: Mapped[int]
    extension: Mapped[str]
    language: Mapped[str]
    is_binary: Mapped[bool]
    content_hash: Mapped[str | None]
    hash_strategy: Mapped[str]
    mtime: Mapped[float | None]

    index_run: Mapped[IndexRunORM] = relationship(back_populates="files")


class SymbolORM(Base):
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(primary_key=True)
    index_run_id: Mapped[int] = mapped_column(ForeignKey("index_runs.id"))
    kind: Mapped[str] = mapped_column(index=True)
    identifier: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    file_path: Mapped[str] = mapped_column(index=True)
    line_start: Mapped[int | None]
    line_end: Mapped[int | None]
    data: Mapped[str]

    index_run: Mapped[IndexRunORM] = relationship(back_populates="symbols")


class RelationshipORM(Base):
    __tablename__ = "relationships"

    id: Mapped[int] = mapped_column(primary_key=True)
    index_run_id: Mapped[int] = mapped_column(ForeignKey("index_runs.id"))
    kind: Mapped[str] = mapped_column(index=True)
    source_kind: Mapped[str]
    source_identifier: Mapped[str] = mapped_column(index=True)
    target_kind: Mapped[str]
    target_identifier: Mapped[str] = mapped_column(index=True)
    evidence: Mapped[str]
    location: Mapped[str | None]

    index_run: Mapped[IndexRunORM] = relationship(back_populates="relationships_")


class UnresolvedCallORM(Base):
    """A call that could not be resolved to a known symbol -- see `domain.models.UnresolvedCall`."""

    __tablename__ = "unresolved_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    index_run_id: Mapped[int] = mapped_column(ForeignKey("index_runs.id"))
    source_kind: Mapped[str]
    source_identifier: Mapped[str] = mapped_column(index=True)
    expression: Mapped[str]
    location: Mapped[str]

    index_run: Mapped[IndexRunORM] = relationship(back_populates="unresolved_calls")


class AICacheORM(Base):
    """A generic key-value cache, currently used for AI file summaries.

    Kept generic (opaque `payload`) rather than typed to a specific AI
    result, so the persistence layer doesn't need to depend on `ai.models`
    -- see `ai.summary_cache` for the typed key/serialization scheme.
    """

    __tablename__ = "ai_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(unique=True, index=True)
    payload: Mapped[str]
    created_at: Mapped[datetime]
