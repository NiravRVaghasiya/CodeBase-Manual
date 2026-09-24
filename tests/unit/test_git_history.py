"""Tests for Git co-change history."""

from __future__ import annotations

import subprocess
from pathlib import Path

from codebase_manual.repository.git_history import co_changed_files


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


def _init_repo(root: Path) -> None:
    _git(["init", "-q"], root)
    _git(["config", "user.email", "test@example.com"], root)
    _git(["config", "user.name", "Test"], root)


def _commit_all(root: Path, message: str) -> None:
    _git(["add", "."], root)
    _git(["commit", "-q", "-m", message], root)


def test_co_changed_files_counts_shared_commits(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 1\n", encoding="utf-8")
    _commit_all(tmp_path, "initial")

    (tmp_path / "a.py").write_text("a = 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 2\n", encoding="utf-8")
    _commit_all(tmp_path, "change both")

    (tmp_path / "a.py").write_text("a = 3\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 3\n", encoding="utf-8")
    _commit_all(tmp_path, "change both again")

    (tmp_path / "a.py").write_text("a = 4\n", encoding="utf-8")
    _commit_all(tmp_path, "change only a")

    facts = co_changed_files(tmp_path, "a.py", min_shared_commits=2)

    # "initial" (which added both files) plus the two "change both" commits.
    assert len(facts) == 1
    assert facts[0].path == "b.py"
    assert facts[0].shared_commit_count == 3


def test_co_changed_files_returns_empty_below_threshold(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 1\n", encoding="utf-8")
    _commit_all(tmp_path, "initial")

    facts = co_changed_files(tmp_path, "a.py", min_shared_commits=2)
    assert facts == []


def test_co_changed_files_returns_empty_when_not_a_git_repository(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    assert co_changed_files(tmp_path, "a.py") == []
