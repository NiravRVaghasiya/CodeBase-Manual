"""FastAPI web UI over an indexed repository's snapshot.

Server-rendered with Jinja2, no separate frontend build -- consistent with
the Python-first, no-unnecessary-infrastructure approach used elsewhere in
this project. Views navigate: overview -> files -> a file's symbols and
relationships -> ask/change/impact, each grounded in the same snapshot.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from codebase_manual.ai.change_planner import plan_change
from codebase_manual.ai.impact import analyze_impact, compute_impact_facts
from codebase_manual.ai.provider import AIProviderNotConfiguredError, AISynthesisError
from codebase_manual.ai.qa import answer_question
from codebase_manual.cli.context import get_provider
from codebase_manual.domain.models import EntityKind, EntityRef
from codebase_manual.persistence.database import create_database_engine, default_database_url
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.persistence.store import IndexStore
from codebase_manual.query.entity_resolution import find_entity_ref
from codebase_manual.query.graph import RelationshipGraph
from codebase_manual.repository.scanner import read_git_metadata, repository_identity

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class RepositoryNotIndexedError(RuntimeError):
    pass


def _load_snapshot(repository_root: Path) -> RepositorySnapshot:
    identity = repository_identity(repository_root, read_git_metadata(repository_root))
    engine = create_database_engine(default_database_url(repository_root))
    snapshot = IndexStore(engine).latest_snapshot(identity)
    if snapshot is None:
        raise RepositoryNotIndexedError(
            f"No index found for {repository_root}. Run `codebase-manual index` first."
        )
    return snapshot


def create_app(repository_root: Path) -> FastAPI:
    """Build a FastAPI app serving the manual for one already-indexed repository."""
    repository_root = repository_root.resolve()
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app = FastAPI(title="Codebase Manual")

    @app.exception_handler(RepositoryNotIndexedError)
    async def _not_indexed(request: Request, exc: RepositoryNotIndexedError) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "error.html", {"message": str(exc)}, status_code=409
        )

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request) -> HTMLResponse:
        snapshot = _load_snapshot(repository_root)
        return templates.TemplateResponse(request, "overview.html", {"snapshot": snapshot})

    @app.get("/files", response_class=HTMLResponse)
    def files(request: Request) -> HTMLResponse:
        snapshot = _load_snapshot(repository_root)
        modules = sorted(snapshot.modules, key=lambda m: m.path)
        return templates.TemplateResponse(request, "files.html", {"modules": modules})

    @app.get("/files/{file_path:path}", response_class=HTMLResponse)
    def file_detail(request: Request, file_path: str) -> HTMLResponse:
        snapshot = _load_snapshot(repository_root)
        module = next((m for m in snapshot.modules if m.path == file_path), None)
        if module is None:
            return templates.TemplateResponse(
                request, "file_detail.html", {"module": None, "file_path": file_path}
            )

        graph = RelationshipGraph(snapshot.relationships)
        ref = EntityRef(kind=EntityKind.MODULE, identifier=module.module_name or "")
        return templates.TemplateResponse(
            request,
            "file_detail.html",
            {
                "module": module,
                "file_path": file_path,
                "dependents": graph.dependents_of(ref),
                "dependencies": graph.dependencies_of(ref),
            },
        )

    @app.get("/ask", response_class=HTMLResponse)
    def ask_form(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "ask.html", {"answer": None, "error": None, "question": ""}
        )

    @app.post("/ask", response_class=HTMLResponse)
    def ask_submit(request: Request, question: str = Form(...)) -> HTMLResponse:
        snapshot = _load_snapshot(repository_root)
        answer, error = None, None
        try:
            answer = answer_question(question, snapshot, get_provider())
        except (AIProviderNotConfiguredError, AISynthesisError) as exc:
            error = str(exc)
        return templates.TemplateResponse(
            request, "ask.html", {"answer": answer, "error": error, "question": question}
        )

    @app.get("/change", response_class=HTMLResponse)
    def change_form(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "change.html", {"plan": None, "error": None, "change_request": ""}
        )

    @app.post("/change", response_class=HTMLResponse)
    def change_submit(request: Request, change_request: str = Form(...)) -> HTMLResponse:
        snapshot = _load_snapshot(repository_root)
        plan, error = None, None
        try:
            plan = plan_change(change_request, snapshot, get_provider())
        except (AIProviderNotConfiguredError, AISynthesisError) as exc:
            error = str(exc)
        return templates.TemplateResponse(
            request,
            "change.html",
            {"plan": plan, "error": error, "change_request": change_request},
        )

    @app.get("/impact", response_class=HTMLResponse)
    def impact_form(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "impact.html",
            {"facts": None, "explanation": None, "error": None, "target": ""},
        )

    @app.post("/impact", response_class=HTMLResponse)
    def impact_submit(request: Request, target: str = Form(...)) -> HTMLResponse:
        snapshot = _load_snapshot(repository_root)
        facts, explanation, error = None, None, None

        ref = find_entity_ref(target, snapshot)
        if ref is None:
            error = (
                f"'{target}' does not match any indexed file path, module, class, or function name."
            )
        else:
            facts = compute_impact_facts(ref, snapshot)
            try:
                report = analyze_impact(ref, snapshot, get_provider())
                explanation = report.explanation
            except (AIProviderNotConfiguredError, AISynthesisError) as exc:
                explanation = f"AI explanation unavailable: {exc}"

        return templates.TemplateResponse(
            request,
            "impact.html",
            {"facts": facts, "explanation": explanation, "error": error, "target": target},
        )

    return app
