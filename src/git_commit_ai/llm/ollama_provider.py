"""Ollama backend (``POST /api/chat``) for a local or remote server."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from git_commit_ai.core.exceptions import OllamaUnreachableError
from git_commit_ai.llm._common import dig, require_text, text_or_none
from git_commit_ai.llm.http import HttpClient

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from git_commit_ai.llm.base import LLMMessage


def ollama_headers(api_key: str | None) -> dict[str, str]:
    """A Bearer header when a key is set (Ollama Cloud, or a protected server)."""
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def sends_key_unencrypted(base_url: str) -> bool:
    """True for a plain ``http://`` URL to anything but this computer."""
    parts = urlsplit(base_url)
    if parts.scheme != "http":
        return False
    host = parts.hostname or ""
    if host == "localhost":
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


class OllamaProvider:
    """``LLMProvider`` backed by an Ollama server. A refused connection is reported as
    ``OllamaUnreachableError`` (not retried); timeouts and 5xx are transient. ``api_key``
    is sent as a Bearer token when given."""

    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str,
        temperature: float,
        timeout: float,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._http = HttpClient(
            self.name,
            base_url,
            ollama_headers(api_key),
            timeout,
            transport,
            secrets=(api_key,) if api_key else (),
            unreachable=lambda: OllamaUnreachableError(base_url),
        )

    @property
    def model(self) -> str:
        """Model identifier requests are sent to."""
        return self._model

    def complete(self, messages: Sequence[LLMMessage]) -> str:
        """Return the assistant reply for the conversation."""
        body = {
            "model": self._model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": self._temperature},
        }
        response = self._http.post_json("/api/chat", body, model=self._model)
        return require_text(self.name, text_or_none(dig(response, "message", "content")))
