"""Integration test: a conventional src/ layout resolves module names correctly."""

from __future__ import annotations

from pathlib import Path

from codebase_manual.analyzer.registry import analyze_repository
from codebase_manual.repository.scanner import RepositoryScanner

FIXTURE_PROJECT = Path(__file__).parents[1] / "fixtures" / "src_layout"


def test_src_layout_module_name_excludes_the_src_prefix() -> None:
    scanner = RepositoryScanner(FIXTURE_PROJECT)
    scan_result = scanner.scan()
    modules = analyze_repository(scanner.root, scan_result)

    module_names = {m.module_name for m in modules}
    assert "app.service" in module_names
    assert "src.app.service" not in module_names
