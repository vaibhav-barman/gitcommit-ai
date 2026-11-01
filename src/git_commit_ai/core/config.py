"""Pydantic-based configuration for GitCommit AI."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError

from git_commit_ai.core.exceptions import ConfigurationError
from git_commit_ai.core.schemas import DiffMode

if TYPE_CHECKING:
    from collections.abc import Mapping


class ProviderName(enum.StrEnum):
    """Supported LLM backends."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"


_ENV_FIELDS: dict[str, str] = {
    "GITCOMMIT_AI_PROVIDER": "llm_provider",
    "GITCOMMIT_AI_MODEL": "llm_model",
    "GITCOMMIT_AI_TEMPERATURE": "llm_temperature",
    "GITCOMMIT_AI_TIMEOUT": "llm_timeout_seconds",
    "GITCOMMIT_AI_OLLAMA_URL": "ollama_base_url",
    "GITCOMMIT_AI_MAX_ITERATIONS": "max_iterations",
    "GITCOMMIT_AI_MAX_DIFF_CHARS": "max_prompt_diff_chars",
}


class GitCommitAIConfig(BaseModel):
    """Application configuration with sensible defaults.

    Covers Git intelligence parameters, LLM provider selection, and the
    agent reflection loop. API keys are deliberately not stored here; they
    are read from the environment when a provider is created.
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
    llm_provider: ProviderName = Field(
        default=ProviderName.OPENAI,
        description="LLM backend used for generation",
    )
    llm_model: str | None = Field(
        default=None,
        description="Model identifier; the provider default is used when unset",
    )
    llm_temperature: float = Field(
        default=0.3,
        ge=0,
        le=2,
        description="Sampling temperature for generation",
    )
    llm_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Timeout in seconds for a single LLM request",
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Base URL of the Ollama server",
    )
    max_iterations: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum generator/critic rounds before falling back to the best draft",
    )
    max_prompt_diff_chars: int = Field(
        default=24_000,
        ge=1_000,
        description="Character budget for diff content included in LLM prompts",
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> GitCommitAIConfig:
        """Build a configuration from ``GITCOMMIT_AI_*`` environment variables.

        Variables that are unset or empty keep their defaults.

        Raises:
            ConfigurationError: If a variable holds an invalid value.
        """
        values = {field: env[var] for var, field in _ENV_FIELDS.items() if env.get(var)}
        try:
            return cls.model_validate(values)
        except ValidationError as exc:
            problems = "; ".join(
                f"{_env_var_for(str(err['loc'][0]))}: {err['msg']}" for err in exc.errors()
            )
            raise ConfigurationError(f"Invalid configuration: {problems}") from exc


def _env_var_for(field: str) -> str:
    """Return the environment variable that populates a config field."""
    return next((var for var, name in _ENV_FIELDS.items() if name == field), field)
