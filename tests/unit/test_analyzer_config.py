"""Tests for Python source-root detection and security/context-limit config."""

from __future__ import annotations

from pathlib import Path

from codebase_manual.analyzer.config import (
    DEFAULT_IGNORED_PATTERNS,
    SecurityConfig,
    detect_source_roots,
    load_security_config,
)


def test_detects_a_conventional_src_layout(tmp_path: Path) -> None:
    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    assert detect_source_roots(tmp_path) == ("src",)


def test_no_source_root_for_a_plain_layout(tmp_path: Path) -> None:
    pkg = tmp_path / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    assert detect_source_roots(tmp_path) == ()


def test_a_src_directory_without_packages_is_not_treated_as_a_source_root(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "script.py").write_text("", encoding="utf-8")

    assert detect_source_roots(tmp_path) == ()


def test_explicit_pyproject_override_takes_precedence(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.codebase-manual]\npython-source-roots = ["lib"]\n', encoding="utf-8"
    )
    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    # Even though a conventional src/ layout is present, the explicit
    # override in pyproject.toml wins.
    assert detect_source_roots(tmp_path) == ("lib",)


def test_explicit_override_of_no_source_roots_is_respected(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.codebase-manual]\npython-source-roots = []\n', encoding="utf-8"
    )
    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    assert detect_source_roots(tmp_path) == ()


def test_load_security_config_defaults_when_no_pyproject(tmp_path: Path) -> None:
    config = load_security_config(tmp_path)

    assert config.ignored_paths == ()
    assert config.ignored_extensions == ()
    assert config.max_file_size == 5 * 1024 * 1024
    assert config.max_context_size == 24_000
    assert config.ignore_patterns == DEFAULT_IGNORED_PATTERNS


def test_load_security_config_reads_pyproject_overrides(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.codebase-manual]\n"
        'ignored-paths = ["build/", "*.local"]\n'
        'ignored-extensions = [".log"]\n'
        "max-file-size = 1024\n"
        "max-context-size = 500\n",
        encoding="utf-8",
    )

    config = load_security_config(tmp_path)

    assert config.ignored_paths == ("build/", "*.local")
    assert config.ignored_extensions == (".log",)
    assert config.max_file_size == 1024
    assert config.max_context_size == 500
    assert "build/" in config.ignore_patterns
    assert all(default in config.ignore_patterns for default in DEFAULT_IGNORED_PATTERNS)


def test_security_config_normalizes_extensions_without_a_leading_dot() -> None:
    config = SecurityConfig(ignored_extensions=("log", ".BAK"))

    assert config.normalized_ignored_extensions == {".log", ".bak"}
