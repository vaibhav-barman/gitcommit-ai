"""Unit tests for provider selection."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from git_commit_ai.core.config import GitCommitAIConfig, ProviderName
from git_commit_ai.core.exceptions import MissingAPIKeyError
from git_commit_ai.llm import LLMMessage, Role, create_provider
from tests.fixtures.http import FakeHttp, json_response

if TYPE_CHECKING:
    pass


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
        create_provider(config, api_key=None)


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
    llm = create_provider(GitCommitAIConfig(llm_provider=provider), api_key="test-key")
    assert llm.name == provider.value
    assert llm.model == default_model


def test_model_override_wins_over_default() -> None:
    config = GitCommitAIConfig(llm_provider=ProviderName.OPENAI, llm_model="gpt-custom")
    assert create_provider(config, api_key="k").model == "gpt-custom"


def test_ollama_needs_no_key() -> None:
    llm = create_provider(GitCommitAIConfig(llm_provider=ProviderName.OLLAMA), api_key=None)
    assert llm.name == "ollama"
    assert llm.model == "llama3.1"


def test_an_empty_key_counts_as_missing() -> None:
    with pytest.raises(MissingAPIKeyError):
        create_provider(GitCommitAIConfig(llm_provider=ProviderName.ANTHROPIC), api_key="")


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        (ProviderName.OPENAI, "https://api.openai.com/v1/chat/completions"),
        (ProviderName.ANTHROPIC, "https://api.anthropic.com/v1/messages"),
        (
            ProviderName.GEMINI,
            "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent",
        ),
        (ProviderName.OLLAMA, "http://ollama.test:1/api/chat"),
    ],
)
def test_requests_go_through_the_given_transport(provider: ProviderName, url: str) -> None:
    replies = {
        ProviderName.OPENAI: {"choices": [{"message": {"content": "ok"}}]},
        ProviderName.ANTHROPIC: {"content": [{"type": "text", "text": "ok"}]},
        ProviderName.GEMINI: {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        ProviderName.OLLAMA: {"message": {"content": "ok"}},
    }
    http = FakeHttp().on("POST", url, json_response(200, replies[provider]))
    config = GitCommitAIConfig(
        llm_provider=provider, llm_model="m", ollama_base_url="http://ollama.test:1"
    )
    llm = create_provider(config, api_key="k", transport=http.transport)
    assert llm.complete([LLMMessage(role=Role.USER, content="hi")]) == "ok"
    assert len(http.calls) == 1
