"""Unit tests for provider selection."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import pytest

from git_commit_ai.core.config import GitCommitAIConfig, ProviderName
from git_commit_ai.core.exceptions import MissingAPIKeyError, ProviderNotInstalledError
from git_commit_ai.llm import create_provider

if TYPE_CHECKING:
    from pytest import MonkeyPatch


@pytest.mark.parametrize(
    ("provider", "env_var"),
    [
        (ProviderName.OPENAI, "OPENAI_API_KEY"),
        (ProviderName.ANTHROPIC, "ANTHROPIC_API_KEY"),
        (ProviderName.GEMINI, "GOOGLE_API_KEY"),
    ],
)
def test_missing_key_names_the_env_var(provider: ProviderName, env_var: str) -> None:
    config = GitCommitAIConfig(llm_provider=provider)
    with pytest.raises(MissingAPIKeyError, match=env_var):
        create_provider(config, env={})


@pytest.mark.parametrize(
    ("provider", "env_var", "default_model"),
    [
        (ProviderName.OPENAI, "OPENAI_API_KEY", "gpt-4o-mini"),
        (ProviderName.ANTHROPIC, "ANTHROPIC_API_KEY", "claude-sonnet-5-5"),
        (ProviderName.GEMINI, "GOOGLE_API_KEY", "gemini-2.5-flash"),
    ],
)
def test_builds_keyed_provider_with_default_model(
    provider: ProviderName, env_var: str, default_model: str
) -> None:
    llm = create_provider(GitCommitAIConfig(llm_provider=provider), env={env_var: "test-key"})
    assert llm.name == provider.value
    assert llm.model == default_model


def test_model_override_wins_over_default() -> None:
    config = GitCommitAIConfig(llm_provider=ProviderName.OPENAI, llm_model="gpt-custom")
    assert create_provider(config, env={"OPENAI_API_KEY": "k"}).model == "gpt-custom"


def test_ollama_needs_no_key() -> None:
    llm = create_provider(GitCommitAIConfig(llm_provider=ProviderName.OLLAMA), env={})
    assert llm.name == "ollama"
    assert llm.model == "llama3.1"


def test_missing_sdk_raises_install_hint(monkeypatch: MonkeyPatch) -> None:
    def fail(name: str) -> None:
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", fail)
    config = GitCommitAIConfig(llm_provider=ProviderName.GEMINI)
    with pytest.raises(ProviderNotInstalledError, match=r"gitcommit-ai\[gemini\]"):
        create_provider(config, env={"GOOGLE_API_KEY": "k"})
