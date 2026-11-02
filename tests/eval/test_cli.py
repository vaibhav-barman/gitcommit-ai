"""The evaluation command, driven the way ``python -m scripts.eval`` drives it."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from scripts.eval.cli import app
from scripts.eval.report import SCHEMA_VERSION, Report
from typer.testing import CliRunner

from git_commit_ai.core.settings import ENV_FILE_VARIABLE
from git_commit_ai.llm import FakeLLM
from tests.fixtures.git_repo import scoped_history_repo
from tests.fixtures.scripted_llm import scripted_llm

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import Result

    from git_commit_ai.core.config import GitCommitAIConfig

runner = CliRunner()


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FILE_VARIABLE, "")
    for var in (
        "GITCOMMIT_AI_PROVIDER",
        "GITCOMMIT_AI_MODEL",
        "GITCOMMIT_AI_MAX_ITERATIONS",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return scoped_history_repo(tmp_path / "repo")


def _invoke(repo: Path, out: Path, *args: str) -> Result:
    return runner.invoke(app, [str(repo), "--output-dir", str(out), *args])


def _report(out: Path) -> Report:
    return Report.model_validate_json((out / "report.json").read_text(encoding="utf-8"))


def test_offline_run_writes_both_reports_and_prints_the_summary(
    repo: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"

    result = _invoke(repo, out, "--offline", "--count", "4")

    assert result.exit_code == 0, result.stderr
    assert "# Evaluation report" in result.stdout
    assert "| Imperative mood |" in result.stdout
    assert (out / "report.md").read_text(encoding="utf-8") == result.stdout.rstrip("\n") + "\n"
    report = _report(out)
    assert report.schema_version == SCHEMA_VERSION
    assert report.mode == "offline"
    assert (report.requested, report.aggregate.evaluated, report.aggregate.failed) == (4, 4, 0)
    assert "[4/4]" in result.stderr
    assert f"Wrote {out / 'report.json'}" in result.stderr


def test_the_default_is_the_last_thirty_commits(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert _invoke(repo, out, "--offline").exit_code == 0
    report = _report(out)
    assert report.requested == 30
    assert len(report.commits) == 5


def test_the_json_is_valid_json_with_the_documented_top_level_keys(
    repo: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    _invoke(repo, out, "--offline", "-n", "1")
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert {"schema_version", "commits", "aggregate", "mode", "provider", "model"} <= set(data)


def test_offline_runs_ignore_the_environment(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITCOMMIT_AI_PROVIDER", "not-a-provider")
    result = _invoke(repo, tmp_path / "out", "--offline", "-n", "1")
    assert result.exit_code == 0, result.stderr


def test_max_iterations_is_applied_and_recorded(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    _invoke(repo, out, "--offline", "-n", "1", "--max-iterations", "1")
    report = _report(out)
    assert report.max_iterations == 1
    assert report.commits[0].outcome is not None
    assert report.commits[0].outcome.iterations == 1


def test_offline_cannot_be_combined_with_a_provider(repo: Path, tmp_path: Path) -> None:
    result = _invoke(repo, tmp_path / "out", "--offline", "--provider", "openai")
    assert result.exit_code == 1
    assert "--offline replaces the LLM" in result.stderr
    assert not (tmp_path / "out").exists()


def test_a_directory_that_is_not_a_repository_is_a_clean_error(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    result = _invoke(plain, tmp_path / "out", "--offline")
    assert result.exit_code == 1
    assert result.stderr.startswith("Error:")


def test_a_count_below_one_is_a_usage_error(repo: Path, tmp_path: Path) -> None:
    assert _invoke(repo, tmp_path / "out", "--offline", "--count", "0").exit_code == 2


def test_live_mode_uses_the_provider_and_announces_the_cost(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: dict[str, object] = {}

    def fake_create(config: GitCommitAIConfig, env: object = None, **_: object) -> FakeLLM:
        created["provider"] = config.llm_provider
        return scripted_llm(["fix(api): handle empty list"] * 4)

    monkeypatch.setattr("scripts.eval.cli.create_provider", fake_create)
    out = tmp_path / "out"

    result = _invoke(repo, out, "-n", "1", "--provider", "anthropic", "--model", "m-1")

    assert result.exit_code == 0, result.stderr
    assert "Live run with fake/fake-model: up to 7 model calls per commit" in result.stderr
    assert str(created["provider"]) == "anthropic"
    report = _report(out)
    assert report.mode == "live"
    assert report.provider == "fake"
    assert report.aggregate.evaluated == 1
    assert "live: fake/fake-model" in result.stdout


def test_live_mode_without_an_api_key_is_a_clean_error(repo: Path, tmp_path: Path) -> None:
    result = _invoke(repo, tmp_path / "out", "-n", "1")
    assert result.exit_code == 1
    assert "No API key for openai." in result.stderr
    assert "OPENAI_API_KEY" in result.stderr


def test_a_run_where_nothing_can_be_evaluated_fails_but_still_writes_the_report(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("scripts.eval.cli.create_provider", lambda *_a, **_k: FakeLLM([]))
    out = tmp_path / "out"

    result = _invoke(repo, out, "-n", "2")

    assert result.exit_code == 1
    assert "No commit could be evaluated" in result.stderr
    report = _report(out)
    assert (report.aggregate.evaluated, report.aggregate.failed) == (0, 2)
    assert all(c.error for c in report.commits)
