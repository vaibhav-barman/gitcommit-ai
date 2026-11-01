"""Helpers for driving the CLI with a fake LLM."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

    from git_commit_ai.core.config import GitCommitAIConfig
    from git_commit_ai.llm import FakeLLM


class Created:
    """What the CLI passed to ``create_provider`` (for assertions)."""

    def __init__(self) -> None:
        self.configs: list[GitCommitAIConfig] = []
        self.envs: list[dict[str, str]] = []


def use_llm(monkeypatch: pytest.MonkeyPatch, llm: FakeLLM) -> Created:
    """Make the CLI use ``llm`` and record the config and environment it was given."""
    created = Created()

    def fake_create(
        config: GitCommitAIConfig, env: dict[str, str] | None = None, **_: object
    ) -> FakeLLM:
        created.configs.append(config)
        created.envs.append(dict(env or {}))
        return llm

    monkeypatch.setattr("git_commit_ai.cli.generate.create_provider", fake_create)
    return created
