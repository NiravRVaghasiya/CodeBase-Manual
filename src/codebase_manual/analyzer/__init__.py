"""Source code analysis, dispatched by language through a small registry."""

from codebase_manual.analyzer.python_analyzer import analyze_module, infer_module_name
from codebase_manual.analyzer.registry import (
    LanguageAnalyzer,
    analyze_repository,
    analyzer_for,
    supported_languages,
)

__all__ = [
    "LanguageAnalyzer",
    "analyze_module",
    "analyze_repository",
    "analyzer_for",
    "infer_module_name",
    "supported_languages",
]
