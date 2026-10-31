"""Pydantic-based configuration for GitCommit AI."""

from __future__ import annotations

from pydantic import BaseModel, Field

from git_commit_ai.core.schemas import DiffMode


class GitCommitAIConfig(BaseModel):
    """Application configuration with sensible defaults.

    Phase 1 configuration covers Git intelligence parameters only.
    LLM and agent configuration will be added in later phases.
    """

    history_commit_count: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of historical commits to retrieve",
    )
    git_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Timeout in seconds for Git subprocess commands",
    )
    default_diff_mode: DiffMode = Field(
        default=DiffMode.STAGED,
        description="Default mode for diff extraction (staged or unstaged)",
    )
    max_diff_size_bytes: int = Field(
        default=500_000,
        gt=0,
        description="Maximum diff size in bytes before emitting a warning",
    )
