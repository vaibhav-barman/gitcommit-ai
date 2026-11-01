"""Layered settings: flags > process environment > ``.env`` file > defaults."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import dotenv_values

from git_commit_ai.core.config import ENV_FIELDS, GitCommitAIConfig
from git_commit_ai.core.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Mapping

ENV_FILE_VARIABLE = "GITCOMMIT_AI_ENV_FILE"
_DOTENV_NAME = ".env"
_MASK_VISIBLE = 4
_MASK_MIN_LENGTH = 8


class SettingSource(enum.StrEnum):
    """Where an effective setting came from."""

    FLAG = "flag"
    ENVIRONMENT = "environment"
    DOTENV = ".env"
    DEFAULT = "default"


@dataclass(frozen=True)
class Settings:
    """The effective configuration plus where each value came from.

    Attributes:
        config: Validated application configuration.
        environment: Process environment overlaid on ``.env`` values. API keys
            are read from here. It is never written back to ``os.environ``.
        process_env: Variables that were set in the real environment.
        dotenv: Variables read from the ``.env`` file.
        dotenv_path: The ``.env`` file that was loaded, if any.
        flag_fields: Config fields overridden by command-line flags.
    """

    config: GitCommitAIConfig
    environment: Mapping[str, str]
    process_env: Mapping[str, str]
    dotenv: Mapping[str, str]
    dotenv_path: Path | None
    flag_fields: frozenset[str] = field(default_factory=frozenset)

    def with_flags(self, **overrides: object) -> Settings:
        """Apply command-line flags on top; ``None`` values mean "flag not given"."""
        given = {name: value for name, value in overrides.items() if value is not None}
        merged = {**self.config.model_dump(), **given}
        return Settings(
            config=GitCommitAIConfig.model_validate(merged),
            environment=self.environment,
            process_env=self.process_env,
            dotenv=self.dotenv,
            dotenv_path=self.dotenv_path,
            flag_fields=self.flag_fields | frozenset(given),
        )

    def source_of_variable(self, name: str) -> SettingSource:
        """Source of an environment-style variable such as ``OPENAI_API_KEY``."""
        if self.process_env.get(name):
            return SettingSource.ENVIRONMENT
        if self.dotenv.get(name):
            return SettingSource.DOTENV
        return SettingSource.DEFAULT

    def source_of_field(self, config_field: str) -> SettingSource:
        """Source of a ``GitCommitAIConfig`` field."""
        if config_field in self.flag_fields:
            return SettingSource.FLAG
        variable = next((var for var, name in ENV_FIELDS.items() if name == config_field), None)
        return self.source_of_variable(variable) if variable else SettingSource.DEFAULT


def load_settings(
    env: Mapping[str, str],
    start_dir: Path | None = None,
    repo_root: Path | None = None,
) -> Settings:
    """Load settings from ``env`` and a ``.env`` file in one of two places.

    Only ``start_dir`` (default: the current directory) and ``repo_root`` are
    searched, in that order. Parent directories, the home directory and anything
    above the repository root are never looked at, so a stray ``.env`` elsewhere
    cannot change the configuration. Set ``GITCOMMIT_AI_ENV_FILE`` to a path to
    use that file instead, or to an empty string to skip ``.env`` loading.
    Real environment variables always win over ``.env`` values.

    Raises:
        ConfigurationError: If a value is invalid or the named env file is missing.
    """
    dotenv_path = _locate_dotenv(env, start_dir or Path.cwd(), repo_root)
    file_values = _read_dotenv(dotenv_path) if dotenv_path else {}
    process_env = {key: value for key, value in env.items() if value}
    environment = {**file_values, **process_env}
    return Settings(
        config=GitCommitAIConfig.from_env(environment),
        environment=environment,
        process_env=process_env,
        dotenv=file_values,
        dotenv_path=dotenv_path,
    )


def mask_secret(value: str) -> str:
    """Hide all but the last few characters of a secret."""
    if len(value) < _MASK_MIN_LENGTH:
        return "*" * len(value)
    return "*" * _MASK_MIN_LENGTH + value[-_MASK_VISIBLE:]


def _locate_dotenv(env: Mapping[str, str], start_dir: Path, repo_root: Path | None) -> Path | None:
    if ENV_FILE_VARIABLE in env:
        explicit = env[ENV_FILE_VARIABLE]
        if not explicit:
            return None
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigurationError(f"{ENV_FILE_VARIABLE} points to a missing file: {path}")
        return path
    searched = (start_dir, repo_root) if repo_root else (start_dir,)
    for directory in searched:
        candidate = directory / _DOTENV_NAME
        if candidate.is_file():
            return candidate
    return None


def _read_dotenv(path: Path) -> dict[str, str]:
    return {key: value for key, value in dotenv_values(path).items() if value}
