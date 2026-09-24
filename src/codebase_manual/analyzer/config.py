"""Repository-level analysis configuration: Python source roots and security limits.

Resolving `src/pkg/mod.py` to the module name `pkg.mod` (not `src.pkg.mod`)
requires knowing which top-level directories are source roots rather than
package segments. This is repository-specific, so it's detected once per
`analyze_repository` call, not baked into the analyzer registry.

`SecurityConfig` lives here rather than in a separate module because it's
read from the same `[tool.codebase-manual]` pyproject table, using the same
"explicit override, else a sane default" resolution pattern already
established for source roots.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AnalysisContext:
    """Configuration threaded through language analyzers for one indexing run."""

    source_roots: tuple[str, ...] = field(default_factory=tuple)


# Patterns excluded from every scan regardless of `.gitignore` or explicit
# config, because their content is never legitimately needed for code
# intelligence and often holds secrets. Gitignore syntax: a trailing `/`
# anchors to directories, a bare pattern matches the basename anywhere.
DEFAULT_IGNORED_PATTERNS: tuple[str, ...] = (
    "*.pem",
    "*.key",
    "*.p12",
    "credentials.*",
    "secrets/",
    "node_modules/",
    "vendor/",
)

# `.env*` files are deliberately not in `DEFAULT_IGNORED_PATTERNS`: their
# *existence* is useful information for the generated manual (see
# `ai.manual._configuration_section`), so they stay in the scan results.
# What's excluded instead is their content -- see `repository.scanner`,
# which never reads `.env*` bytes into memory (always METADATA_ONLY),
# so their content can never reach a hash, the database, or an AI prompt.

_DEFAULT_MAX_FILE_SIZE = 5 * 1024 * 1024
_DEFAULT_MAX_CONTEXT_SIZE = 24_000


@dataclass(frozen=True)
class SecurityConfig:
    """Repository-configurable limits on what's scanned and what reaches an AI prompt."""

    ignored_paths: tuple[str, ...] = field(default_factory=tuple)
    ignored_extensions: tuple[str, ...] = field(default_factory=tuple)
    max_file_size: int = _DEFAULT_MAX_FILE_SIZE
    max_context_size: int = _DEFAULT_MAX_CONTEXT_SIZE

    @property
    def ignore_patterns(self) -> tuple[str, ...]:
        """All gitignore-style patterns to exclude, defaults plus repository-specific ones."""
        return (*DEFAULT_IGNORED_PATTERNS, *self.ignored_paths)

    @property
    def normalized_ignored_extensions(self) -> frozenset[str]:
        return frozenset(
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in self.ignored_extensions
        )


def detect_source_roots(repo_root: Path) -> tuple[str, ...]:
    """Determine Python source roots for `repo_root`.

    Resolution order:
    1. An explicit `[tool.codebase-manual] python-source-roots` override in
       `pyproject.toml`.
    2. A conventional `src/` layout: a top-level `src/` directory whose
       immediate subdirectories look like packages (contain `__init__.py`).
    3. No source root -- paths are used as-is (the plain `pkg/mod.py` layout).
    """
    configured = _tool_config(repo_root).get("python-source-roots")
    if isinstance(configured, list):
        return tuple(str(root).strip("/") for root in configured)

    src_dir = repo_root / "src"
    if src_dir.is_dir() and _looks_like_package_root(src_dir):
        return ("src",)

    return ()


def load_security_config(repo_root: Path) -> SecurityConfig:
    """Read `[tool.codebase-manual]` scan/context limits, falling back to defaults."""
    tool_config = _tool_config(repo_root)

    ignored_paths = tool_config.get("ignored-paths")
    ignored_extensions = tool_config.get("ignored-extensions")
    max_file_size = tool_config.get("max-file-size")
    max_context_size = tool_config.get("max-context-size")

    return SecurityConfig(
        ignored_paths=tuple(str(p) for p in ignored_paths)
        if isinstance(ignored_paths, list)
        else (),
        ignored_extensions=tuple(str(e) for e in ignored_extensions)
        if isinstance(ignored_extensions, list)
        else (),
        max_file_size=max_file_size
        if isinstance(max_file_size, int)
        else _DEFAULT_MAX_FILE_SIZE,
        max_context_size=max_context_size
        if isinstance(max_context_size, int)
        else _DEFAULT_MAX_CONTEXT_SIZE,
    )


def _tool_config(repo_root: Path) -> dict[str, Any]:
    pyproject_path = repo_root / "pyproject.toml"
    if not pyproject_path.is_file():
        return {}

    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}

    tool_config = data.get("tool", {}).get("codebase-manual", {})
    return tool_config if isinstance(tool_config, dict) else {}


def _looks_like_package_root(src_dir: Path) -> bool:
    try:
        children = list(src_dir.iterdir())
    except OSError:
        return False
    return any(child.is_dir() and (child / "__init__.py").is_file() for child in children)
