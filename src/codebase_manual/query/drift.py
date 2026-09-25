"""Index drift: comparing a fresh scan against the last persisted index.

Purely a content-hash/metadata diff -- no interpretation. "Changed" means
the file's fingerprint differs from what was last indexed; it says nothing
about whether any generated documentation is actually stale, only that the
underlying facts might be. Named "index drift," not "documentation drift":
this never compares generated documentation against code semantics.

Files without a full content hash (binary, or larger than the hashing
threshold -- see `repository.scanner`) are compared by `size_bytes`/`mtime`
instead of being treated as unconditionally changed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from codebase_manual.domain.models import FileRecord, file_fingerprint_matches
from codebase_manual.persistence.snapshot import RepositorySnapshot


class IndexDriftReport(BaseModel):
    """Pydantic so `cli.main check --json` can serialize it with `.model_dump_json()`."""

    added_files: list[str] = Field(default_factory=list)
    removed_files: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    unchanged_count: int = 0

    @property
    def has_drift(self) -> bool:
        return bool(self.added_files or self.removed_files or self.changed_files)


def detect_index_drift(
    previous: RepositorySnapshot, current_files: list[FileRecord]
) -> IndexDriftReport:
    previous_by_path = {f.path: f for f in previous.files}
    current_by_path = {f.path: f for f in current_files}

    added = sorted(set(current_by_path) - set(previous_by_path))
    removed = sorted(set(previous_by_path) - set(current_by_path))

    changed: list[str] = []
    unchanged = 0
    for path in sorted(set(current_by_path) & set(previous_by_path)):
        if file_fingerprint_matches(previous_by_path[path], current_by_path[path]):
            unchanged += 1
        else:
            changed.append(path)

    return IndexDriftReport(
        added_files=added,
        removed_files=removed,
        changed_files=changed,
        unchanged_count=unchanged,
    )
