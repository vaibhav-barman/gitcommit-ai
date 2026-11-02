"""Where the per-user config directory lives on each platform."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_commit_ai.core.paths import CONFIG_DIR_VARIABLE, config_dir


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    return tmp_path / "home"


def test_override_wins(home: Path, tmp_path: Path) -> None:
    chosen = tmp_path / "elsewhere"
    env = {CONFIG_DIR_VARIABLE: str(chosen), "XDG_CONFIG_HOME": "/xdg"}
    assert config_dir(env, platform="linux") == chosen


def test_override_expands_the_home_directory(home: Path) -> None:
    assert config_dir({CONFIG_DIR_VARIABLE: "~/cfg"}, platform="linux") == home / "cfg"


def test_empty_override_is_ignored(home: Path) -> None:
    assert config_dir({CONFIG_DIR_VARIABLE: ""}, platform="linux") == home / ".config/gitcommit-ai"


def test_windows_uses_appdata(home: Path) -> None:
    env = {"APPDATA": r"C:\Users\a\AppData\Roaming"}
    assert config_dir(env, platform="win32") == Path(env["APPDATA"]) / "gitcommit-ai"


def test_windows_without_appdata_uses_the_roaming_folder(home: Path) -> None:
    assert config_dir({}, platform="win32") == home / "AppData" / "Roaming" / "gitcommit-ai"


def test_absolute_xdg_config_home_is_used(home: Path, tmp_path: Path) -> None:
    xdg = tmp_path / "xdg"
    assert config_dir({"XDG_CONFIG_HOME": str(xdg)}, platform="linux") == xdg / "gitcommit-ai"


def test_relative_xdg_config_home_is_ignored(home: Path) -> None:
    env = {"XDG_CONFIG_HOME": "relative/dir"}
    assert config_dir(env, platform="linux") == home / ".config" / "gitcommit-ai"


def test_macos_uses_dot_config(home: Path) -> None:
    assert config_dir({}, platform="darwin") == home / ".config" / "gitcommit-ai"
