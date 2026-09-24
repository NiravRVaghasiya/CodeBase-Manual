"""Tests for the language-analyzer registry."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from codebase_manual.analyzer.registry import (
    analyze_repository,
    analyzer_for,
    supported_languages,
)
from codebase_manual.domain.models import (
    FileLanguage,
    FileRecord,
    GitMetadata,
    RepositoryRecord,
    ScanResult,
)


def test_python_is_registered() -> None:
    assert analyzer_for(FileLanguage.PYTHON) is not None
    assert FileLanguage.PYTHON in supported_languages()


def test_unregistered_language_returns_none() -> None:
    assert analyzer_for(FileLanguage.MARKDOWN) is None


def test_analyze_repository_dispatches_only_registered_languages(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Title\n", encoding="utf-8")

    scan_result = ScanResult(
        repository=RepositoryRecord(
            root=str(tmp_path),
            git=GitMetadata(is_git_repository=False),
            indexed_at=datetime.now(UTC),
        ),
        directories=[],
        files=[
            FileRecord(path="app.py", size_bytes=1, extension=".py", language=FileLanguage.PYTHON),
            FileRecord(
                path="README.md", size_bytes=1, extension=".md", language=FileLanguage.MARKDOWN
            ),
        ],
    )

    modules = analyze_repository(tmp_path, scan_result)

    assert len(modules) == 1
    assert modules[0].path == "app.py"
