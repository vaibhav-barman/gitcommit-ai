"""Ollama local-server backend."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import ollama
from ollama import Client

from git_commit_ai.core.exceptions import (
    LLMError,
    OllamaUnreachableError,
    RateLimitError,
    TransientLLMError,
)
from git_commit_ai.llm._common import require_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from git_commit_ai.llm.base import LLMMessage

_RATE_LIMITED = 429
_SERVER_ERROR = 500


class OllamaProvider:
    """``LLMProvider`` backed by a local or remote Ollama server."""

    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str,
        temperature: float,
        timeout: float,
        client: Client | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._temperature = temperature
        self._client = client or Client(host=base_url, timeout=timeout)

    @property
    def model(self) -> str:
        """Model identifier requests are sent to."""
        return self._model

    def complete(self, messages: Sequence[LLMMessage]) -> str:
        """Return the assistant reply for the conversation."""
        try:
            response = self._client.chat(
                model=self._model,
                messages=[{"role": m.role.value, "content": m.content} for m in messages],
                options={"temperature": self._temperature},
            )
        except ConnectionError as exc:
            raise OllamaUnreachableError(self._base_url) from exc
        except httpx.TimeoutException as exc:
            raise TransientLLMError(self.name, "request timed out") from exc
        except ollama.ResponseError as exc:
            if exc.status_code == _RATE_LIMITED:
                raise RateLimitError(self.name) from exc
            if exc.status_code >= _SERVER_ERROR:
                raise TransientLLMError(self.name, f"HTTP {exc.status_code}") from exc
            raise LLMError(f"Ollama request failed ({exc.status_code}): {exc.error}") from exc
        return require_text(self.name, response.message.content)
