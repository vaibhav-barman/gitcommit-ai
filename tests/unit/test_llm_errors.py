"""Unit tests for LLM-related exceptions."""

from __future__ import annotations

from git_commit_ai.core.exceptions import (
    AuthenticationError,
    GitCommitAIError,
    LLMError,
    MissingAPIKeyError,
    ModelNotAvailableError,
    OllamaUnreachableError,
    RateLimitError,
)


def test_all_llm_errors_share_a_base() -> None:
    errors: list[LLMError] = [
        MissingAPIKeyError("openai", "OPENAI_API_KEY"),
        AuthenticationError("openai", 401),
        ModelNotAvailableError("openai", "gpt-x"),
        OllamaUnreachableError("http://localhost:11434"),
        RateLimitError("anthropic"),
    ]
    assert all(isinstance(err, GitCommitAIError) for err in errors)


def test_missing_key_message_names_env_var() -> None:
    err = MissingAPIKeyError("openai", "OPENAI_API_KEY")
    assert "OPENAI_API_KEY" in str(err)
    assert err.provider == "openai"


def test_auth_error_names_the_provider_and_status_only() -> None:
    assert (
        str(AuthenticationError("anthropic", 401)) == "anthropic rejected the API key (HTTP 401)."
    )


def test_model_error_names_the_model() -> None:
    assert "gpt-x isn't available from openai" in str(ModelNotAvailableError("openai", "gpt-x"))


def test_rate_limit_includes_retry_hint_when_known() -> None:
    assert "30s" in str(RateLimitError("openai", retry_after=30))
    assert "Retry" not in str(RateLimitError("openai"))
