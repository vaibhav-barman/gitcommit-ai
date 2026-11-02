"""Reading and writing config.toml."""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from pathlib import Path

import pytest

from git_commit_ai.core.config import CommitMode, ProviderName
from git_commit_ai.core.exceptions import ConfigurationError, UnsupportedConfigVersionError
from git_commit_ai.core.user_config import (
    CONFIG_FILE,
    SCHEMA_VERSION,
    CredentialStoreKind,
    GenerationOverrides,
    ProviderPreferences,
    UserConfig,
    atomic_write,
    load_user_config,
    save_user_config,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def _write(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONFIG_FILE
    path.write_text(text, encoding="utf-8")
    return path


def test_absent_file_loads_as_none(tmp_path: Path) -> None:
    assert load_user_config(tmp_path / "missing") is None


def test_loads_a_valid_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        'schema_version = 1\nprovider = "anthropic"\ncommit_mode = "auto"\n'
        '[providers.anthropic]\nmodel = "claude-x"\n[providers.ollama]\n'
        'base_url = "http://box:11434"\n[generation]\ntemperature = 0.7\n',
    )
    config = load_user_config(tmp_path)
    assert config is not None
    assert config.provider is ProviderName.ANTHROPIC
    assert config.commit_mode is CommitMode.AUTO
    assert config.credential_store is CredentialStoreKind.FILE
    assert config.providers[ProviderName.ANTHROPIC].model == "claude-x"
    assert config.providers[ProviderName.OLLAMA].base_url == "http://box:11434"
    assert config.generation.temperature == 0.7


def test_invalid_toml_names_the_file_line_and_column(tmp_path: Path) -> None:
    path = _write(tmp_path, 'provider = "openai"\nmodel = = 3\n')
    with pytest.raises(ConfigurationError) as caught:
        load_user_config(tmp_path)
    message = str(caught.value)
    assert str(path) in message
    assert "is not valid TOML" in message
    assert "line 2" in message
    assert "column" in message
    assert "git ai-commit reset" in message


def test_a_bad_value_names_the_key(tmp_path: Path) -> None:
    _write(tmp_path, 'commit_mode = "sometimes"\n')
    with pytest.raises(ConfigurationError, match=r"config\.toml: commit_mode: "):
        load_user_config(tmp_path)


def test_a_bad_nested_value_names_the_dotted_key(tmp_path: Path) -> None:
    _write(tmp_path, '[generation]\ntemperature = "warm"\n')
    with pytest.raises(ConfigurationError, match=r"generation\.temperature"):
        load_user_config(tmp_path)


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    _write(tmp_path, 'provider = "gemini"\nfuture_setting = true\n[providers.gemini]\nx = 1\n')
    config = load_user_config(tmp_path)
    assert config is not None
    assert config.provider is ProviderName.GEMINI


def test_a_newer_version_is_read_and_flagged(tmp_path: Path) -> None:
    _write(tmp_path, f'schema_version = {SCHEMA_VERSION + 1}\nprovider = "openai"\n')
    config = load_user_config(tmp_path)
    assert config is not None
    assert config.written_by_newer_version
    assert config.provider is ProviderName.OPENAI


def test_a_newer_version_refuses_to_be_saved(tmp_path: Path) -> None:
    newer = UserConfig(schema_version=SCHEMA_VERSION + 1, provider=ProviderName.OPENAI)
    with pytest.raises(UnsupportedConfigVersionError, match="git ai-commit reset"):
        save_user_config(tmp_path, newer)
    assert not (tmp_path / CONFIG_FILE).exists()


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    config = UserConfig(
        provider=ProviderName.OLLAMA,
        commit_mode=CommitMode.PREVIEW,
        providers={
            ProviderName.OLLAMA: ProviderPreferences(model="gemma3:4b", base_url="http://h:1"),
            ProviderName.OPENAI: ProviderPreferences(model="gpt-5"),
        },
        generation=GenerationOverrides(timeout=12.5, max_iterations=2),
    )
    path = save_user_config(tmp_path / "cfg", config)
    assert path == tmp_path / "cfg" / CONFIG_FILE
    assert load_user_config(tmp_path / "cfg") == config


def test_save_omits_unset_values(tmp_path: Path) -> None:
    save_user_config(tmp_path, UserConfig(provider=ProviderName.GEMINI))
    data = tomllib.loads((tmp_path / CONFIG_FILE).read_text(encoding="utf-8"))
    assert data == {
        "schema_version": SCHEMA_VERSION,
        "provider": "gemini",
        "credential_store": "file",
    }


@posix_only
def test_config_file_is_0644_and_its_directory_0700(tmp_path: Path) -> None:
    directory = tmp_path / "cfg"
    path = save_user_config(directory, UserConfig())
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_atomic_write_keeps_the_old_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "file.toml"
    path.write_text("old\n", encoding="utf-8")

    def broken_replace(src: object, dst: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(KeyboardInterrupt):
        atomic_write(path, "new\n", 0o644)
    assert path.read_text(encoding="utf-8") == "old\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["file.toml"]


def test_atomic_write_replaces_the_contents(tmp_path: Path) -> None:
    path = tmp_path / "file.toml"
    path.write_text("old\n", encoding="utf-8")
    atomic_write(path, "new\n", 0o644)
    assert path.read_text(encoding="utf-8") == "new\n"
