"""The seam for adding language analyzers beyond Python.

Python and TypeScript are registered -- TypeScript as a proof that the
domain model/relationship/retrieval/AI pipeline above `analyzer/` is
genuinely language-agnostic (see `analyzer.typescript_analyzer`'s module
docstring for what it does and doesn't extract), not a breadth push. Adding
either was a matter of registering an analyzer here, not restructuring the
domain model or the scanner/CLI that drive it.

`analyze_repository_incremental` is the incremental-analysis entry point:
reuse a previous index run's `PythonModule` for a file whose fingerprint
hasn't changed instead of re-parsing it. It does not attempt incremental
*relationship* derivation -- `domain.relationships.build_relationships`
always recomputes the full graph from whatever `PythonModule`s this
returns, which is cheap relative to re-parsing every file at every measured
repository size (`docs/performance.md`). Correctness over a partial graph
diff, per this project's stated preference for the latter over the former
when both are on the table.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from codebase_manual.analyzer import typescript_analyzer
from codebase_manual.analyzer.config import AnalysisContext, detect_source_roots
from codebase_manual.analyzer.python_analyzer import analyze_module, infer_module_name
from codebase_manual.domain.models import (
    PYTHON_MODULE_SCHEMA_VERSION,
    FileLanguage,
    FileRecord,
    PythonModule,
    ScanResult,
    file_fingerprint_matches,
)
from codebase_manual.logging_config import get_logger

_logger = get_logger("analyzer.registry")


class LanguageAnalyzer(Protocol):
    def __call__(
        self, *, file_path: Path, repo_relative_path: str, context: AnalysisContext
    ) -> PythonModule:
        """Extract structural facts from a single source file."""
        ...


def _analyze_python_file(
    *, file_path: Path, repo_relative_path: str, context: AnalysisContext
) -> PythonModule:
    return analyze_module(
        file_path=file_path,
        repo_relative_path=repo_relative_path,
        module_name=infer_module_name(repo_relative_path, context.source_roots),
    )


_REGISTRY: dict[FileLanguage, LanguageAnalyzer] = {
    FileLanguage.PYTHON: _analyze_python_file,
    FileLanguage.TYPESCRIPT: typescript_analyzer.analyze_module,
}


def analyzer_for(language: FileLanguage) -> LanguageAnalyzer | None:
    """Return the registered analyzer for `language`, or None if unsupported."""
    return _REGISTRY.get(language)


def supported_languages() -> list[FileLanguage]:
    return list(_REGISTRY)


def analyze_repository(root: Path, scan_result: ScanResult) -> list[PythonModule]:
    """Analyze every file whose language has a registered analyzer."""
    return analyze_repository_incremental(root, scan_result, previous_files=[], previous_modules=[])


def analyze_repository_incremental(
    root: Path,
    scan_result: ScanResult,
    *,
    previous_files: list[FileRecord],
    previous_modules: list[PythonModule],
    previous_analyzer_version: str | None = None,
) -> list[PythonModule]:
    """Like `analyze_repository`, but reuses a previous run's already-analyzed
    `PythonModule` for any file whose fingerprint hasn't changed since that run
    (`domain.models.file_fingerprint_matches` -- the same check `check`'s drift
    report uses), instead of re-parsing it.

    Falls back to full re-analysis for any file with no reusable prior module
    (new, changed, or there was no previous run -- the empty-list defaults
    `analyze_repository` passes) and for the *whole* repository if
    `previous_analyzer_version` doesn't match the current
    `PYTHON_MODULE_SCHEMA_VERSION` -- an old fact shape is not safe to reuse
    silently just because its file happens to look unchanged.
    """
    context = AnalysisContext(source_roots=detect_source_roots(root))
    reusable = _reusable_modules_by_path(
        previous_files, previous_modules, previous_analyzer_version
    )

    modules: list[PythonModule] = []
    reused = 0
    for file_record in scan_result.files:
        if file_record.is_binary:
            continue
        analyzer = analyzer_for(file_record.language)
        if analyzer is None:
            continue

        cached = reusable.get(file_record.path)
        if cached is not None and file_fingerprint_matches(cached[0], file_record):
            modules.append(cached[1])
            reused += 1
            continue

        modules.append(
            analyzer(
                file_path=root / file_record.path,
                repo_relative_path=file_record.path,
                context=context,
            )
        )

    if reusable:
        _logger.info(
            "analyze_repository_incremental total=%d reused=%d reanalyzed=%d",
            len(modules),
            reused,
            len(modules) - reused,
        )
    return modules


def _reusable_modules_by_path(
    previous_files: list[FileRecord],
    previous_modules: list[PythonModule],
    previous_analyzer_version: str | None,
) -> dict[str, tuple[FileRecord, PythonModule]]:
    if previous_analyzer_version != PYTHON_MODULE_SCHEMA_VERSION:
        return {}
    previous_files_by_path = {f.path: f for f in previous_files}
    reusable: dict[str, tuple[FileRecord, PythonModule]] = {}
    for module in previous_modules:
        file_record = previous_files_by_path.get(module.path)
        if file_record is not None:
            reusable[module.path] = (file_record, module)
    return reusable
