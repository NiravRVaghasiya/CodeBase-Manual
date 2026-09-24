"""Tests for the repository scanner."""

from __future__ import annotations

import subprocess
from pathlib import Path

from codebase_manual.domain.models import FileLanguage
from codebase_manual.repository.scanner import RepositoryScanner


def test_scan_discovers_files_and_directories(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "module.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Title\n", encoding="utf-8")

    result = RepositoryScanner(tmp_path).scan()

    file_paths = {f.path for f in result.files}
    assert "pkg/module.py" in file_paths
    assert "README.md" in file_paths
    assert any(d.path == "pkg" for d in result.directories)


def test_scan_detects_language_by_extension(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("key: value\n", encoding="utf-8")
    (tmp_path / "data.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("KEY=1\n", encoding="utf-8")

    result = RepositoryScanner(tmp_path).scan()
    by_path = {f.path: f for f in result.files}

    assert by_path["module.py"].language is FileLanguage.PYTHON
    assert by_path["config.yaml"].language is FileLanguage.YAML
    assert by_path["data.json"].language is FileLanguage.JSON
    assert by_path[".env.local"].language is FileLanguage.ENV


def test_scan_respects_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored/\n*.log\n", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "secret.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "kept.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "debug.log").write_text("log\n", encoding="utf-8")

    result = RepositoryScanner(tmp_path).scan()
    file_paths = {f.path for f in result.files}

    assert "kept.py" in file_paths
    assert "ignored/secret.py" not in file_paths
    assert "debug.log" not in file_paths
    assert "debug.log" in result.ignored_file_paths


def test_scan_ignores_git_directory(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("", encoding="utf-8")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    result = RepositoryScanner(tmp_path).scan()
    file_paths = {f.path for f in result.files}

    assert not any(path.startswith(".git/") for path in file_paths)
    assert "app.py" in file_paths


def test_scan_ignores_own_data_directory(tmp_path: Path) -> None:
    (tmp_path / ".codebase_manual").mkdir()
    (tmp_path / ".codebase_manual" / "index.db").write_text("", encoding="utf-8")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    result = RepositoryScanner(tmp_path).scan()
    file_paths = {f.path for f in result.files}

    assert not any(path.startswith(".codebase_manual/") for path in file_paths)
    assert "app.py" in file_paths


def test_scan_detects_binary_files(tmp_path: Path) -> None:
    (tmp_path / "image.bin").write_bytes(b"\x00\x01\x02\x03")
    (tmp_path / "text.py").write_text("x = 1\n", encoding="utf-8")

    result = RepositoryScanner(tmp_path).scan()
    by_path = {f.path: f for f in result.files}

    assert by_path["image.bin"].is_binary is True
    assert by_path["text.py"].is_binary is False


def test_scan_reports_no_git_repository(tmp_path: Path) -> None:
    result = RepositoryScanner(tmp_path).scan()

    assert result.repository.git.is_git_repository is False
    assert result.repository.git.commit_sha is None


def test_scan_identifies_git_commit(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    result = RepositoryScanner(tmp_path).scan()

    assert result.repository.git.is_git_repository is True
    assert result.repository.git.commit_sha is not None
    assert len(result.repository.git.commit_sha) == 40
