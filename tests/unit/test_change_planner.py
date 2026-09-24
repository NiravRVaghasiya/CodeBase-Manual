"""Tests for the change planner."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from codebase_manual.ai.change_planner import plan_change
from codebase_manual.domain.models import ClassSymbol, PythonModule, SourceLocation
from codebase_manual.persistence.snapshot import RepositorySnapshot

_LOCATION = SourceLocation(line_start=1, line_end=2)


class _StubProvider:
    def __init__(self, response: str | None = None) -> None:
        self.response = response
        self.called = False
        self.last_prompt: str | None = None

    def complete(self, *, system: str, prompt: str) -> str:
        self.called = True
        self.last_prompt = prompt
        if self.response is None:
            raise AssertionError("provider should not have been called")
        return self.response


def _snapshot() -> RepositorySnapshot:
    modules = [
        PythonModule(
            path="app/auth/providers/github.py",
            module_name="app.auth.providers.github",
            docstring="GitHub OAuth provider.",
            classes=[
                ClassSymbol(
                    name="GithubProvider",
                    qualified_name="app.auth.providers.github.GithubProvider",
                    bases=["OAuthProvider"],
                    location=_LOCATION,
                )
            ],
        ),
        PythonModule(
            path="app/auth/providers/gitlab.py",
            module_name="app.auth.providers.gitlab",
            docstring="GitLab OAuth provider.",
            classes=[
                ClassSymbol(
                    name="GitlabProvider",
                    qualified_name="app.auth.providers.gitlab.GitlabProvider",
                    bases=["OAuthProvider"],
                    location=_LOCATION,
                )
            ],
        ),
        PythonModule(
            path="app/auth/providers/base.py",
            module_name="app.auth.providers.base",
            docstring="Base OAuth provider interface.",
        ),
    ]
    return RepositorySnapshot(
        repository_identity="repo",
        repository_root="/repo",
        commit_sha="abc",
        branch="main",
        remote_url=None,
        indexed_at=datetime.now(UTC),
        files=[],
        modules=modules,
        relationships=[],
    )


def test_plan_change_skips_provider_when_no_evidence_found() -> None:
    provider = _StubProvider(response=None)
    plan = plan_change("deploy a kubernetes cluster", _snapshot(), provider)

    assert provider.called is False
    assert plan.confidence.value == "low"
    assert plan.files_to_create == []


def test_plan_change_includes_sibling_files_and_parses_response() -> None:
    response = json.dumps(
        {
            "goal": "Add a Google OAuth provider",
            "subsystem": "Authentication / OAuth",
            "implementation_pattern": "GitHub and GitLab providers implement OAuthProvider",
            "files_to_modify": [
                {
                    "path": "app/auth/service.py",
                    "reasoning": "register the new provider",
                    "confidence": "medium",
                }
            ],
            "files_to_create": [
                {
                    "path": "app/auth/providers/google.py",
                    "reasoning": "new provider, sibling to github.py/gitlab.py",
                    "confidence": "high",
                }
            ],
            "relevant_symbols": ["app.auth.providers.github.GithubProvider"],
            "tests_to_update": [],
            "potential_impact": ["AuthService provider registry"],
            "reasoning": "Follows the existing provider pattern.",
            "confidence": "high",
        }
    )
    provider = _StubProvider(response=response)

    plan = plan_change("Add Google OAuth", _snapshot(), provider)

    assert provider.called is True
    assert "app/auth/providers/gitlab.py" in (provider.last_prompt or "")
    assert plan.files_to_create[0].path == "app/auth/providers/google.py"
    assert plan.files_to_create[0].action.value == "create"
    assert plan.files_to_modify[0].action.value == "modify"
    assert plan.confidence.value == "high"
    assert plan.evidence
