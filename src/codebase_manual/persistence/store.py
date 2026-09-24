"""Saves and reloads indexed repository facts.

Re-indexing the same (repository, commit) pair is idempotent: the prior run
for that commit is replaced rather than accumulated. Runs with no commit
(no Git, or a dirty tree) are never deduplicated, since there's no stable
key to dedupe against.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from codebase_manual.domain.models import (
    EntityKind,
    EntityRef,
    FileLanguage,
    FileRecord,
    PythonModule,
    Relationship,
    RelationshipKind,
    ScanResult,
    SourceLocation,
)
from codebase_manual.persistence.orm import (
    FileORM,
    IndexRunORM,
    RelationshipORM,
    RepositoryORM,
    SymbolORM,
)
from codebase_manual.persistence.snapshot import RepositorySnapshot


def repository_identity(scan_result: ScanResult) -> str:
    return scan_result.repository.git.remote_url or scan_result.repository.root


def _to_naive_utc(value: datetime) -> datetime:
    """Normalize to naive UTC before storage; SQLite does not round-trip tzinfo."""
    return value.astimezone(UTC).replace(tzinfo=None)


def _to_aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


class IndexStore:
    """Persistence for repository scans, analyzed modules, and relationships."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(
        self,
        scan_result: ScanResult,
        modules: list[PythonModule],
        relationships: list[Relationship],
    ) -> int:
        identity = repository_identity(scan_result)
        commit_sha = scan_result.repository.git.commit_sha

        with Session(self._engine) as session:
            repo_row = session.execute(
                select(RepositoryORM).where(RepositoryORM.identity == identity)
            ).scalar_one_or_none()
            if repo_row is None:
                repo_row = RepositoryORM(
                    identity=identity,
                    root=scan_result.repository.root,
                    created_at=_to_naive_utc(datetime.now(UTC)),
                )
                session.add(repo_row)
                session.flush()

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
                commit_sha=commit_sha,
                branch=scan_result.repository.git.branch,
                is_dirty=scan_result.repository.git.is_dirty,
                remote_url=scan_result.repository.git.remote_url,
                indexed_at=_to_naive_utc(scan_result.repository.indexed_at),
            )
            session.add(run)
            session.flush()

            for file_record in scan_result.files:
                session.add(
                    FileORM(
                        index_run_id=run.id,
                        path=file_record.path,
                        size_bytes=file_record.size_bytes,
                        extension=file_record.extension,
                        language=file_record.language.value,
                        is_binary=file_record.is_binary,
                        content_hash=file_record.content_hash,
                    )
                )

            for row in _symbol_rows(run.id, modules):
                session.add(row)

            for relationship in relationships:
                session.add(_relationship_row(run.id, relationship))

            session.commit()
            return run.id

    def latest_snapshot(self, identity: str) -> RepositorySnapshot | None:
        with Session(self._engine) as session:
            repo_row = session.execute(
                select(RepositoryORM).where(RepositoryORM.identity == identity)
            ).scalar_one_or_none()
            if repo_row is None:
                return None

            run = session.execute(
                select(IndexRunORM)
                .where(IndexRunORM.repository_id == repo_row.id)
                .order_by(IndexRunORM.indexed_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if run is None:
                return None

            return _load_snapshot(session, repo_row, run)


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
    )


def _file_record(row: FileORM) -> FileRecord:
    return FileRecord(
        path=row.path,
        size_bytes=row.size_bytes,
        extension=row.extension,
        language=FileLanguage(row.language),
        is_binary=row.is_binary,
        content_hash=row.content_hash,
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
