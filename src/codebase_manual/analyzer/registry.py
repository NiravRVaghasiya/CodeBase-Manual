"""The seam for adding language analyzers beyond Python.

Only Python is registered. Per the plan, additional languages come after
the Python implementation is proven useful -- this module exists so that
adding one later is a matter of registering an analyzer, not restructuring
the domain model or the scanner/CLI that drive it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from codebase_manual.analyzer.python_analyzer import analyze_module, infer_module_name
from codebase_manual.domain.models import FileLanguage, PythonModule, ScanResult


class LanguageAnalyzer(Protocol):
    def __call__(self, *, file_path: Path, repo_relative_path: str) -> PythonModule:
        """Extract structural facts from a single source file."""
        ...


def _analyze_python_file(*, file_path: Path, repo_relative_path: str) -> PythonModule:
    return analyze_module(
        file_path=file_path,
        repo_relative_path=repo_relative_path,
        module_name=infer_module_name(repo_relative_path),
    )


_REGISTRY: dict[FileLanguage, LanguageAnalyzer] = {
    FileLanguage.PYTHON: _analyze_python_file,
}


def analyzer_for(language: FileLanguage) -> LanguageAnalyzer | None:
    """Return the registered analyzer for `language`, or None if unsupported."""
    return _REGISTRY.get(language)


def supported_languages() -> list[FileLanguage]:
    return list(_REGISTRY)


def analyze_repository(root: Path, scan_result: ScanResult) -> list[PythonModule]:
    """Analyze every file whose language has a registered analyzer."""
    modules: list[PythonModule] = []
    for file_record in scan_result.files:
        if file_record.is_binary:
            continue
        analyzer = analyzer_for(file_record.language)
        if analyzer is None:
            continue
        modules.append(
            analyzer(file_path=root / file_record.path, repo_relative_path=file_record.path)
        )
    return modules
