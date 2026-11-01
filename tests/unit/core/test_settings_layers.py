"""The saved-settings layer: flags > environment > .env > config.toml > defaults."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from git_commit_ai.core.config import ProviderName
from git_commit_ai.core.credentials import FileCredentialStore
from git_commit_ai.core.exceptions import ConfigurationError
from git_commit_ai.core.settings import ENV_FILE_VARIABLE, SettingSource, load_settings
from git_commit_ai.core.user_config import (
    GenerationOverrides,
    ProviderPreferences,
    UserConfig,
)

if TYPE_CHECKING:
    from pathlib import Path

NO_DOTENV = {ENV_FILE_VARIABLE: ""}
KEY_ENV = "FAKE-" + "e" * 16 + "env1"
KEY_DOTENV = "FAKE-" + "d" * 16 + "dot2"
KEY_SAVED = "FAKE-" + "s" * 16 + "sav3"

SAVED = UserConfig(
    provider=ProviderName.ANTHROPIC,
    providers={
        ProviderName.ANTHROPIC: ProviderPreferences(model="saved-claude"),
        ProviderName.GEMINI: ProviderPreferences(model="saved-gemini"),
        ProviderName.OLLAMA: ProviderPreferences(base_url="http://saved:11434"),
    },
    generation=GenerationOverrides(temperature=0.9, max_iterations=2),
)


def _dotenv(tmp_path: Path, text: str) -> dict[str, str]:
    path = tmp_path / "x.env"
    path.write_text(text, encoding="utf-8")
    return {ENV_FILE_VARIABLE: str(path)}


class TestSettingsPrecedence:
    def test_saved_values_beat_defaults(self, tmp_path: Path) -> None:
        settings = load_settings(NO_DOTENV, tmp_path, user_config=SAVED)
        config = settings.config
        assert config.llm_provider is ProviderName.ANTHROPIC
        assert config.llm_model == "saved-claude"
        assert config.llm_temperature == 0.9
        assert config.max_iterations == 2
        assert config.ollama_base_url == "http://saved:11434"
        for field in ("llm_provider", "llm_model", "llm_temperature", "ollama_base_url"):
            assert settings.source_of_field(field) is SettingSource.USER_CONFIG
        assert settings.source_of_field("llm_timeout_seconds") is SettingSource.DEFAULT

    def test_dotenv_beats_saved_values(self, tmp_path: Path) -> None:
        env = _dotenv(tmp_path, "GITCOMMIT_AI_TEMPERATURE=0.1\nGITCOMMIT_AI_OLLAMA_URL=http://d\n")
        settings = load_settings(env, tmp_path, user_config=SAVED)
        assert settings.config.llm_temperature == 0.1
        assert settings.config.ollama_base_url == "http://d"
        assert settings.source_of_field("llm_temperature") is SettingSource.DOTENV

    def test_environment_beats_dotenv_and_saved_values(self, tmp_path: Path) -> None:
        env = {
            **_dotenv(tmp_path, "GITCOMMIT_AI_TEMPERATURE=0.1\n"),
            "GITCOMMIT_AI_TEMPERATURE": "0.2",
            "GITCOMMIT_AI_PROVIDER": "gemini",
        }
        settings = load_settings(env, tmp_path, user_config=SAVED)
        assert settings.config.llm_temperature == 0.2
        assert settings.config.llm_provider is ProviderName.GEMINI
        assert settings.source_of_field("llm_provider") is SettingSource.ENVIRONMENT

    def test_flags_beat_everything(self, tmp_path: Path) -> None:
        env = {**NO_DOTENV, "GITCOMMIT_AI_MAX_ITERATIONS": "4"}
        settings = load_settings(env, tmp_path, user_config=SAVED).with_flags(max_iterations=7)
        assert settings.config.max_iterations == 7
        assert settings.source_of_field("max_iterations") is SettingSource.FLAG

    def test_without_a_config_file_nothing_changes(self, tmp_path: Path) -> None:
        settings = load_settings(NO_DOTENV, tmp_path, user_config=None)
        assert settings.config.llm_provider is ProviderName.OPENAI
        assert settings.config.llm_model is None
        assert settings.source_of_field("llm_provider") is SettingSource.DEFAULT

    def test_a_saved_value_out_of_range_names_the_config_key(self, tmp_path: Path) -> None:
        bad = UserConfig(generation=GenerationOverrides(temperature=5.0))
        with pytest.raises(ConfigurationError, match=r"config\.toml: temperature: "):
            load_settings(NO_DOTENV, tmp_path, user_config=bad)

    def test_an_environment_value_out_of_range_still_names_the_variable(
        self, tmp_path: Path
    ) -> None:
        env = {**NO_DOTENV, "GITCOMMIT_AI_TEMPERATURE": "5"}
        with pytest.raises(ConfigurationError, match="GITCOMMIT_AI_TEMPERATURE"):
            load_settings(env, tmp_path, user_config=SAVED)


class TestModelPerProvider:
    def test_the_model_saved_for_the_effective_provider_is_used(self, tmp_path: Path) -> None:
        env = {**NO_DOTENV, "GITCOMMIT_AI_PROVIDER": "gemini"}
        settings = load_settings(env, tmp_path, user_config=SAVED)
        assert settings.config.llm_model == "saved-gemini"

    def test_a_provider_without_a_saved_model_uses_its_default(self, tmp_path: Path) -> None:
        env = {**NO_DOTENV, "GITCOMMIT_AI_PROVIDER": "openai"}
        settings = load_settings(env, tmp_path, user_config=SAVED)
        assert settings.config.llm_model is None
        assert settings.source_of_field("llm_model") is SettingSource.DEFAULT

    def test_the_model_variable_beats_the_saved_model(self, tmp_path: Path) -> None:
        env = {**_dotenv(tmp_path, "GITCOMMIT_AI_MODEL=dotenv-model\n")}
        settings = load_settings(env, tmp_path, user_config=SAVED)
        assert settings.config.llm_model == "dotenv-model"
        assert settings.source_of_field("llm_model") is SettingSource.DOTENV

    def test_a_provider_flag_brings_that_providers_saved_model(self, tmp_path: Path) -> None:
        settings = load_settings(NO_DOTENV, tmp_path, user_config=SAVED)
        flagged = settings.with_flags(llm_provider=ProviderName.GEMINI)
        assert flagged.config.llm_model == "saved-gemini"
        assert flagged.source_of_field("llm_model") is SettingSource.USER_CONFIG

    def test_a_provider_flag_drops_another_providers_saved_model(self, tmp_path: Path) -> None:
        settings = load_settings(NO_DOTENV, tmp_path, user_config=SAVED)
        flagged = settings.with_flags(llm_provider=ProviderName.OPENAI)
        assert flagged.config.llm_model is None
        assert flagged.source_of_field("llm_model") is SettingSource.DEFAULT

    def test_a_provider_flag_keeps_a_model_from_the_environment(self, tmp_path: Path) -> None:
        env = {**NO_DOTENV, "GITCOMMIT_AI_MODEL": "env-model"}
        settings = load_settings(env, tmp_path, user_config=SAVED)
        flagged = settings.with_flags(llm_provider=ProviderName.OPENAI)
        assert flagged.config.llm_model == "env-model"

    def test_a_model_flag_beats_everything(self, tmp_path: Path) -> None:
        settings = load_settings(NO_DOTENV, tmp_path, user_config=SAVED)
        flagged = settings.with_flags(llm_provider=ProviderName.GEMINI, llm_model="flag-model")
        assert flagged.config.llm_model == "flag-model"
        assert flagged.source_of_field("llm_model") is SettingSource.FLAG


class TestApiKeys:
    def _store(self, tmp_path: Path) -> FileCredentialStore:
        store = FileCredentialStore(tmp_path / "cfg")
        store.set(ProviderName.ANTHROPIC, KEY_SAVED)
        return store

    def test_saved_key_is_used_when_no_variable_is_set(self, tmp_path: Path) -> None:
        settings = load_settings(NO_DOTENV, tmp_path, credentials=self._store(tmp_path))
        key = settings.api_key_for(ProviderName.ANTHROPIC)
        assert key is not None
        assert key.value == KEY_SAVED
        assert key.source is SettingSource.CREDENTIALS_FILE
        assert key.variable is None

    def test_dotenv_beats_the_saved_key(self, tmp_path: Path) -> None:
        env = _dotenv(tmp_path, f"ANTHROPIC_API_KEY={KEY_DOTENV}\n")
        settings = load_settings(env, tmp_path, credentials=self._store(tmp_path))
        key = settings.api_key_for(ProviderName.ANTHROPIC)
        assert key is not None
        assert (key.value, key.source, key.variable) == (
            KEY_DOTENV,
            SettingSource.DOTENV,
            "ANTHROPIC_API_KEY",
        )

    def test_environment_beats_dotenv(self, tmp_path: Path) -> None:
        env = {
            **_dotenv(tmp_path, f"ANTHROPIC_API_KEY={KEY_DOTENV}\n"),
            "ANTHROPIC_API_KEY": KEY_ENV,
        }
        settings = load_settings(env, tmp_path, credentials=self._store(tmp_path))
        key = settings.api_key_for(ProviderName.ANTHROPIC)
        assert key is not None
        assert (key.value, key.source) == (KEY_ENV, SettingSource.ENVIRONMENT)

    def test_no_key_anywhere(self, tmp_path: Path) -> None:
        settings = load_settings(NO_DOTENV, tmp_path, credentials=self._store(tmp_path))
        assert settings.api_key_for(ProviderName.OPENAI) is None

    def test_ollama_reads_no_variable_before_its_key_exists(self, tmp_path: Path) -> None:
        settings = load_settings(NO_DOTENV, tmp_path)
        assert settings.api_key_for(ProviderName.OLLAMA) is None

    def test_the_key_never_appears_in_its_repr(self, tmp_path: Path) -> None:
        settings = load_settings(NO_DOTENV, tmp_path, credentials=self._store(tmp_path))
        key = settings.api_key_for(ProviderName.ANTHROPIC)
        assert KEY_SAVED not in repr(key)
        assert KEY_SAVED not in repr(settings)


class TestConfiguredByEnvironment:
    @pytest.mark.parametrize(
        "variables",
        [{"GITCOMMIT_AI_PROVIDER": "ollama"}, {"OPENAI_API_KEY": KEY_ENV}],
    )
    def test_provider_or_key_variable_counts(
        self, tmp_path: Path, variables: dict[str, str]
    ) -> None:
        settings = load_settings({**NO_DOTENV, **variables}, tmp_path)
        assert settings.provider_configured_by_environment()

    def test_a_dotenv_key_counts(self, tmp_path: Path) -> None:
        settings = load_settings(_dotenv(tmp_path, f"GOOGLE_API_KEY={KEY_DOTENV}\n"), tmp_path)
        assert settings.provider_configured_by_environment()

    def test_other_variables_do_not_count(self, tmp_path: Path) -> None:
        env = {**NO_DOTENV, "GITCOMMIT_AI_TEMPERATURE": "0.5", "GITCOMMIT_AI_MODEL": "x"}
        assert not load_settings(env, tmp_path).provider_configured_by_environment()


def test_a_cross_field_error_is_reported_not_crashed(tmp_path: Path) -> None:
    bad = UserConfig(generation=GenerationOverrides(retry_base_delay=50.0))
    with pytest.raises(ConfigurationError, match="retry base delay must not exceed"):
        load_settings(NO_DOTENV, tmp_path, user_config=bad)
