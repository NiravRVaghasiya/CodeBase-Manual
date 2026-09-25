"""Assembles the living manual: a navigable Markdown document over a
repository's deterministic facts, optionally enriched with AI summaries.

Facts-only sections are always populated, with or without a provider. A
missing or failing AI summary is reported honestly inline, never silently
dropped or replaced with a guess. When a `cache` is supplied, a module
whose file content hash hasn't changed reuses its previously generated
summary instead of requesting a new one on every invocation -- see
`ai.summary_cache`.
"""

from __future__ import annotations

from codebase_manual.ai.provider import (
    AIProvider,
    AIProviderError,
    AIProviderNotConfiguredError,
    AISynthesisError,
)
from codebase_manual.ai.summarizer import summarize_file
from codebase_manual.ai.summary_cache import (
    SummaryCacheStore,
    cache_summary,
    get_cached_summary,
    summary_cache_key,
)
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


def _module_summary_line(
    module: PythonModule,
    provider: AIProvider | None,
    content_hash: str | None,
    cache: SummaryCacheStore | None,
) -> str:
    if provider is None:
        return "AI summary unavailable: no provider configured."

    cache_key = None
    if cache is not None:
        cache_key = summary_cache_key(
            content_hash=content_hash, model_identifier=provider.model_identifier
        )
        if cache_key is not None:
            cached = get_cached_summary(cache, cache_key)
            if cached is not None:
                return (
                    f"AI summary ({cached.confidence.value} confidence, cached): {cached.purpose}"
                )

    try:
        summary = summarize_file(module, provider)
    except (AIProviderNotConfiguredError, AIProviderError, AISynthesisError) as exc:
        return f"AI summary unavailable: {exc}"

    if cache is not None and cache_key is not None:
        cache_summary(cache, cache_key, summary)
    return f"AI summary ({summary.confidence.value} confidence): {summary.purpose}"


def _modules_section(
    snapshot: RepositorySnapshot, provider: AIProvider | None, cache: SummaryCacheStore | None
) -> str:
    content_hash_by_path = {f.path: f.content_hash for f in snapshot.files}
    lines = ["", "## Modules", ""]
    for module in sorted(snapshot.modules, key=lambda m: m.path):
        lines.append(f"### `{module.path}`")
        if module.module_name:
            lines.append(f"Module: `{module.module_name}`")
        if module.docstring:
            lines.append(f"> {module.docstring}")
        lines.append(
            _module_summary_line(module, provider, content_hash_by_path.get(module.path), cache)
        )
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
            handler = (
                f"`{endpoint.function_qualified_name}`"
                if endpoint.function_qualified_name
                else "(handler unresolved)"
            )
            lines.append(f"- `{endpoint.http_method} {path}` -> {handler} ({endpoint.file_path})")
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


def generate_manual(
    snapshot: RepositorySnapshot,
    provider: AIProvider | None = None,
    cache: SummaryCacheStore | None = None,
) -> str:
    sections = [
        _overview_section(snapshot),
        _modules_section(snapshot, provider, cache),
        _configuration_section(snapshot),
        _apis_section(snapshot),
        _database_section(snapshot),
    ]
    return "\n".join(sections) + "\n"
