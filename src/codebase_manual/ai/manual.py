"""Assembles the living manual: a navigable Markdown document over a
repository's deterministic facts, optionally enriched with AI summaries.

Facts-only sections are always populated, with or without a provider. A
missing or failing AI summary is reported honestly inline, never silently
dropped or replaced with a guess.
"""

from __future__ import annotations

from codebase_manual.ai.provider import AIProvider, AIProviderNotConfiguredError, AISynthesisError
from codebase_manual.ai.summarizer import summarize_file
from codebase_manual.domain.models import FileLanguage, PythonModule
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.api_endpoints import detect_api_endpoints

_DATABASE_PATH_MARKERS = {"database", "db", "models"}


def _overview_section(snapshot: RepositorySnapshot) -> str:
    lines = [
        "# Codebase Manual",
        "",
        "## Overview",
        "",
        f"- Repository: `{snapshot.repository_root}`",
        f"- Commit: `{snapshot.commit_sha or '(none)'}`",
    ]
    if snapshot.branch:
        lines.append(f"- Branch: `{snapshot.branch}`")
    if snapshot.remote_url:
        lines.append(f"- Remote: {snapshot.remote_url}")
    lines.append(f"- Indexed at: {snapshot.indexed_at.isoformat()}")
    lines.append(f"- Files: {len(snapshot.files)}")
    lines.append(f"- Python modules: {len(snapshot.modules)}")
    return "\n".join(lines)


def _module_summary_line(module: PythonModule, provider: AIProvider | None) -> str:
    if provider is None:
        return "AI summary unavailable: no provider configured."
    try:
        summary = summarize_file(module, provider)
        return f"AI summary ({summary.confidence.value} confidence): {summary.purpose}"
    except (AIProviderNotConfiguredError, AISynthesisError) as exc:
        return f"AI summary unavailable: {exc}"


def _modules_section(snapshot: RepositorySnapshot, provider: AIProvider | None) -> str:
    lines = ["", "## Modules", ""]
    for module in sorted(snapshot.modules, key=lambda m: m.path):
        lines.append(f"### `{module.path}`")
        if module.module_name:
            lines.append(f"Module: `{module.module_name}`")
        if module.docstring:
            lines.append(f"> {module.docstring}")
        lines.append(_module_summary_line(module, provider))
        if module.classes:
            lines.append("Classes:")
            for klass in module.classes:
                bases = f" (bases: {', '.join(klass.bases)})" if klass.bases else ""
                lines.append(f"  - `{klass.qualified_name}`{bases}")
        if module.functions:
            lines.append("Functions:")
            lines.extend(f"  - `{function.qualified_name}`" for function in module.functions)
        lines.append("")
    return "\n".join(lines)


def _configuration_section(snapshot: RepositorySnapshot) -> str:
    lines = ["## Configuration", ""]
    env_files = sorted(f.path for f in snapshot.files if f.language is FileLanguage.ENV)
    if env_files:
        lines.append("Environment files:")
        lines.extend(f"  - `{path}`" for path in env_files)
    else:
        lines.append("No `.env*` files detected.")
    return "\n".join(lines)


def _apis_section(snapshot: RepositorySnapshot) -> str:
    endpoints = detect_api_endpoints(snapshot.modules)
    lines = ["", "## APIs", ""]
    if not endpoints:
        lines.append("No route-decorator based endpoints detected.")
    else:
        for endpoint in sorted(endpoints, key=lambda e: (e.file_path, e.http_method)):
            path = endpoint.path or "(dynamic path)"
            lines.append(
                f"- `{endpoint.http_method} {path}` -> "
                f"`{endpoint.function_qualified_name}` ({endpoint.file_path})"
            )
    return "\n".join(lines)


def _database_section(snapshot: RepositorySnapshot) -> str:
    lines = ["", "## Database", ""]
    candidates = sorted(
        {
            module.path
            for module in snapshot.modules
            if _DATABASE_PATH_MARKERS & set(module.path.replace("\\", "/").split("/"))
            or module.path.endswith("models.py")
        }
    )
    if not candidates:
        lines.append(
            "No database-related modules detected "
            "(heuristic: paths containing `database`/`db`/`models`)."
        )
    else:
        lines.append(
            "Heuristically detected database-related modules "
            "(paths containing `database`/`db`/`models`):"
        )
        lines.extend(f"  - `{path}`" for path in candidates)
    return "\n".join(lines)


def generate_manual(snapshot: RepositorySnapshot, provider: AIProvider | None = None) -> str:
    sections = [
        _overview_section(snapshot),
        _modules_section(snapshot, provider),
        _configuration_section(snapshot),
        _apis_section(snapshot),
        _database_section(snapshot),
    ]
    return "\n".join(sections) + "\n"
