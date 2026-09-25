"""Tests for the language-analyzer registry."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from codebase_manual.analyzer.registry import (
    analyze_repository,
    analyze_repository_incremental,
    analyzer_for,
    supported_languages,
)
from codebase_manual.domain.models import (
    PYTHON_MODULE_SCHEMA_VERSION,
    FileLanguage,
    FileRecord,
    GitMetadata,
    PythonModule,
    RepositoryRecord,
    ScanResult,
)


def test_python_is_registered() -> None:
    assert analyzer_for(FileLanguage.PYTHON) is not None
    assert FileLanguage.PYTHON in supported_languages()


def test_typescript_is_registered() -> None:
    assert analyzer_for(FileLanguage.TYPESCRIPT) is not None
    assert FileLanguage.TYPESCRIPT in supported_languages()


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


def _scan_result(tmp_path: Path, *, source: str) -> ScanResult:
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    file_record = FileRecord(
        path="app.py",
        size_bytes=len(source),
        extension=".py",
        language=FileLanguage.PYTHON,
        mtime=1.0,
    )
    return ScanResult(
        repository=RepositoryRecord(
            root=str(tmp_path),
            git=GitMetadata(is_git_repository=False),
            indexed_at=datetime.now(UTC),
        ),
        directories=[],
        files=[file_record],
    )


def test_incremental_reuses_a_module_whose_file_is_unchanged(tmp_path: Path) -> None:
    scan_result = _scan_result(tmp_path, source="def run():\n    pass\n")
    previous_module = PythonModule(path="app.py", module_name="stale_cached_name")

    modules = analyze_repository_incremental(
        tmp_path,
        scan_result,
        previous_files=scan_result.files,  # identical fingerprint -> "unchanged"
        previous_modules=[previous_module],
        previous_analyzer_version=PYTHON_MODULE_SCHEMA_VERSION,
    )

    # The stale cached module is returned as-is, proving it was reused rather
    # than re-derived from the (different) source actually on disk.
    assert modules == [previous_module]


def test_incremental_reanalyzes_a_changed_file(tmp_path: Path) -> None:
    scan_result = _scan_result(tmp_path, source="def run():\n    pass\n")
    changed_previous_file = FileRecord(
        path="app.py", size_bytes=999, extension=".py", language=FileLanguage.PYTHON, mtime=1.0
    )
    previous_module = PythonModule(path="app.py", module_name="stale_cached_name")

    modules = analyze_repository_incremental(
        tmp_path,
        scan_result,
        previous_files=[changed_previous_file],
        previous_modules=[previous_module],
        previous_analyzer_version=PYTHON_MODULE_SCHEMA_VERSION,
    )

    assert modules != [previous_module]
    assert modules[0].functions[0].name == "run"


def test_incremental_ignores_previous_data_from_a_different_analyzer_version(
    tmp_path: Path,
) -> None:
    scan_result = _scan_result(tmp_path, source="def run():\n    pass\n")
    previous_module = PythonModule(path="app.py", module_name="stale_cached_name")

    modules = analyze_repository_incremental(
        tmp_path,
        scan_result,
        previous_files=scan_result.files,
        previous_modules=[previous_module],
        previous_analyzer_version="0",  # not PYTHON_MODULE_SCHEMA_VERSION
    )

    assert modules != [previous_module]
    assert modules[0].functions[0].name == "run"


def test_incremental_with_no_previous_run_behaves_like_a_full_analysis(
    tmp_path: Path,
) -> None:
    scan_result = _scan_result(tmp_path, source="def run():\n    pass\n")

    modules = analyze_repository_incremental(
        tmp_path, scan_result, previous_files=[], previous_modules=[]
    )

    assert modules[0].functions[0].name == "run"


def test_analyze_repository_produces_identical_output_to_incremental_with_no_previous(
    tmp_path: Path,
) -> None:
    scan_result = _scan_result(tmp_path, source="def run():\n    pass\n")

    assert analyze_repository(tmp_path, scan_result) == analyze_repository_incremental(
        tmp_path, scan_result, previous_files=[], previous_modules=[]
    )
