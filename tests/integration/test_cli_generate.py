"""Integration tests for the ``generate`` command and the git subcommand entry."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from git_commit_ai.cli.git_subcommand import git_ai_commit
from git_commit_ai.cli.interaction import Interaction
from git_commit_ai.cli.main import app
from git_commit_ai.core.exceptions import RateLimitError, TransientLLMError
from git_commit_ai.core.user_config import UserConfig, save_user_config
from git_commit_ai.llm import FakeLLM
from git_commit_ai.llm.retry import RetryEvent
from tests.fixtures.cli import plain, use_llm
from tests.fixtures.git_repo import (
    commit_count,
    diverged_repo,
    head_message,
    init_repo,
    install_hook,
    resolve_conflict,
    run_git,
    scoped_history_repo,
    stage_file,
    start_operation,
)
from tests.fixtures.prompter import ScriptedPrompter
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
    monkeypatch.setenv("GITCOMMIT_AI_ENV_FILE", "")
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


@pytest.fixture
def staged_repo(tmp_path: Path) -> Path:
    repo = scoped_history_repo(tmp_path / "repo")
    stage_file(repo, "src/api/orders.py", "def orders(): ...\n")
    return repo


def _generate(repo: Path, *args: str, answers: ScriptedPrompter | None = None) -> Result:
    """Run ``generate``; with ``answers`` the run is interactive and answers from them."""
    obj = None
    if answers is not None:
        obj = Interaction(interactive=True, prompter_factory=lambda: answers)
    return runner.invoke(app, ["generate", "--path", str(repo), *args], obj=obj)


class TestOutput:
    def test_dry_run_prints_the_message_and_does_not_commit(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD]))
        before = commit_count(staged_repo)

        result = _generate(staged_repo, "--dry-run")

        assert result.exit_code == 0
        assert result.stdout.strip() == GOOD
        assert "Generating a commit message with fake/fake-model" in result.stderr
        assert commit_count(staged_repo) == before

    def test_verbose_reports_convention_examples_and_critic_rounds(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm(["Add orders endpoint", GOOD]))

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
        use_llm(monkeypatch, scripted_llm(["Added orders", "Added orders again"]))

        result = _generate(staged_repo, "--dry-run", "--max-iterations", "2")

        assert result.exit_code == 0
        assert "No draft passed review after 2 round(s)" in result.stderr
        assert "imperative-mood" in result.stderr
        assert result.stdout.strip() in {"Added orders", "Added orders again"}


class TestDegradation:
    def test_critic_failure_warns_and_still_offers_the_draft(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = scripted_llm([GOOD], verdict_errors={1: RateLimitError("openai")})
        use_llm(monkeypatch, llm)

        result = _generate(staged_repo, "--dry-run")

        assert result.exit_code == 0
        assert result.stdout.strip() == GOOD
        assert "Stopped early" in result.stderr
        assert "could not be checked against the diff" in result.stderr
        assert "Showing the best draft so far" in result.stderr

    def test_later_round_failure_shows_the_best_draft_so_far(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = scripted_llm(
            ["Added orders endpoint"],
            generator_errors={2: TransientLLMError("openai", "request timed out")},
        )
        use_llm(monkeypatch, llm)

        result = _generate(staged_repo, "--dry-run")

        assert result.exit_code == 0
        assert result.stdout.strip() == "Added orders endpoint"
        assert "round 2 could not be generated" in result.stderr
        assert "imperative-mood" in result.stderr

    def test_first_generation_failure_aborts_with_a_clean_error(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = scripted_llm([], generator_errors={1: RateLimitError("openai")})
        use_llm(monkeypatch, llm)

        result = _generate(staged_repo, "--dry-run")

        assert result.exit_code == 1
        assert "rate limit" in result.stderr
        assert result.stdout.strip() == ""

    def test_retries_are_announced(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch, isolated_user_config: Path
    ) -> None:
        save_user_config(isolated_user_config, UserConfig())

        def announce(
            config: GitCommitAIConfig, api_key: str | None, on_retry: object = None
        ) -> FakeLLM:
            assert callable(on_retry)
            on_retry(RetryEvent("openai", 1, 3, 2.0, RateLimitError("openai")))
            return scripted_llm([GOOD])

        monkeypatch.setattr("git_commit_ai.cli.generate.create_provider", announce)

        result = _generate(staged_repo, "--dry-run")

        assert result.exit_code == 0
        assert "Retrying in 2s (1/3)" in result.stderr

    def test_yes_refuses_to_commit_an_interrupted_draft(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD], verdict_errors={1: RateLimitError("openai")}))
        before = commit_count(staged_repo)

        result = _generate(staged_repo, "--yes")

        assert result.exit_code == 1
        assert commit_count(staged_repo) == before
        assert "Not committing: the run stopped early" in result.stderr
        assert "--accept-partial" in result.stderr
        assert result.stdout.strip() == GOOD

    def test_accept_partial_lets_yes_commit_an_interrupted_draft(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD], verdict_errors={1: RateLimitError("openai")}))
        before = commit_count(staged_repo)

        result = _generate(staged_repo, "--yes", "--accept-partial")

        assert result.exit_code == 0
        assert commit_count(staged_repo) == before + 1
        assert head_message(staged_repo) == GOOD
        assert "Stopped early" in result.stderr

    def test_yes_still_commits_an_uninterrupted_run(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD]))

        result = _generate(staged_repo, "--yes")

        assert result.exit_code == 0
        assert head_message(staged_repo) == GOOD

    def test_without_yes_an_interrupted_draft_goes_to_the_prompt(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "FAKE-" + "g" * 24)
        use_llm(monkeypatch, scripted_llm([GOOD], verdict_errors={1: RateLimitError("openai")}))
        before = commit_count(staged_repo)

        prompter = ScriptedPrompter([False])
        result = _generate(staged_repo, "--accept-partial", answers=prompter)

        assert result.exit_code == 1
        assert commit_count(staged_repo) == before
        assert prompter.messages() == ["Commit with this message?"]

    def test_dry_run_with_yes_is_not_an_error_for_an_interrupted_draft(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD], verdict_errors={1: RateLimitError("openai")}))

        result = _generate(staged_repo, "--dry-run", "--yes")

        assert result.exit_code == 0
        assert result.stdout.strip() == GOOD


class TestHint:
    def test_hint_reaches_both_prompts(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = scripted_llm([GOOD])
        use_llm(monkeypatch, llm)

        result = _generate(staged_repo, "--dry-run", "--hint", "orders power the new checkout")

        assert result.exit_code == 0
        analyst_prompt, generator_prompt = llm.calls[0][1].content, llm.calls[1][1].content
        assert "orders power the new checkout" in analyst_prompt
        assert "orders power the new checkout" in generator_prompt

    def test_overlong_hint_is_rejected(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = scripted_llm([GOOD])
        use_llm(monkeypatch, llm)

        result = _generate(staged_repo, "--dry-run", "--hint", "x" * 1_001)

        assert result.exit_code == 1
        assert "--hint is 1001 characters" in result.stderr
        assert llm.calls == []


class TestBody:
    LONG_BODY = (
        "This change lets callers bound the size of each response so that slow "
        "connections do not stall while a very large page is transferred."
    )

    def test_body_is_generated_wrapped_and_committed_where_bodies_are_common(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = scoped_history_repo(tmp_path / "repo", with_bodies=True)
        stage_file(repo, "src/api/orders.py", "def orders(): ...\n")
        draft = f"{GOOD}\n\n{self.LONG_BODY}"
        llm = scripted_llm([draft])
        use_llm(monkeypatch, llm)

        result = _generate(repo, "--yes")

        assert result.exit_code == 0
        assert "Add a short body after a blank line" in llm.calls[1][1].content
        lines = head_message(repo).splitlines()
        assert lines[0] == GOOD
        assert lines[1] == ""
        assert len(lines) > 3
        assert all(len(line) <= 72 for line in lines)

    def test_missing_body_is_requested_again(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = scoped_history_repo(tmp_path / "repo", with_bodies=True)
        stage_file(repo, "src/api/orders.py", "def orders(): ...\n")
        use_llm(monkeypatch, scripted_llm([GOOD, f"{GOOD}\n\nBecause orders matter."]))

        result = _generate(repo, "--dry-run", "--verbose")

        assert result.exit_code == 0
        assert "[error] body-missing" in result.stderr
        assert result.stdout.strip().endswith("Because orders matter.")

    def test_large_diff_gets_a_body_even_in_a_subject_only_repo(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for n in range(6):
            stage_file(staged_repo, f"src/api/extra{n}.py", f"x = {n}\n")
        llm = scripted_llm([f"{GOOD}\n\nAdds several modules.", GOOD])
        use_llm(monkeypatch, llm)

        result = _generate(staged_repo, "--dry-run")

        assert result.exit_code == 0
        assert "Add a short body after a blank line" in llm.calls[1][1].content
        assert result.stdout.strip() == f"{GOOD}\n\nAdds several modules."


class TestRepositoryStates:
    @pytest.mark.parametrize("operation", ["merge", "cherry-pick", "revert"])
    def test_refuses_while_git_owns_the_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
    ) -> None:
        repo = diverged_repo(tmp_path / "repo")
        start_operation(repo, operation)
        resolve_conflict(repo)
        llm = scripted_llm([GOOD])
        use_llm(monkeypatch, llm)

        result = _generate(repo, "--dry-run")

        assert result.exit_code == 1
        assert f"A {operation} is in progress" in result.stderr
        assert "git commit" in result.stderr
        assert llm.calls == []

    def test_rebase_in_progress_proceeds_with_a_note(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = diverged_repo(tmp_path / "repo")
        start_operation(repo, "rebase")
        resolve_conflict(repo)
        use_llm(monkeypatch, scripted_llm(["fix: resolve shared file conflict"]))

        result = _generate(repo, "--dry-run")

        assert result.exit_code == 0
        assert "A rebase is in progress" in result.stderr
        assert result.stdout.strip() == "fix: resolve shared file conflict"

    def test_detached_head_warns_before_committing(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_git(staged_repo, "checkout", "-q", "--detach")
        use_llm(monkeypatch, scripted_llm([GOOD]))

        result = _generate(staged_repo, "--yes")

        assert result.exit_code == 0
        assert "HEAD is detached" in result.stderr
        assert head_message(staged_repo) == GOOD

    def test_detached_head_is_not_mentioned_for_a_dry_run(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_git(staged_repo, "checkout", "-q", "--detach")
        use_llm(monkeypatch, scripted_llm([GOOD]))

        result = _generate(staged_repo, "--dry-run")

        assert result.exit_code == 0
        assert "detached" not in result.stderr

    def test_a_normal_branch_has_no_state_noise(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD]))
        result = _generate(staged_repo, "--yes")
        assert "detached" not in result.stderr
        assert "in progress" not in result.stderr


class TestAmend:
    def test_message_covers_head_plus_staged_and_replaces_head(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = scripted_llm(["fix(api): handle empty orders list"])
        use_llm(monkeypatch, llm)
        before = commit_count(staged_repo)

        result = _generate(staged_repo, "--amend", "--yes")

        assert result.exit_code == 0
        assert "Amending the last commit" in result.stderr
        analyst_prompt = llm.calls[0][1].content
        assert "src/api/orders.py" in analyst_prompt  # staged change
        assert "src/api/users.py" in analyst_prompt  # the commit being amended
        assert commit_count(staged_repo) == before
        assert head_message(staged_repo) == "fix(api): handle empty orders list"

    def test_the_commit_being_replaced_is_not_offered_as_an_example(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = scripted_llm(["fix(api): handle empty orders list"])
        use_llm(monkeypatch, llm)

        _generate(staged_repo, "--amend", "--dry-run")

        generator_prompt = llm.calls[1][1].content
        assert "fix(api): handle empty list" not in generator_prompt
        assert "feat(api): add users endpoint" in generator_prompt

    def test_amend_with_nothing_staged_rewords_the_last_commit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = scoped_history_repo(tmp_path / "repo")
        use_llm(monkeypatch, scripted_llm(["fix(api): handle an empty users list"]))

        result = _generate(repo, "--amend", "--yes")

        assert result.exit_code == 0
        assert head_message(repo) == "fix(api): handle an empty users list"

    def test_nothing_to_amend_in_an_empty_repository(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = init_repo(tmp_path / "fresh")
        use_llm(monkeypatch, scripted_llm(["docs: add readme"]))

        result = _generate(repo, "--amend", "--dry-run")

        assert result.exit_code == 1
        assert "no commit to amend" in result.stderr

    def test_head_moving_during_generation_blocks_the_amend(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inner = scripted_llm(["fix(api): handle empty orders list"])

        def move_head(messages: Sequence[LLMMessage]) -> str:
            subprocess.run(
                ["git", "-C", str(staged_repo), "commit", "--allow-empty", "-m", "chore: empty"],
                check=True,
                capture_output=True,
            )
            return inner.complete(messages)

        use_llm(monkeypatch, FakeLLM(move_head))

        result = _generate(staged_repo, "--amend", "--yes")

        assert result.exit_code == 1
        assert "nothing was committed" in result.stderr
        assert head_message(staged_repo) == "chore: empty"


class TestNoVerify:
    def test_failing_pre_commit_hook_blocks_the_commit_by_default(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_hook(staged_repo, "pre-commit", "#!/bin/sh\necho lint failed >&2\nexit 1\n")
        use_llm(monkeypatch, scripted_llm([GOOD]))
        before = commit_count(staged_repo)

        result = _generate(staged_repo, "--yes")

        assert result.exit_code == 1
        assert "lint failed" in result.stderr
        assert commit_count(staged_repo) == before

    def test_no_verify_skips_the_hook(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_hook(staged_repo, "pre-commit", "#!/bin/sh\nexit 1\n")
        use_llm(monkeypatch, scripted_llm([GOOD]))

        result = _generate(staged_repo, "--yes", "--no-verify")

        assert result.exit_code == 0
        assert head_message(staged_repo) == GOOD


class TestCommitting:
    def test_yes_commits_without_prompting(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD]))

        result = _generate(staged_repo, "--yes")

        assert result.exit_code == 0
        assert head_message(staged_repo) == GOOD
        assert "Committed" in result.stderr

    def test_confirming_commits(self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "FAKE-" + "g" * 24)
        use_llm(monkeypatch, scripted_llm([GOOD]))

        result = _generate(staged_repo, answers=ScriptedPrompter([True]))

        assert result.exit_code == 0
        assert head_message(staged_repo) == GOOD

    def test_declining_aborts_without_committing(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "FAKE-" + "g" * 24)
        use_llm(monkeypatch, scripted_llm([GOOD]))
        before = commit_count(staged_repo)

        result = _generate(staged_repo, answers=ScriptedPrompter([False]))

        assert result.exit_code == 1
        assert commit_count(staged_repo) == before

    def test_staging_changed_during_generation_blocks_the_commit(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inner = scripted_llm([GOOD])

        def restage_midway(messages: Sequence[LLMMessage]) -> str:
            stage_file(staged_repo, "src/api/late.py", "x = 1\n")
            return inner.complete(messages)

        use_llm(monkeypatch, FakeLLM(restage_midway))
        before = commit_count(staged_repo)

        result = _generate(staged_repo, "--yes")

        assert result.exit_code == 1
        assert "nothing was committed" in result.stderr
        assert commit_count(staged_repo) == before


class TestEdgeCases:
    def test_nothing_staged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = scoped_history_repo(tmp_path / "repo")
        llm = scripted_llm([GOOD])
        use_llm(monkeypatch, llm)

        result = _generate(repo, "--dry-run")

        assert result.exit_code == 1
        assert "Nothing is staged and there are no changes" in result.stderr
        assert llm.calls == []

    def test_unstaged_changes_get_a_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = scoped_history_repo(tmp_path / "repo")
        (repo / "src/api/users.py").write_text("changed\n", encoding="utf-8")
        use_llm(monkeypatch, scripted_llm([GOOD]))

        result = _generate(repo, "--dry-run")

        assert result.exit_code == 1
        assert "git add" in result.stderr

    def test_empty_repository_with_staged_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = init_repo(tmp_path / "fresh")
        stage_file(repo, "README.md", "# Fresh\n")
        use_llm(monkeypatch, scripted_llm(["docs: add readme"]))

        result = _generate(repo, "--yes")

        assert result.exit_code == 0
        assert head_message(repo) == "docs: add readme"

    def test_binary_only_diff_is_explained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = scoped_history_repo(tmp_path / "binary")
        stage_file(repo, "assets/logo.png", bytes(range(256)) * 4)
        llm = scripted_llm(["feat(api): add logo asset"])
        use_llm(monkeypatch, llm)

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
        use_llm(monkeypatch, llm)

        result = _generate(repo, "--dry-run")

        assert result.exit_code == 0
        assert "too large to send in full" in result.stderr
        assert len(llm.calls[0][1].content) < 30_000

    def test_not_a_repository(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD]))
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
        seen = use_llm(monkeypatch, scripted_llm([GOOD])).configs

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
        seen = use_llm(monkeypatch, scripted_llm([GOOD])).configs

        _generate(staged_repo, "--dry-run")

        assert seen[0].llm_provider.value == "anthropic"
        assert seen[0].max_iterations == 4

    def test_missing_api_key_names_the_variable(
        self, staged_repo: Path, isolated_user_config: Path
    ) -> None:
        save_user_config(isolated_user_config, UserConfig())
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
        assert "--dry-run" in plain(capsys.readouterr().out)

    def test_runs_generate_directly_without_a_subcommand_name(self, staged_repo: Path) -> None:
        launcher = "from git_commit_ai.cli.git_subcommand import git_ai_commit; git_ai_commit()"
        completed = subprocess.run(
            [sys.executable, "-c", launcher, "--path", str(staged_repo), "--provider", "openai"],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": "/usr/bin:/bin"},
        )
        assert completed.returncode == 1
        assert "OPENAI_API_KEY" in completed.stderr
