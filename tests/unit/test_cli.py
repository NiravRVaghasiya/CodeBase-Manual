"""Tests for the CLI's exit-code scheme and `--json`/`--verbose`/`--quiet` flags."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from codebase_manual.ai.models import Answer, ChangePlan
from codebase_manual.ai.provider import AIProviderError
from codebase_manual.cli import main as cli_main
from codebase_manual.cli.exit_codes import ExitCode
from codebase_manual.query.drift import IndexDriftReport

runner = CliRunner()


class _StubProvider:
    model_identifier = "stub"

    def __init__(self, response: str | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error

    def complete(self, *, system: str, prompt: str) -> str:
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _index(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        '"""Application entry point."""\n\n\ndef main() -> None:\n    """Run the app."""\n',
        encoding="utf-8",
    )
    result = runner.invoke(cli_main.app, ["index", str(tmp_path)])
    assert result.exit_code == ExitCode.SUCCESS, result.output


def test_index_json_round_trips(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    result = runner.invoke(cli_main.app, ["--json", "index", str(tmp_path)])

    assert result.exit_code == ExitCode.SUCCESS
    summary = cli_main.IndexSummary.model_validate_json(result.output)
    assert summary.files == 1
    assert summary.modules == 1


def test_index_quiet_suppresses_output(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

    result = runner.invoke(cli_main.app, ["--quiet", "index", str(tmp_path)])

    assert result.exit_code == ExitCode.SUCCESS
    assert result.output.strip() == ""


def test_ask_without_an_index_exits_user_error(tmp_path: Path) -> None:
    result = runner.invoke(cli_main.app, ["ask", str(tmp_path), "what does main do"])

    assert result.exit_code == ExitCode.USER_ERROR


def test_ask_without_a_configured_provider_exits_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _index(tmp_path)

    result = runner.invoke(cli_main.app, ["ask", str(tmp_path), "what does main do"])

    assert result.exit_code == ExitCode.CONFIG_ERROR


def test_ask_json_round_trips_through_the_answer_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _index(tmp_path)
    response = '{"answer": "It runs main().", "cited_ids": []}'
    monkeypatch.setattr(cli_main, "get_provider", lambda: _StubProvider(response=response))

    result = runner.invoke(cli_main.app, ["--json", "ask", str(tmp_path), "what does main do"])

    assert result.exit_code == ExitCode.SUCCESS, result.output
    answer = Answer.model_validate_json(result.output)
    assert answer.text == "It runs main()."


def test_ask_provider_request_failure_exits_provider_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _index(tmp_path)
    monkeypatch.setattr(
        cli_main, "get_provider", lambda: _StubProvider(error=AIProviderError("timed out"))
    )

    result = runner.invoke(cli_main.app, ["ask", str(tmp_path), "what does main do"])

    assert result.exit_code == ExitCode.PROVIDER_ERROR


def test_ask_unexpected_provider_failure_exits_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _index(tmp_path)
    monkeypatch.setattr(
        cli_main, "get_provider", lambda: _StubProvider(error=RuntimeError("boom"))
    )

    result = runner.invoke(cli_main.app, ["ask", str(tmp_path), "what does main do"])

    assert result.exit_code == ExitCode.INTERNAL_ERROR
    assert "Traceback" not in result.output


def test_verbose_shows_a_traceback_on_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _index(tmp_path)
    monkeypatch.setattr(
        cli_main, "get_provider", lambda: _StubProvider(error=RuntimeError("boom"))
    )

    result = runner.invoke(cli_main.app, ["--verbose", "ask", str(tmp_path), "what does main do"])

    assert result.exit_code == ExitCode.INTERNAL_ERROR
    assert "RuntimeError" in result.output


def test_change_json_round_trips_through_the_change_plan_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _index(tmp_path)
    response = '{"goal": "log every call to main", "reasoning": "matched main() directly"}'
    monkeypatch.setattr(cli_main, "get_provider", lambda: _StubProvider(response=response))

    result = runner.invoke(cli_main.app, ["--json", "change", str(tmp_path), "improve main"])

    assert result.exit_code == ExitCode.SUCCESS, result.output
    plan = ChangePlan.model_validate_json(result.output)
    assert plan.goal == "log every call to main"


def test_impact_unresolvable_target_exits_user_error(tmp_path: Path) -> None:
    _index(tmp_path)

    result = runner.invoke(cli_main.app, ["impact", str(tmp_path), "nonexistent.symbol"])

    assert result.exit_code == ExitCode.USER_ERROR


def test_impact_degrades_gracefully_without_a_configured_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _index(tmp_path)

    result = runner.invoke(cli_main.app, ["impact", str(tmp_path), "app.main"])

    assert result.exit_code == ExitCode.SUCCESS
    assert "AI explanation unavailable" in result.output


def test_check_exits_user_error_when_drift_is_found(tmp_path: Path) -> None:
    _index(tmp_path)
    (tmp_path / "new_file.py").write_text("y = 2\n", encoding="utf-8")

    result = runner.invoke(cli_main.app, ["check", str(tmp_path)])

    assert result.exit_code == ExitCode.USER_ERROR
    assert "Index drift detected" in result.output


def test_check_json_round_trips_through_the_drift_report_model(tmp_path: Path) -> None:
    _index(tmp_path)
    (tmp_path / "new_file.py").write_text("y = 2\n", encoding="utf-8")

    result = runner.invoke(cli_main.app, ["--json", "check", str(tmp_path)])

    assert result.exit_code == ExitCode.USER_ERROR
    report = IndexDriftReport.model_validate_json(result.output)
    assert report.added_files == ["new_file.py"]


def test_check_exits_success_with_no_drift(tmp_path: Path) -> None:
    _index(tmp_path)

    result = runner.invoke(cli_main.app, ["check", str(tmp_path)])

    assert result.exit_code == ExitCode.SUCCESS


def test_bad_repository_path_exits_config_error(tmp_path: Path) -> None:
    result = runner.invoke(cli_main.app, ["index", str(tmp_path / "does-not-exist")])

    assert result.exit_code == ExitCode.CONFIG_ERROR
