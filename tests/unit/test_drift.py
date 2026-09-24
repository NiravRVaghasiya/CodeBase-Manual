"""Tests for documentation drift detection."""

from __future__ import annotations

from datetime import UTC, datetime

from codebase_manual.domain.models import FileLanguage, FileRecord
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.drift import detect_drift


def _file(path: str, content_hash: str | None) -> FileRecord:
    return FileRecord(
        path=path,
        size_bytes=10,
        extension=".py",
        language=FileLanguage.PYTHON,
        content_hash=content_hash,
    )


def _snapshot(files: list[FileRecord]) -> RepositorySnapshot:
    return RepositorySnapshot(
        repository_identity="repo",
        repository_root="/repo",
        commit_sha="abc",
        branch="main",
        remote_url=None,
        indexed_at=datetime.now(UTC),
        files=files,
        modules=[],
        relationships=[],
    )


def test_detect_drift_reports_added_removed_and_changed_files() -> None:
    previous = _snapshot(
        [_file("a.py", "hash-a"), _file("b.py", "hash-b"), _file("c.py", "hash-c")]
    )
    current_files = [
        _file("a.py", "hash-a"),  # unchanged
        _file("b.py", "hash-b-2"),  # changed
        _file("d.py", "hash-d"),  # added ("c.py" removed)
    ]

    report = detect_drift(previous, current_files)

    assert report.added_files == ["d.py"]
    assert report.removed_files == ["c.py"]
    assert report.changed_files == ["b.py"]
    assert report.unchanged_count == 1
    assert report.has_drift is True


def test_detect_drift_reports_no_drift_when_nothing_changed() -> None:
    files = [_file("a.py", "hash-a"), _file("b.py", "hash-b")]
    report = detect_drift(_snapshot(files), files)

    assert report.has_drift is False
    assert report.unchanged_count == 2


def test_detect_drift_treats_missing_hashes_as_changed() -> None:
    previous = _snapshot([_file("a.py", None)])
    report = detect_drift(previous, [_file("a.py", None)])

    assert report.changed_files == ["a.py"]
