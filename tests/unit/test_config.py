"""Unit tests for configuration loading and validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from git_commit_ai.core.config import GitCommitAIConfig
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
