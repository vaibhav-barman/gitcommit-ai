"""Ollama backend (``POST /api/chat``) for a local or remote server."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_commit_ai.core.exceptions import OllamaUnreachableError
from git_commit_ai.llm._common import dig, require_text, text_or_none
from git_commit_ai.llm.http import HttpClient

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from git_commit_ai.llm.base import LLMMessage


class OllamaProvider:
    """``LLMProvider`` backed by an Ollama server. A refused connection is reported as
    ``OllamaUnreachableError`` (not retried); timeouts and 5xx are transient."""

    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str,
        temperature: float,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._http = HttpClient(
            self.name,
            base_url,
            {},
            timeout,
            transport,
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
