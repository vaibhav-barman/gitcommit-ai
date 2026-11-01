"""Anthropic Messages API backend."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from git_commit_ai.llm._common import dig, require_text, split_system
from git_commit_ai.llm.base import Role
from git_commit_ai.llm.http import HttpClient

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from git_commit_ai.llm.base import LLMMessage

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
_MAX_OUTPUT_TOKENS = 1024


def anthropic_headers(api_key: str) -> dict[str, str]:
    """Auth headers for the Anthropic API."""
    return {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}


class AnthropicProvider:
    """``LLMProvider`` backed by ``POST /v1/messages``."""

    name = "anthropic"

    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float,
        timeout: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._http = HttpClient(
            self.name,
            ANTHROPIC_BASE_URL,
            anthropic_headers(api_key),
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
            "model": self._model,
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "temperature": self._temperature,
            "messages": [
                {"role": "assistant" if m.role is Role.ASSISTANT else "user", "content": m.content}
                for m in turns
            ],
        }
        if system:
            body["system"] = system
        response = self._http.post_json("/messages", body, model=self._model)
        blocks = dig(response, "content")
        text = "".join(
            block["text"]
            for block in (blocks if isinstance(blocks, list) else [])
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
        return require_text(self.name, text)
