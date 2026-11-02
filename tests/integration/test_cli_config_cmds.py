"""``config get/set/unset/path``, ``reset`` and ``doctor``, on both entry points."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from git_commit_ai.cli.git_subcommand import git_app
from git_commit_ai.cli.interaction import Interaction
from git_commit_ai.cli.main import app
from git_commit_ai.core.config import CommitMode, ProviderName
from git_commit_ai.core.credentials import FileCredentialStore
from git_commit_ai.core.settings import ENV_FILE_VARIABLE
from git_commit_ai.core.user_config import (
    CONFIG_FILE,
    SCHEMA_VERSION,
    GenerationOverrides,
    ProviderPreferences,
    UserConfig,
    load_user_config,
    save_user_config,
)
from tests.fixtures.http import FakeHttp, json_response
from tests.fixtures.prompter import ScriptedPrompter
from tests.fixtures.provider_payloads import ANTHROPIC_PAGES

if TYPE_CHECKING:
    from pathlib import Path

    import typer
    from typer.testing import Result

runner = CliRunner()
KEY = "FAKE-" + "d" * 24 + "a91c"
ANTHROPIC = "https://api.anthropic.com/v1"
APPS = pytest.mark.parametrize("entry", [app, git_app], ids=["gitcommit-ai", "git-ai-commit"])


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_FILE_VARIABLE, "")


def _session(
    answers: list[object] | None = None, http: FakeHttp | None = None, interactive: bool = True
) -> Interaction:
    prompter = ScriptedPrompter(answers or [])
    transport = (http or FakeHttp()).transport
    return Interaction(
        interactive=interactive, prompter_factory=lambda: prompter, transport=transport
    )


def _saved(directory: Path) -> UserConfig:
    config = load_user_config(directory)
    assert config is not None
    return config


class TestConfigShow:
    @APPS
    def test_no_subcommand_still_shows_the_settings(self, entry: typer.Typer) -> None:
        result = runner.invoke(entry, ["config"])
        assert result.exit_code == 0, result.stderr
        assert "Effective settings" in result.stdout
        assert "GITCOMMIT_AI_COMMIT_MODE" in result.stdout


class TestConfigGetSet:
    @APPS
    def test_set_then_get(self, entry: typer.Typer, isolated_user_config: Path) -> None:
        assert runner.invoke(entry, ["config", "set", "temperature", "0.7"]).exit_code == 0
        assert _saved(isolated_user_config).generation.temperature == 0.7
        result = runner.invoke(entry, ["config", "get", "temperature"])
        assert result.exit_code == 0
        assert result.stdout == "0.7\n"

    @pytest.mark.parametrize(
        ("key", "value", "check"),
        [
            ("provider", "gemini", lambda c: c.provider is ProviderName.GEMINI),
            ("commit_mode", "preview", lambda c: c.commit_mode is CommitMode.PREVIEW),
            ("max_iterations", "2", lambda c: c.generation.max_iterations == 2),
            ("timeout", "12.5", lambda c: c.generation.timeout == 12.5),
            (
                "ollama_url",
                "https://ollama.com/",
                lambda c: c.preferences(ProviderName.OLLAMA).base_url == "https://ollama.com",
            ),
        ],
    )
    def test_each_kind_of_key(
        self, isolated_user_config: Path, key: str, value: str, check: object
    ) -> None:
        result = runner.invoke(app, ["config", "set", key, value])
        assert result.exit_code == 0, result.stderr
        assert check(_saved(isolated_user_config))  # type: ignore[operator]

    def test_model_goes_to_the_active_provider_or_another(
        self, isolated_user_config: Path
    ) -> None:
        save_user_config(isolated_user_config, UserConfig(provider=ProviderName.ANTHROPIC))
        runner.invoke(app, ["config", "set", "model", "claude-x"])
        runner.invoke(app, ["config", "set", "model", "gpt-x", "--provider", "openai"])
        saved = _saved(isolated_user_config)
        assert saved.preferences(ProviderName.ANTHROPIC).model == "claude-x"
        assert saved.preferences(ProviderName.OPENAI).model == "gpt-x"
        result = runner.invoke(app, ["config", "get", "model", "--provider", "openai"])
        assert result.stdout == "gpt-x\n"

    def test_get_shows_the_effective_value_from_any_layer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITCOMMIT_AI_MAX_RETRIES", "5")
        assert runner.invoke(app, ["config", "get", "max_retries"]).stdout == "5\n"
        assert runner.invoke(app, ["config", "get", "commit_mode"]).stdout == "confirm\n"
        assert runner.invoke(app, ["config", "get", "model"]).stdout == "gpt-4o-mini\n"

    @pytest.mark.parametrize(
        ("key", "value", "message"),
        [
            ("temperature", "5", "config.toml: temperature: "),
            ("temperature", "warm", "temperature"),
            ("max_iterations", "lots", "max_iterations"),
            ("provider", "skynet", "provider"),
            ("commit_mode", "sometimes", "commit_mode"),
            ("retry_base_delay", "99", "retry base delay must not exceed"),
            ("ollama_url", "box:1", "Include http:// or https://"),
            ("credential_store", "cloud", "credential_store"),
        ],
    )
    def test_invalid_values_are_refused_and_nothing_is_written(
        self, isolated_user_config: Path, key: str, value: str, message: str
    ) -> None:
        result = runner.invoke(app, ["config", "set", key, value])
        assert result.exit_code == 1
        assert message in result.stderr
        assert not (isolated_user_config / CONFIG_FILE).exists()

    def test_unknown_keys_list_the_valid_ones(self) -> None:
        result = runner.invoke(app, ["config", "set", "colour", "blue"])
        assert result.exit_code == 1
        assert "Unknown setting 'colour'" in result.stderr
        assert "commit_mode" in result.stderr
        assert runner.invoke(app, ["config", "get", "colour"]).exit_code == 1

    @pytest.mark.parametrize("name", ["api_key", "key", "openai_key", "ANTHROPIC_API_KEY"])
    def test_keys_are_never_set_through_config(
        self, isolated_user_config: Path, name: str
    ) -> None:
        result = runner.invoke(app, ["config", "set", name, KEY])
        assert result.exit_code == 1
        assert "API keys aren't set with config; run git ai-commit key." in result.stderr
        assert KEY not in result.stdout + result.stderr
        assert not isolated_user_config.exists()

    def test_unset(self, isolated_user_config: Path) -> None:
        save_user_config(
            isolated_user_config,
            UserConfig(
                provider=ProviderName.ANTHROPIC,
                commit_mode=CommitMode.AUTO,
                generation=GenerationOverrides(temperature=0.9),
                providers={ProviderName.ANTHROPIC: ProviderPreferences(model="m")},
            ),
        )
        for key in ("temperature", "commit_mode", "model"):
            assert runner.invoke(app, ["config", "unset", key]).exit_code == 0
        saved = _saved(isolated_user_config)
        assert saved.generation.temperature is None
        assert saved.commit_mode is None
        assert saved.preferences(ProviderName.ANTHROPIC).model is None
        assert saved.provider is ProviderName.ANTHROPIC

    def test_writes_refuse_a_newer_config(self, isolated_user_config: Path) -> None:
        isolated_user_config.mkdir(parents=True)
        newer = f"schema_version = {SCHEMA_VERSION + 1}\n"
        (isolated_user_config / CONFIG_FILE).write_text(newer, encoding="utf-8")
        for args in (["set", "temperature", "0.5"], ["unset", "temperature"]):
            result = runner.invoke(app, ["config", *args])
            assert result.exit_code == 1
            assert "Upgrade gitcommit-ai, or run git ai-commit reset." in result.stderr
        assert (isolated_user_config / CONFIG_FILE).read_text(encoding="utf-8") == newer

    @APPS
    def test_path(self, entry: typer.Typer, isolated_user_config: Path) -> None:
        result = runner.invoke(entry, ["config", "path"])
        assert result.exit_code == 0
        assert result.stdout == f"{isolated_user_config / CONFIG_FILE}\n"

    def test_switching_to_the_file_store_moves_keys_back(
        self, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save_user_config(isolated_user_config, UserConfig())
        FileCredentialStore(isolated_user_config).set(ProviderName.OPENAI, KEY)
        result = runner.invoke(app, ["config", "set", "credential_store", "file"])
        assert result.exit_code == 0, result.stderr
        assert FileCredentialStore(isolated_user_config).get(ProviderName.OPENAI) == KEY

    def test_the_keychain_needs_the_keyring_extra(
        self, isolated_user_config: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "keyring", None)
        FileCredentialStore(isolated_user_config).set(ProviderName.OPENAI, KEY)
        result = runner.invoke(app, ["config", "set", "credential_store", "keyring"])
        assert result.exit_code == 1
        assert "keychain isn't available" in result.stderr
        assert FileCredentialStore(isolated_user_config).get(ProviderName.OPENAI) == KEY


class TestReset:
    @pytest.fixture
    def everything(self, isolated_user_config: Path) -> Path:
        save_user_config(isolated_user_config, UserConfig(provider=ProviderName.ANTHROPIC))
        store = FileCredentialStore(isolated_user_config)
        store.set(ProviderName.ANTHROPIC, KEY)
        store.set(ProviderName.OPENROUTER, KEY)
        return isolated_user_config

    @APPS
    def test_confirmed_reset_removes_everything(
        self, entry: typer.Typer, everything: Path
    ) -> None:
        result = runner.invoke(entry, ["reset"], obj=_session([True]))
        assert result.exit_code == 0, result.stderr
        assert "keys for: anthropic, openrouter" in result.stderr
        assert "Environment variables and .env files are not touched." in result.stderr
        assert "Removed. Run `git ai-commit` to set up again." in result.stderr
        assert not (everything / CONFIG_FILE).exists()
        assert not (everything / "credentials.toml").exists()

    def test_declining_keeps_everything(self, everything: Path) -> None:
        result = runner.invoke(app, ["reset"], obj=_session([False]))
        assert result.exit_code == 1
        assert (everything / CONFIG_FILE).exists()

    def test_without_a_terminal_it_needs_yes(self, everything: Path) -> None:
        result = runner.invoke(app, ["reset"], obj=_session(interactive=False))
        assert result.exit_code == 1
        assert "reset needs --yes when there is no terminal to confirm in." in result.stderr
        assert (everything / CONFIG_FILE).exists()
        result = runner.invoke(app, ["reset", "--yes"], obj=_session(interactive=False))
        assert result.exit_code == 0
        assert not (everything / CONFIG_FILE).exists()

    def test_works_on_a_corrupted_or_newer_config(self, isolated_user_config: Path) -> None:
        isolated_user_config.mkdir(parents=True)
        (isolated_user_config / CONFIG_FILE).write_text("= = =", encoding="utf-8")
        (isolated_user_config / "credentials.toml").write_text("= = =", encoding="utf-8")
        result = runner.invoke(app, ["reset", "--yes"])
        assert result.exit_code == 0, result.stderr
        assert not (isolated_user_config / CONFIG_FILE).exists()
        assert not (isolated_user_config / "credentials.toml").exists()

    def test_nothing_to_remove(self) -> None:
        result = runner.invoke(app, ["reset", "--yes"])
        assert result.exit_code == 0
        assert "Nothing is saved" in result.stderr

    def test_keychain_entries_are_removed_too(
        self, everything: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.unit.core.test_credentials import FakeKeyring

        fake = FakeKeyring()
        fake.passwords[("gitcommit-ai", "gemini")] = KEY
        monkeypatch.setattr("git_commit_ai.cli.config_cmd.keyring_available", lambda: True)
        monkeypatch.setattr("git_commit_ai.cli.config_cmd.keyring_backend", lambda: fake)
        result = runner.invoke(app, ["reset", "--yes"])
        assert result.exit_code == 0, result.stderr
        assert fake.passwords == {}
        assert "keychain entries for: gemini" in result.stderr


def _doctor_http(**overrides: object) -> FakeHttp:
    models = {"data": ANTHROPIC_PAGES[0]["data"], "has_more": False}
    http = FakeHttp()
    http.on("GET", f"{ANTHROPIC}/models", overrides.get("models", json_response(200, models)))  # type: ignore[arg-type]
    http.on("POST", f"{ANTHROPIC}/messages", overrides.get("probe", json_response(200, {})))  # type: ignore[arg-type]
    return http


class TestDoctor:
    @pytest.fixture
    def configured(self, isolated_user_config: Path) -> Path:
        save_user_config(
            isolated_user_config,
            UserConfig(
                provider=ProviderName.ANTHROPIC,
                providers={ProviderName.ANTHROPIC: ProviderPreferences(model="claude-sonnet-5-5")},
            ),
        )
        FileCredentialStore(isolated_user_config).set(ProviderName.ANTHROPIC, KEY)
        return isolated_user_config

    def _run(self, http: FakeHttp, *args: str, entry: typer.Typer = app) -> Result:
        return runner.invoke(entry, ["doctor", *args], obj=_session(http=http, interactive=False))

    @APPS
    def test_all_checks_pass(self, entry: typer.Typer, configured: Path) -> None:
        http = _doctor_http()
        result = self._run(http, entry=entry)
        assert result.exit_code == 0, result.stdout + result.stderr
        out = result.stdout
        assert "Provider   anthropic" in out
        assert "[config file]" in out
        assert "API key    ********a91c" in out
        assert "[credentials file]" in out
        assert "Key check            ok" in out
        assert "Model listed         ok" in out
        assert "Test request         ok" in out
        assert KEY not in out + result.stderr

    def test_no_request_skips_the_test_request(self, configured: Path) -> None:
        http = _doctor_http()
        result = self._run(http, "--no-request")
        assert result.exit_code == 0
        assert "Test request         skipped (--no-request)" in result.stdout
        assert all(call.method == "GET" for call in http.calls)

    def test_a_revoked_key_fails_and_says_what_to_run(self, configured: Path) -> None:
        result = self._run(_doctor_http(models=json_response(401)))
        assert result.exit_code == 1
        assert "Key check            failed: Anthropic rejected the key" in result.stdout
        assert "git ai-commit key" in result.stdout

    def test_a_retired_model_fails_and_says_what_to_run(self, isolated_user_config: Path) -> None:
        save_user_config(
            isolated_user_config,
            UserConfig(
                provider=ProviderName.ANTHROPIC,
                providers={ProviderName.ANTHROPIC: ProviderPreferences(model="claude-2")},
            ),
        )
        FileCredentialStore(isolated_user_config).set(ProviderName.ANTHROPIC, KEY)
        http = _doctor_http(probe=json_response(404, {"error": {"message": "model not found"}}))
        result = self._run(http)
        assert result.exit_code == 1
        assert "Model listed         not listed by Anthropic" in result.stdout
        assert "Test request         failed: claude-2 isn't available" in result.stdout
        assert "git ai-commit model" in result.stdout

    def test_no_key_fails_without_any_request(self, isolated_user_config: Path) -> None:
        save_user_config(isolated_user_config, UserConfig(provider=ProviderName.ANTHROPIC))
        http = _doctor_http()
        result = self._run(http)
        assert result.exit_code == 1
        assert "API key    not set" in result.stdout
        assert "git ai-commit key" in result.stdout
        assert http.calls == []

    def test_an_unreachable_ollama_fails(self, isolated_user_config: Path) -> None:
        import httpx

        save_user_config(isolated_user_config, UserConfig(provider=ProviderName.OLLAMA))
        http = FakeHttp().on(
            "GET", "http://localhost:11434/api/version", httpx.ConnectError("refused")
        )
        result = self._run(http)
        assert result.exit_code == 1
        assert "Ollama server        not reachable (connection refused)" in result.stdout

    def test_unknown_config_keys_are_listed(self, configured: Path) -> None:
        path = configured / CONFIG_FILE
        path.write_text("colour = 'blue'\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        result = self._run(_doctor_http(), "--no-request")
        assert "Warning: config.toml has settings this version ignores: colour" in result.stdout
