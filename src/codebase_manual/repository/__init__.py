"""Repository discovery, scanning, and Git history."""

from codebase_manual.repository.git_history import CoChangeFact, co_changed_files
from codebase_manual.repository.scanner import (
    RepositoryScanner,
    read_git_metadata,
    repository_identity,
)

__all__ = [
    "CoChangeFact",
    "RepositoryScanner",
    "co_changed_files",
    "read_git_metadata",
    "repository_identity",
]
