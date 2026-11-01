"""Integration tests for the ``generate`` command and the git subcommand entry."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from git_commit_ai.cli.generate import git_ai_commit
from git_commit_ai.cli.main import app
from git_commit_ai.llm import FakeLLM
from tests.fixtures.git_repo import (
    commit_count,
    head_message,
    init_repo,
    scoped_history_repo,
    stage_file,
)
from tests.fixtures.scripted_llm import scripted_llm

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from typer.testing import Result

    from git_commit_ai.core.config import GitCommitAIConfig
    from git_commit_ai.llm import LLMMessage

runner = CliRunner()
GOOD = "feat(api): add orders endpoint"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "GITCOMMIT_AI_PROVIDER",
        "GITCOMMIT_AI_MODEL",
        "GITCOMMIT_AI_MAX_ITERATIONS",
        "GITCOMMIT_AI_MAX_DIFF_CHARS",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def _use(monkeypatch: pytest.MonkeyPatch, llm: FakeLLM) -> list[GitCommitAIConfig]:
    """Make the CLI use ``llm`` and record the configs it was created with."""
    seen: list[GitCommitAIConfig] = []

    def fake_create(config: GitCommitAIConfig) -> FakeLLM:
        seen.append(config)
        return llm

    monkeypatch.setattr("git_commit_ai.cli.generate.create_provider", fake_create)
    return seen


@pytest.fixture
def staged_repo(tmp_path: Path) -> Path:
    repo = scoped_history_repo(tmp_path / "repo")
    stage_file(repo, "src/api/orders.py", "def orders(): ...\n")
    return repo


def _generate(repo: Path, *args: str, stdin: str | None = None) -> Result:
    return runner.invoke(app, ["generate", "--path", str(repo), *args], input=stdin)


class TestOutput:
    def test_dry_run_prints_the_message_and_does_not_commit(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use(monkeypatch, scripted_llm([GOOD]))
        before = commit_count(staged_repo)

        result = _generate(staged_repo, "--dry-run")

        assert result.exit_code == 0
        assert result.stdout.strip() == GOOD
        assert "Generating a commit message with fake/fake-model" in result.stderr
        assert commit_count(staged_repo) == before

    def test_verbose_reports_convention_examples_and_critic_rounds(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use(monkeypatch, scripted_llm(["Add orders endpoint", GOOD]))

        result = _generate(staged_repo, "--dry-run", "--verbose")

        assert result.exit_code == 0
        assert "Convention: scoped_conventional" in result.stderr
        assert "Examples used" in result.stderr
        assert "feat(api): add users endpoint" in result.stderr
        assert "Critic rounds (2)" in result.stderr
        assert "1. Add orders endpoint -> rejected" in result.stderr
        assert "[error] format" in result.stderr
        assert "2. feat(api): add orders endpoint -> approved" in result.stderr
        assert result.stdout.strip() == GOOD

    def test_unapproved_best_draft_is_shown_with_its_remaining_issues(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use(monkeypatch, scripted_llm(["Added orders", "Added orders again"]))

        result = _generate(staged_repo, "--dry-run", "--max-iterations", "2")

        assert result.exit_code == 0
        assert "No draft passed review after 2 round(s)" in result.stderr
        assert "imperative-mood" in result.stderr
        assert result.stdout.strip() in {"Added orders", "Added orders again"}


class TestCommitting:
    def test_yes_commits_without_prompting(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use(monkeypatch, scripted_llm([GOOD]))

        result = _generate(staged_repo, "--yes")

        assert result.exit_code == 0
        assert head_message(staged_repo) == GOOD
        assert "Committed" in result.stderr

    def test_confirming_commits(self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _use(monkeypatch, scripted_llm([GOOD]))

        result = _generate(staged_repo, stdin="y\n")

        assert result.exit_code == 0
        assert head_message(staged_repo) == GOOD

    def test_declining_aborts_without_committing(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use(monkeypatch, scripted_llm([GOOD]))
        before = commit_count(staged_repo)

        result = _generate(staged_repo, stdin="n\n")

        assert result.exit_code == 1
        assert commit_count(staged_repo) == before

    def test_staging_changed_during_generation_blocks_the_commit(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inner = scripted_llm([GOOD])

        def restage_midway(messages: Sequence[LLMMessage]) -> str:
            stage_file(staged_repo, "src/api/late.py", "x = 1\n")
            return inner.complete(messages)

        _use(monkeypatch, FakeLLM(restage_midway))
        before = commit_count(staged_repo)

        result = _generate(staged_repo, "--yes")

        assert result.exit_code == 1
        assert "nothing was committed" in result.stderr
        assert commit_count(staged_repo) == before


class TestEdgeCases:
    def test_nothing_staged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = scoped_history_repo(tmp_path / "repo")
        llm = scripted_llm([GOOD])
        _use(monkeypatch, llm)

        result = _generate(repo, "--dry-run")

        assert result.exit_code == 1
        assert "Nothing is staged and there are no changes" in result.stderr
        assert llm.calls == []

    def test_unstaged_changes_get_a_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = scoped_history_repo(tmp_path / "repo")
        (repo / "src/api/users.py").write_text("changed\n", encoding="utf-8")
        _use(monkeypatch, scripted_llm([GOOD]))

        result = _generate(repo, "--dry-run")

        assert result.exit_code == 1
        assert "git add" in result.stderr

    def test_empty_repository_with_staged_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = init_repo(tmp_path / "fresh")
        stage_file(repo, "README.md", "# Fresh\n")
        _use(monkeypatch, scripted_llm(["docs: add readme"]))

        result = _generate(repo, "--yes")

        assert result.exit_code == 0
        assert head_message(repo) == "docs: add readme"

    def test_binary_only_diff_is_explained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = scoped_history_repo(tmp_path / "binary")
        stage_file(repo, "assets/logo.png", bytes(range(256)) * 4)
        llm = scripted_llm(["feat(api): add logo asset"])
        _use(monkeypatch, llm)

        result = _generate(repo, "--dry-run")

        assert result.exit_code == 0
        assert "Only binary files are staged" in result.stderr
        assert "logo.png [binary]" in llm.calls[0][1].content
        assert "Binary files" not in llm.calls[0][1].content

    def test_very_large_diff_is_truncated_with_a_notice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = scoped_history_repo(tmp_path / "repo")
        stage_file(repo, "src/api/big.py", "".join(f"value_{n} = {n}\n" for n in range(5_000)))
        llm = scripted_llm(["feat(api): add big module"])
        _use(monkeypatch, llm)

        result = _generate(repo, "--dry-run")

        assert result.exit_code == 0
        assert "too large to send in full" in result.stderr
        assert len(llm.calls[0][1].content) < 30_000

    def test_not_a_repository(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _use(monkeypatch, scripted_llm([GOOD]))
        plain = tmp_path / "plain"
        plain.mkdir()

        result = _generate(plain, "--dry-run")

        assert result.exit_code == 1
        assert "Not a Git repository" in result.stderr


class TestConfiguration:
    def test_flags_override_environment(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITCOMMIT_AI_PROVIDER", "gemini")
        monkeypatch.setenv("GITCOMMIT_AI_MAX_ITERATIONS", "5")
        seen = _use(monkeypatch, scripted_llm([GOOD]))

        result = _generate(
            staged_repo,
            "--dry-run",
            "--provider",
            "ollama",
            "--model",
            "llama3.2",
            "--max-iterations",
            "2",
        )

        assert result.exit_code == 0
        assert seen[0].llm_provider.value == "ollama"
        assert seen[0].llm_model == "llama3.2"
        assert seen[0].max_iterations == 2

    def test_environment_is_used_when_no_flag_is_given(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITCOMMIT_AI_PROVIDER", "anthropic")
        monkeypatch.setenv("GITCOMMIT_AI_MAX_ITERATIONS", "4")
        seen = _use(monkeypatch, scripted_llm([GOOD]))

        _generate(staged_repo, "--dry-run")

        assert seen[0].llm_provider.value == "anthropic"
        assert seen[0].max_iterations == 4

    def test_missing_api_key_names_the_variable(self, staged_repo: Path) -> None:
        result = _generate(staged_repo, "--dry-run", "--provider", "openai")

        assert result.exit_code == 1
        assert "OPENAI_API_KEY" in result.stderr

    def test_invalid_environment_is_reported(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITCOMMIT_AI_PROVIDER", "skynet")

        result = _generate(staged_repo, "--dry-run")

        assert result.exit_code == 1
        assert "GITCOMMIT_AI_PROVIDER" in result.stderr

    def test_out_of_range_iterations_are_rejected_by_the_parser(self, staged_repo: Path) -> None:
        assert _generate(staged_repo, "--max-iterations", "0").exit_code == 2


class TestGitSubcommand:
    def test_help_is_available_as_dash_h(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["git-ai-commit", "-h"])
        with pytest.raises(SystemExit) as info:
            git_ai_commit()
        assert info.value.code == 0
        assert "--dry-run" in capsys.readouterr().out

    def test_runs_generate_directly_without_a_subcommand_name(self, staged_repo: Path) -> None:
        launcher = "from git_commit_ai.cli.generate import git_ai_commit; git_ai_commit()"
        completed = subprocess.run(
            [sys.executable, "-c", launcher, "--path", str(staged_repo), "--provider", "openai"],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert completed.returncode == 1
        assert "OPENAI_API_KEY" in completed.stderr
