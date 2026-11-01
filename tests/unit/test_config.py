"""Unit tests for configuration loading and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from git_commit_ai.core.config import GitCommitAIConfig, ProviderName
from git_commit_ai.core.exceptions import ConfigurationError
from git_commit_ai.core.schemas import DiffMode


def test_config_defaults() -> None:
    """Verify default configuration parameters."""
    config = GitCommitAIConfig()
    assert config.history_commit_count == 10
    assert config.git_timeout_seconds == 30.0
    assert config.default_diff_mode == DiffMode.STAGED
    assert config.max_diff_size_bytes == 500_000


def test_config_custom_values() -> None:
    """Verify custom configuration instantiation."""
    config = GitCommitAIConfig(
        history_commit_count=25,
        git_timeout_seconds=15.0,
        default_diff_mode=DiffMode.UNSTAGED,
        max_diff_size_bytes=100_000,
    )
    assert config.history_commit_count == 25
    assert config.git_timeout_seconds == 15.0
    assert config.default_diff_mode == DiffMode.UNSTAGED
    assert config.max_diff_size_bytes == 100_000


def test_config_validation_errors() -> None:
    """Verify bounds and validation errors on invalid values."""
    with pytest.raises(ValidationError):
        GitCommitAIConfig(history_commit_count=0)  # ge=1

    with pytest.raises(ValidationError):
        GitCommitAIConfig(history_commit_count=101)  # le=100

    with pytest.raises(ValidationError):
        GitCommitAIConfig(git_timeout_seconds=0)  # gt=0

    with pytest.raises(ValidationError):
        GitCommitAIConfig(max_diff_size_bytes=0)  # gt=0


class TestLLMConfig:
    """Tests for provider and agent loop settings."""

    def test_defaults(self) -> None:
        config = GitCommitAIConfig()
        assert config.llm_provider is ProviderName.OPENAI
        assert config.llm_model is None
        assert config.max_iterations == 3

    def test_from_env_reads_known_variables(self) -> None:
        config = GitCommitAIConfig.from_env(
            {
                "GITCOMMIT_AI_PROVIDER": "ollama",
                "GITCOMMIT_AI_MODEL": "llama3.1",
                "GITCOMMIT_AI_TEMPERATURE": "0.7",
                "GITCOMMIT_AI_MAX_ITERATIONS": "5",
            }
        )
        assert config.llm_provider is ProviderName.OLLAMA
        assert config.llm_model == "llama3.1"
        assert config.llm_temperature == 0.7
        assert config.max_iterations == 5

    def test_from_env_ignores_empty_values(self) -> None:
        config = GitCommitAIConfig.from_env({"GITCOMMIT_AI_MODEL": ""})
        assert config.llm_model is None

    def test_from_env_names_the_offending_variable(self) -> None:
        with pytest.raises(ConfigurationError, match="GITCOMMIT_AI_PROVIDER"):
            GitCommitAIConfig.from_env({"GITCOMMIT_AI_PROVIDER": "skynet"})

    def test_from_env_rejects_out_of_range_iterations(self) -> None:
        with pytest.raises(ConfigurationError, match="GITCOMMIT_AI_MAX_ITERATIONS"):
            GitCommitAIConfig.from_env({"GITCOMMIT_AI_MAX_ITERATIONS": "0"})


class TestRetryConfig:
    def test_defaults(self) -> None:
        config = GitCommitAIConfig()
        assert (config.llm_max_retries, config.llm_retry_base_delay_seconds) == (3, 1.0)
        assert config.llm_retry_max_delay_seconds == 30.0

    def test_from_env(self) -> None:
        config = GitCommitAIConfig.from_env(
            {
                "GITCOMMIT_AI_MAX_RETRIES": "5",
                "GITCOMMIT_AI_RETRY_BASE_DELAY": "0.5",
                "GITCOMMIT_AI_RETRY_MAX_DELAY": "10",
            }
        )
        assert config.llm_max_retries == 5
        assert config.llm_retry_base_delay_seconds == 0.5
        assert config.llm_retry_max_delay_seconds == 10

    def test_retries_can_be_disabled(self) -> None:
        assert GitCommitAIConfig.from_env({"GITCOMMIT_AI_MAX_RETRIES": "0"}).llm_max_retries == 0

    def test_base_delay_may_not_exceed_max_delay(self) -> None:
        with pytest.raises(ValidationError, match="base delay"):
            GitCommitAIConfig(llm_retry_base_delay_seconds=60, llm_retry_max_delay_seconds=30)
