"""Tests for the persistence layer."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from codebase_manual.domain.models import (
    PYTHON_MODULE_SCHEMA_VERSION,
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
    UnresolvedCall,
)
from codebase_manual.persistence.database import create_database_engine
from codebase_manual.persistence.orm import IndexRunORM, RepositoryORM, WorkingCopyORM
from codebase_manual.persistence.store import (
    IndexStore,
    _get_or_create_repository,
    _get_or_create_working_copy,
    repository_identity,
)


def _engine(tmp_path: Path):
    return create_database_engine(f"sqlite:///{(tmp_path / 'index.db').as_posix()}")


def _scan_result(
    root: str,
    *,
    commit_sha: str | None,
    indexed_at: datetime,
    remote_url: str | None = None,
) -> ScanResult:
    return ScanResult(
        repository=RepositoryRecord(
            root=root,
            git=GitMetadata(
                is_git_repository=commit_sha is not None,
                commit_sha=commit_sha,
                remote_url=remote_url,
            ),
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


def _unresolved_call() -> UnresolvedCall:
    return UnresolvedCall(
        source=EntityRef(kind=EntityKind.FUNCTION, identifier="app.main"),
        expression="service.run",
        location=SourceLocation(line_start=2, line_end=2),
    )


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    store = IndexStore(_engine(tmp_path))
    scan_result = _scan_result(
        str(tmp_path), commit_sha="a" * 40, indexed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )

    run_id = store.save(
        scan_result, [_module()], [_relationship()], unresolved_calls=[_unresolved_call()]
    )
    assert run_id > 0

    snapshot = store.latest_snapshot(repository_identity(scan_result))
    assert snapshot is not None
    assert snapshot.commit_sha == "a" * 40
    assert [f.path for f in snapshot.files] == ["app.py"]
    assert snapshot.modules[0].module_name == "app"
    assert snapshot.modules[0].functions[0].qualified_name == "app.main"
    assert snapshot.relationships[0].kind is RelationshipKind.CONTAINS
    assert len(snapshot.unresolved_calls) == 1
    assert snapshot.unresolved_calls[0].source.identifier == "app.main"
    assert snapshot.unresolved_calls[0].expression == "service.run"
    assert snapshot.unresolved_calls[0].location.line_start == 2
    assert snapshot.analyzer_version == PYTHON_MODULE_SCHEMA_VERSION


def test_save_without_unresolved_calls_defaults_to_empty(tmp_path: Path) -> None:
    """`unresolved_calls` is optional -- existing callers that don't pass it (or
    a snapshot from before this fact existed) get an empty list, not an error."""
    store = IndexStore(_engine(tmp_path))
    scan_result = _scan_result(
        str(tmp_path), commit_sha="c" * 40, indexed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )

    store.save(scan_result, [_module()], [_relationship()])

    snapshot = store.latest_snapshot(repository_identity(scan_result))
    assert snapshot is not None
    assert snapshot.unresolved_calls == []


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


def test_latest_snapshot_prefers_the_requested_working_copys_own_run(tmp_path: Path) -> None:
    """Two working copies of the same remote must not silently overwrite each other's "latest"."""
    engine = _engine(tmp_path)
    store = IndexStore(engine)

    remote_url = "https://example.com/team/repo.git"
    copy_a = _scan_result(
        "/checkouts/a",
        commit_sha="a" * 40,
        indexed_at=datetime(2026, 1, 1, tzinfo=UTC),
        remote_url=remote_url,
    )
    copy_b = _scan_result(
        "/checkouts/b",
        commit_sha="b" * 40,
        indexed_at=datetime(2026, 1, 2, tzinfo=UTC),
        remote_url=remote_url,
    )
    store.save(copy_a, [_module(function_name="from_a")], [])
    # copy_b indexes *later* than copy_a -- without working-copy scoping,
    # "latest" would flip to copy_b's run even when asked about copy_a.
    store.save(copy_b, [_module(function_name="from_b")], [])

    identity = repository_identity(copy_a)
    snapshot_a = store.latest_snapshot(identity, working_copy_root="/checkouts/a")
    snapshot_b = store.latest_snapshot(identity, working_copy_root="/checkouts/b")

    assert snapshot_a is not None and snapshot_a.modules[0].functions[0].name == "from_a"
    assert snapshot_b is not None and snapshot_b.modules[0].functions[0].name == "from_b"


def test_latest_snapshot_falls_back_to_global_latest_for_an_unknown_working_copy(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    store = IndexStore(engine)
    scan_result = _scan_result(
        str(tmp_path), commit_sha="c" * 40, indexed_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    store.save(scan_result, [_module()], [])

    snapshot = store.latest_snapshot(
        repository_identity(scan_result), working_copy_root="/never/indexed/from/here"
    )

    assert snapshot is not None


def test_get_or_create_repository_recovers_from_a_concurrent_insert_race(tmp_path: Path) -> None:
    # Simulates the race window in `IndexStore.save`: two writers both see
    # no existing row for a brand-new identity, then race to insert it.
    engine = _engine(tmp_path)
    identity = "race-identity"

    with Session(engine) as session_a:
        assert (
            session_a.execute(
                select(RepositoryORM).where(RepositoryORM.identity == identity)
            ).scalar_one_or_none()
            is None
        )

        with Session(engine) as session_b:
            session_b.add(
                RepositoryORM(identity=identity, root="root-b", created_at=datetime(2026, 1, 1))
            )
            session_b.commit()

        # session_a proceeds as if it had not seen session_b's commit --
        # its own insert must recover, not crash, and return session_b's row.
        repo_row = _get_or_create_repository(session_a, identity, "root-a")

        assert repo_row.root == "root-b"

    with Session(engine) as session:
        count = session.execute(
            select(func.count())
            .select_from(RepositoryORM)
            .where(RepositoryORM.identity == identity)
        ).scalar_one()
    assert count == 1


def test_get_or_create_working_copy_recovers_from_a_concurrent_insert_race(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as setup_session:
        repo_row = RepositoryORM(identity="repo", root="/repo", created_at=datetime(2026, 1, 1))
        setup_session.add(repo_row)
        setup_session.commit()
        repository_id = repo_row.id

    with Session(engine) as session_a:
        with Session(engine) as session_b:
            session_b.add(
                WorkingCopyORM(
                    repository_id=repository_id, root="/repo", created_at=datetime(2026, 1, 1)
                )
            )
            session_b.commit()

        working_copy_row = _get_or_create_working_copy(session_a, repository_id, "/repo")

    with Session(engine) as session:
        count = session.execute(select(func.count()).select_from(WorkingCopyORM)).scalar_one()
    assert count == 1
    assert working_copy_row.repository_id == repository_id


def test_cache_roundtrips_a_value_by_key(tmp_path: Path) -> None:
    store = IndexStore(_engine(tmp_path))

    assert store.get_cached_value("missing") is None

    store.set_cached_value("summary:abc", '{"purpose": "does things"}')
    assert store.get_cached_value("summary:abc") == '{"purpose": "does things"}'

    store.set_cached_value("summary:abc", '{"purpose": "updated"}')
    assert store.get_cached_value("summary:abc") == '{"purpose": "updated"}'
