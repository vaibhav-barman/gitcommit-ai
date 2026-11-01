"""Unit tests for provider backends using mocked SDK clients (no network)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import anthropic
import httpx
import ollama
import openai
import pytest
from google.genai import errors as genai_errors

from git_commit_ai.core.exceptions import (
    LLMError,
    LLMResponseError,
    OllamaUnreachableError,
    RateLimitError,
    TransientLLMError,
)
from git_commit_ai.llm import LLMMessage, Role
from git_commit_ai.llm.anthropic_provider import AnthropicProvider
from git_commit_ai.llm.gemini_provider import GeminiProvider
from git_commit_ai.llm.ollama_provider import OllamaProvider
from git_commit_ai.llm.openai_provider import OpenAIProvider

if TYPE_CHECKING:
    from collections.abc import Sequence

CONVERSATION: Sequence[LLMMessage] = [
    LLMMessage(role=Role.SYSTEM, content="be terse"),
    LLMMessage(role=Role.USER, content="write a commit"),
]
_REQUEST = httpx.Request("POST", "https://example.invalid")


def _rate_limited_response() -> httpx.Response:
    return httpx.Response(429, headers={"retry-after": "7"}, request=_REQUEST)


class TestOpenAIProvider:
    def _provider(self, client: MagicMock) -> OpenAIProvider:
        return OpenAIProvider(
            model="gpt-test",
            api_key="k",
            temperature=0.2,
            timeout=5,
            client=cast("openai.OpenAI", client),
        )

    def test_returns_stripped_text_and_forwards_settings(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="  feat: add x \n"))
        ]
        assert self._provider(client).complete(CONVERSATION) == "feat: add x"
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-test"
        assert kwargs["temperature"] == 0.2
        assert kwargs["messages"][0] == {"role": "system", "content": "be terse"}

    def test_empty_content_raises(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content=None))
        ]
        with pytest.raises(LLMResponseError):
            self._provider(client).complete(CONVERSATION)

    def test_rate_limit_maps_with_retry_hint(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = openai.RateLimitError(
            "slow down", response=_rate_limited_response(), body=None
        )
        with pytest.raises(RateLimitError) as info:
            self._provider(client).complete(CONVERSATION)
        assert info.value.retry_after == 7

    def test_connection_error_is_transient(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = openai.APIConnectionError(request=_REQUEST)
        with pytest.raises(TransientLLMError, match="openai"):
            self._provider(client).complete(CONVERSATION)

    def test_timeout_is_transient(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = openai.APITimeoutError(request=_REQUEST)
        with pytest.raises(TransientLLMError):
            self._provider(client).complete(CONVERSATION)

    def test_server_error_is_transient(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = openai.InternalServerError(
            "boom", response=httpx.Response(503, request=_REQUEST), body=None
        )
        with pytest.raises(TransientLLMError):
            self._provider(client).complete(CONVERSATION)

    def test_bad_key_is_not_transient(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = openai.AuthenticationError(
            "no", response=httpx.Response(401, request=_REQUEST), body=None
        )
        with pytest.raises(LLMError) as info:
            self._provider(client).complete(CONVERSATION)
        assert not isinstance(info.value, TransientLLMError)


class TestAnthropicProvider:
    def _provider(self, client: MagicMock) -> AnthropicProvider:
        return AnthropicProvider(
            model="claude-test",
            api_key="k",
            temperature=0.2,
            timeout=5,
            client=cast("anthropic.Anthropic", client),
        )

    def test_system_prompt_is_sent_out_of_band(self) -> None:
        client = MagicMock()
        client.messages.create.return_value.content = [MagicMock(type="text", text="fix: y")]
        assert self._provider(client).complete(CONVERSATION) == "fix: y"
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["system"] == "be terse"
        assert kwargs["messages"] == [{"role": "user", "content": "write a commit"}]

    def test_non_text_blocks_are_ignored(self) -> None:
        client = MagicMock()
        client.messages.create.return_value.content = [
            MagicMock(type="thinking"),
            MagicMock(type="text", text="docs: z"),
        ]
        assert self._provider(client).complete(CONVERSATION) == "docs: z"

    def test_rate_limit_maps_with_retry_hint(self) -> None:
        client = MagicMock()
        client.messages.create.side_effect = anthropic.RateLimitError(
            "slow down", response=_rate_limited_response(), body=None
        )
        with pytest.raises(RateLimitError) as info:
            self._provider(client).complete(CONVERSATION)
        assert info.value.retry_after == 7

    def test_connection_and_server_errors_are_transient(self) -> None:
        for error in (
            anthropic.APIConnectionError(request=_REQUEST),
            anthropic.APITimeoutError(request=_REQUEST),
            anthropic.InternalServerError(
                "overloaded", response=httpx.Response(529, request=_REQUEST), body=None
            ),
        ):
            client = MagicMock()
            client.messages.create.side_effect = error
            with pytest.raises(TransientLLMError):
                self._provider(client).complete(CONVERSATION)

    def test_empty_response_raises(self) -> None:
        client = MagicMock()
        client.messages.create.return_value.content = []
        with pytest.raises(LLMResponseError):
            self._provider(client).complete(CONVERSATION)


class TestGeminiProvider:
    def _provider(self, client: MagicMock) -> GeminiProvider:
        return GeminiProvider(
            model="gemini-test",
            api_key="k",
            temperature=0.2,
            timeout=5,
            client=cast("Any", client),
        )

    def test_system_instruction_goes_in_config(self) -> None:
        client = MagicMock()
        client.models.generate_content.return_value.text = "chore: w"
        assert self._provider(client).complete(CONVERSATION) == "chore: w"
        kwargs = client.models.generate_content.call_args.kwargs
        assert kwargs["config"].system_instruction == "be terse"
        assert kwargs["config"].temperature == 0.2
        assert len(kwargs["contents"]) == 1

    def test_429_maps_to_rate_limit(self) -> None:
        client = MagicMock()
        client.models.generate_content.side_effect = genai_errors.ClientError(
            429, {"error": {"message": "quota"}}
        )
        with pytest.raises(RateLimitError):
            self._provider(client).complete(CONVERSATION)

    def test_server_errors_are_transient(self) -> None:
        client = MagicMock()
        client.models.generate_content.side_effect = genai_errors.ServerError(
            503, {"error": {"message": "unavailable"}}
        )
        with pytest.raises(TransientLLMError, match="503"):
            self._provider(client).complete(CONVERSATION)

    def test_network_failures_are_transient(self) -> None:
        client = MagicMock()
        client.models.generate_content.side_effect = httpx.ReadTimeout("slow")
        with pytest.raises(TransientLLMError):
            self._provider(client).complete(CONVERSATION)

    def test_other_client_errors_are_not_transient(self) -> None:
        client = MagicMock()
        client.models.generate_content.side_effect = genai_errors.ClientError(
            400, {"error": {"message": "bad request"}}
        )
        with pytest.raises(LLMError, match="400") as info:
            self._provider(client).complete(CONVERSATION)
        assert not isinstance(info.value, TransientLLMError)


class TestOllamaProvider:
    def _provider(self, client: MagicMock) -> OllamaProvider:
        return OllamaProvider(
            model="llama-test",
            base_url="http://localhost:11434",
            temperature=0.2,
            timeout=5,
            client=cast("ollama.Client", client),
        )

    def test_returns_text_and_forwards_temperature(self) -> None:
        client = MagicMock()
        client.chat.return_value.message.content = "refactor: v"
        assert self._provider(client).complete(CONVERSATION) == "refactor: v"
        kwargs = client.chat.call_args.kwargs
        assert kwargs["options"] == {"temperature": 0.2}
        assert kwargs["messages"][1]["role"] == "user"

    def test_connection_failure_is_reported_as_unreachable(self) -> None:
        client = MagicMock()
        client.chat.side_effect = ConnectionError("refused")
        with pytest.raises(OllamaUnreachableError, match="localhost:11434"):
            self._provider(client).complete(CONVERSATION)

    def test_timeout_and_server_errors_are_transient(self) -> None:
        for error in (
            httpx.ReadTimeout("slow"),
            ollama.ResponseError("loading model", status_code=503),
        ):
            client = MagicMock()
            client.chat.side_effect = error
            with pytest.raises(TransientLLMError):
                self._provider(client).complete(CONVERSATION)

    def test_response_error_maps_to_llm_error(self) -> None:
        client = MagicMock()
        client.chat.side_effect = ollama.ResponseError("model not found", status_code=404)
        with pytest.raises(LLMError, match="404"):
            self._provider(client).complete(CONVERSATION)
