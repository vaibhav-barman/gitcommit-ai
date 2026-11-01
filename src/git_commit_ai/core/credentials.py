"""Saved API keys: a private ``credentials.toml`` by default, the OS keychain on request.

Keys never go in ``config.toml``, so that file can be shown and shared safely. The
keychain is opt-in because it can show a permission dialog, and a dialog must never
block ``git commit`` through the hook.
"""

from __future__ import annotations

import contextlib
import stat
import sys
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from git_commit_ai.core.config import ProviderName
from git_commit_ai.core.exceptions import ConfigurationError
from git_commit_ai.core.toml_writer import dumps
from git_commit_ai.core.user_config import (
    SCHEMA_VERSION,
    CredentialStoreKind,
    atomic_write,
    read_toml,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

CREDENTIALS_FILE = "credentials.toml"
CREDENTIALS_FILE_MODE = 0o600
KEYRING_SERVICE = "gitcommit-ai"
_T = TypeVar("_T")


class CredentialStore(Protocol):
    """Somewhere API keys are saved, one per provider."""

    @property
    def kind(self) -> CredentialStoreKind:
        """Which kind of store this is."""
        ...

    def get(self, provider: ProviderName) -> str | None:
        """The saved key for ``provider``, if any."""
        ...

    def set(self, provider: ProviderName, key: str) -> None:
        """Save ``key`` for ``provider``, replacing any earlier one."""
        ...

    def delete(self, provider: ProviderName) -> None:
        """Forget ``provider``'s key; a missing key is not an error."""
        ...

    def saved_providers(self) -> list[ProviderName]:
        """Providers that have a saved key, in ``ProviderName`` order."""
        ...


class FileCredentialStore:
    """Keys in ``<directory>/credentials.toml``, readable only by the owner (mode 0600).

    On Windows the file inherits the per-user permissions of ``%APPDATA%``.
    """

    def __init__(self, directory: Path) -> None:
        self._path = directory / CREDENTIALS_FILE

    def __repr__(self) -> str:
        return f"FileCredentialStore({str(self._path)!r})"

    @property
    def kind(self) -> CredentialStoreKind:
        """Always ``FILE``."""
        return CredentialStoreKind.FILE

    @property
    def path(self) -> Path:
        """The credentials file."""
        return self._path

    def get(self, provider: ProviderName) -> str | None:
        """The saved key for ``provider``, if any."""
        return self._keys().get(provider.value)

    def set(self, provider: ProviderName, key: str) -> None:
        """Save ``key`` for ``provider``, keeping every other entry."""
        keys = self._raw_keys()
        keys[provider.value] = key
        self._write(keys)

    def delete(self, provider: ProviderName) -> None:
        """Forget ``provider``'s key; the file goes away with its last key."""
        keys = self._raw_keys()
        if keys.pop(provider.value, None) is None:
            return
        if keys:
            self._write(keys)
        else:
            with contextlib.suppress(FileNotFoundError):
                self._path.unlink()

    def saved_providers(self) -> list[ProviderName]:
        """Providers with a saved key."""
        keys = self._keys()
        return [provider for provider in ProviderName if provider.value in keys]

    def permission_problem(self) -> str | None:
        """A warning when other users can read the file (POSIX only), else ``None``."""
        if sys.platform == "win32":
            return None
        try:
            mode = stat.S_IMODE(self._path.stat().st_mode)
        except FileNotFoundError:
            return None
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            return f"{CREDENTIALS_FILE} is readable by other users; run chmod 600 {self._path}"
        return None

    def _keys(self) -> dict[str, str]:
        keys = self._raw_keys()
        for name, value in keys.items():
            if not isinstance(value, str):
                raise ConfigurationError(
                    f"{self._path}: keys.{name} must be a string. "
                    "Replace it with git ai-commit key, or run git ai-commit reset."
                )
        return keys

    def _raw_keys(self) -> dict[str, Any]:
        data = read_toml(self._path) or {}
        keys = data.get("keys", {})
        if not isinstance(keys, dict):
            raise ConfigurationError(f"{self._path}: [keys] must be a table.")
        return keys

    def _write(self, keys: dict[str, Any]) -> None:
        text = dumps({"schema_version": SCHEMA_VERSION, "keys": keys})
        atomic_write(self._path, text, CREDENTIALS_FILE_MODE)


class _KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


class KeyringCredentialStore:
    """Keys in the OS keychain under service ``gitcommit-ai``, username ``<provider>``.

    Needs the ``keyring`` extra. ``backend`` is the ``keyring`` module unless a test
    passes a stand-in.

    Raises:
        ConfigurationError: If no usable keychain is available, or it fails.
    """

    def __init__(self, backend: _KeyringBackend | None = None) -> None:
        if backend is None:
            if not keyring_available():
                raise ConfigurationError(
                    "The system keychain isn't available. Install gitcommit-ai[keyring], "
                    "or set credential_store back to file with git ai-commit config set."
                )
            import keyring

            backend = keyring
        self._backend = backend

    def __repr__(self) -> str:
        return "KeyringCredentialStore()"

    @property
    def kind(self) -> CredentialStoreKind:
        """Always ``KEYRING``."""
        return CredentialStoreKind.KEYRING

    def get(self, provider: ProviderName) -> str | None:
        """The saved key for ``provider``, if any."""
        return self._call(lambda: self._backend.get_password(KEYRING_SERVICE, provider.value))

    def set(self, provider: ProviderName, key: str) -> None:
        """Save ``key`` for ``provider``."""
        self._call(lambda: self._backend.set_password(KEYRING_SERVICE, provider.value, key))

    def delete(self, provider: ProviderName) -> None:
        """Forget ``provider``'s key; a missing entry is not an error."""
        if self.get(provider) is not None:
            self._call(lambda: self._backend.delete_password(KEYRING_SERVICE, provider.value))

    def saved_providers(self) -> list[ProviderName]:
        """Providers with a saved key."""
        return [provider for provider in ProviderName if self.get(provider) is not None]

    def _call(self, operation: Callable[[], _T]) -> _T:
        try:
            return operation()
        except Exception as exc:  # keyring backends raise many unrelated error types
            raise ConfigurationError(
                f"The system keychain failed ({type(exc).__name__})."
            ) from None


def keyring_available() -> bool:
    """True if ``keyring`` is installed and has a real backend (not fail or null)."""
    try:
        import keyring
        from keyring.backends import fail, null
    except ImportError:
        return False
    backend = keyring.get_keyring()
    return not isinstance(backend, fail.Keyring | null.Keyring)


def open_credential_store(directory: Path, kind: CredentialStoreKind) -> CredentialStore:
    """The store ``config.toml`` asks for."""
    if kind is CredentialStoreKind.KEYRING:
        return KeyringCredentialStore()
    return FileCredentialStore(directory)
