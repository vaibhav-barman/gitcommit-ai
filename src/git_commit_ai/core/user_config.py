"""The per-user settings file, ``config.toml``. It never holds secrets."""

from __future__ import annotations

import contextlib
import enum
import os
import tempfile
import tomllib
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

from git_commit_ai.core.config import CommitMode, ProviderName
from git_commit_ai.core.exceptions import ConfigurationError, UnsupportedConfigVersionError
from git_commit_ai.core.toml_writer import dumps

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA_VERSION = 1
CONFIG_FILE = "config.toml"
CONFIG_FILE_MODE = 0o644
DIRECTORY_MODE = 0o700
NEWER_VERSION_NOTE = (
    "config.toml was written by a newer gitcommit-ai; some settings may be ignored."
)


class CredentialStoreKind(enum.StrEnum):
    """Where saved API keys live."""

    FILE = "file"
    KEYRING = "keyring"


class ProviderPreferences(BaseModel):
    """Saved choices for one provider."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    model: str | None = None
    base_url: str | None = None


class GenerationOverrides(BaseModel):
    """Generation settings saved with ``config set``."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    temperature: float | None = None
    timeout: float | None = None
    max_iterations: int | None = None
    max_diff_chars: int | None = None
    max_retries: int | None = None
    retry_base_delay: float | None = None
    retry_max_delay: float | None = None


class UserConfig(BaseModel):
    """Contents of ``config.toml``. Ranges are validated after merging, by
    ``GitCommitAIConfig``; this model only checks types and names."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    schema_version: int = SCHEMA_VERSION
    provider: ProviderName | None = None
    commit_mode: CommitMode | None = None
    credential_store: CredentialStoreKind = CredentialStoreKind.FILE
    providers: dict[ProviderName, ProviderPreferences] = {}
    generation: GenerationOverrides = GenerationOverrides()

    @property
    def written_by_newer_version(self) -> bool:
        """True when a newer release wrote the file; such a file is never rewritten."""
        return self.schema_version > SCHEMA_VERSION

    def preferences(self, provider: ProviderName) -> ProviderPreferences:
        """Saved choices for ``provider`` (empty when there are none)."""
        return self.providers.get(provider, ProviderPreferences())

    def to_toml_data(self) -> dict[str, Any]:
        """The file contents as plain data, with unset values left out."""
        data: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
        if self.provider is not None:
            data["provider"] = self.provider.value
        if self.commit_mode is not None:
            data["commit_mode"] = self.commit_mode.value
        data["credential_store"] = self.credential_store.value
        data["providers"] = {
            name.value: prefs.model_dump(exclude_none=True)
            for name, prefs in self.providers.items()
        }
        data["generation"] = self.generation.model_dump(exclude_none=True)
        return data


def load_user_config(directory: Path) -> UserConfig | None:
    """Read ``config.toml`` from ``directory``; ``None`` when there is no file.

    Raises:
        ConfigurationError: If the file is not valid TOML or holds a bad value.
    """
    path = directory / CONFIG_FILE
    data = read_toml(path)
    if data is None:
        return None
    try:
        return UserConfig.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigurationError(
            f"{CONFIG_FILE}: {problems}. Fix {path}, or run git ai-commit reset."
        ) from exc


def unknown_keys(directory: Path) -> list[str]:
    """Dotted names in ``config.toml`` that this version does not read, sorted."""
    data = read_toml(directory / CONFIG_FILE) or {}
    found: list[str] = []
    for key, value in data.items():
        if key == "providers" and isinstance(value, dict):
            for name, table in value.items():
                if name not in {p.value for p in ProviderName}:
                    found.append(f"providers.{name}")
                elif isinstance(table, dict):
                    found += [
                        f"providers.{name}.{field}"
                        for field in table
                        if field not in ProviderPreferences.model_fields
                    ]
        elif key == "generation" and isinstance(value, dict):
            found += [
                f"generation.{field}"
                for field in value
                if field not in GenerationOverrides.model_fields
            ]
        elif key not in UserConfig.model_fields:
            found.append(key)
    return sorted(found)


def save_user_config(directory: Path, config: UserConfig) -> Path:
    """Write ``config`` to ``directory/config.toml`` atomically and return the path.

    Raises:
        UnsupportedConfigVersionError: If ``config`` came from a newer release.
    """
    path = directory / CONFIG_FILE
    if config.written_by_newer_version:
        raise UnsupportedConfigVersionError(str(path))
    atomic_write(path, dumps(config.to_toml_data()), CONFIG_FILE_MODE)
    return path


def read_toml(path: Path) -> dict[str, Any] | None:
    """Parse a TOML file, or return ``None`` if it does not exist.

    The error names the file and position only, never its contents, because
    the file may hold keys.

    Raises:
        ConfigurationError: If the file is not valid TOML.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(
            f"{path} is not valid TOML ({_position(exc)}). Fix it or run git ai-commit reset."
        ) from None


def atomic_write(path: Path, text: str, mode: int) -> None:
    """Replace ``path`` with ``text`` so readers see the old file or the new one, never a
    partial one. The temporary file is created owner-only in the same directory, given
    ``mode``, and moved into place. Any failure, Ctrl+C included, leaves no stray file."""
    path.parent.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _position(exc: tomllib.TOMLDecodeError) -> str:
    message = str(exc)
    start = message.find("(at ")
    return message[start + 4 : -1] if start != -1 and message.endswith(")") else message
