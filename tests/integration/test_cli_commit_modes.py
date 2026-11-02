"""Commit modes: preview, confirm and auto, the flags that override them, and ``mode``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from git_commit_ai.cli.git_subcommand import git_app
from git_commit_ai.cli.interaction import Interaction
from git_commit_ai.cli.main import app
from git_commit_ai.core.config import CommitMode
from git_commit_ai.core.exceptions import RateLimitError
from git_commit_ai.core.settings import ENV_FILE_VARIABLE
from git_commit_ai.core.user_config import (
    CONFIG_FILE,
    SCHEMA_VERSION,
    UserConfig,
    load_user_config,
    save_user_config,
)
from tests.fixtures.cli import use_llm
from tests.fixtures.git_repo import commit_count, head_message, scoped_history_repo, stage_file
from tests.fixtures.prompter import CTRL_C, ScriptedPrompter
from tests.fixtures.scripted_llm import scripted_llm

if TYPE_CHECKING:
    from pathlib import Path

    import typer
    from typer.testing import Result

runner = CliRunner()
GOOD = "feat(api): add orders endpoint"
APPS = pytest.mark.parametrize("entry", [app, git_app], ids=["gitcommit-ai", "git-ai-commit"])


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FILE_VARIABLE, "")
    monkeypatch.setenv("OPENAI_API_KEY", "FAKE-" + "m" * 24)


@pytest.fixture
def staged_repo(tmp_path: Path) -> Path:
    repo = scoped_history_repo(tmp_path / "repo")
    stage_file(repo, "src/api/orders.py", "def orders(): ...\n")
    return repo


def _saved_mode(directory: Path, mode: CommitMode | None) -> None:
    save_user_config(directory, UserConfig(commit_mode=mode))


def _run(
    repo: Path, *flags: str, prompter: ScriptedPrompter | None = None, interactive: bool = True
) -> Result:
    def factory() -> ScriptedPrompter:
        assert prompter is not None, "this run must not prompt"
        return prompter

    obj = Interaction(interactive=interactive, prompter_factory=factory)
    return runner.invoke(git_app, ["--path", str(repo), *flags], obj=obj)


# (saved mode, flags) -> (asks to confirm, commits); confirmations are answered "yes".
MATRIX = [
    (CommitMode.PREVIEW, (), False, False),
    (CommitMode.CONFIRM, (), True, True),
    (CommitMode.AUTO, (), False, True),
    (None, (), True, True),
    (CommitMode.PREVIEW, ("--dry-run",), False, False),
    (CommitMode.CONFIRM, ("--dry-run",), False, False),
    (CommitMode.AUTO, ("--dry-run",), False, False),
    (CommitMode.PREVIEW, ("--yes",), False, True),
    (CommitMode.CONFIRM, ("--yes",), False, True),
    (CommitMode.AUTO, ("--yes",), False, True),
    (CommitMode.PREVIEW, ("--confirm",), True, True),
    (CommitMode.CONFIRM, ("--confirm",), True, True),
    (CommitMode.AUTO, ("--confirm",), True, True),
]


@pytest.mark.parametrize(("mode", "flags", "asks", "commits"), MATRIX)
def test_mode_and_flag_matrix(
    staged_repo: Path,
    isolated_user_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: CommitMode | None,
    flags: tuple[str, ...],
    asks: bool,
    commits: bool,
) -> None:
    _saved_mode(isolated_user_config, mode)
    use_llm(monkeypatch, scripted_llm([GOOD]))
    before = commit_count(staged_repo)
    prompter = ScriptedPrompter([True] if asks else [])
    result = _run(staged_repo, *flags, prompter=prompter)
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == GOOD
    assert prompter.messages() == (["Commit with this message?"] if asks else [])
    assert commit_count(staged_repo) == before + (1 if commits else 0)


def test_yes_and_confirm_together_is_a_usage_error(
    staged_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_llm(monkeypatch, scripted_llm([GOOD]))
    result = _run(staged_repo, "--yes", "--confirm")
    assert result.exit_code == 2
    assert "--yes and --confirm can't be used together" in result.stderr


def test_the_variable_beats_the_saved_mode(
    staged_repo: Path, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _saved_mode(isolated_user_config, CommitMode.AUTO)
    monkeypatch.setenv("GITCOMMIT_AI_COMMIT_MODE", "preview")
    use_llm(monkeypatch, scripted_llm([GOOD]))
    before = commit_count(staged_repo)
    result = _run(staged_repo)
    assert result.exit_code == 0, result.stderr
    assert commit_count(staged_repo) == before


def test_an_invalid_mode_variable_is_reported(
    staged_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITCOMMIT_AI_COMMIT_MODE", "sometimes")
    use_llm(monkeypatch, scripted_llm([GOOD]))
    result = _run(staged_repo)
    assert result.exit_code == 1
    assert "GITCOMMIT_AI_COMMIT_MODE" in result.stderr


class TestAuto:
    def test_an_interrupted_run_is_not_committed(
        self, staged_repo: Path, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _saved_mode(isolated_user_config, CommitMode.AUTO)
        use_llm(monkeypatch, scripted_llm([GOOD], verdict_errors={1: RateLimitError("openai")}))
        before = commit_count(staged_repo)
        result = _run(staged_repo)
        assert result.exit_code == 1
        assert commit_count(staged_repo) == before
        assert "Not committing: the run stopped early" in result.stderr

    def test_accept_partial_lets_auto_commit_it(
        self, staged_repo: Path, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _saved_mode(isolated_user_config, CommitMode.AUTO)
        use_llm(monkeypatch, scripted_llm([GOOD], verdict_errors={1: RateLimitError("openai")}))
        result = _run(staged_repo, "--accept-partial")
        assert result.exit_code == 0, result.stderr
        assert head_message(staged_repo) == GOOD

    def test_auto_never_prompts_without_a_terminal(
        self, staged_repo: Path, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _saved_mode(isolated_user_config, CommitMode.AUTO)
        use_llm(monkeypatch, scripted_llm([GOOD]))
        result = _run(staged_repo, interactive=False)
        assert result.exit_code == 0, result.stderr
        assert head_message(staged_repo) == GOOD


class TestNoTerminal:
    def test_confirm_mode_refuses_instead_of_prompting(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD]))
        before = commit_count(staged_repo)
        result = _run(staged_repo, interactive=False)
        assert result.exit_code == 1
        assert result.stdout.strip() == GOOD
        assert (
            "Not committing: there is no terminal to confirm in. "
            "Pass --yes, or --dry-run to print only." in result.stderr
        )
        assert commit_count(staged_repo) == before

    def test_piped_yes_no_longer_confirms(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD]))
        before = commit_count(staged_repo)
        result = runner.invoke(git_app, ["--path", str(staged_repo)], input="y\n")
        assert result.exit_code == 1
        assert commit_count(staged_repo) == before

    def test_preview_mode_is_fine_without_a_terminal(
        self, staged_repo: Path, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _saved_mode(isolated_user_config, CommitMode.PREVIEW)
        use_llm(monkeypatch, scripted_llm([GOOD]))
        result = _run(staged_repo, interactive=False)
        assert result.exit_code == 0, result.stderr


def test_ctrl_c_at_the_confirmation_commits_nothing(
    staged_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_llm(monkeypatch, scripted_llm([GOOD]))
    before = commit_count(staged_repo)
    result = _run(staged_repo, prompter=ScriptedPrompter([CTRL_C]))
    assert result.exit_code == 1
    assert "Aborted." in result.stderr
    assert "Setup" not in result.stderr
    assert commit_count(staged_repo) == before


class TestModeCommand:
    @APPS
    def test_interactive_choice_is_saved(
        self, entry: typer.Typer, isolated_user_config: Path
    ) -> None:
        prompter = ScriptedPrompter([CommitMode.AUTO, True])
        obj = Interaction(interactive=True, prompter_factory=lambda: prompter)
        result = runner.invoke(entry, ["mode"], obj=obj)
        assert result.exit_code == 0, result.stderr
        prompter.assert_done()
        saved = load_user_config(isolated_user_config)
        assert saved is not None
        assert saved.commit_mode is CommitMode.AUTO
        assert prompter.output.startswith("Current: confirm")
        assert "Saved. Commit mode: auto (override once with --confirm or --dry-run)" in (
            result.stderr
        )

    @APPS
    @pytest.mark.parametrize("mode", list(CommitMode))
    def test_a_named_mode_is_saved_without_asking(
        self, entry: typer.Typer, isolated_user_config: Path, mode: CommitMode
    ) -> None:
        obj = Interaction(interactive=False, prompter_factory=lambda: ScriptedPrompter([]))
        result = runner.invoke(entry, ["mode", mode.value], obj=obj)
        assert result.exit_code == 0, result.stderr
        saved = load_user_config(isolated_user_config)
        assert saved is not None
        assert saved.commit_mode is mode

    def test_an_unknown_mode_is_refused(self, isolated_user_config: Path) -> None:
        result = runner.invoke(app, ["mode", "sometimes"])
        assert result.exit_code == 2
        assert not isolated_user_config.exists()

    def test_without_a_name_or_a_terminal_it_explains(self) -> None:
        obj = Interaction(interactive=False, prompter_factory=lambda: ScriptedPrompter([]))
        result = runner.invoke(app, ["mode"], obj=obj)
        assert result.exit_code == 1
        assert "mode needs a terminal, or pass a mode" in result.stderr

    def test_an_overriding_variable_is_pointed_out(
        self, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITCOMMIT_AI_COMMIT_MODE", "auto")
        result = runner.invoke(app, ["mode", "preview"])
        assert "GITCOMMIT_AI_COMMIT_MODE=auto in your environment overrides this setting." in (
            result.stderr
        )

    def test_a_newer_config_is_left_alone(self, isolated_user_config: Path) -> None:
        isolated_user_config.mkdir(parents=True)
        newer = f"schema_version = {SCHEMA_VERSION + 1}\n"
        (isolated_user_config / CONFIG_FILE).write_text(newer, encoding="utf-8")
        result = runner.invoke(app, ["mode", "auto"])
        assert result.exit_code == 1
        assert (isolated_user_config / CONFIG_FILE).read_text(encoding="utf-8") == newer
