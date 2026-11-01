"""Chat completions over the OpenAI-compatible API (OpenAI, OpenRouter, Hugging Face)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from git_commit_ai.core.exceptions import RequestRejectedError
from git_commit_ai.llm._common import dig, require_text, text_or_none
from git_commit_ai.llm.http import HttpClient

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import httpx

    from git_commit_ai.llm.base import LLMMessage

_BAD_REQUEST = 400


class OpenAICompatibleBackend:
    """``LLMProvider`` for any endpoint that speaks ``POST {base}/chat/completions``.

    Some models (OpenAI's reasoning models among them) refuse a custom
    temperature. When the provider blames the ``temperature`` field, the request
    is sent once more without it, and later requests leave it out.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        model: str,
        api_key: str,
        temperature: float,
        timeout: float,
        extra_headers: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self._model = model
        self._temperature: float | None = temperature
        headers = {"Authorization": f"Bearer {api_key}", **(extra_headers or {})}
        self._http = HttpClient(name, base_url, headers, timeout, transport, secrets=(api_key,))

    @property
    def model(self) -> str:
        """Model identifier requests are sent to."""
        return self._model

    def complete(self, messages: Sequence[LLMMessage]) -> str:
        """Return the assistant reply for the conversation."""
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
        }
        if self._temperature is not None:
            body["temperature"] = self._temperature
        try:
            response = self._http.post_json("/chat/completions", body, model=self._model)
        except RequestRejectedError as exc:
            if (
                exc.status != _BAD_REQUEST
                or exc.param != "temperature"
                or "temperature" not in body
            ):
                raise
            self._temperature = None
            del body["temperature"]
            response = self._http.post_json("/chat/completions", body, model=self._model)
        content = dig(response, "choices", 0, "message", "content")
        return require_text(self.name, text_or_none(content))
