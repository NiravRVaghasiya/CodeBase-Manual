"""Command-line entry point for Codebase Manual."""

from __future__ import annotations

import time
from pathlib import Path

import typer

from codebase_manual.ai.change_planner import plan_change
from codebase_manual.ai.impact import analyze_impact, compute_impact_facts
from codebase_manual.ai.manual import generate_manual
from codebase_manual.ai.models import FileRecommendation
from codebase_manual.ai.provider import AIProviderNotConfiguredError, AISynthesisError
from codebase_manual.ai.qa import answer_question
from codebase_manual.analyzer.registry import analyze_repository
from codebase_manual.cli.context import (
    get_provider,
    load_snapshot_or_exit,
    open_store,
    resolve_entity_ref,
)
from codebase_manual.domain.relationships import build_relationships
from codebase_manual.query.drift import detect_drift
from codebase_manual.repository.scanner import RepositoryScanner

app = typer.Typer(help="Codebase Manual: a living manual for software repositories.")

_REPO_ARG = typer.Argument(..., exists=True, file_okay=False, help="Path to the repository.")


@app.callback()
def main() -> None:
    """Codebase Manual: a living manual for software repositories."""


@app.command()
def index(repository_path: Path = _REPO_ARG) -> None:
    """Scan a repository, extract code intelligence, and persist it."""
    started = time.perf_counter()

    scanner = RepositoryScanner(repository_path)
    scan_result = scanner.scan()
    modules = analyze_repository(scanner.root, scan_result)
    relationships = build_relationships(modules)

    store = open_store(scanner.root)
    store.save(scan_result, modules, relationships)

    duration = time.perf_counter() - started
    parse_errors = [m for m in modules if m.parse_error]

    typer.echo("Repository indexed")
    typer.echo("")
    typer.echo(f"Files:         {len(scan_result.files)}")
    typer.echo(f"Python files:  {len(scan_result.python_files)}")
    typer.echo(f"Modules:       {len(modules)}")
    typer.echo(f"Functions:     {sum(len(m.functions) for m in modules)}")
    typer.echo(f"Classes:       {sum(len(m.classes) for m in modules)}")
    typer.echo(f"Imports:       {sum(len(m.imports) for m in modules)}")
    typer.echo(f"Relationships: {len(relationships)}")
    if scan_result.ignored_file_paths:
        typer.echo(f"Ignored:       {len(scan_result.ignored_file_paths)}")
    if parse_errors:
        typer.echo(f"Parse errors:  {len(parse_errors)}")
    typer.echo("")
    typer.echo(f"Commit:        {scan_result.repository.git.commit_sha or '(none)'}")
    typer.echo(f"Duration:      {duration:.2f}s")


@app.command()
def ask(
    repository_path: Path = _REPO_ARG,
    question: str = typer.Argument(..., help="A natural-language question about the repository."),
) -> None:
    """Answer a question about the repository, grounded in retrieved evidence."""
    snapshot = load_snapshot_or_exit(repository_path)

    try:
        answer = answer_question(question, snapshot, get_provider())
    except (AIProviderNotConfiguredError, AISynthesisError) as exc:
        typer.echo(f"Could not answer: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(answer.text)
    typer.echo("")
    typer.echo(f"Confidence: {answer.confidence.value}")
    if answer.evidence:
        typer.echo("Evidence:")
        for item in answer.evidence:
            location = f" ({item.file_path})" if item.file_path else ""
            typer.echo(f"  - {item.description}{location}")


@app.command()
def change(
    repository_path: Path = _REPO_ARG,
    request: str = typer.Argument(..., help="A description of the change you want to make."),
) -> None:
    """Plan where to work for a requested change: files to modify/create, tests, and impact."""
    snapshot = load_snapshot_or_exit(repository_path)

    try:
        plan = plan_change(request, snapshot, get_provider())
    except (AIProviderNotConfiguredError, AISynthesisError) as exc:
        typer.echo(f"Could not plan this change: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Goal: {plan.goal}")
    if plan.subsystem:
        typer.echo(f"Subsystem: {plan.subsystem}")
    if plan.implementation_pattern:
        typer.echo(f"Existing implementation pattern: {plan.implementation_pattern}")

    _echo_recommendations("Files to modify", plan.files_to_modify)
    _echo_recommendations("Files to create", plan.files_to_create)
    _echo_bullets("Relevant symbols", plan.relevant_symbols)
    _echo_bullets("Tests to update", plan.tests_to_update)
    _echo_bullets("Potential impact", plan.potential_impact)

    typer.echo("")
    typer.echo(f"Reasoning: {plan.reasoning}")
    typer.echo(f"Confidence: {plan.confidence.value}")


@app.command()
def impact(
    repository_path: Path = _REPO_ARG,
    target: str = typer.Argument(
        ..., help="A file path, module name, class name, or function's qualified name."
    ),
) -> None:
    """Show what depends on `target`: dependents, tests, and API endpoints, plus an explanation."""
    snapshot = load_snapshot_or_exit(repository_path)
    ref = resolve_entity_ref(target, snapshot)
    facts = compute_impact_facts(ref, snapshot)

    typer.echo(f"Target: {ref.kind.value} `{ref.identifier}`")
    typer.echo("")
    _echo_bullets("Direct dependents", [r.identifier for r in facts.direct_dependents])
    _echo_bullets("Indirect dependents", [r.identifier for r in facts.indirect_dependents])
    _echo_bullets("Affected tests", [r.identifier for r in facts.affected_tests])
    _echo_bullets("Affected API endpoints", facts.affected_apis)

    typer.echo("")
    try:
        report = analyze_impact(ref, snapshot, get_provider())
        typer.echo(f"Explanation: {report.explanation}")
        typer.echo(f"Confidence: {report.confidence.value}")
    except (AIProviderNotConfiguredError, AISynthesisError) as exc:
        typer.echo(f"AI explanation unavailable: {exc}")


@app.command()
def manual(
    repository_path: Path = _REPO_ARG,
    out: Path | None = typer.Option(  # noqa: B008
        None, "--out", help="Write the manual to this file instead of stdout."
    ),
    with_ai: bool = typer.Option(
        True, help="Include AI-generated summaries where a provider is configured."
    ),
) -> None:
    """Generate the living manual: a navigable Markdown document over the indexed facts."""
    snapshot = load_snapshot_or_exit(repository_path)
    provider = get_provider() if with_ai else None
    text = generate_manual(snapshot, provider)

    if out is not None:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"Manual written to {out}")
    else:
        typer.echo(text)


@app.command()
def check(repository_path: Path = _REPO_ARG) -> None:
    """Detect documentation drift: how the current tree differs from the last index."""
    snapshot = load_snapshot_or_exit(repository_path)

    scanner = RepositoryScanner(repository_path)
    current_files = scanner.scan().files
    report = detect_drift(snapshot, current_files)

    if not report.has_drift:
        typer.echo("No documentation drift detected.")
        return

    typer.echo("Documentation drift detected")
    typer.echo("")
    _echo_bullets("Added files", report.added_files)
    _echo_bullets("Removed files", report.removed_files)
    _echo_bullets("Changed files", report.changed_files)
    typer.echo(f"Unchanged: {report.unchanged_count}")
    raise typer.Exit(code=1)


def _echo_bullets(title: str, items: list[str]) -> None:
    if not items:
        return
    typer.echo(f"{title}:")
    for item in items:
        typer.echo(f"  - {item}")


def _echo_recommendations(title: str, items: list[FileRecommendation]) -> None:
    if not items:
        return
    typer.echo(f"{title}:")
    for item in items:
        typer.echo(f"  - {item.path} ({item.confidence.value}) -- {item.reasoning}")


@app.command()
def serve(
    repository_path: Path = _REPO_ARG,
    host: str = typer.Option("127.0.0.1", help="Host to bind."),  # noqa: B008
    port: int = typer.Option(8000, help="Port to bind."),  # noqa: B008
) -> None:
    """Serve the web UI for an already-indexed repository."""
    import uvicorn

    from codebase_manual.api.app import create_app

    uvicorn.run(create_app(repository_path), host=host, port=port)


if __name__ == "__main__":
    app()
