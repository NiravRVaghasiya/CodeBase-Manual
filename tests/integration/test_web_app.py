"""Integration test: the web UI over an indexed repository."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codebase_manual.analyzer.registry import analyze_repository
from codebase_manual.api.app import create_app
from codebase_manual.domain.relationships import build_relationships
from codebase_manual.persistence.database import create_database_engine, default_database_url
from codebase_manual.persistence.store import IndexStore
from codebase_manual.repository.scanner import RepositoryScanner


@pytest.fixture
def indexed_repo(tmp_path: Path) -> Path:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "service.py").write_text(
        '"""A tiny service module."""\n'
        "\n"
        "\n"
        "def greet(name: str) -> str:\n"
        '    """Greet someone."""\n'
        '    return f"hi {name}"\n',
        encoding="utf-8",
    )

    scanner = RepositoryScanner(tmp_path)
    scan_result = scanner.scan()
    modules = analyze_repository(scanner.root, scan_result)
    relationships = build_relationships(modules)

    engine = create_database_engine(default_database_url(tmp_path))
    IndexStore(engine).save(scan_result, modules, relationships)

    return tmp_path


def test_overview_page_renders(indexed_repo: Path) -> None:
    client = TestClient(create_app(indexed_repo))
    response = client.get("/")
    assert response.status_code == 200
    assert "Overview" in response.text


def test_files_page_lists_modules(indexed_repo: Path) -> None:
    client = TestClient(create_app(indexed_repo))
    response = client.get("/files")
    assert response.status_code == 200
    assert "app/service.py" in response.text


def test_file_detail_page_shows_symbols(indexed_repo: Path) -> None:
    client = TestClient(create_app(indexed_repo))
    response = client.get("/files/app/service.py")
    assert response.status_code == 200
    assert "app.service.greet" in response.text


def test_file_detail_page_handles_unknown_path(indexed_repo: Path) -> None:
    client = TestClient(create_app(indexed_repo))
    response = client.get("/files/does/not/exist.py")
    assert response.status_code == 200
    assert "Not found" in response.text


def test_ask_form_renders(indexed_repo: Path) -> None:
    client = TestClient(create_app(indexed_repo))
    response = client.get("/ask")
    assert response.status_code == 200


def test_ask_submit_shows_error_without_ai_configured(
    indexed_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = TestClient(create_app(indexed_repo))
    response = client.post("/ask", data={"question": "what does greet do"})
    assert response.status_code == 200
    assert "ANTHROPIC_API_KEY" in response.text


def test_impact_submit_shows_facts_for_known_target(indexed_repo: Path) -> None:
    client = TestClient(create_app(indexed_repo))
    response = client.post("/impact", data={"target": "app.service"})
    assert response.status_code == 200
    assert "module" in response.text


def test_impact_submit_shows_error_for_unknown_target(indexed_repo: Path) -> None:
    client = TestClient(create_app(indexed_repo))
    response = client.post("/impact", data={"target": "does.not.exist"})
    assert response.status_code == 200
    assert "does not match" in response.text


def test_overview_returns_error_when_not_indexed(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    response = client.get("/")
    assert response.status_code == 409
    assert "No index found" in response.text
