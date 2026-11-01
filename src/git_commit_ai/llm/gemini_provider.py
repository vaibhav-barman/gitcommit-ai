"""Google Gemini backend."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from google import genai
from google.genai import errors, types

from git_commit_ai.core.exceptions import LLMError, RateLimitError, TransientLLMError
from git_commit_ai.llm._common import require_text, split_system

if TYPE_CHECKING:
    from collections.abc import Sequence

    from git_commit_ai.llm.base import LLMMessage

_RATE_LIMITED = 429


class GeminiProvider:
    """``LLMProvider`` backed by the Gemini API."""

    name = "gemini"

    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float,
        timeout: float,
        client: genai.Client | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._client = client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout * 1000)),
        )

    @property
    def model(self) -> str:
        """Model identifier requests are sent to."""
        return self._model

    def complete(self, messages: Sequence[LLMMessage]) -> str:
        """Return the assistant reply for the conversation."""
        system, turns = split_system(messages)
        contents = [
            types.Content(
                role="model" if m.role.value == "assistant" else "user",
                parts=[types.Part(text=m.content)],
            )
            for m in turns
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self._temperature,
        )
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
        except httpx.TransportError as exc:
            raise TransientLLMError(self.name, str(exc) or type(exc).__name__) from exc
        except errors.APIError as exc:
            if exc.code == _RATE_LIMITED:
                raise RateLimitError(self.name) from exc
            if isinstance(exc, errors.ServerError):
                raise TransientLLMError(self.name, f"HTTP {exc.code}") from exc
            raise LLMError(f"Gemini request failed ({exc.code}): {exc.message}") from exc
        return require_text(self.name, response.text)
