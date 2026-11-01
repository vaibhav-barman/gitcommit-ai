"""Saved API keys: the private credentials file and the opt-in keychain."""

from __future__ import annotations

import stat
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from git_commit_ai.core import credentials
from git_commit_ai.core.config import ProviderName
from git_commit_ai.core.credentials import (
    CREDENTIALS_FILE,
    FileCredentialStore,
    KeyringCredentialStore,
    keyring_available,
    migrate_keys,
    open_credential_store,
)
from git_commit_ai.core.exceptions import ConfigurationError
from git_commit_ai.core.user_config import CredentialStoreKind

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
# Built at runtime so no string that looks like a real key is committed.
KEY_A = "FAKE-" + "a" * 20 + "1111"
KEY_B = "FAKE-" + "b" * 20 + "2222"


def test_absent_file_has_no_keys(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path)
    assert store.get(ProviderName.OPENAI) is None
    assert store.saved_providers() == []


def test_set_get_delete(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path / "cfg")
    store.set(ProviderName.ANTHROPIC, KEY_A)
    assert store.get(ProviderName.ANTHROPIC) == KEY_A
    assert FileCredentialStore(tmp_path / "cfg").get(ProviderName.ANTHROPIC) == KEY_A
    store.delete(ProviderName.ANTHROPIC)
    assert store.get(ProviderName.ANTHROPIC) is None


def test_other_providers_keys_are_preserved(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path)
    store.set(ProviderName.ANTHROPIC, KEY_A)
    store.set(ProviderName.OPENAI, KEY_B)
    store.delete(ProviderName.OPENAI)
    assert store.get(ProviderName.ANTHROPIC) == KEY_A
    assert store.saved_providers() == [ProviderName.ANTHROPIC]


def test_saved_providers_follow_the_provider_order(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path)
    store.set(ProviderName.GEMINI, KEY_A)
    store.set(ProviderName.OPENAI, KEY_B)
    assert store.saved_providers() == [ProviderName.OPENAI, ProviderName.GEMINI]


def test_deleting_the_last_key_removes_the_file(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path)
    store.set(ProviderName.OPENAI, KEY_A)
    store.delete(ProviderName.OPENAI)
    assert not (tmp_path / CREDENTIALS_FILE).exists()


def test_unknown_entries_survive_a_write(tmp_path: Path) -> None:
    (tmp_path / CREDENTIALS_FILE).write_text(
        'schema_version = 1\n[keys]\nfuture = "kept"\n', encoding="utf-8"
    )
    FileCredentialStore(tmp_path).set(ProviderName.OPENAI, KEY_A)
    assert 'future = "kept"' in (tmp_path / CREDENTIALS_FILE).read_text(encoding="utf-8")


def test_invalid_file_is_a_configuration_error_without_its_contents(tmp_path: Path) -> None:
    (tmp_path / CREDENTIALS_FILE).write_text(f'[keys]\nopenai = "{KEY_A}\n', encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        FileCredentialStore(tmp_path).get(ProviderName.OPENAI)
    assert "credentials.toml is not valid TOML" in str(caught.value)
    assert KEY_A not in str(caught.value)


def test_a_non_string_key_is_a_configuration_error(tmp_path: Path) -> None:
    (tmp_path / CREDENTIALS_FILE).write_text("[keys]\nopenai = 5\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match=r"keys\.openai"):
        FileCredentialStore(tmp_path).get(ProviderName.OPENAI)


@posix_only
def test_file_is_0600_and_directory_0700(tmp_path: Path) -> None:
    directory = tmp_path / "cfg"
    FileCredentialStore(directory).set(ProviderName.OPENAI, KEY_A)
    assert stat.S_IMODE((directory / CREDENTIALS_FILE).stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


@posix_only
def test_a_readable_file_is_reported_and_still_used(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path)
    store.set(ProviderName.OPENAI, KEY_A)
    assert store.permission_problem() is None
    (tmp_path / CREDENTIALS_FILE).chmod(0o644)
    problem = store.permission_problem()
    assert problem is not None
    assert "readable by other users" in problem
    assert f"chmod 600 {tmp_path / CREDENTIALS_FILE}" in problem
    assert store.get(ProviderName.OPENAI) == KEY_A


@posix_only
def test_the_next_write_fixes_the_mode(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path)
    store.set(ProviderName.OPENAI, KEY_A)
    (tmp_path / CREDENTIALS_FILE).chmod(0o644)
    store.set(ProviderName.GEMINI, KEY_B)
    assert store.permission_problem() is None


def test_store_repr_never_shows_keys(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path)
    store.set(ProviderName.OPENAI, KEY_A)
    assert KEY_A not in repr(store)


class FakeKeyring:
    """In-memory stand-in for the ``keyring`` module's password functions."""

    def __init__(self) -> None:
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.passwords[(service, username)]


def test_keyring_store_round_trips_under_the_service_name() -> None:
    fake = FakeKeyring()
    store = KeyringCredentialStore(backend=fake)
    store.set(ProviderName.ANTHROPIC, KEY_A)
    assert fake.passwords == {("gitcommit-ai", "anthropic"): KEY_A}
    assert store.get(ProviderName.ANTHROPIC) == KEY_A
    assert store.saved_providers() == [ProviderName.ANTHROPIC]
    store.delete(ProviderName.ANTHROPIC)
    store.delete(ProviderName.ANTHROPIC)  # deleting a missing entry is not an error
    assert store.get(ProviderName.ANTHROPIC) is None


def test_keyring_store_kind() -> None:
    assert KeyringCredentialStore(backend=FakeKeyring()).kind is CredentialStoreKind.KEYRING


def test_keyring_not_importable_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "keyring", None)
    assert keyring_available() is False
    with pytest.raises(ConfigurationError, match="keychain"):
        KeyringCredentialStore()


def test_fail_and_null_backends_are_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring
    from keyring.backends import fail, null

    unusable: list[type[Any]] = [fail.Keyring, null.Keyring]
    for backend_class in unusable:
        backend = backend_class()
        monkeypatch.setattr(keyring, "get_keyring", lambda backend=backend: backend)
        assert keyring_available() is False


def test_a_real_backend_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring

    monkeypatch.setattr(keyring, "get_keyring", lambda: types.SimpleNamespace(priority=1))
    assert keyring_available() is True


def test_open_credential_store_picks_by_kind(tmp_path: Path) -> None:
    assert isinstance(
        open_credential_store(tmp_path, CredentialStoreKind.FILE), FileCredentialStore
    )


def test_keyring_errors_become_configuration_errors() -> None:
    from keyring.errors import KeyringError

    class Broken(FakeKeyring):
        def get_password(self, service: str, username: str) -> str | None:
            raise KeyringError("locked")

    with pytest.raises(ConfigurationError, match="keychain"):
        KeyringCredentialStore(backend=Broken()).get(ProviderName.OPENAI)


def test_module_exposes_the_service_name() -> None:
    assert credentials.KEYRING_SERVICE == "gitcommit-ai"


def test_migrate_keys_moves_every_key_then_clears_the_source(tmp_path: Path) -> None:
    source = FileCredentialStore(tmp_path)
    source.set(ProviderName.OPENAI, KEY_A)
    source.set(ProviderName.GEMINI, KEY_B)
    destination = KeyringCredentialStore(backend=FakeKeyring())
    migrate_keys(source, destination)
    assert destination.get(ProviderName.OPENAI) == KEY_A
    assert destination.get(ProviderName.GEMINI) == KEY_B
    assert source.saved_providers() == []


def test_migrate_keys_leaves_the_source_alone_if_writing_fails(tmp_path: Path) -> None:
    source = FileCredentialStore(tmp_path)
    source.set(ProviderName.OPENAI, KEY_A)

    class Locked(FakeKeyring):
        def set_password(self, service: str, username: str, password: str) -> None:
            raise RuntimeError("locked")

    with pytest.raises(ConfigurationError):
        migrate_keys(source, KeyringCredentialStore(backend=Locked()))
    assert source.get(ProviderName.OPENAI) == KEY_A
