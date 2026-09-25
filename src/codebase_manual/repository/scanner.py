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

from codebase_manual.analyzer.config import SecurityConfig, load_security_config
from codebase_manual.domain.models import (
    DirectoryRecord,
    FileHashStrategy,
    FileLanguage,
    FileRecord,
    GitMetadata,
    RepositoryRecord,
    ScanResult,
)

_LANGUAGE_BY_EXTENSION: dict[str, FileLanguage] = {
    ".py": FileLanguage.PYTHON,
    ".ts": FileLanguage.TYPESCRIPT,
    ".tsx": FileLanguage.TYPESCRIPT,
    ".toml": FileLanguage.TOML,
    ".yaml": FileLanguage.YAML,
    ".yml": FileLanguage.YAML,
    ".json": FileLanguage.JSON,
    ".md": FileLanguage.MARKDOWN,
    ".sql": FileLanguage.SQL,
}

_ALWAYS_IGNORED_DIRS = {".git", ".codebase_manual"}


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


def _fingerprint(
    path: Path,
    size_bytes: int,
    *,
    is_binary: bool,
    language: FileLanguage,
    max_hashable_bytes: int,
) -> tuple[str | None, FileHashStrategy]:
    """A file's content hash when affordable and safe, else `None` with `METADATA_ONLY`.

    `METADATA_ONLY` is not "unknown" -- drift detection compares
    `size_bytes`/`mtime` for these files instead of treating them as
    unconditionally changed (see `query.drift`). `.env*` files are always
    `METADATA_ONLY`: their bytes are never read into process memory at all,
    so their content can never reach a hash, the database, or an AI prompt
    (see `analyzer.config.DEFAULT_IGNORED_PATTERNS` for the rest of the
    secret-shaped-file policy).
    """
    if is_binary or size_bytes > max_hashable_bytes or language is FileLanguage.ENV:
        return None, FileHashStrategy.METADATA_ONLY
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest(), FileHashStrategy.FULL_HASH
    except OSError:
        return None, FileHashStrategy.METADATA_ONLY


def _load_ignore_spec(root: Path, extra_patterns: tuple[str, ...]) -> pathspec.PathSpec[Pattern]:
    patterns: list[str] = list(extra_patterns)
    gitignore_path = root / ".gitignore"
    if gitignore_path.is_file():
        patterns.extend(gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines())
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

    def __init__(self, root: Path, security_config: SecurityConfig | None = None) -> None:
        self._root = Path(root).resolve()
        self._security_config = security_config or load_security_config(self._root)

    @property
    def root(self) -> Path:
        return self._root

    def scan(self) -> ScanResult:
        root = self._root
        config = self._security_config
        ignore_spec = _load_ignore_spec(root, config.ignore_patterns)
        ignored_extensions = config.normalized_ignored_extensions

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
                abs_path = current_path / filename
                is_ignored = ignore_spec.match_file(rel_path) or (
                    abs_path.suffix.lower() in ignored_extensions
                )
                if is_ignored:
                    ignored_file_paths.append(rel_path)
                    continue

                try:
                    stat_result = abs_path.stat()
                except OSError:
                    continue

                is_binary = _is_binary(abs_path)
                language = _detect_language(abs_path)
                content_hash, hash_strategy = _fingerprint(
                    abs_path,
                    stat_result.st_size,
                    is_binary=is_binary,
                    language=language,
                    max_hashable_bytes=config.max_file_size,
                )
                files.append(
                    FileRecord(
                        path=rel_path,
                        size_bytes=stat_result.st_size,
                        extension=abs_path.suffix.lower(),
                        language=language,
                        is_binary=is_binary,
                        content_hash=content_hash,
                        hash_strategy=hash_strategy,
                        mtime=stat_result.st_mtime,
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
