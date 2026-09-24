"""Documentation drift: comparing a fresh scan against the last persisted index.

Purely a content-hash/path diff -- no interpretation. "Changed" means the
file's content hash differs from what was last indexed; it says nothing
about whether any generated documentation is actually stale, only that the
underlying facts might be.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codebase_manual.domain.models import FileRecord
from codebase_manual.persistence.snapshot import RepositorySnapshot


@dataclass
class DriftReport:
    added_files: list[str] = field(default_factory=list)
    removed_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    unchanged_count: int = 0

    @property
    def has_drift(self) -> bool:
        return bool(self.added_files or self.removed_files or self.changed_files)


def detect_drift(previous: RepositorySnapshot, current_files: list[FileRecord]) -> DriftReport:
    previous_by_path = {f.path: f for f in previous.files}
    current_by_path = {f.path: f for f in current_files}

    added = sorted(set(current_by_path) - set(previous_by_path))
    removed = sorted(set(previous_by_path) - set(current_by_path))

    changed: list[str] = []
    unchanged = 0
    for path in sorted(set(current_by_path) & set(previous_by_path)):
        previous_hash = previous_by_path[path].content_hash
        current_hash = current_by_path[path].content_hash
        if previous_hash is None or current_hash is None or previous_hash != current_hash:
            changed.append(path)
        else:
            unchanged += 1

    return DriftReport(
        added_files=added,
        removed_files=removed,
        changed_files=changed,
        unchanged_count=unchanged,
    )
