"""Shared CLI plumbing: opening the store and resolving a repository's latest snapshot."""

from __future__ import annotations

from pathlib import Path

import typer

from codebase_manual.ai.anthropic_provider import AnthropicProvider
from codebase_manual.ai.provider import AIProvider
from codebase_manual.domain.models import EntityRef
from codebase_manual.persistence.database import create_database_engine, default_database_url
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.persistence.store import IndexStore
from codebase_manual.query.entity_resolution import find_entity_ref
from codebase_manual.repository.scanner import read_git_metadata, repository_identity


def open_store(repository_root: Path) -> IndexStore:
    engine = create_database_engine(default_database_url(repository_root))
    return IndexStore(engine)


def load_snapshot_or_exit(repository_path: Path) -> RepositorySnapshot:
    root = repository_path.resolve()
    identity = repository_identity(root, read_git_metadata(root))
    snapshot = open_store(root).latest_snapshot(identity)
    if snapshot is None:
        typer.echo(
            f"No index found for {root}. Run `codebase-manual index {repository_path}` first.",
            err=True,
        )
        raise typer.Exit(code=1)
    return snapshot


def get_provider() -> AIProvider:
    return AnthropicProvider()


def resolve_entity_ref(target: str, snapshot: RepositorySnapshot) -> EntityRef:
    """CLI-facing wrapper: resolve `target`, or exit with an error message."""
    ref = find_entity_ref(target, snapshot)
    if ref is None:
        typer.echo(
            f"'{target}' does not match any indexed file path, module, class, or function name.",
            err=True,
        )
        raise typer.Exit(code=1)
    return ref
