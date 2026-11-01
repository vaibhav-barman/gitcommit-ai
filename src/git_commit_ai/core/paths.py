"""Where GitCommit AI keeps its per-user files."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

CONFIG_DIR_VARIABLE = "GITCOMMIT_AI_CONFIG_DIR"
_APP_DIR = "gitcommit-ai"


def config_dir(env: Mapping[str, str], platform: str = sys.platform) -> Path:
    """Return the directory holding ``config.toml`` and ``credentials.toml``.

    ``GITCOMMIT_AI_CONFIG_DIR`` wins when it is set and non-empty. Otherwise
    Windows uses ``%APPDATA%\\gitcommit-ai`` and every other platform, macOS
    included, uses ``$XDG_CONFIG_HOME/gitcommit-ai`` (only if that variable is an
    absolute path) or ``~/.config/gitcommit-ai``.
    """
    override = env.get(CONFIG_DIR_VARIABLE)
    if override:
        return Path(override).expanduser()
    if platform == "win32":
        appdata = env.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / _APP_DIR
    xdg = env.get("XDG_CONFIG_HOME")
    if xdg and Path(xdg).is_absolute():
        return Path(xdg) / _APP_DIR
    return Path.home() / ".config" / _APP_DIR
