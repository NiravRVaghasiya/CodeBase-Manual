"""Tests for deterministic keyword retrieval."""

from __future__ import annotations

from datetime import UTC, datetime

from codebase_manual.domain.models import ClassSymbol, FunctionSymbol, PythonModule, SourceLocation
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.retrieval import retrieve_relevant

_LOCATION = SourceLocation(line_start=1, line_end=2)


def _snapshot(modules: list[PythonModule]) -> RepositorySnapshot:
    return RepositorySnapshot(
        repository_identity="repo",
        repository_root="/repo",
        commit_sha="deadbeef",
        branch="main",
        remote_url=None,
        indexed_at=datetime.now(UTC),
        files=[],
        modules=modules,
        relationships=[],
    )


def _auth_module() -> PythonModule:
    return PythonModule(
        path="app/auth/service.py",
        module_name="app.auth.service",
        docstring="Authentication service coordinating OAuth providers.",
        classes=[
            ClassSymbol(
                name="AuthService",
                qualified_name="app.auth.service.AuthService",
                docstring="Coordinates OAuth providers.",
                location=_LOCATION,
            )
        ],
        functions=[
            FunctionSymbol(
                name="login_with_provider",
                qualified_name="app.auth.service.login_with_provider",
                docstring="Authenticate a user against a provider.",
                location=_LOCATION,
            )
        ],
    )


def _email_module() -> PythonModule:
    return PythonModule(
        path="app/email/client.py",
        module_name="app.email.client",
        functions=[
            FunctionSymbol(
                name="send_welcome_email",
                qualified_name="app.email.client.send_welcome_email",
                docstring="Sends a welcome email to a new user.",
                location=_LOCATION,
            )
        ],
    )


def test_retrieve_relevant_ranks_matching_files_first() -> None:
    snapshot = _snapshot([_auth_module(), _email_module()])
    result = retrieve_relevant("How does OAuth authentication work?", snapshot)

    assert result.files
    assert result.files[0].module.module_name == "app.auth.service"


def test_retrieve_relevant_matches_symbols_by_name_and_docstring() -> None:
    snapshot = _snapshot([_auth_module(), _email_module()])
    result = retrieve_relevant("send a welcome email", snapshot)

    identifiers = [s.identifier for s in result.symbols]
    assert "app.email.client.send_welcome_email" in identifiers


def test_retrieve_relevant_returns_empty_when_nothing_matches() -> None:
    snapshot = _snapshot([_auth_module()])
    result = retrieve_relevant("kubernetes deployment manifests", snapshot)

    assert result.is_empty


def test_retrieve_relevant_matches_class_by_docstring() -> None:
    snapshot = _snapshot([_auth_module()])
    result = retrieve_relevant("which class coordinates OAuth providers", snapshot)

    matched_classes = [s for s in result.symbols if s.kind == "class"]
    assert any(s.identifier == "app.auth.service.AuthService" for s in matched_classes)
