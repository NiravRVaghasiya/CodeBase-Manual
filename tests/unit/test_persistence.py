"""Tests for the persistence layer."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from codebase_manual.domain.models import (
    EntityKind,
    EntityRef,
    FileLanguage,
    FileRecord,
    FunctionSymbol,
    GitMetadata,
    PythonModule,
    Relationship,
    RelationshipKind,
    RepositoryRecord,
    ScanResult,
    SourceLocation,
)
from codebase_manual.persistence.database import create_database_engine
from codebase_manual.persistence.orm import IndexRunORM
from codebase_manual.persistence.store import IndexStore, repository_identity


def _engine(tmp_path: Path):
    return create_database_engine(f"sqlite:///{(tmp_path / 'index.db').as_posix()}")


def _scan_result(root: str, *, commit_sha: str | None, indexed_at: datetime) -> ScanResult:
    return ScanResult(
        repository=RepositoryRecord(
            root=root,
            git=GitMetadata(is_git_repository=commit_sha is not None, commit_sha=commit_sha),
            indexed_at=indexed_at,
        ),
        directories=[],
        files=[
            FileRecord(path="app.py", size_bytes=10, extension=".py", language=FileLanguage.PYTHON)
        ],
    )


def _module(function_name: str = "main") -> PythonModule:
    return PythonModule(
        path="app.py",
        module_name="app",
        functions=[
            FunctionSymbol(
                name=function_name,
                qualified_name=f"app.{function_name}",
                location=SourceLocation(line_start=1, line_end=2),
            )
        ],
    )


def _relationship() -> Relationship:
    return Relationship(
        kind=RelationshipKind.CONTAINS,
        source=EntityRef(kind=EntityKind.MODULE, identifier="app"),
        target=EntityRef(kind=EntityKind.FUNCTION, identifier="app.main"),
        evidence="app.main is defined at app.py:1",
    )


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    store = IndexStore(_engine(tmp_path))
    scan_result = _scan_result(
        str(tmp_path), commit_sha="a" * 40, indexed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )

    run_id = store.save(scan_result, [_module()], [_relationship()])
    assert run_id > 0

    snapshot = store.latest_snapshot(repository_identity(scan_result))
    assert snapshot is not None
    assert snapshot.commit_sha == "a" * 40
    assert [f.path for f in snapshot.files] == ["app.py"]
    assert snapshot.modules[0].module_name == "app"
    assert snapshot.modules[0].functions[0].qualified_name == "app.main"
    assert snapshot.relationships[0].kind is RelationshipKind.CONTAINS


def test_latest_snapshot_returns_none_for_unknown_repository(tmp_path: Path) -> None:
    store = IndexStore(_engine(tmp_path))
    assert store.latest_snapshot("unknown") is None


def test_reindexing_same_commit_is_idempotent(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    store = IndexStore(engine)
    commit_sha = "b" * 40

    first = _scan_result(
        str(tmp_path), commit_sha=commit_sha, indexed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    store.save(first, [_module()], [_relationship()])

    second = _scan_result(
        str(tmp_path), commit_sha=commit_sha, indexed_at=datetime(2026, 1, 2, tzinfo=UTC)
    )
    store.save(second, [_module(function_name="renamed")], [])

    with Session(engine) as session:
        run_count = session.execute(
            select(func.count())
            .select_from(IndexRunORM)
            .where(IndexRunORM.commit_sha == commit_sha)
        ).scalar_one()
    assert run_count == 1

    snapshot = store.latest_snapshot(repository_identity(second))
    assert snapshot is not None
    assert snapshot.modules[0].functions[0].name == "renamed"
    assert snapshot.relationships == []


def test_runs_without_a_commit_are_not_deduplicated(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    store = IndexStore(engine)

    first = _scan_result(
        str(tmp_path), commit_sha=None, indexed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    second = _scan_result(
        str(tmp_path), commit_sha=None, indexed_at=datetime(2026, 1, 2, tzinfo=UTC)
    )
    store.save(first, [_module()], [])
    store.save(second, [_module()], [])

    with Session(engine) as session:
        run_count = session.execute(select(func.count()).select_from(IndexRunORM)).scalar_one()
    assert run_count == 2

    snapshot = store.latest_snapshot(repository_identity(second))
    assert snapshot is not None
    assert snapshot.indexed_at == datetime(2026, 1, 2, tzinfo=UTC)
