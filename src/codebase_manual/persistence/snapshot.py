"""The read-model consumed by everything downstream of persistence.

Query, AI, and API code work against a `RepositorySnapshot` regardless of
whether it was just produced by scanning/analyzing or reloaded from a prior
index run -- they never touch SQLAlchemy directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from codebase_manual.domain.models import FileRecord, PythonModule, Relationship


@dataclass
class RepositorySnapshot:
    repository_identity: str
    repository_root: str
    commit_sha: str | None
    branch: str | None
    remote_url: str | None
    indexed_at: datetime
    files: list[FileRecord]
    modules: list[PythonModule]
    relationships: list[Relationship]

    @property
    def is_dirty_or_unversioned(self) -> bool:
        return self.commit_sha is None
