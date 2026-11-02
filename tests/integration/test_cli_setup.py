"""First-run setup and the setup, provider, model and key commands, on both entry points."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from git_commit_ai.cli.git_subcommand import git_app
from git_commit_ai.cli.interaction import Interaction
from git_commit_ai.cli.main import app
from git_commit_ai.core.config import CommitMode, ProviderName
from git_commit_ai.core.credentials import FileCredentialStore
from git_commit_ai.core.exceptions import AuthenticationError, ModelNotAvailableError
from git_commit_ai.core.settings import ENV_FILE_VARIABLE
from git_commit_ai.core.user_config import (
    CONFIG_FILE,
    SCHEMA_VERSION,
    ProviderPreferences,
    UserConfig,
    load_user_config,
    save_user_config,
)
from git_commit_ai.llm import FakeLLM
from git_commit_ai.onboarding.wizard import KeyAction
from tests.fixtures.cli import use_llm
from tests.fixtures.git_repo import scoped_history_repo, stage_file
from tests.fixtures.http import FakeHttp, json_response
from tests.fixtures.prompter import CTRL_C, ScriptedPrompter
from tests.fixtures.provider_payloads import ANTHROPIC_PAGES
from tests.fixtures.scripted_llm import scripted_llm

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import typer
    from typer.testing import Result

runner = CliRunner()
GOOD = "feat(api): add orders endpoint"
KEY = "FAKE-" + "s" * 24 + "3f2a"
OTHER_KEY = "FAKE-" + "t" * 24 + "a91c"
ANTHROPIC = "https://api.anthropic.com/v1"
APPS = pytest.mark.parametrize("entry", [app, git_app], ids=["gitcommit-ai", "git-ai-commit"])


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FILE_VARIABLE, "")


@pytest.fixture
def staged_repo(tmp_path: Path) -> Path:
    repo = scoped_history_repo(tmp_path / "repo")
    stage_file(repo, "src/api/orders.py", "def orders(): ...\n")
    return repo


def _http() -> FakeHttp:
    models = {"data": ANTHROPIC_PAGES[0]["data"], "has_more": False}
    return (
        FakeHttp()
        .on("GET", f"{ANTHROPIC}/models", json_response(200, models))
        .on("POST", f"{ANTHROPIC}/messages", json_response(200, {}))
    )


class Session:
    """An interactive session: scripted answers and a fake HTTP layer."""

    def __init__(self, answers: Sequence[object], http: FakeHttp | None = None) -> None:
        self.prompter = ScriptedPrompter(answers)
        self.http = http or _http()
        self.obj = Interaction(
            interactive=True,
            prompter_factory=lambda: self.prompter,
            transport=self.http.transport,
            keyring_ok=lambda: False,
        )

    def invoke(self, entry: typer.Typer, args: list[str], input: str | None = None) -> Result:
        return runner.invoke(entry, args, obj=self.obj, input=input)


def _non_interactive() -> Interaction:
    def no_prompts() -> ScriptedPrompter:
        raise AssertionError("a non-interactive run must not prompt")

    return Interaction(interactive=False, prompter_factory=no_prompts, transport=_http().transport)


FIRST_RUN = [ProviderName.ANTHROPIC, KEY, "claude-sonnet-5-5", CommitMode.CONFIRM]


class TestFirstRun:
    def test_the_wizard_runs_then_generation_continues(
        self, staged_repo: Path, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = use_llm(monkeypatch, scripted_llm([GOOD]), configured=False)
        session = Session(FIRST_RUN)
        result = session.invoke(git_app, ["--path", str(staged_repo), "--dry-run"])
        assert result.exit_code == 0, result.stderr
        session.prompter.assert_done()
        assert result.stdout.strip() == GOOD
        assert created.configs[0].llm_provider is ProviderName.ANTHROPIC
        assert created.configs[0].llm_model == "claude-sonnet-5-5"
        assert created.api_keys == [KEY]
        saved = load_user_config(isolated_user_config)
        assert saved is not None
        assert saved.provider is ProviderName.ANTHROPIC
        assert FileCredentialStore(isolated_user_config).get(ProviderName.ANTHROPIC) == KEY
        assert "Saved. Provider: anthropic · model: claude-sonnet-5-5 · commits: ask first" in (
            result.stderr
        )
        assert KEY not in result.stdout + result.stderr

    def test_a_cancelled_wizard_saves_nothing(
        self, staged_repo: Path, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = use_llm(monkeypatch, scripted_llm([GOOD]), configured=False)
        session = Session([ProviderName.ANTHROPIC, KEY, CTRL_C])
        result = session.invoke(git_app, ["--path", str(staged_repo), "--dry-run"])
        assert result.exit_code == 1
        assert "Setup cancelled; nothing was saved." in result.stderr
        assert not isolated_user_config.exists()
        assert created.configs == []

    def test_without_a_terminal_it_explains_instead(
        self, staged_repo: Path, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_llm(monkeypatch, scripted_llm([GOOD]), configured=False)
        result = runner.invoke(
            git_app, ["--path", str(staged_repo), "--dry-run"], obj=_non_interactive()
        )
        assert result.exit_code == 1
        assert "No provider is set up. Run `git ai-commit setup` in a terminal, or set" in (
            result.stderr
        )
        assert "GITCOMMIT_AI_PROVIDER and that provider's key variable" in result.stderr
        assert not isolated_user_config.exists()

    @pytest.mark.parametrize(
        "variables",
        [
            {"OPENAI_API_KEY": KEY},
            {"GITCOMMIT_AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": KEY},
        ],
    )
    def test_environment_configured_users_never_see_the_wizard(
        self,
        staged_repo: Path,
        isolated_user_config: Path,
        monkeypatch: pytest.MonkeyPatch,
        variables: dict[str, str],
    ) -> None:
        for name, value in variables.items():
            monkeypatch.setenv(name, value)
        created = use_llm(monkeypatch, scripted_llm([GOOD]), configured=False)
        session = Session([])
        result = session.invoke(git_app, ["--path", str(staged_repo), "--dry-run"])
        assert result.exit_code == 0, result.stderr
        assert created.api_keys == [KEY]
        assert session.prompter.asked == []
        assert not isolated_user_config.exists()

    def test_a_provider_without_a_key_does_not_start_setup(
        self, staged_repo: Path, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = use_llm(monkeypatch, scripted_llm([GOOD]), configured=False)
        result = Session([]).invoke(
            git_app, ["--path", str(staged_repo), "--dry-run", "--provider", "ollama"]
        )
        assert result.exit_code == 0, result.stderr
        assert created.configs[0].llm_provider is ProviderName.OLLAMA

    def test_nothing_staged_fails_before_any_setup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = scoped_history_repo(tmp_path / "clean")
        use_llm(monkeypatch, scripted_llm([GOOD]), configured=False)
        session = Session([])
        result = session.invoke(git_app, ["--path", str(repo), "--dry-run"])
        assert result.exit_code == 1
        assert "Nothing is staged" in result.stderr
        assert session.prompter.asked == []


class TestMissingKey:
    @pytest.fixture
    def saved_without_key(self, isolated_user_config: Path) -> Path:
        save_user_config(isolated_user_config, UserConfig(provider=ProviderName.ANTHROPIC))
        return isolated_user_config

    def test_interactive_runs_ask_for_the_key_only(
        self, staged_repo: Path, saved_without_key: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created = use_llm(monkeypatch, scripted_llm([GOOD]))
        session = Session([KEY])
        result = session.invoke(git_app, ["--path", str(staged_repo), "--dry-run"])
        assert result.exit_code == 0, result.stderr
        assert session.prompter.messages() == ["Anthropic API key:"]
        assert created.api_keys == [KEY]
        assert FileCredentialStore(saved_without_key).get(ProviderName.ANTHROPIC) == KEY

    def test_non_interactive_runs_name_both_fixes(
        self, staged_repo: Path, saved_without_key: Path
    ) -> None:
        result = runner.invoke(
            git_app, ["--path", str(staged_repo), "--dry-run"], obj=_non_interactive()
        )
        assert result.exit_code == 1
        assert "No API key for anthropic." in result.stderr
        assert "git ai-commit key" in result.stderr
        assert "ANTHROPIC_API_KEY" in result.stderr


class TestProviderErrors:
    def _failing(self, monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
        def fail(_messages: object) -> str:
            raise error

        use_llm(monkeypatch, FakeLLM(fail))

    def test_a_rejected_saved_key_points_to_the_key_command(
        self, staged_repo: Path, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save_user_config(isolated_user_config, UserConfig(provider=ProviderName.ANTHROPIC))
        FileCredentialStore(isolated_user_config).set(ProviderName.ANTHROPIC, KEY)
        self._failing(monkeypatch, AuthenticationError("anthropic", 401))
        result = runner.invoke(git_app, ["--path", str(staged_repo), "--dry-run"])
        assert result.exit_code == 1
        assert (
            "Anthropic rejected the API key from the credentials file. "
            "Run `git ai-commit key` to replace it." in result.stderr
        )

    def test_a_rejected_environment_key_points_to_the_variable(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", KEY)
        self._failing(monkeypatch, AuthenticationError("openai", 401))
        result = runner.invoke(git_app, ["--path", str(staged_repo), "--dry-run"])
        assert "OpenAI rejected the API key from the environment. Update OPENAI_API_KEY." in (
            result.stderr
        )

    def test_a_retired_model_points_to_the_model_command(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", KEY)
        self._failing(monkeypatch, ModelNotAvailableError("openai", "gpt-old"))
        result = runner.invoke(git_app, ["--path", str(staged_repo), "--dry-run"])
        assert "gpt-old isn't available from openai" in result.stderr
        assert "Run `git ai-commit model` to choose another." in result.stderr


class TestSetupCommand:
    @APPS
    def test_runs_the_full_wizard_and_saves(
        self, entry: typer.Typer, isolated_user_config: Path
    ) -> None:
        session = Session(FIRST_RUN)
        result = session.invoke(entry, ["setup"])
        assert result.exit_code == 0, result.stderr
        session.prompter.assert_done()
        assert (isolated_user_config / CONFIG_FILE).exists()
        assert "Key stored in" in result.stderr
        assert "readable only by you" in result.stderr

    @APPS
    def test_needs_a_terminal(self, entry: typer.Typer, isolated_user_config: Path) -> None:
        result = runner.invoke(entry, ["setup"], obj=_non_interactive())
        assert result.exit_code == 1
        assert "setup needs a terminal" in result.stderr
        assert not isolated_user_config.exists()

    def test_refuses_to_overwrite_a_newer_config(self, isolated_user_config: Path) -> None:
        isolated_user_config.mkdir(parents=True)
        newer = f"schema_version = {SCHEMA_VERSION + 1}\n"
        (isolated_user_config / CONFIG_FILE).write_text(newer, encoding="utf-8")
        result = Session(FIRST_RUN).invoke(app, ["setup"])
        assert result.exit_code == 1
        assert "Upgrade gitcommit-ai, or run git ai-commit reset." in result.stderr
        assert (isolated_user_config / CONFIG_FILE).read_text(encoding="utf-8") == newer
        assert not (isolated_user_config / "credentials.toml").exists()


class TestProviderCommand:
    @APPS
    def test_interactive_switch_keeps_the_other_key(
        self, entry: typer.Typer, isolated_user_config: Path
    ) -> None:
        save_user_config(
            isolated_user_config,
            UserConfig(
                provider=ProviderName.OPENAI,
                commit_mode=CommitMode.AUTO,
                providers={ProviderName.OPENAI: ProviderPreferences(model="gpt-5")},
            ),
        )
        FileCredentialStore(isolated_user_config).set(ProviderName.OPENAI, OTHER_KEY)
        session = Session([ProviderName.ANTHROPIC, KEY, "claude-sonnet-5-5"])
        result = session.invoke(entry, ["provider"])
        assert result.exit_code == 0, result.stderr
        saved = load_user_config(isolated_user_config)
        assert saved is not None
        assert saved.provider is ProviderName.ANTHROPIC
        assert saved.commit_mode is CommitMode.AUTO
        assert saved.preferences(ProviderName.OPENAI).model == "gpt-5"
        assert "Your OpenAI key is still saved" in result.stderr

    @APPS
    def test_a_name_saves_without_asking(
        self, entry: typer.Typer, isolated_user_config: Path
    ) -> None:
        result = runner.invoke(entry, ["provider", "gemini"], obj=_non_interactive())
        assert result.exit_code == 0, result.stderr
        saved = load_user_config(isolated_user_config)
        assert saved is not None
        assert saved.provider is ProviderName.GEMINI
        assert "Saved. Provider: gemini" in result.stderr

    def test_an_unknown_name_is_refused(self, isolated_user_config: Path) -> None:
        result = runner.invoke(app, ["provider", "skynet"], obj=_non_interactive())
        assert result.exit_code != 0
        assert not isolated_user_config.exists()

    def test_without_a_name_or_a_terminal_it_explains(self) -> None:
        result = runner.invoke(app, ["provider"], obj=_non_interactive())
        assert result.exit_code == 1
        assert "provider needs a terminal" in result.stderr


class TestModelCommand:
    @APPS
    def test_interactive_lists_probes_and_saves(
        self, entry: typer.Typer, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
        save_user_config(isolated_user_config, UserConfig(provider=ProviderName.ANTHROPIC))
        session = Session(["claude-opus-5-5"])
        result = session.invoke(entry, ["model"])
        assert result.exit_code == 0, result.stderr
        saved = load_user_config(isolated_user_config)
        assert saved is not None
        assert saved.preferences(ProviderName.ANTHROPIC).model == "claude-opus-5-5"
        assert "Saved. Model for anthropic: claude-opus-5-5" in result.stderr

    @APPS
    def test_a_name_saves_without_listing(
        self, entry: typer.Typer, isolated_user_config: Path
    ) -> None:
        save_user_config(isolated_user_config, UserConfig(provider=ProviderName.ANTHROPIC))
        result = runner.invoke(entry, ["model", "claude-opus-5-5"], obj=_non_interactive())
        assert result.exit_code == 0, result.stderr
        saved = load_user_config(isolated_user_config)
        assert saved is not None
        assert saved.provider is ProviderName.ANTHROPIC
        assert saved.preferences(ProviderName.ANTHROPIC).model == "claude-opus-5-5"
        assert "Saved. Model for anthropic: claude-opus-5-5 (not tested" in result.stderr

    def test_another_provider_can_be_named(self, isolated_user_config: Path) -> None:
        result = runner.invoke(
            app, ["model", "gpt-5", "--provider", "openai"], obj=_non_interactive()
        )
        assert result.exit_code == 0, result.stderr
        saved = load_user_config(isolated_user_config)
        assert saved is not None
        assert saved.provider is None
        assert saved.preferences(ProviderName.OPENAI).model == "gpt-5"

    def test_an_overriding_variable_is_pointed_out(
        self, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITCOMMIT_AI_MODEL", "pinned")
        result = runner.invoke(app, ["model", "gpt-5"], obj=_non_interactive())
        assert result.exit_code == 0
        assert "GITCOMMIT_AI_MODEL=pinned in your environment overrides this setting." in (
            result.stderr
        )


class TestKeyCommand:
    @APPS
    def test_interactive_replaces_the_key(
        self, entry: typer.Typer, isolated_user_config: Path
    ) -> None:
        save_user_config(isolated_user_config, UserConfig(provider=ProviderName.ANTHROPIC))
        session = Session([KEY])
        result = session.invoke(entry, ["key"])
        assert result.exit_code == 0, result.stderr
        assert session.prompter.messages() == ["Anthropic API key:"]
        assert FileCredentialStore(isolated_user_config).get(ProviderName.ANTHROPIC) == KEY
        assert result.stderr.rstrip().endswith("Saved.")

    @APPS
    def test_stdin_checks_then_saves(self, entry: typer.Typer, isolated_user_config: Path) -> None:
        session = Session([])
        result = session.invoke(entry, ["key", "--provider", "anthropic", "--stdin"], input=KEY)
        assert result.exit_code == 0, result.stderr
        assert "Checking the key... ok" in result.stderr
        assert FileCredentialStore(isolated_user_config).get(ProviderName.ANTHROPIC) == KEY
        assert session.prompter.asked == []
        assert KEY not in result.stdout + result.stderr

    def test_stdin_rejects_a_bad_key_without_saving(self, isolated_user_config: Path) -> None:
        http = FakeHttp().on("GET", f"{ANTHROPIC}/models", json_response(401))
        session = Session([], http=http)
        result = session.invoke(app, ["key", "--provider", "anthropic", "--stdin"], input=KEY)
        assert result.exit_code == 1
        assert "Anthropic rejected this key." in result.stderr
        assert not (isolated_user_config / "credentials.toml").exists()

    def test_stdin_needs_a_key(self, isolated_user_config: Path) -> None:
        result = runner.invoke(
            app, ["key", "--provider", "anthropic", "--stdin"], input="\n", obj=_non_interactive()
        )
        assert result.exit_code == 1
        assert "No key was given on stdin." in result.stderr

    def test_without_stdin_or_a_terminal_it_explains(self) -> None:
        result = runner.invoke(app, ["key"], obj=_non_interactive())
        assert result.exit_code == 1
        assert "key needs a terminal, or pass --stdin" in result.stderr

    def test_a_rejected_interactive_key_can_be_retried(self, isolated_user_config: Path) -> None:
        http = _http().on("GET", f"{ANTHROPIC}/models", json_response(401), json_response(200, {}))
        session = Session([OTHER_KEY, KeyAction.REENTER, KEY], http=http)
        result = session.invoke(app, ["key", "--provider", "anthropic"])
        assert result.exit_code == 0, result.stderr
        assert FileCredentialStore(isolated_user_config).get(ProviderName.ANTHROPIC) == KEY


class TestConfigDirectoryWarning:
    def test_a_config_directory_inside_the_repository_is_warned_about(
        self, staged_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITCOMMIT_AI_CONFIG_DIR", str(staged_repo / "cfg"))
        use_llm(monkeypatch, scripted_llm([GOOD]))
        monkeypatch.setenv("OPENAI_API_KEY", KEY)
        result = runner.invoke(git_app, ["--path", str(staged_repo), "--dry-run"])
        assert "is inside this repository" in result.stderr
