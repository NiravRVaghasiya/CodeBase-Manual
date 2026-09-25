"""Saves and reloads indexed repository facts.

Re-indexing the same (repository, commit) pair is idempotent: the prior run
for that commit is replaced rather than accumulated. Runs with no commit
(no Git, or a dirty tree) are never deduplicated, since there's no stable
key to dedupe against.

Concurrent indexing is safe: `save` retries once if a concurrent writer
raced it on either the repository/working-copy get-or-create step or the
commit-dedup delete-then-insert step, both of which are otherwise subject
to a check-then-act race under concurrent writers.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from codebase_manual.domain.models import (
    PYTHON_MODULE_SCHEMA_VERSION,
    EntityKind,
    EntityRef,
    FileHashStrategy,
    FileLanguage,
    FileRecord,
    PythonModule,
    Relationship,
    RelationshipKind,
    ScanResult,
    SourceLocation,
    UnresolvedCall,
)
from codebase_manual.persistence.orm import (
    AICacheORM,
    FileORM,
    IndexRunORM,
    RelationshipORM,
    RepositoryORM,
    SymbolORM,
    UnresolvedCallORM,
    WorkingCopyORM,
)
from codebase_manual.persistence.snapshot import RepositorySnapshot

_MAX_SAVE_ATTEMPTS = 2


def repository_identity(scan_result: ScanResult) -> str:
    return scan_result.repository.git.remote_url or scan_result.repository.root


def _to_naive_utc(value: datetime) -> datetime:
    """Normalize to naive UTC before storage; SQLite does not round-trip tzinfo."""
    return value.astimezone(UTC).replace(tzinfo=None)


def _to_aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


def _get_or_create_repository(session: Session, identity: str, root: str) -> RepositoryORM:
    repo_row = session.execute(
        select(RepositoryORM).where(RepositoryORM.identity == identity)
    ).scalar_one_or_none()
    if repo_row is not None:
        return repo_row

    repo_row = RepositoryORM(
        identity=identity, root=root, created_at=_to_naive_utc(datetime.now(UTC))
    )
    session.add(repo_row)
    try:
        session.flush()
    except IntegrityError:
        # A concurrent writer created the same identity first -- use theirs.
        session.rollback()
        repo_row = session.execute(
            select(RepositoryORM).where(RepositoryORM.identity == identity)
        ).scalar_one()
    return repo_row


def _get_or_create_working_copy(session: Session, repository_id: int, root: str) -> WorkingCopyORM:
    working_copy_row = session.execute(
        select(WorkingCopyORM).where(
            WorkingCopyORM.repository_id == repository_id, WorkingCopyORM.root == root
        )
    ).scalar_one_or_none()
    if working_copy_row is not None:
        return working_copy_row

    working_copy_row = WorkingCopyORM(
        repository_id=repository_id, root=root, created_at=_to_naive_utc(datetime.now(UTC))
    )
    session.add(working_copy_row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        working_copy_row = session.execute(
            select(WorkingCopyORM).where(
                WorkingCopyORM.repository_id == repository_id, WorkingCopyORM.root == root
            )
        ).scalar_one()
    return working_copy_row


class IndexStore:
    """Persistence for repository scans, analyzed modules, and relationships."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(
        self,
        scan_result: ScanResult,
        modules: list[PythonModule],
        relationships: list[Relationship],
        unresolved_calls: Sequence[UnresolvedCall] = (),
    ) -> int:
        identity = repository_identity(scan_result)
        commit_sha = scan_result.repository.git.commit_sha
        working_copy_root = scan_result.repository.root

        last_error: IntegrityError | None = None
        for _ in range(_MAX_SAVE_ATTEMPTS):
            try:
                with Session(self._engine) as session:
                    repo_row = _get_or_create_repository(
                        session, identity, scan_result.repository.root
                    )
                    working_copy_row = _get_or_create_working_copy(
                        session, repo_row.id, working_copy_root
                    )

                    if commit_sha is not None:
                        existing_run = session.execute(
                            select(IndexRunORM).where(
                                IndexRunORM.repository_id == repo_row.id,
                                IndexRunORM.commit_sha == commit_sha,
                            )
                        ).scalar_one_or_none()
                        if existing_run is not None:
                            session.delete(existing_run)
                            session.flush()

                    run = IndexRunORM(
                        repository_id=repo_row.id,
                        working_copy_id=working_copy_row.id,
                        commit_sha=commit_sha,
                        branch=scan_result.repository.git.branch,
                        is_dirty=scan_result.repository.git.is_dirty,
                        remote_url=scan_result.repository.git.remote_url,
                        indexed_at=_to_naive_utc(scan_result.repository.indexed_at),
                        analyzer_version=PYTHON_MODULE_SCHEMA_VERSION,
                    )
                    session.add(run)
                    session.flush()

                    for file_record in scan_result.files:
                        session.add(_file_row(run.id, file_record))
                    for row in _symbol_rows(run.id, modules):
                        session.add(row)
                    for relationship in relationships:
                        session.add(_relationship_row(run.id, relationship))
                    for unresolved_call in unresolved_calls:
                        session.add(_unresolved_call_row(run.id, unresolved_call))

                    session.commit()
                    return run.id
            except IntegrityError as exc:
                # Another writer raced us on the same (repository, commit) --
                # retry so we observe and replace their committed row.
                last_error = exc
                continue

        assert last_error is not None
        raise last_error

    def latest_snapshot(
        self, identity: str, *, working_copy_root: str | None = None
    ) -> RepositorySnapshot | None:
        with Session(self._engine) as session:
            repo_row = session.execute(
                select(RepositoryORM).where(RepositoryORM.identity == identity)
            ).scalar_one_or_none()
            if repo_row is None:
                return None

            run = None
            if working_copy_root is not None:
                run = self._latest_run_for_working_copy(session, repo_row.id, working_copy_root)
            if run is None:
                run = self._latest_run(session, repo_row.id)
            if run is None:
                return None

            return _load_snapshot(session, repo_row, run)

    def _latest_run_for_working_copy(
        self, session: Session, repository_id: int, working_copy_root: str
    ) -> IndexRunORM | None:
        working_copy_row = session.execute(
            select(WorkingCopyORM).where(
                WorkingCopyORM.repository_id == repository_id,
                WorkingCopyORM.root == working_copy_root,
            )
        ).scalar_one_or_none()
        if working_copy_row is None:
            return None
        return session.execute(
            select(IndexRunORM)
            .where(IndexRunORM.working_copy_id == working_copy_row.id)
            .order_by(IndexRunORM.indexed_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def _latest_run(self, session: Session, repository_id: int) -> IndexRunORM | None:
        return session.execute(
            select(IndexRunORM)
            .where(IndexRunORM.repository_id == repository_id)
            .order_by(IndexRunORM.indexed_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def get_cached_value(self, cache_key: str) -> str | None:
        """A generic key-value cache read -- see `ai.summary_cache` for the typed usage."""
        with Session(self._engine) as session:
            row = session.execute(
                select(AICacheORM).where(AICacheORM.cache_key == cache_key)
            ).scalar_one_or_none()
            return row.payload if row is not None else None

    def set_cached_value(self, cache_key: str, payload: str) -> None:
        with Session(self._engine) as session:
            existing = session.execute(
                select(AICacheORM).where(AICacheORM.cache_key == cache_key)
            ).scalar_one_or_none()
            if existing is not None:
                existing.payload = payload
            else:
                session.add(
                    AICacheORM(
                        cache_key=cache_key,
                        payload=payload,
                        created_at=_to_naive_utc(datetime.now(UTC)),
                    )
                )
            session.commit()


def _file_row(index_run_id: int, file_record: FileRecord) -> FileORM:
    return FileORM(
        index_run_id=index_run_id,
        path=file_record.path,
        size_bytes=file_record.size_bytes,
        extension=file_record.extension,
        language=file_record.language.value,
        is_binary=file_record.is_binary,
        content_hash=file_record.content_hash,
        hash_strategy=file_record.hash_strategy.value,
        mtime=file_record.mtime,
    )


def _symbol_rows(index_run_id: int, modules: list[PythonModule]) -> list[SymbolORM]:
    rows: list[SymbolORM] = []
    for module in modules:
        if not module.module_name:
            continue
        rows.append(
            SymbolORM(
                index_run_id=index_run_id,
                kind="module",
                identifier=module.module_name,
                name=module.module_name.rsplit(".", 1)[-1],
                file_path=module.path,
                line_start=None,
                line_end=None,
                data=module.model_dump_json(),
            )
        )
        for function in module.functions:
            rows.append(
                SymbolORM(
                    index_run_id=index_run_id,
                    kind="function",
                    identifier=function.qualified_name,
                    name=function.name,
                    file_path=module.path,
                    line_start=function.location.line_start,
                    line_end=function.location.line_end,
                    data=function.model_dump_json(),
                )
            )
        for klass in module.classes:
            rows.append(
                SymbolORM(
                    index_run_id=index_run_id,
                    kind="class",
                    identifier=klass.qualified_name,
                    name=klass.name,
                    file_path=module.path,
                    line_start=klass.location.line_start,
                    line_end=klass.location.line_end,
                    data=klass.model_dump_json(),
                )
            )
            for method in klass.methods:
                rows.append(
                    SymbolORM(
                        index_run_id=index_run_id,
                        kind="function",
                        identifier=method.qualified_name,
                        name=method.name,
                        file_path=module.path,
                        line_start=method.location.line_start,
                        line_end=method.location.line_end,
                        data=method.model_dump_json(),
                    )
                )
    return rows


def _relationship_row(index_run_id: int, relationship: Relationship) -> RelationshipORM:
    return RelationshipORM(
        index_run_id=index_run_id,
        kind=relationship.kind.value,
        source_kind=relationship.source.kind.value,
        source_identifier=relationship.source.identifier,
        target_kind=relationship.target.kind.value,
        target_identifier=relationship.target.identifier,
        evidence=relationship.evidence,
        location=relationship.location.model_dump_json() if relationship.location else None,
    )


def _unresolved_call_row(index_run_id: int, unresolved_call: UnresolvedCall) -> UnresolvedCallORM:
    return UnresolvedCallORM(
        index_run_id=index_run_id,
        source_kind=unresolved_call.source.kind.value,
        source_identifier=unresolved_call.source.identifier,
        expression=unresolved_call.expression,
        location=unresolved_call.location.model_dump_json(),
    )


def _load_snapshot(
    session: Session, repo_row: RepositoryORM, run: IndexRunORM
) -> RepositorySnapshot:
    file_rows = (
        session.execute(select(FileORM).where(FileORM.index_run_id == run.id)).scalars().all()
    )
    symbol_rows = (
        session.execute(
            select(SymbolORM).where(SymbolORM.index_run_id == run.id, SymbolORM.kind == "module")
        )
        .scalars()
        .all()
    )
    relationship_rows = (
        session.execute(select(RelationshipORM).where(RelationshipORM.index_run_id == run.id))
        .scalars()
        .all()
    )
    unresolved_call_rows = (
        session.execute(select(UnresolvedCallORM).where(UnresolvedCallORM.index_run_id == run.id))
        .scalars()
        .all()
    )

    return RepositorySnapshot(
        repository_identity=repo_row.identity,
        repository_root=repo_row.root,
        commit_sha=run.commit_sha,
        branch=run.branch,
        remote_url=run.remote_url,
        indexed_at=_to_aware_utc(run.indexed_at),
        files=[_file_record(row) for row in file_rows],
        modules=_reconstruct_modules(symbol_rows),
        relationships=[_relationship(row) for row in relationship_rows],
        unresolved_calls=[_unresolved_call(row) for row in unresolved_call_rows],
        analyzer_version=run.analyzer_version,
    )


def _file_record(row: FileORM) -> FileRecord:
    return FileRecord(
        path=row.path,
        size_bytes=row.size_bytes,
        extension=row.extension,
        language=FileLanguage(row.language),
        is_binary=row.is_binary,
        content_hash=row.content_hash,
        hash_strategy=FileHashStrategy(row.hash_strategy),
        mtime=row.mtime,
    )


def _reconstruct_modules(symbol_rows: Sequence[SymbolORM]) -> list[PythonModule]:
    return [PythonModule.model_validate_json(row.data) for row in symbol_rows]


def _relationship(row: RelationshipORM) -> Relationship:
    return Relationship(
        kind=RelationshipKind(row.kind),
        source=EntityRef(kind=EntityKind(row.source_kind), identifier=row.source_identifier),
        target=EntityRef(kind=EntityKind(row.target_kind), identifier=row.target_identifier),
        evidence=row.evidence,
        location=SourceLocation.model_validate_json(row.location) if row.location else None,
    )


def _unresolved_call(row: UnresolvedCallORM) -> UnresolvedCall:
    return UnresolvedCall(
        source=EntityRef(kind=EntityKind(row.source_kind), identifier=row.source_identifier),
        expression=row.expression,
        location=SourceLocation.model_validate_json(row.location),
    )
