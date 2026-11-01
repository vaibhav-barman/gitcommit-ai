"""Unit tests for LLM-related exceptions."""

from __future__ import annotations

from git_commit_ai.core.exceptions import (
    GitCommitAIError,
    LLMError,
    MissingAPIKeyError,
    OllamaUnreachableError,
    ProviderNotInstalledError,
    RateLimitError,
)


def test_all_llm_errors_share_a_base() -> None:
    errors: list[LLMError] = [
        MissingAPIKeyError("openai", "OPENAI_API_KEY"),
        ProviderNotInstalledError("openai", "openai"),
        OllamaUnreachableError("http://localhost:11434"),
        RateLimitError("anthropic"),
    ]
    assert all(isinstance(err, GitCommitAIError) for err in errors)


def test_missing_key_message_names_env_var() -> None:
    err = MissingAPIKeyError("openai", "OPENAI_API_KEY")
    assert "OPENAI_API_KEY" in str(err)
    assert err.provider == "openai"


def test_not_installed_message_gives_install_command() -> None:
    message = str(ProviderNotInstalledError("gemini", "gemini"))
    assert "pip install 'gitcommit-ai[gemini]'" in message
    assert "pipx install --force 'gitcommit-ai[gemini]'" in message


def test_rate_limit_includes_retry_hint_when_known() -> None:
    assert "30s" in str(RateLimitError("openai", retry_after=30))
    assert "Retry" not in str(RateLimitError("openai"))
