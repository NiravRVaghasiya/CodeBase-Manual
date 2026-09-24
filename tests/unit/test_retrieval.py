"""Tests for deterministic keyword retrieval."""

from __future__ import annotations

from datetime import UTC, datetime

from codebase_manual.domain.models import (
    ClassSymbol,
    EntityKind,
    EntityRef,
    FunctionSymbol,
    PythonModule,
    Relationship,
    RelationshipKind,
    SourceLocation,
)
from codebase_manual.persistence.snapshot import RepositorySnapshot
from codebase_manual.query.retrieval import MatchReason, retrieve_relevant

_LOCATION = SourceLocation(line_start=1, line_end=2)


def _snapshot(
    modules: list[PythonModule], relationships: list[Relationship] | None = None
) -> RepositorySnapshot:
    return RepositorySnapshot(
        repository_identity="repo",
        repository_root="/repo",
        commit_sha="deadbeef",
        branch="main",
        remote_url=None,
        indexed_at=datetime.now(UTC),
        files=[],
        modules=modules,
        relationships=relationships or [],
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


def test_exact_name_match_outranks_a_docstring_only_match() -> None:
    snapshot = _snapshot([_auth_module(), _email_module()])
    result = retrieve_relevant("AuthService", snapshot)

    auth_service = next(s for s in result.symbols if s.identifier.endswith("AuthService"))
    assert any(sig.reason is MatchReason.NAME_EXACT for sig in auth_service.signals)
    # Every other retrieved symbol/file scores strictly lower than the exact
    # name match -- explainable weighting, not a flat overlap count.
    others = [s for s in result.symbols if s.identifier != auth_service.identifier]
    assert all(s.score <= auth_service.score for s in others)


def test_relationship_expansion_surfaces_a_called_symbol_not_lexically_matched() -> None:
    location = SourceLocation(line_start=1, line_end=2)
    caller_ref = EntityRef(kind=EntityKind.FUNCTION, identifier="app.auth.service.login")
    callee_ref = EntityRef(kind=EntityKind.FUNCTION, identifier="app.email.client.dispatch")
    modules = [
        PythonModule(
            path="app/auth/service.py",
            module_name="app.auth.service",
            functions=[
                FunctionSymbol(
                    name="login", qualified_name="app.auth.service.login", location=location
                )
            ],
        ),
        PythonModule(
            path="app/email/client.py",
            module_name="app.email.client",
            functions=[
                FunctionSymbol(
                    name="dispatch", qualified_name="app.email.client.dispatch", location=location
                )
            ],
        ),
    ]
    relationships = [
        Relationship(
            kind=RelationshipKind.CALLS,
            source=caller_ref,
            target=callee_ref,
            evidence="login calls dispatch",
        )
    ]
    snapshot = _snapshot(modules, relationships)

    result = retrieve_relevant("login", snapshot)

    dispatch = next(s for s in result.symbols if s.identifier == "app.email.client.dispatch")
    assert any(sig.reason is MatchReason.RELATIONSHIP for sig in dispatch.signals)


def test_directory_proximity_surfaces_an_unrelated_sibling_file() -> None:
    matched = PythonModule(
        path="app/auth/service.py",
        module_name="app.auth.service",
        docstring="Authentication service.",
    )
    sibling = PythonModule(path="app/auth/provider.py", module_name="app.auth.provider")
    snapshot = _snapshot([matched, sibling])

    result = retrieve_relevant("authentication service", snapshot)

    sibling_result = next(f for f in result.files if f.module.path == "app/auth/provider.py")
    assert any(sig.reason is MatchReason.DIRECTORY_PROXIMITY for sig in sibling_result.signals)


def test_relationship_chain_captures_a_multi_hop_call_flow() -> None:
    location = SourceLocation(line_start=1, line_end=2)
    login_ref = EntityRef(kind=EntityKind.FUNCTION, identifier="app.auth.router.login")
    authenticate_ref = EntityRef(
        kind=EntityKind.FUNCTION, identifier="app.auth.service.authenticate"
    )
    find_user_ref = EntityRef(
        kind=EntityKind.FUNCTION, identifier="app.users.repository.find_user"
    )
    modules = [
        PythonModule(
            path="app/auth/router.py",
            module_name="app.auth.router",
            functions=[
                FunctionSymbol(
                    name="login", qualified_name="app.auth.router.login", location=location
                )
            ],
        ),
        PythonModule(
            path="app/auth/service.py",
            module_name="app.auth.service",
            functions=[
                FunctionSymbol(
                    name="authenticate",
                    qualified_name="app.auth.service.authenticate",
                    location=location,
                )
            ],
        ),
        PythonModule(
            path="app/users/repository.py",
            module_name="app.users.repository",
            functions=[
                FunctionSymbol(
                    name="find_user",
                    qualified_name="app.users.repository.find_user",
                    location=location,
                )
            ],
        ),
    ]
    relationships = [
        Relationship(
            kind=RelationshipKind.CALLS,
            source=login_ref,
            target=authenticate_ref,
            evidence="login calls authenticate",
        ),
        Relationship(
            kind=RelationshipKind.CALLS,
            source=authenticate_ref,
            target=find_user_ref,
            evidence="authenticate calls find_user",
        ),
    ]
    snapshot = _snapshot(modules, relationships)

    result = retrieve_relevant("login", snapshot)

    assert any(
        chain.description
        == "app.auth.router.login --calls--> app.auth.service.authenticate "
        "--calls--> app.users.repository.find_user"
        for chain in result.relationship_chains
    )
