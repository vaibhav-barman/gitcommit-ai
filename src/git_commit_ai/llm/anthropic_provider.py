"""Anthropic messages backend."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anthropic
from anthropic import Anthropic

from git_commit_ai.core.exceptions import LLMError, RateLimitError
from git_commit_ai.llm._common import parse_retry_after, require_text, split_system

if TYPE_CHECKING:
    from collections.abc import Sequence

    from git_commit_ai.llm.base import LLMMessage

_MAX_OUTPUT_TOKENS = 1024


class AnthropicProvider:
    """``LLMProvider`` backed by the Anthropic API."""

    name = "anthropic"

    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float,
        timeout: float,
        client: Anthropic | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._client = client or Anthropic(api_key=api_key, timeout=timeout)

    @property
    def model(self) -> str:
        """Model identifier requests are sent to."""
        return self._model

    def complete(self, messages: Sequence[LLMMessage]) -> str:
        """Return the assistant reply for the conversation."""
        system, turns = split_system(messages)
        turn_params: list[anthropic.types.MessageParam] = [
            {"role": "assistant" if m.role.value == "assistant" else "user", "content": m.content}
            for m in turns
        ]
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                temperature=self._temperature,
                system=system or anthropic.omit,
                messages=turn_params,
            )
        except anthropic.RateLimitError as exc:
            raise RateLimitError(self.name, parse_retry_after(exc.response.headers)) from exc
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic rejected the API key") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach Anthropic: {exc}") from exc
        text = "".join(block.text for block in response.content if block.type == "text")
        return require_text(self.name, text)
