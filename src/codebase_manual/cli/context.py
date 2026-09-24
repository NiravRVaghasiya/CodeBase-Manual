"""Shared CLI plumbing: global state, exit-code mapping, and repository/store access."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import typer

from codebase_manual.ai.anthropic_provider import AnthropicProvider
from codebase_manual.ai.provider import (
    AIProvider,
    AIProviderError,
    AIProviderNotConfiguredError,
    AISynthesisError,
)
from codebase_manual.cli.exit_codes import ExitCode
from codebase_manual.domain.models import EntityRef
from codebase_manual.persistence.database import create_database_engine, default_database_url
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.persistence.store import IndexStore
from codebase_manual.query.entity_resolution import find_entity_ref
from codebase_manual.repository.scanner import read_git_metadata, repository_identity


@dataclass(frozen=True)
class CliState:
    """Global `--json`/`--verbose`/`--quiet` flags, set once in `main()`'s callback."""

    json_output: bool = False
    verbose: bool = False
    quiet: bool = False


def cli_state(ctx: typer.Context) -> CliState:
    state = ctx.obj
    if not isinstance(state, CliState):
        # Every command is reached through the `main()` callback, which always
        # sets `ctx.obj` -- this only happens if a command is invoked in a way
        # that bypasses it (e.g. directly in a test), which is a caller bug.
        raise RuntimeError("CLI state was never initialized; call through `main()`.")
    return state


def fail(message: str, code: ExitCode, cause: BaseException | None, *, verbose: bool) -> NoReturn:
    """Report `message` and exit with `code`. Full traceback only under `--verbose`."""
    typer.echo(message, err=True)
    if verbose and cause is not None:
        typer.echo("", err=True)
        formatted = "".join(traceback.format_exception(type(cause), cause, cause.__traceback__))
        typer.echo(formatted, err=True)
    raise typer.Exit(code=code)


def run_ai_call[T](action_description: str, state: CliState, thunk: Callable[[], T]) -> T:
    """Run an AI-backed call, mapping its failure modes to the CLI's exit-code scheme.

    `AIProviderNotConfiguredError` -> CONFIG_ERROR (nothing to retry, fix the
    setup). `AIProviderError`/`AISynthesisError` -> PROVIDER_ERROR (the
    provider request or its response failed). Anything else is an
    INTERNAL_ERROR -- a bug, not a usage or provider problem -- rather than
    a raw traceback with Python's default exit code.
    """
    try:
        return thunk()
    except AIProviderNotConfiguredError as exc:
        fail(f"{action_description}: {exc}", ExitCode.CONFIG_ERROR, exc, verbose=state.verbose)
    except (AIProviderError, AISynthesisError) as exc:
        fail(f"{action_description}: {exc}", ExitCode.PROVIDER_ERROR, exc, verbose=state.verbose)
    except Exception as exc:  # noqa: BLE001 - last resort: never leak a bare traceback by default
        fail(
            f"{action_description}: unexpected error: {exc}",
            ExitCode.INTERNAL_ERROR,
            exc,
            verbose=state.verbose,
        )


def open_store(repository_root: Path) -> IndexStore:
    engine = create_database_engine(default_database_url(repository_root))
    return IndexStore(engine)


def load_snapshot_or_exit(repository_path: Path, *, verbose: bool = False) -> RepositorySnapshot:
    root = repository_path.resolve()
    identity = repository_identity(root, read_git_metadata(root))
    snapshot = open_store(root).latest_snapshot(identity, working_copy_root=str(root))
    if snapshot is None:
        fail(
            f"No index found for {root}. Run `codebase-manual index {repository_path}` first.",
            ExitCode.USER_ERROR,
            None,
            verbose=verbose,
        )
    return snapshot


def get_provider() -> AIProvider:
    return AnthropicProvider()


def resolve_entity_ref(
    target: str, snapshot: RepositorySnapshot, *, verbose: bool = False
) -> EntityRef:
    """CLI-facing wrapper: resolve `target`, or exit with an error message."""
    ref = find_entity_ref(target, snapshot)
    if ref is None:
        fail(
            f"'{target}' does not match any indexed file path, module, class, or function name.",
            ExitCode.USER_ERROR,
            None,
            verbose=verbose,
        )
    return ref
