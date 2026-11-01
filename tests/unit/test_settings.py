"""Unit tests for layered settings: flags > environment > .env > defaults."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from git_commit_ai.core.config import ProviderName
from git_commit_ai.core.exceptions import ConfigurationError
from git_commit_ai.core.settings import (
    ENV_FILE_VARIABLE,
    SettingSource,
    load_settings,
    mask_secret,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_env(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_when_nothing_is_configured(tmp_path: Path) -> None:
    settings = load_settings({ENV_FILE_VARIABLE: ""}, tmp_path)
    assert settings.config.llm_provider is ProviderName.OPENAI
    assert settings.dotenv_path is None
    assert settings.source_of_field("llm_provider") is SettingSource.DEFAULT


def test_dotenv_supplies_values_when_the_environment_does_not(tmp_path: Path) -> None:
    path = _write_env(tmp_path, "GITCOMMIT_AI_PROVIDER=ollama\nGITCOMMIT_AI_MAX_ITERATIONS=5\n")
    settings = load_settings({}, tmp_path)
    assert settings.dotenv_path == path
    assert settings.config.llm_provider is ProviderName.OLLAMA
    assert settings.config.max_iterations == 5
    assert settings.source_of_field("llm_provider") is SettingSource.DOTENV


def test_environment_beats_dotenv(tmp_path: Path) -> None:
    _write_env(tmp_path, "GITCOMMIT_AI_PROVIDER=ollama\nGITCOMMIT_AI_MAX_ITERATIONS=5\n")
    settings = load_settings({"GITCOMMIT_AI_PROVIDER": "gemini"}, tmp_path)
    assert settings.config.llm_provider is ProviderName.GEMINI
    assert settings.config.max_iterations == 5
    assert settings.source_of_field("llm_provider") is SettingSource.ENVIRONMENT
    assert settings.source_of_field("max_iterations") is SettingSource.DOTENV


def test_flags_beat_environment_and_dotenv(tmp_path: Path) -> None:
    _write_env(tmp_path, "GITCOMMIT_AI_PROVIDER=ollama\n")
    settings = load_settings({"GITCOMMIT_AI_PROVIDER": "gemini"}, tmp_path)

    flagged = settings.with_flags(llm_provider=ProviderName.ANTHROPIC, llm_model=None)

    assert flagged.config.llm_provider is ProviderName.ANTHROPIC
    assert flagged.source_of_field("llm_provider") is SettingSource.FLAG
    assert flagged.config.llm_model is None
    assert flagged.source_of_field("llm_model") is SettingSource.DEFAULT


def test_full_precedence_chain_for_one_setting(tmp_path: Path) -> None:
    _write_env(tmp_path, "GITCOMMIT_AI_MODEL=from-dotenv\n")
    assert load_settings({}, tmp_path).config.llm_model == "from-dotenv"
    env = {"GITCOMMIT_AI_MODEL": "from-env"}
    assert load_settings(env, tmp_path).config.llm_model == "from-env"
    assert load_settings(env, tmp_path).with_flags(llm_model="from-flag").config.llm_model == (
        "from-flag"
    )


def test_api_keys_follow_the_same_precedence(tmp_path: Path) -> None:
    _write_env(tmp_path, "OPENAI_API_KEY=dotenv-key\nANTHROPIC_API_KEY=dotenv-anthropic\n")
    settings = load_settings({"OPENAI_API_KEY": "env-key"}, tmp_path)
    assert settings.environment["OPENAI_API_KEY"] == "env-key"
    assert settings.environment["ANTHROPIC_API_KEY"] == "dotenv-anthropic"
    assert settings.source_of_variable("OPENAI_API_KEY") is SettingSource.ENVIRONMENT
    assert settings.source_of_variable("ANTHROPIC_API_KEY") is SettingSource.DOTENV
    assert settings.source_of_variable("GOOGLE_API_KEY") is SettingSource.DEFAULT


def test_dotenv_is_never_written_into_the_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _write_env(tmp_path, "OPENAI_API_KEY=dotenv-key\n")
    before = dict(os.environ)
    load_settings({}, tmp_path)
    assert dict(os.environ) == before
    assert "OPENAI_API_KEY" not in os.environ


def test_dotenv_is_found_in_a_parent_directory(tmp_path: Path) -> None:
    path = _write_env(tmp_path, "GITCOMMIT_AI_MODEL=parent\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    settings = load_settings({}, nested)
    assert settings.dotenv_path == path
    assert settings.config.llm_model == "parent"


def test_the_nearest_dotenv_wins(tmp_path: Path) -> None:
    _write_env(tmp_path, "GITCOMMIT_AI_MODEL=parent\n")
    _write_env(tmp_path / "child", "GITCOMMIT_AI_MODEL=child\n")
    assert load_settings({}, tmp_path / "child").config.llm_model == "child"


def test_env_file_variable_selects_a_specific_file(tmp_path: Path) -> None:
    _write_env(tmp_path, "GITCOMMIT_AI_MODEL=discovered\n")
    other = tmp_path / "custom.env"
    other.write_text("GITCOMMIT_AI_MODEL=custom\n", encoding="utf-8")
    settings = load_settings({ENV_FILE_VARIABLE: str(other)}, tmp_path)
    assert settings.config.llm_model == "custom"
    assert settings.dotenv_path == other


def test_empty_env_file_variable_disables_loading(tmp_path: Path) -> None:
    _write_env(tmp_path, "GITCOMMIT_AI_MODEL=ignored\n")
    settings = load_settings({ENV_FILE_VARIABLE: ""}, tmp_path)
    assert settings.config.llm_model is None
    assert settings.dotenv_path is None


def test_missing_named_env_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="missing file"):
        load_settings({ENV_FILE_VARIABLE: str(tmp_path / "nope.env")}, tmp_path)


def test_blank_dotenv_values_do_not_count_as_set(tmp_path: Path) -> None:
    _write_env(tmp_path, "GITCOMMIT_AI_MODEL=\nOPENAI_API_KEY=\n")
    settings = load_settings({}, tmp_path)
    assert settings.config.llm_model is None
    assert settings.source_of_variable("OPENAI_API_KEY") is SettingSource.DEFAULT


def test_dotenv_syntax_comments_and_quotes_are_understood(tmp_path: Path) -> None:
    _write_env(
        tmp_path, '# a comment\nexport GITCOMMIT_AI_MODEL="model with spaces"  # trailing\n'
    )
    assert load_settings({}, tmp_path).config.llm_model == "model with spaces"


def test_invalid_dotenv_value_names_the_variable(tmp_path: Path) -> None:
    _write_env(tmp_path, "GITCOMMIT_AI_MAX_ITERATIONS=lots\n")
    with pytest.raises(ConfigurationError, match="GITCOMMIT_AI_MAX_ITERATIONS"):
        load_settings({}, tmp_path)


@pytest.mark.parametrize(
    ("secret", "masked"),
    [("", ""), ("abc", "***"), ("sevench", "*******"), ("sk-abcdefgh1234", "********1234")],
)
def test_mask_secret_never_reveals_more_than_the_tail(secret: str, masked: str) -> None:
    assert mask_secret(secret) == masked
