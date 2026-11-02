"""Integration tests for the hook commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from git_commit_ai.cli.git_subcommand import git_app
from git_commit_ai.cli.interaction import Interaction
from git_commit_ai.cli.main import app
from git_commit_ai.core.config import ProviderName
from git_commit_ai.core.credentials import FileCredentialStore
from git_commit_ai.core.exceptions import RateLimitError
from git_commit_ai.core.settings import ENV_FILE_VARIABLE
from git_commit_ai.core.user_config import UserConfig, save_user_config
from git_commit_ai.git.hooks import HOOK_NAME
from tests.fixtures.cli import use_llm
from tests.fixtures.git_repo import (
    diverged_repo,
    install_hook,
    resolve_conflict,
    scoped_history_repo,
    stage_file,
    start_operation,
)
from tests.fixtures.prompter import ScriptedPrompter
from tests.fixtures.scripted_llm import scripted_llm

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import Result

runner = CliRunner()
GOOD = "feat(api): add orders endpoint"
GIT_TEMPLATE = (
    "\n# Please enter the commit message for your changes.\n"
    "# Lines starting with '#' are ignored.\n"
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FILE_VARIABLE, "")
    monkeypatch.delenv("GITCOMMIT_AI_HOOK", raising=False)


@pytest.fixture
def staged_repo(tmp_path: Path) -> Path:
    repo = scoped_history_repo(tmp_path / "repo")
    stage_file(repo, "src/api/orders.py", "def orders(): ...\n")
    return repo


class TestInstallCommands:
    def test_install_and_uninstall_round_trip(self, staged_repo: Path) -> None:
        installed = runner.invoke(app, ["hook", "install", "--path", str(staged_repo)])
        hook = staged_repo / ".git" / "hooks" / HOOK_NAME
        assert installed.exit_code == 0
        assert "Installed" in installed.stdout
        assert hook.exists()

        removed = runner.invoke(app, ["hook", "uninstall", "--path", str(staged_repo)])
        assert removed.exit_code == 0
        assert "Removed" in removed.stdout
        assert not hook.exists()

    def test_install_reports_a_backed_up_hook(self, staged_repo: Path) -> None:
        install_hook(staged_repo, HOOK_NAME, "#!/bin/sh\nexit 0\n")
        result = runner.invoke(app, ["hook", "install", "--path", str(staged_repo)])
        assert result.exit_code == 0
        assert "moved to prepare-commit-msg.pre-ai-commit" in result.stdout

        restored = runner.invoke(app, ["hook", "uninstall", "--path", str(staged_repo)])
        assert "Restored" in restored.stdout

    def test_uninstall_refuses_a_foreign_hook(self, staged_repo: Path) -> None:
        install_hook(staged_repo, HOOK_NAME, "#!/bin/sh\nexit 0\n")
        result = runner.invoke(app, ["hook", "uninstall", "--path", str(staged_repo)])
        assert result.exit_code == 1
        assert "not installed by git-ai-commit" in result.stderr

    def test_uninstall_when_nothing_is_installed(self, staged_repo: Path) -> None:
        result = runner.invoke(app, ["hook", "uninstall", "--path", str(staged_repo)])
        assert result.exit_code == 0
        assert "No prepare-commit-msg hook is installed" in result.stdout

    def test_not_a_repository(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["hook", "install", "--path", str(tmp_path)])
        assert result.exit_code == 1
        assert "Not a Git repository" in result.stderr

    def test_commands_exist_on_the_git_entry_point_too(self, staged_repo: Path) -> None:
        result = runner.invoke(git_app, ["hook", "install", "--path", str(staged_repo)])
        assert result.exit_code == 0

    def test_run_is_hidden_from_help(self) -> None:
        result = runner.invoke(app, ["hook", "--help"])
        assert "install" in result.stdout
        assert "uninstall" in result.stdout
        assert "run" not in result.stdout.split("Commands")[1]


class TestRun:
    def _run(self, repo: Path, message_file: Path, source: str = "") -> Result:
        args = ["hook", "run", str(message_file), "--path", str(repo)]
        if source:
            args.insert(3, source)
        return runner.invoke(app, args)

    @pytest.fixture
    def message_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "COMMIT_EDITMSG"
        path.write_text(GIT_TEMPLATE)
        return path

    def test_proposal_is_written_above_git_comments(
        self, staged_repo: Path, message_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD]))

        result = self._run(staged_repo, message_file)

        assert result.exit_code == 0
        assert message_file.read_text() == f"{GOOD}\n{GIT_TEMPLATE}"

    def test_proposal_is_written_into_an_empty_file(
        self, staged_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty = tmp_path / "EMPTY"
        empty.write_text("")
        use_llm(monkeypatch, scripted_llm([GOOD]))

        self._run(staged_repo, empty)

        assert empty.read_text() == f"{GOOD}\n"

    @pytest.mark.parametrize("source", ["message", "merge", "squash", "commit"])
    def test_sources_that_already_have_a_message_are_skipped(
        self, staged_repo: Path, message_file: Path, monkeypatch: pytest.MonkeyPatch, source: str
    ) -> None:
        llm = scripted_llm([GOOD])
        use_llm(monkeypatch, llm)

        result = self._run(staged_repo, message_file, source)

        assert result.exit_code == 0
        assert message_file.read_text() == GIT_TEMPLATE
        assert llm.calls == []

    def test_template_source_is_still_handled(
        self, staged_repo: Path, message_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD]))
        self._run(staged_repo, message_file, "template")
        assert message_file.read_text().startswith(GOOD)

    def test_environment_switch_skips_it(
        self, staged_repo: Path, message_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITCOMMIT_AI_HOOK", "0")
        llm = scripted_llm([GOOD])
        use_llm(monkeypatch, llm)
        self._run(staged_repo, message_file)
        assert message_file.read_text() == GIT_TEMPLATE
        assert llm.calls == []

    def test_a_message_the_user_template_already_provides_is_kept(
        self, staged_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        template = tmp_path / "TEMPLATE"
        template.write_text("TICKET-1: \n" + GIT_TEMPLATE)
        llm = scripted_llm([GOOD])
        use_llm(monkeypatch, llm)
        self._run(staged_repo, template, "template")
        assert template.read_text().startswith("TICKET-1: ")
        assert llm.calls == []

    def test_nothing_staged_is_skipped_quietly(
        self, tmp_path: Path, message_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = scoped_history_repo(tmp_path / "clean")
        llm = scripted_llm([GOOD])
        use_llm(monkeypatch, llm)
        result = self._run(repo, message_file)
        assert result.exit_code == 0
        assert message_file.read_text() == GIT_TEMPLATE
        assert llm.calls == []

    def test_an_operation_in_progress_is_skipped(
        self, tmp_path: Path, message_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = diverged_repo(tmp_path / "repo")
        start_operation(repo, "merge")
        resolve_conflict(repo)
        llm = scripted_llm([GOOD])
        use_llm(monkeypatch, llm)
        self._run(repo, message_file)
        assert message_file.read_text() == GIT_TEMPLATE
        assert llm.calls == []

    def test_provider_failure_never_fails_the_hook(
        self, staged_repo: Path, message_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        llm = scripted_llm([], analysis_error=RateLimitError("openai"))
        use_llm(monkeypatch, llm)

        result = self._run(staged_repo, message_file)

        assert result.exit_code == 0
        assert "no proposal" in result.stderr
        assert message_file.read_text() == GIT_TEMPLATE

    def test_missing_api_key_never_fails_the_hook(
        self,
        staged_repo: Path,
        message_file: Path,
        isolated_user_config: Path,
    ) -> None:
        save_user_config(isolated_user_config, UserConfig())
        result = self._run(staged_repo, message_file)
        assert result.exit_code == 0
        assert "OPENAI_API_KEY" in result.stderr
        assert message_file.read_text() == GIT_TEMPLATE

    def test_even_an_unexpected_error_never_fails_the_hook(
        self, staged_repo: Path, message_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*_: object, **__: object) -> None:
            raise RuntimeError("bug")

        monkeypatch.setattr("git_commit_ai.cli.hook.CommitMessageGraph.run", explode)
        use_llm(monkeypatch, scripted_llm([GOOD]))

        result = self._run(staged_repo, message_file)

        assert result.exit_code == 0
        assert "bug" in result.stderr
        assert message_file.read_text() == GIT_TEMPLATE


class TestRunWithoutSetup:
    """The hook never asks anything, even when a terminal is attached."""

    KEY = "FAKE-" + "h" * 24 + "0b0b"

    @pytest.fixture
    def message_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "COMMIT_EDITMSG"
        path.write_text(GIT_TEMPLATE)
        return path

    def _interactive(self) -> Interaction:
        def no_prompts() -> ScriptedPrompter:
            raise AssertionError("the hook must never prompt")

        return Interaction(interactive=True, prompter_factory=no_prompts)

    def test_no_setup_leaves_a_note_and_never_prompts(
        self,
        staged_repo: Path,
        message_file: Path,
        isolated_user_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created = use_llm(monkeypatch, scripted_llm([GOOD]), configured=False)
        args = ["hook", "run", str(message_file), "--path", str(staged_repo)]
        result = runner.invoke(git_app, args, obj=self._interactive())
        assert result.exit_code == 0
        assert (
            "git-ai-commit: no proposal (no provider is set up; run git ai-commit setup)"
            in result.stderr
        )
        assert message_file.read_text() == GIT_TEMPLATE
        assert created.configs == []
        assert not isolated_user_config.exists()

    def test_uses_the_saved_provider_and_key(
        self,
        staged_repo: Path,
        message_file: Path,
        isolated_user_config: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        save_user_config(isolated_user_config, UserConfig(provider=ProviderName.ANTHROPIC))
        FileCredentialStore(isolated_user_config).set(ProviderName.ANTHROPIC, self.KEY)
        created = use_llm(monkeypatch, scripted_llm([GOOD]))
        args = ["hook", "run", str(message_file), "--path", str(staged_repo)]
        result = runner.invoke(git_app, args, obj=self._interactive())
        assert result.exit_code == 0, result.stderr
        assert created.configs[0].llm_provider is ProviderName.ANTHROPIC
        assert created.api_keys == [self.KEY]
        assert message_file.read_text().startswith(GOOD)

    def test_a_missing_key_with_a_saved_setup_is_a_note_not_a_prompt(
        self,
        staged_repo: Path,
        message_file: Path,
        isolated_user_config: Path,
    ) -> None:
        save_user_config(isolated_user_config, UserConfig(provider=ProviderName.ANTHROPIC))
        args = ["hook", "run", str(message_file), "--path", str(staged_repo)]
        result = runner.invoke(git_app, args, obj=self._interactive())
        assert result.exit_code == 0
        assert "git-ai-commit: no proposal (No API key for anthropic." in result.stderr
