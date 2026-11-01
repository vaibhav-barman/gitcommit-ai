"""Google Gemini API backend (``generateContent``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from git_commit_ai.core.config import ProviderName
from git_commit_ai.llm._common import dig, require_text, split_system
from git_commit_ai.llm.base import Role
from git_commit_ai.llm.http import HttpClient
from git_commit_ai.llm.registry import spec

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from git_commit_ai.llm.base import LLMMessage

GEMINI_BASE_URL = spec(ProviderName.GEMINI).base_url


def gemini_headers(api_key: str) -> dict[str, str]:
    """Auth header for the Gemini API; the key never goes in a URL."""
    return {"x-goog-api-key": api_key}


class GeminiProvider:
    """``LLMProvider`` backed by ``POST /v1beta/models/{model}:generateContent``."""

    name = "gemini"

    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model.removeprefix("models/")
        self._temperature = temperature
        self._http = HttpClient(
            self.name,
            GEMINI_BASE_URL,
            gemini_headers(api_key),
            timeout,
            transport,
            secrets=(api_key,),
        )

    @property
    def model(self) -> str:
        """Model identifier requests are sent to."""
        return self._model

    def complete(self, messages: Sequence[LLMMessage]) -> str:
        """Return the assistant reply for the conversation."""
        system, turns = split_system(messages)
        body: dict[str, Any] = {
            "contents": [
                {
                    "role": "model" if m.role is Role.ASSISTANT else "user",
                    "parts": [{"text": m.content}],
                }
                for m in turns
            ],
            "generationConfig": {"temperature": self._temperature},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        path = f"/models/{quote(self._model, safe='')}:generateContent"
        response = self._http.post_json(path, body, model=self._model)
        parts = dig(response, "candidates", 0, "content", "parts")
        text = "".join(
            part["text"]
            for part in (parts if isinstance(parts, list) else [])
            if isinstance(part, dict)
            and isinstance(part.get("text"), str)
            and not part.get("thought")
        )
        return require_text(self.name, text)
