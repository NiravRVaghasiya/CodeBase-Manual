"""Tests for index drift detection."""

from __future__ import annotations

from datetime import UTC, datetime

from codebase_manual.domain.models import FileHashStrategy, FileLanguage, FileRecord
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.drift import detect_index_drift


def _file(
    path: str,
    content_hash: str | None,
    *,
    hash_strategy: FileHashStrategy = FileHashStrategy.FULL_HASH,
    mtime: float | None = None,
    size_bytes: int = 10,
) -> FileRecord:
    return FileRecord(
        path=path,
        size_bytes=size_bytes,
        extension=".py",
        language=FileLanguage.PYTHON,
        content_hash=content_hash,
        hash_strategy=hash_strategy,
        mtime=mtime,
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


def test_detect_index_drift_reports_added_removed_and_changed_files() -> None:
    previous = _snapshot(
        [_file("a.py", "hash-a"), _file("b.py", "hash-b"), _file("c.py", "hash-c")]
    )
    current_files = [
        _file("a.py", "hash-a"),  # unchanged
        _file("b.py", "hash-b-2"),  # changed
        _file("d.py", "hash-d"),  # added ("c.py" removed)
    ]

    report = detect_index_drift(previous, current_files)

    assert report.added_files == ["d.py"]
    assert report.removed_files == ["c.py"]
    assert report.changed_files == ["b.py"]
    assert report.unchanged_count == 1
    assert report.has_drift is True


def test_detect_index_drift_reports_no_drift_when_nothing_changed() -> None:
    files = [_file("a.py", "hash-a"), _file("b.py", "hash-b")]
    report = detect_index_drift(_snapshot(files), files)

    assert report.has_drift is False
    assert report.unchanged_count == 2


def test_large_file_with_unchanged_metadata_is_not_treated_as_changed() -> None:
    # The original bug: a missing content hash on either side was treated
    # as evidence of change, regardless of metadata -- so a large file
    # showed as "changed" on every single run.
    previous = _snapshot(
        [
            _file(
                "big.bin",
                None,
                hash_strategy=FileHashStrategy.METADATA_ONLY,
                mtime=1000.0,
                size_bytes=10_000_000,
            )
        ]
    )
    current = [
        _file(
            "big.bin",
            None,
            hash_strategy=FileHashStrategy.METADATA_ONLY,
            mtime=1000.0,
            size_bytes=10_000_000,
        )
    ]

    report = detect_index_drift(previous, current)

    assert report.changed_files == []
    assert report.unchanged_count == 1


def test_large_file_with_changed_metadata_is_treated_as_changed() -> None:
    previous = _snapshot(
        [
            _file(
                "big.bin",
                None,
                hash_strategy=FileHashStrategy.METADATA_ONLY,
                mtime=1000.0,
                size_bytes=10_000_000,
            )
        ]
    )
    current = [
        _file(
            "big.bin",
            None,
            hash_strategy=FileHashStrategy.METADATA_ONLY,
            mtime=2000.0,
            size_bytes=10_000_000,
        )
    ]

    report = detect_index_drift(previous, current)

    assert report.changed_files == ["big.bin"]


def test_a_file_that_shrank_below_the_hashing_threshold_falls_back_to_metadata() -> None:
    # previously METADATA_ONLY, now hashable -- mixed strategies still
    # compare safely via metadata rather than crashing or always-changing.
    previous = _snapshot(
        [
            _file(
                "shrunk.bin",
                None,
                hash_strategy=FileHashStrategy.METADATA_ONLY,
                mtime=1000.0,
                size_bytes=10,
            )
        ]
    )
    current = [_file("shrunk.bin", "some-hash", hash_strategy=FileHashStrategy.FULL_HASH)]

    report = detect_index_drift(previous, current)

    # No matching mtime on the current side -> conservatively "changed".
    assert report.changed_files == ["shrunk.bin"]
