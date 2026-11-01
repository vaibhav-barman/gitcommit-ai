"""The Ollama part of setup: this computer, Ollama Cloud, or another server."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from git_commit_ai.core.config import ProviderName
from git_commit_ai.llm.ollama_provider import sends_key_unencrypted
from git_commit_ai.llm.registry import spec
from git_commit_ai.onboarding.common import (
    ChooseProvider,
    Credentials,
    cancel,
    offer_existing_key,
)
from git_commit_ai.onboarding.prompts import Choice

if TYPE_CHECKING:
    from git_commit_ai.core.settings import Settings
    from git_commit_ai.onboarding.common import CatalogFactory, OllamaDetector
    from git_commit_ai.onboarding.prompts import Prompter

LOCAL_URL = spec(ProviderName.OLLAMA).base_url
CLOUD_URL = "https://ollama.com"
KEY_URL = spec(ProviderName.OLLAMA).key_url
INSTALL_HINT = (
    "Install Ollama from https://ollama.com/download, then start it with `ollama serve`\n"
    "(or open the Ollama app)."
)
PULL_HINT = (
    "No chat models are pulled yet. Pull one in another terminal, for example:\n"
    "    ollama pull <model>          browse models at https://ollama.com/library"
)


class OllamaLocation(enum.Enum):
    """Where Ollama runs."""

    LOCAL = "local"
    CLOUD = "cloud"
    OTHER = "other"


class ServerAction(enum.Enum):
    """Answers to "What now?" when a server is unreachable or has no models."""

    CHECK_AGAIN = "check"
    OTHER_URL = "url"
    TYPE_NAME = "type"
    OTHER_PROVIDER = "provider"
    CANCEL = "cancel"


def normalize_url(text: str) -> tuple[str | None, str | None]:
    """Return ``(url, None)`` with any trailing ``/`` removed, or ``(None, problem)``."""
    url = text.strip().rstrip("/")
    if not url:
        return None, "Enter a URL."
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None, "Include http:// or https://, for example http://192.168.1.20:11434"
    return url, None


class OllamaFlow:
    """Find the server, and its key if it needs one."""

    def __init__(
        self,
        prompter: Prompter,
        settings: Settings,
        catalogs: CatalogFactory,
        detector: OllamaDetector,
    ) -> None:
        self._prompter = prompter
        self._settings = settings
        self._catalogs = catalogs
        self._detector = detector

    def choose_server(self) -> Credentials:
        """Ask where Ollama runs and return how to reach it."""
        current = self._settings.config.ollama_base_url
        default = (
            OllamaLocation.LOCAL
            if current == LOCAL_URL
            else OllamaLocation.CLOUD
            if current == CLOUD_URL
            else OllamaLocation.OTHER
        )
        where = self._prompter.select(
            "Where does Ollama run?",
            [
                Choice(OllamaLocation.LOCAL, "On this computer", LOCAL_URL),
                Choice(OllamaLocation.CLOUD, "Ollama Cloud", "https://ollama.com · API key"),
                Choice(OllamaLocation.OTHER, "Another server", "enter its URL"),
            ],
            default=default,
        )
        if where is OllamaLocation.LOCAL:
            return self._find(LOCAL_URL, key=None, new_key=None)
        if where is OllamaLocation.CLOUD:
            existing = offer_existing_key(
                self._prompter, self._settings, ProviderName.OLLAMA, "Ollama"
            )
            if existing:
                return self._credentials(CLOUD_URL, existing, None)
            key = self.ask_key()
            return self._credentials(CLOUD_URL, key, key)
        return self._find(self._ask_url(), key=self._existing_key(), new_key=None)

    def ask_key(self) -> str:
        """Ask for an Ollama Cloud key (checked later by the test request)."""
        self._prompter.note(f"Create a key at {KEY_URL}")
        while True:
            key = self._prompter.secret("Ollama API key:")
            if key:
                return key
            self._prompter.note("Nothing was entered.")

    def no_models(self) -> ServerAction:
        """Ask what to do when the server has no chat models."""
        self._prompter.note(PULL_HINT)
        return self._prompter.select(
            "What now?",
            [
                Choice(ServerAction.CHECK_AGAIN, "I've pulled a model; check again"),
                Choice(ServerAction.TYPE_NAME, "Type a model name"),
                Choice(ServerAction.OTHER_PROVIDER, "Choose a different provider"),
                Choice(ServerAction.CANCEL, "Cancel setup"),
            ],
        )

    def _find(self, url: str, key: str | None, new_key: str | None) -> Credentials:
        while True:
            with self._prompter.status(f"Looking for Ollama at {url}") as done:
                server = self._detector(url, key)
                if server.reachable and not server.needs_key:
                    done(f"found (version {server.version})" if server.version else "found")
                elif server.needs_key:
                    done("found, but it needs a key")
                else:
                    done(f"not reachable ({server.error}).")
            if server.reachable and not server.needs_key:
                return self._credentials(url, key, new_key)
            if server.needs_key:
                new_key = key = self._prompter.secret("This server needs a key. Key:")
                continue
            if url == LOCAL_URL:
                self._prompter.note(INSTALL_HINT)
            action = self._prompter.select(
                "What now?",
                [
                    Choice(ServerAction.CHECK_AGAIN, "Check again"),
                    Choice(ServerAction.OTHER_URL, "Use a different URL"),
                    Choice(ServerAction.OTHER_PROVIDER, "Choose a different provider"),
                    Choice(ServerAction.CANCEL, "Cancel setup"),
                ],
            )
            if action is ServerAction.OTHER_URL:
                url = self._ask_url()
            elif action is ServerAction.OTHER_PROVIDER:
                raise ChooseProvider
            elif action is ServerAction.CANCEL:
                raise cancel()

    def _ask_url(self) -> str:
        url = self._prompter.text("Ollama URL:", validate=lambda text: normalize_url(text)[1])
        normalized = normalize_url(url)[0] or url
        if sends_key_unencrypted(normalized):
            self._prompter.note(
                f"Requests to {normalized} are not encrypted (plain http://), so anything "
                "sent to it, a key included, can be read on the network."
            )
        return normalized

    def _existing_key(self) -> str | None:
        found = self._settings.api_key_for(ProviderName.OLLAMA)
        return found.value if found else None

    def _credentials(self, url: str, key: str | None, new_key: str | None) -> Credentials:
        catalog = self._catalogs(ProviderName.OLLAMA, key, url)
        return Credentials(ProviderName.OLLAMA, url, key, new_key, catalog)
