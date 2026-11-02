"""Helpers for driving the CLI with a fake LLM."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from git_commit_ai.core.paths import CONFIG_DIR_VARIABLE
from git_commit_ai.core.user_config import CONFIG_FILE, UserConfig, save_user_config

if TYPE_CHECKING:
    import pytest

    from git_commit_ai.core.config import GitCommitAIConfig
    from git_commit_ai.llm import FakeLLM


class Created:
    """What the CLI passed to ``create_provider`` (for assertions)."""

    def __init__(self) -> None:
        self.configs: list[GitCommitAIConfig] = []
        self.api_keys: list[str | None] = []


def use_llm(monkeypatch: pytest.MonkeyPatch, llm: FakeLLM, configured: bool = True) -> Created:
    """Make the CLI use ``llm`` and record the config and API key it was given.

    With ``configured`` (the default) an empty ``config.toml`` is saved unless one exists,
    so the run counts as set up and first-run setup never starts. Pass ``False`` to test
    first-run behaviour.
    """
    created = Created()

    def fake_create(config: GitCommitAIConfig, api_key: str | None, **_: object) -> FakeLLM:
        created.configs.append(config)
        created.api_keys.append(api_key)
        return llm

    monkeypatch.setattr("git_commit_ai.cli.generate.create_provider", fake_create)
    monkeypatch.setattr("git_commit_ai.cli.hook.create_provider", fake_create)
    directory = Path(os.environ[CONFIG_DIR_VARIABLE])
    if configured and not (directory / CONFIG_FILE).exists():
        save_user_config(directory, UserConfig())
    return created
