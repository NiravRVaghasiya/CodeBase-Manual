"""Repository discovery: walks a repository and records deterministic facts
about its structure (files, directories, languages, Git identity) while
respecting `.gitignore`.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pathspec
from pathspec.pattern import Pattern

from codebase_manual.domain.models import (
    DirectoryRecord,
    FileLanguage,
    FileRecord,
    GitMetadata,
    RepositoryRecord,
    ScanResult,
)

_LANGUAGE_BY_EXTENSION: dict[str, FileLanguage] = {
    ".py": FileLanguage.PYTHON,
    ".toml": FileLanguage.TOML,
    ".yaml": FileLanguage.YAML,
    ".yml": FileLanguage.YAML,
    ".json": FileLanguage.JSON,
    ".md": FileLanguage.MARKDOWN,
    ".sql": FileLanguage.SQL,
}

_ALWAYS_IGNORED_DIRS = {".git", ".codebase_manual"}

# Files larger than this are not hashed; content drift detection isn't worth
# the I/O cost for large assets, and they're rarely source files anyway.
_MAX_HASHABLE_BYTES = 5 * 1024 * 1024


def _detect_language(path: Path) -> FileLanguage:
    if path.name.startswith(".env"):
        return FileLanguage.ENV
    return _LANGUAGE_BY_EXTENSION.get(path.suffix.lower(), FileLanguage.UNKNOWN)


def _is_binary(path: Path, sample_size: int = 8192) -> bool:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(sample_size)
    except OSError:
        return False
    return b"\x00" in chunk


def _content_hash(path: Path, size_bytes: int) -> str | None:
    if size_bytes > _MAX_HASHABLE_BYTES:
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _load_ignore_spec(root: Path) -> pathspec.PathSpec[Pattern]:
    patterns: list[str] = []
    gitignore_path = root / ".gitignore"
    if gitignore_path.is_file():
        patterns = gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def read_git_metadata(root: Path) -> GitMetadata:
    try:
        import git
    except ImportError:
        return GitMetadata(is_git_repository=False)

    try:
        repo = git.Repo(root, search_parent_directories=False)
    except git.InvalidGitRepositoryError:
        return GitMetadata(is_git_repository=False)

    try:
        commit_sha = repo.head.commit.hexsha
    except (ValueError, TypeError):
        commit_sha = None

    try:
        branch = repo.active_branch.name
    except TypeError:
        branch = None

    try:
        is_dirty = repo.is_dirty(untracked_files=True)
    except Exception:  # noqa: BLE001 - Git state can fail in many ways; treat as unknown.
        is_dirty = None

    try:
        remote_url = repo.remotes.origin.url
    except (AttributeError, ValueError):
        remote_url = None

    return GitMetadata(
        is_git_repository=True,
        commit_sha=commit_sha,
        branch=branch,
        is_dirty=is_dirty,
        remote_url=remote_url,
    )


def repository_identity(root: Path, git: GitMetadata) -> str:
    """A stable key for a repository: its Git remote URL, or its filesystem root."""
    return git.remote_url or str(root)


class RepositoryScanner:
    """Discovers repository structure: directories, files, languages, and Git metadata."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def scan(self) -> ScanResult:
        root = self._root
        ignore_spec = _load_ignore_spec(root)

        directories: list[DirectoryRecord] = []
        files: list[FileRecord] = []
        ignored_file_paths: list[str] = []

        for current_dir, subdirs, filenames in os.walk(root):
            current_path = Path(current_dir)
            rel_dir = current_path.relative_to(root)
            at_root = rel_dir == Path()

            subdirs[:] = sorted(
                d
                for d in subdirs
                if d not in _ALWAYS_IGNORED_DIRS
                and not ignore_spec.match_file(f"{(rel_dir / d).as_posix()}/")
            )

            if not at_root:
                directories.append(DirectoryRecord(path=rel_dir.as_posix()))

            for filename in sorted(filenames):
                rel_path = filename if at_root else (rel_dir / filename).as_posix()
                if ignore_spec.match_file(rel_path):
                    ignored_file_paths.append(rel_path)
                    continue

                abs_path = current_path / filename
                try:
                    size_bytes = abs_path.stat().st_size
                except OSError:
                    continue

                is_binary = _is_binary(abs_path)
                files.append(
                    FileRecord(
                        path=rel_path,
                        size_bytes=size_bytes,
                        extension=abs_path.suffix.lower(),
                        language=_detect_language(abs_path),
                        is_binary=is_binary,
                        content_hash=None if is_binary else _content_hash(abs_path, size_bytes),
                    )
                )

        repository = RepositoryRecord(
            root=str(root),
            git=read_git_metadata(root),
            indexed_at=datetime.now(UTC),
        )
        return ScanResult(
            repository=repository,
            directories=directories,
            files=files,
            ignored_file_paths=ignored_file_paths,
        )
