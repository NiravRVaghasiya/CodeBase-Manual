"""SQLAlchemy schema for persisted repository intelligence.

Deliberately small: `repositories`, `index_runs`, `files`, `symbols`,
`relationships`. Symbol- and relationship-specific detail is kept in a JSON
payload rather than further normalized tables, per the plan's guidance to
add tables only when justified.
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

    index_runs: Mapped[list[IndexRunORM]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )


class IndexRunORM(Base):
    __tablename__ = "index_runs"
    __table_args__ = (UniqueConstraint("repository_id", "commit_sha", name="uq_repo_commit"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    commit_sha: Mapped[str | None]
    branch: Mapped[str | None]
    is_dirty: Mapped[bool | None]
    remote_url: Mapped[str | None]
    indexed_at: Mapped[datetime]

    repository: Mapped[RepositoryORM] = relationship(back_populates="index_runs")
    files: Mapped[list[FileORM]] = relationship(
        back_populates="index_run", cascade="all, delete-orphan"
    )
    symbols: Mapped[list[SymbolORM]] = relationship(
        back_populates="index_run", cascade="all, delete-orphan"
    )
    relationships_: Mapped[list[RelationshipORM]] = relationship(
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
