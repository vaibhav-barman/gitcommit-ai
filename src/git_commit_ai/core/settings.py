"""Layered settings: flags > process environment > ``.env`` file > ``config.toml`` > defaults."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import dotenv_values

from git_commit_ai.core.config import (
    ENV_FIELDS,
    PROVIDER_KEY_VARIABLES,
    GitCommitAIConfig,
    ProviderName,
    config_key,
)
from git_commit_ai.core.exceptions import ConfigurationError
from git_commit_ai.core.user_config import CONFIG_FILE, CredentialStoreKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from git_commit_ai.core.credentials import CredentialStore
    from git_commit_ai.core.user_config import UserConfig

ENV_FILE_VARIABLE = "GITCOMMIT_AI_ENV_FILE"
_DEFAULT_PROVIDER: ProviderName = GitCommitAIConfig.model_fields["llm_provider"].default
_DOTENV_NAME = ".env"
_MASK_VISIBLE = 4
_MASK_MIN_LENGTH = 8


class SettingSource(enum.StrEnum):
    """Where an effective setting came from."""

    FLAG = "flag"
    ENVIRONMENT = "environment"
    DOTENV = ".env"
    USER_CONFIG = "config file"
    CREDENTIALS_FILE = "credentials file"
    KEYCHAIN = "keychain"
    DEFAULT = "default"


@dataclass(frozen=True)
class ResolvedKey:
    """An API key and where it was found. The key itself is left out of ``repr``."""

    value: str = field(repr=False)
    source: SettingSource
    variable: str | None


@dataclass(frozen=True)
class Settings:
    """The effective configuration plus where each value came from.

    Attributes:
        config: Validated application configuration.
        environment: Process environment overlaid on ``.env`` values. It is never
            written back to ``os.environ``.
        process_env: Variables that were set in the real environment.
        dotenv: Variables read from the ``.env`` file.
        dotenv_path: The ``.env`` file that was loaded, if any.
        user_config: The saved ``config.toml``, if there is one.
        credentials: Where saved API keys are read from, if anywhere.
        flag_fields: Config fields overridden by command-line flags.
        user_config_fields: Config fields whose value came from ``config.toml``.

    The three variable mappings can hold API keys, so they are left out of ``repr``.
    """

    config: GitCommitAIConfig
    environment: Mapping[str, str] = field(repr=False)
    process_env: Mapping[str, str] = field(repr=False)
    dotenv: Mapping[str, str] = field(repr=False)
    dotenv_path: Path | None
    user_config: UserConfig | None = None
    credentials: CredentialStore | None = None
    flag_fields: frozenset[str] = field(default_factory=frozenset)
    user_config_fields: frozenset[str] = field(default_factory=frozenset)

    def with_flags(self, **overrides: object) -> Settings:
        """Apply command-line flags on top; ``None`` values mean "flag not given".

        A ``--provider`` flag without ``--model`` swaps in the model saved for that
        provider, so a model saved for one provider is never sent to another.
        """
        given = {name: value for name, value in overrides.items() if value is not None}
        merged = {**self.config.model_dump(), **given}
        saved_fields = self.user_config_fields - frozenset(given)
        provider = given.get("llm_provider")
        model_is_open = self.source_of_field("llm_model") in (
            SettingSource.USER_CONFIG,
            SettingSource.DEFAULT,
        )
        if isinstance(provider, ProviderName) and "llm_model" not in given and model_is_open:
            saved_model = (
                self.user_config.preferences(provider).model if self.user_config else None
            )
            merged["llm_model"] = saved_model
            saved_fields = (
                saved_fields | {"llm_model"} if saved_model else saved_fields - {"llm_model"}
            )
        return Settings(
            config=GitCommitAIConfig.model_validate(merged),
            environment=self.environment,
            process_env=self.process_env,
            dotenv=self.dotenv,
            dotenv_path=self.dotenv_path,
            user_config=self.user_config,
            credentials=self.credentials,
            flag_fields=self.flag_fields | frozenset(given),
            user_config_fields=saved_fields,
        )

    def api_key_for(self, provider: ProviderName) -> ResolvedKey | None:
        """``provider``'s API key: a key variable in the environment, then in ``.env``
        (each in canonical-name order), then the credential store."""
        variables = PROVIDER_KEY_VARIABLES[provider]
        for layer, source in (
            (self.process_env, SettingSource.ENVIRONMENT),
            (self.dotenv, SettingSource.DOTENV),
        ):
            for variable in variables:
                if layer.get(variable):
                    return ResolvedKey(layer[variable], source, variable)
        if self.credentials is None:
            return None
        saved = self.credentials.get(provider)
        if not saved:
            return None
        source = (
            SettingSource.KEYCHAIN
            if self.credentials.kind is CredentialStoreKind.KEYRING
            else SettingSource.CREDENTIALS_FILE
        )
        return ResolvedKey(saved, source, None)

    def provider_configured_by_environment(self) -> bool:
        """True if the environment or ``.env`` names a provider or holds any provider key.
        Such setups predate the config file, so they never trigger first-run setup."""
        variables = ["GITCOMMIT_AI_PROVIDER"]
        variables += [v for names in PROVIDER_KEY_VARIABLES.values() for v in names]
        return any(self.environment.get(variable) for variable in variables)

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
        source = self.source_of_variable(variable) if variable else SettingSource.DEFAULT
        if source is SettingSource.DEFAULT and config_field in self.user_config_fields:
            return SettingSource.USER_CONFIG
        return source


def load_settings(
    env: Mapping[str, str],
    start_dir: Path | None = None,
    repo_root: Path | None = None,
    user_config: UserConfig | None = None,
    credentials: CredentialStore | None = None,
) -> Settings:
    """Load settings from ``env``, a ``.env`` file in one of two places, and the saved
    ``user_config``, which ranks below both.

    Only ``start_dir`` (default: the current directory) and ``repo_root`` are
    searched, in that order. Parent directories, the home directory and anything
    above the repository root are never looked at, so a stray ``.env`` elsewhere
    cannot change the configuration. Set ``GITCOMMIT_AI_ENV_FILE`` to a path to
    use that file instead, or to an empty string to skip ``.env`` loading.
    Real environment variables always win over ``.env`` values. The saved model is the
    one for the effective provider, so a model saved for another provider is never used.

    Raises:
        ConfigurationError: If a value is invalid or the named env file is missing.
    """
    dotenv_path = _locate_dotenv(env, start_dir or Path.cwd(), repo_root)
    file_values = _read_dotenv(dotenv_path) if dotenv_path else {}
    process_env = {key: value for key, value in env.items() if value}
    environment = {**file_values, **process_env}
    env_values = {f: environment[v] for v, f in ENV_FIELDS.items() if environment.get(v)}
    saved_values = _saved_values(user_config, env_values.get("llm_provider"))
    labels = {f: f"{CONFIG_FILE}: {config_key(v)}" for v, f in ENV_FIELDS.items()}
    labels.update({f: v for v, f in ENV_FIELDS.items() if f in env_values})
    return Settings(
        config=GitCommitAIConfig.from_values({**saved_values, **env_values}, labels),
        environment=environment,
        process_env=process_env,
        dotenv=file_values,
        dotenv_path=dotenv_path,
        user_config=user_config,
        credentials=credentials,
        user_config_fields=frozenset(saved_values) - frozenset(env_values),
    )


def mask_secret(value: str) -> str:
    """Hide all but the last few characters of a secret."""
    if len(value) < _MASK_MIN_LENGTH:
        return "*" * len(value)
    return "*" * _MASK_MIN_LENGTH + value[-_MASK_VISIBLE:]


def _saved_values(user_config: UserConfig | None, env_provider: str | None) -> dict[str, object]:
    """Config fields set by ``config.toml``, keyed by ``GitCommitAIConfig`` field name."""
    if user_config is None:
        return {}
    values: dict[str, object] = {}
    if user_config.provider is not None:
        values["llm_provider"] = user_config.provider
    provider = _provider_or_none(env_provider) or user_config.provider or _DEFAULT_PROVIDER
    model = user_config.preferences(provider).model
    if model:
        values["llm_model"] = model
    ollama_url = user_config.preferences(ProviderName.OLLAMA).base_url
    if ollama_url:
        values["ollama_base_url"] = ollama_url
    for key, value in user_config.generation.model_dump(exclude_none=True).items():
        values[ENV_FIELDS[f"GITCOMMIT_AI_{key.upper()}"]] = value
    return values


def _provider_or_none(name: str | None) -> ProviderName | None:
    try:
        return ProviderName(name) if name else None
    except ValueError:  # reported by validation, with the variable's name
        return None


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
