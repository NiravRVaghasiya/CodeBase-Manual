"""Integration test: scan and analyze the fixture project end-to-end."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from codebase_manual.analyzer.registry import analyze_repository
from codebase_manual.domain.models import PythonModule, ScanResult
from codebase_manual.repository.scanner import RepositoryScanner

FIXTURE_PROJECT = Path(__file__).parents[1] / "fixtures" / "fixture_project"


@pytest.fixture
def indexed_fixture(tmp_path: Path) -> tuple[ScanResult, list[PythonModule]]:
    project_copy = tmp_path / "fixture_project"
    shutil.copytree(FIXTURE_PROJECT, project_copy)
    (project_copy / "ignored.log").write_text("noise\n", encoding="utf-8")
    (project_copy / ".gitignore").write_text("*.log\n", encoding="utf-8")

    run_kwargs: dict[str, Any] = {"cwd": project_copy, "check": True}
    subprocess.run(["git", "init", "-q"], **run_kwargs)
    subprocess.run(["git", "config", "user.email", "test@example.com"], **run_kwargs)
    subprocess.run(["git", "config", "user.name", "Test"], **run_kwargs)
    subprocess.run(["git", "add", "."], **run_kwargs)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], **run_kwargs)

    scanner = RepositoryScanner(project_copy)
    scan_result = scanner.scan()
    modules = analyze_repository(scanner.root, scan_result)
    return scan_result, modules


def test_fixture_files_are_discovered(
    indexed_fixture: tuple[ScanResult, list[PythonModule]],
) -> None:
    scan_result, _ = indexed_fixture
    file_paths = {f.path for f in scan_result.files}

    assert "app/auth/service.py" in file_paths
    assert "config/settings.py" in file_paths


def test_fixture_python_files_are_identified(
    indexed_fixture: tuple[ScanResult, list[PythonModule]],
) -> None:
    scan_result, _ = indexed_fixture
    assert len(scan_result.python_files) >= 15


def test_fixture_ignored_files_are_excluded(
    indexed_fixture: tuple[ScanResult, list[PythonModule]],
) -> None:
    scan_result, _ = indexed_fixture
    file_paths = {f.path for f in scan_result.files}

    assert "ignored.log" not in file_paths
    assert "ignored.log" in scan_result.ignored_file_paths


def test_fixture_git_commit_is_identified(
    indexed_fixture: tuple[ScanResult, list[PythonModule]],
) -> None:
    scan_result, _ = indexed_fixture

    assert scan_result.repository.git.is_git_repository is True
    assert scan_result.repository.git.commit_sha is not None


def test_fixture_symbols_are_extracted(
    indexed_fixture: tuple[ScanResult, list[PythonModule]],
) -> None:
    _, modules = indexed_fixture
    by_module = {m.module_name: m for m in modules}

    assert all(m.parse_error is None for m in modules)

    service_module = by_module["app.auth.service"]
    assert "AuthService" in [c.name for c in service_module.classes]

    aliased_imports = [i for i in service_module.imports if i.alias == "GitHub"]
    assert aliased_imports and aliased_imports[0].name == "GithubProvider"

    github_module = by_module["app.auth.providers.github"]
    github_class = next(c for c in github_module.classes if c.name == "GithubProvider")
    assert github_class.bases == ["OAuthProvider"]
    assert any(m.is_async for m in github_class.methods)
