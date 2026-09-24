"""Command-line entry point for Codebase Manual."""

from __future__ import annotations

import time
from pathlib import Path

import typer
from pydantic import BaseModel

from codebase_manual.ai.change_planner import plan_change
from codebase_manual.ai.impact import (
    ImpactFacts,
    analyze_impact,
    compute_impact_facts,
    confidence_for_impact_facts,
)
from codebase_manual.ai.manual import generate_manual
from codebase_manual.ai.models import Confidence, FileRecommendation, ImpactReport
from codebase_manual.ai.provider import (
    AIProviderError,
    AIProviderNotConfiguredError,
    AISynthesisError,
)
from codebase_manual.ai.qa import answer_question
from codebase_manual.analyzer.registry import analyze_repository
from codebase_manual.cli.context import (
    CliState,
    cli_state,
    fail,
    get_provider,
    load_snapshot_or_exit,
    open_store,
    resolve_entity_ref,
    run_ai_call,
)
from codebase_manual.cli.exit_codes import ExitCode
from codebase_manual.domain.models import EntityRef
from codebase_manual.domain.relationships import build_relationships
from codebase_manual.logging_config import configure_logging, get_logger
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.drift import detect_index_drift
from codebase_manual.repository.scanner import RepositoryScanner

app = typer.Typer(help="Codebase Manual: a living manual for software repositories.")
_logger = get_logger("cli")

_REPO_ARG = typer.Argument(..., exists=True, file_okay=False, help="Path to the repository.")


class IndexSummary(BaseModel):
    """`index`'s `--json` payload -- deterministic scan/analysis counts, no AI."""

    files: int
    python_files: int
    modules: int
    functions: int
    classes: int
    imports: int
    relationships: int
    ignored: int
    parse_errors: int
    commit_sha: str | None
    duration_seconds: float


@app.callback()
def main(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON instead of formatted text."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", help="Show full error details, including tracebacks."
    ),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress non-essential output."),
) -> None:
    """Codebase Manual: a living manual for software repositories."""
    ctx.obj = CliState(json_output=json_output, verbose=verbose, quiet=quiet)
    configure_logging(verbose=verbose, quiet=quiet)


@app.command()
def index(ctx: typer.Context, repository_path: Path = _REPO_ARG) -> None:
    """Scan a repository, extract code intelligence, and persist it."""
    state = cli_state(ctx)
    started = time.perf_counter()

    _logger.info("index started repo=%s", repository_path)
    scanner = RepositoryScanner(repository_path)
    scan_result = scanner.scan()
    modules = analyze_repository(scanner.root, scan_result)
    relationships = build_relationships(modules)

    store = open_store(scanner.root)
    store.save(scan_result, modules, relationships)

    duration = time.perf_counter() - started
    parse_errors = [m for m in modules if m.parse_error]
    summary = IndexSummary(
        files=len(scan_result.files),
        python_files=len(scan_result.python_files),
        modules=len(modules),
        functions=sum(len(m.functions) for m in modules),
        classes=sum(len(m.classes) for m in modules),
        imports=sum(len(m.imports) for m in modules),
        relationships=len(relationships),
        ignored=len(scan_result.ignored_file_paths),
        parse_errors=len(parse_errors),
        commit_sha=scan_result.repository.git.commit_sha,
        duration_seconds=round(duration, 2),
    )
    _logger.info(
        "index finished files=%d modules=%d relationships=%d duration=%.2fs",
        summary.files,
        summary.modules,
        summary.relationships,
        duration,
    )

    if state.json_output:
        typer.echo(summary.model_dump_json())
        return
    if state.quiet:
        return

    typer.echo("Repository indexed")
    typer.echo("")
    typer.echo(f"Files:         {summary.files}")
    typer.echo(f"Python files:  {summary.python_files}")
    typer.echo(f"Modules:       {summary.modules}")
    typer.echo(f"Functions:     {summary.functions}")
    typer.echo(f"Classes:       {summary.classes}")
    typer.echo(f"Imports:       {summary.imports}")
    typer.echo(f"Relationships: {summary.relationships}")
    if summary.ignored:
        typer.echo(f"Ignored:       {summary.ignored}")
    if summary.parse_errors:
        typer.echo(f"Parse errors:  {summary.parse_errors}")
    typer.echo("")
    typer.echo(f"Commit:        {summary.commit_sha or '(none)'}")
    typer.echo(f"Duration:      {summary.duration_seconds:.2f}s")


@app.command()
def ask(
    ctx: typer.Context,
    repository_path: Path = _REPO_ARG,
    question: str = typer.Argument(..., help="A natural-language question about the repository."),
) -> None:
    """Answer a question about the repository, grounded in retrieved evidence."""
    state = cli_state(ctx)
    snapshot = load_snapshot_or_exit(repository_path, verbose=state.verbose)

    answer = run_ai_call(
        "Could not answer", state, lambda: answer_question(question, snapshot, get_provider())
    )

    if state.json_output:
        typer.echo(answer.model_dump_json())
        return

    typer.echo(answer.text)
    if state.quiet:
        return
    typer.echo("")
    typer.echo(f"Confidence: {answer.confidence.value}")
    if answer.evidence:
        typer.echo("Evidence:")
        for item in answer.evidence:
            location = f" ({item.file_path})" if item.file_path else ""
            typer.echo(f"  - {item.description}{location}")


@app.command()
def change(
    ctx: typer.Context,
    repository_path: Path = _REPO_ARG,
    request: str = typer.Argument(..., help="A description of the change you want to make."),
) -> None:
    """Plan where to work for a requested change: files to modify/create, tests, and impact."""
    state = cli_state(ctx)
    snapshot = load_snapshot_or_exit(repository_path, verbose=state.verbose)

    plan = run_ai_call(
        "Could not plan this change", state, lambda: plan_change(request, snapshot, get_provider())
    )

    if state.json_output:
        typer.echo(plan.model_dump_json())
        return

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

    if state.quiet:
        return
    typer.echo("")
    typer.echo(f"Reasoning: {plan.reasoning}")
    typer.echo(f"Confidence: {plan.confidence.value}")


@app.command()
def impact(
    ctx: typer.Context,
    repository_path: Path = _REPO_ARG,
    target: str = typer.Argument(
        ..., help="A file path, module name, class name, or function's qualified name."
    ),
) -> None:
    """Show what depends on `target`: dependents, tests, and API endpoints, plus an explanation."""
    state = cli_state(ctx)
    snapshot = load_snapshot_or_exit(repository_path, verbose=state.verbose)
    ref = resolve_entity_ref(target, snapshot, verbose=state.verbose)
    facts = compute_impact_facts(ref, snapshot)
    explanation, confidence = _impact_explanation(ref, snapshot, facts, state)

    if state.json_output:
        report = ImpactReport(
            target_identifier=facts.target.identifier,
            direct_dependents=[r.identifier for r in facts.direct_dependents],
            indirect_dependents=[r.identifier for r in facts.indirect_dependents],
            affected_tests=[r.identifier for r in facts.affected_tests],
            affected_apis=facts.affected_apis,
            explanation=explanation,
            confidence=confidence,
            truncated=facts.truncated,
        )
        typer.echo(report.model_dump_json())
        return

    typer.echo(f"Target: {ref.kind.value} `{ref.identifier}`")
    typer.echo("")
    _echo_bullets("Direct dependents", [r.identifier for r in facts.direct_dependents])
    _echo_bullets("Indirect dependents", [r.identifier for r in facts.indirect_dependents])
    _echo_bullets("Affected tests", [r.identifier for r in facts.affected_tests])
    _echo_bullets("Affected API endpoints", facts.affected_apis)
    if facts.truncated:
        typer.echo(
            "Note: indirect dependents were truncated at the traversal depth limit -- "
            "the true set may be larger."
        )

    typer.echo("")
    typer.echo(f"Explanation: {explanation}")
    if not state.quiet:
        typer.echo(f"Confidence: {confidence.value}")


def _impact_explanation(
    ref: EntityRef, snapshot: RepositorySnapshot, facts: ImpactFacts, state: CliState
) -> tuple[str, Confidence]:
    """`impact` degrades gracefully on a known AI failure (facts stand alone without
    an explanation); only a genuinely unexpected error is treated as INTERNAL_ERROR.
    """
    try:
        report = analyze_impact(ref, snapshot, get_provider())
        return report.explanation, report.confidence
    except (AIProviderNotConfiguredError, AIProviderError, AISynthesisError) as exc:
        return f"AI explanation unavailable: {exc}", confidence_for_impact_facts(facts)
    except Exception as exc:  # noqa: BLE001
        fail(
            f"Could not compute impact: unexpected error: {exc}",
            ExitCode.INTERNAL_ERROR,
            exc,
            verbose=state.verbose,
        )


@app.command()
def manual(
    ctx: typer.Context,
    repository_path: Path = _REPO_ARG,
    out: Path | None = typer.Option(  # noqa: B008
        None, "--out", help="Write the manual to this file instead of stdout."
    ),
    with_ai: bool = typer.Option(
        True, help="Include AI-generated summaries where a provider is configured."
    ),
) -> None:
    """Generate the living manual: a navigable Markdown document over the indexed facts.

    Always Markdown text, regardless of `--json` -- prose documentation has
    no natural structured form to serialize.
    """
    state = cli_state(ctx)
    snapshot = load_snapshot_or_exit(repository_path, verbose=state.verbose)
    provider = get_provider() if with_ai else None
    cache = open_store(repository_path.resolve()) if with_ai else None
    text = generate_manual(snapshot, provider, cache)

    if out is not None:
        out.write_text(text, encoding="utf-8")
        if not state.quiet:
            typer.echo(f"Manual written to {out}")
    else:
        typer.echo(text)


@app.command()
def check(ctx: typer.Context, repository_path: Path = _REPO_ARG) -> None:
    """Detect index drift: how the current tree differs from the last index.

    This compares file fingerprints only -- it does not compare generated
    documentation against code semantics. Exit code follows `diff`/`grep`
    convention: 1 means "drift was found," not "the command failed."
    """
    state = cli_state(ctx)
    snapshot = load_snapshot_or_exit(repository_path, verbose=state.verbose)

    scanner = RepositoryScanner(repository_path)
    current_files = scanner.scan().files
    report = detect_index_drift(snapshot, current_files)

    if state.json_output:
        typer.echo(report.model_dump_json())
        if report.has_drift:
            raise typer.Exit(code=ExitCode.USER_ERROR)
        return

    if not report.has_drift:
        if not state.quiet:
            typer.echo("No index drift detected.")
        return

    typer.echo("Index drift detected")
    typer.echo("")
    _echo_bullets("Added files", report.added_files)
    _echo_bullets("Removed files", report.removed_files)
    _echo_bullets("Changed files", report.changed_files)
    if not state.quiet:
        typer.echo(f"Unchanged: {report.unchanged_count}")
    raise typer.Exit(code=ExitCode.USER_ERROR)


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
