"""OpenAI chat completions backend."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import openai
from openai import OpenAI

from git_commit_ai.core.exceptions import LLMError, RateLimitError
from git_commit_ai.llm._common import parse_retry_after, require_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from openai.types.chat import ChatCompletionMessageParam

    from git_commit_ai.llm.base import LLMMessage


class OpenAIProvider:
    """``LLMProvider`` backed by the OpenAI API."""

    name = "openai"

    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float,
        timeout: float,
        client: OpenAI | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._client = client or OpenAI(api_key=api_key, timeout=timeout)

    @property
    def model(self) -> str:
        """Model identifier requests are sent to."""
        return self._model

    def complete(self, messages: Sequence[LLMMessage]) -> str:
        """Return the assistant reply for the conversation."""
        payload = cast(
            "list[ChatCompletionMessageParam]",
            [{"role": m.role.value, "content": m.content} for m in messages],
        )
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=payload,
                temperature=self._temperature,
            )
        except openai.RateLimitError as exc:
            raise RateLimitError(self.name, parse_retry_after(exc.response.headers)) from exc
        except openai.AuthenticationError as exc:
            raise LLMError("OpenAI rejected the API key") from exc
        except openai.APIConnectionError as exc:
            raise LLMError(f"Could not reach OpenAI: {exc}") from exc
        return require_text(self.name, response.choices[0].message.content)
