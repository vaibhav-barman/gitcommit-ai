"""Provider backends over plain HTTP, driven by a fake transport (no network)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from git_commit_ai.core.config import GitCommitAIConfig, ProviderName
from git_commit_ai.core.exceptions import (
    AuthenticationError,
    LLMError,
    LLMResponseError,
    ModelNotAvailableError,
    OllamaUnreachableError,
    RateLimitError,
    TransientLLMError,
)
from git_commit_ai.llm import LLMMessage, Role, create_provider
from git_commit_ai.llm.anthropic_provider import AnthropicProvider
from git_commit_ai.llm.gemini_provider import GeminiProvider
from git_commit_ai.llm.ollama_provider import OllamaProvider, sends_key_unencrypted
from git_commit_ai.llm.openai_compatible import OpenAICompatibleBackend
from tests.fixtures.http import FakeHttp, json_response

if TYPE_CHECKING:
    from collections.abc import Sequence

CONVERSATION: Sequence[LLMMessage] = [
    LLMMessage(role=Role.SYSTEM, content="be terse"),
    LLMMessage(role=Role.USER, content="write a commit"),
    LLMMessage(role=Role.ASSISTANT, content="feat: x"),
    LLMMessage(role=Role.USER, content="again"),
]
KEY = "FAKE-" + "q" * 24 + "7a7a"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
OLLAMA_URL = "http://localhost:11434/api/chat"


def _chat(text: str | None) -> httpx.Response:
    return json_response(200, {"choices": [{"message": {"role": "assistant", "content": text}}]})


class TestOpenAICompatible:
    def _provider(self, http: FakeHttp) -> OpenAICompatibleBackend:
        return OpenAICompatibleBackend(
            name="openai",
            base_url="https://api.openai.com/v1",
            model="gpt-test",
            api_key=KEY,
            temperature=0.2,
            timeout=5,
            transport=http.transport,
        )

    def test_returns_stripped_text_and_forwards_settings(self) -> None:
        http = FakeHttp().on("POST", OPENAI_URL, _chat("  feat: add x \n"))
        assert self._provider(http).complete(CONVERSATION) == "feat: add x"
        call = http.calls[0]
        assert call.headers["authorization"] == f"Bearer {KEY}"
        assert call.body["model"] == "gpt-test"
        assert call.body["temperature"] == 0.2
        assert call.body["messages"] == [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "write a commit"},
            {"role": "assistant", "content": "feat: x"},
            {"role": "user", "content": "again"},
        ]

    @pytest.mark.parametrize(
        "payload", [{"choices": [{"message": {"content": None}}]}, {"choices": []}, {}]
    )
    def test_empty_content_raises(self, payload: object) -> None:
        http = FakeHttp().on("POST", OPENAI_URL, json_response(200, payload))
        with pytest.raises(LLMResponseError):
            self._provider(http).complete(CONVERSATION)

    def test_rate_limit_maps_with_retry_hint(self) -> None:
        http = FakeHttp().on("POST", OPENAI_URL, json_response(429, headers={"retry-after": "7"}))
        with pytest.raises(RateLimitError) as info:
            self._provider(http).complete(CONVERSATION)
        assert info.value.retry_after == 7

    @pytest.mark.parametrize(
        "failure",
        [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), json_response(503)],
    )
    def test_connection_timeout_and_server_errors_are_transient(
        self, failure: httpx.Response | Exception
    ) -> None:
        http = FakeHttp().on("POST", OPENAI_URL, failure)
        with pytest.raises(TransientLLMError, match="openai"):
            self._provider(http).complete(CONVERSATION)

    def test_bad_key_is_an_auth_error_without_the_key(self) -> None:
        body = {"error": {"message": f"Incorrect API key provided: {KEY}"}}
        http = FakeHttp().on("POST", OPENAI_URL, json_response(401, body))
        with pytest.raises(AuthenticationError) as info:
            self._provider(http).complete(CONVERSATION)
        assert not isinstance(info.value, TransientLLMError)
        assert KEY not in str(info.value)

    def test_temperature_is_dropped_once_when_the_model_rejects_it(self) -> None:
        rejected = json_response(
            400,
            {"error": {"message": "Unsupported value: 'temperature'", "param": "temperature"}},
        )
        http = FakeHttp().on("POST", OPENAI_URL, rejected, _chat("a"), _chat("b"))
        provider = self._provider(http)
        assert provider.complete(CONVERSATION) == "a"
        assert provider.complete(CONVERSATION) == "b"
        assert "temperature" in http.calls[0].body
        assert "temperature" not in http.calls[1].body
        assert "temperature" not in http.calls[2].body

    def test_other_bad_requests_are_not_retried_without_temperature(self) -> None:
        bad = json_response(400, {"error": {"message": "bad messages", "param": "messages"}})
        http = FakeHttp().on("POST", OPENAI_URL, bad)
        with pytest.raises(LLMError, match="bad messages"):
            self._provider(http).complete(CONVERSATION)
        assert len(http.calls) == 1

    def test_unknown_model_is_reported(self) -> None:
        body = {"error": {"message": "The model `gpt-test` does not exist"}}
        http = FakeHttp().on("POST", OPENAI_URL, json_response(404, body))
        with pytest.raises(ModelNotAvailableError, match="gpt-test"):
            self._provider(http).complete(CONVERSATION)

    def test_extra_headers_are_sent(self) -> None:
        http = FakeHttp().on("POST", "https://openrouter.ai/api/v1/chat/completions", _chat("x"))
        OpenAICompatibleBackend(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            model="m",
            api_key=KEY,
            temperature=0.2,
            timeout=5,
            extra_headers={"X-Title": "GitCommit AI"},
            transport=http.transport,
        ).complete(CONVERSATION)
        assert http.calls[0].headers["x-title"] == "GitCommit AI"


class TestAnthropicProvider:
    def _provider(self, http: FakeHttp) -> AnthropicProvider:
        return AnthropicProvider(
            model="claude-test", api_key=KEY, temperature=0.2, timeout=5, transport=http.transport
        )

    def _reply(self, *blocks: dict[str, str]) -> httpx.Response:
        return json_response(200, {"content": list(blocks)})

    def test_system_prompt_is_sent_out_of_band(self) -> None:
        http = FakeHttp().on(
            "POST", ANTHROPIC_URL, self._reply({"type": "text", "text": "fix: y"})
        )
        assert self._provider(http).complete(CONVERSATION) == "fix: y"
        call = http.calls[0]
        assert call.headers["x-api-key"] == KEY
        assert call.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in call.headers
        assert call.body["system"] == "be terse"
        assert call.body["max_tokens"] == 1024
        assert call.body["temperature"] == 0.2
        assert call.body["messages"] == [
            {"role": "user", "content": "write a commit"},
            {"role": "assistant", "content": "feat: x"},
            {"role": "user", "content": "again"},
        ]

    def test_no_system_key_without_a_system_prompt(self) -> None:
        http = FakeHttp().on("POST", ANTHROPIC_URL, self._reply({"type": "text", "text": "x"}))
        self._provider(http).complete([LLMMessage(role=Role.USER, content="hi")])
        assert "system" not in http.calls[0].body

    def test_text_blocks_are_joined_and_others_ignored(self) -> None:
        http = FakeHttp().on(
            "POST",
            ANTHROPIC_URL,
            self._reply(
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": "docs: "},
                {"type": "text", "text": "z"},
            ),
        )
        assert self._provider(http).complete(CONVERSATION) == "docs: z"

    def test_rate_limit_maps_with_retry_hint(self) -> None:
        http = FakeHttp().on(
            "POST", ANTHROPIC_URL, json_response(429, headers={"retry-after": "7"})
        )
        with pytest.raises(RateLimitError) as info:
            self._provider(http).complete(CONVERSATION)
        assert info.value.retry_after == 7

    @pytest.mark.parametrize(
        "failure",
        [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), json_response(529)],
    )
    def test_connection_and_server_errors_are_transient(
        self, failure: httpx.Response | Exception
    ) -> None:
        http = FakeHttp().on("POST", ANTHROPIC_URL, failure)
        with pytest.raises(TransientLLMError):
            self._provider(http).complete(CONVERSATION)

    def test_empty_response_raises(self) -> None:
        http = FakeHttp().on("POST", ANTHROPIC_URL, self._reply())
        with pytest.raises(LLMResponseError):
            self._provider(http).complete(CONVERSATION)

    def test_bad_key_is_an_auth_error(self) -> None:
        http = FakeHttp().on("POST", ANTHROPIC_URL, json_response(401))
        with pytest.raises(AuthenticationError):
            self._provider(http).complete(CONVERSATION)


class TestGeminiProvider:
    def _provider(self, http: FakeHttp) -> GeminiProvider:
        return GeminiProvider(
            model="gemini-test", api_key=KEY, temperature=0.2, timeout=5, transport=http.transport
        )

    def _reply(self, *parts: dict[str, object]) -> httpx.Response:
        return json_response(200, {"candidates": [{"content": {"parts": list(parts)}}]})

    def test_system_instruction_roles_and_key_header(self) -> None:
        http = FakeHttp().on("POST", GEMINI_URL, self._reply({"text": "chore: w"}))
        assert self._provider(http).complete(CONVERSATION) == "chore: w"
        call = http.calls[0]
        assert call.headers["x-goog-api-key"] == KEY
        assert KEY not in str(call.url)
        assert call.body["systemInstruction"] == {"parts": [{"text": "be terse"}]}
        assert call.body["generationConfig"] == {"temperature": 0.2}
        assert [c["role"] for c in call.body["contents"]] == ["user", "model", "user"]
        assert call.body["contents"][0]["parts"] == [{"text": "write a commit"}]

    def test_thought_parts_are_skipped(self) -> None:
        http = FakeHttp().on(
            "POST", GEMINI_URL, self._reply({"text": "pondering", "thought": True}, {"text": "x"})
        )
        assert self._provider(http).complete(CONVERSATION) == "x"

    @pytest.mark.parametrize("payload", [{"candidates": []}, {}, {"candidates": [{}]}])
    def test_empty_response_raises(self, payload: object) -> None:
        http = FakeHttp().on("POST", GEMINI_URL, json_response(200, payload))
        with pytest.raises(LLMResponseError):
            self._provider(http).complete(CONVERSATION)

    def test_429_maps_to_rate_limit(self) -> None:
        http = FakeHttp().on("POST", GEMINI_URL, json_response(429))
        with pytest.raises(RateLimitError):
            self._provider(http).complete(CONVERSATION)

    def test_server_errors_are_transient(self) -> None:
        http = FakeHttp().on("POST", GEMINI_URL, json_response(503))
        with pytest.raises(TransientLLMError, match="503"):
            self._provider(http).complete(CONVERSATION)

    def test_network_failures_are_transient(self) -> None:
        http = FakeHttp().on("POST", GEMINI_URL, httpx.ReadTimeout("slow"))
        with pytest.raises(TransientLLMError):
            self._provider(http).complete(CONVERSATION)

    def test_other_client_errors_are_not_transient(self) -> None:
        body = {"error": {"code": 400, "message": "bad request"}}
        http = FakeHttp().on("POST", GEMINI_URL, json_response(400, body))
        with pytest.raises(LLMError, match="400") as info:
            self._provider(http).complete(CONVERSATION)
        assert not isinstance(info.value, TransientLLMError)

    def test_invalid_key_is_an_auth_error(self) -> None:
        body = {"error": {"code": 400, "details": [{"reason": "API_KEY_INVALID"}]}}
        http = FakeHttp().on("POST", GEMINI_URL, json_response(400, body))
        with pytest.raises(AuthenticationError):
            self._provider(http).complete(CONVERSATION)


class TestOllamaProvider:
    def _provider(self, http: FakeHttp) -> OllamaProvider:
        return OllamaProvider(
            model="llama-test",
            base_url="http://localhost:11434",
            temperature=0.2,
            timeout=5,
            transport=http.transport,
        )

    def test_returns_text_and_forwards_settings(self) -> None:
        reply = json_response(200, {"message": {"role": "assistant", "content": "refactor: v"}})
        http = FakeHttp().on("POST", OLLAMA_URL, reply)
        assert self._provider(http).complete(CONVERSATION) == "refactor: v"
        call = http.calls[0]
        assert call.body["options"] == {"temperature": 0.2}
        assert call.body["stream"] is False
        assert call.body["model"] == "llama-test"
        assert call.body["messages"][1] == {"role": "user", "content": "write a commit"}
        assert "authorization" not in call.headers

    def test_connection_failure_is_reported_as_unreachable(self) -> None:
        http = FakeHttp().on("POST", OLLAMA_URL, httpx.ConnectError("refused"))
        with pytest.raises(OllamaUnreachableError, match="localhost:11434"):
            self._provider(http).complete(CONVERSATION)

    @pytest.mark.parametrize("failure", [httpx.ReadTimeout("slow"), json_response(503)])
    def test_timeout_and_server_errors_are_transient(
        self, failure: httpx.Response | Exception
    ) -> None:
        http = FakeHttp().on("POST", OLLAMA_URL, failure)
        with pytest.raises(TransientLLMError):
            self._provider(http).complete(CONVERSATION)

    def test_missing_model_is_reported(self) -> None:
        body = {"error": "model 'llama-test' not found"}
        http = FakeHttp().on("POST", OLLAMA_URL, json_response(404, body))
        with pytest.raises(ModelNotAvailableError, match="llama-test"):
            self._provider(http).complete(CONVERSATION)

    def test_other_errors_map_to_llm_error_with_the_message(self) -> None:
        http = FakeHttp().on("POST", OLLAMA_URL, json_response(400, {"error": "bad options"}))
        with pytest.raises(LLMError, match="bad options"):
            self._provider(http).complete(CONVERSATION)

    def test_empty_reply_raises(self) -> None:
        http = FakeHttp().on("POST", OLLAMA_URL, json_response(200, {"message": {"content": ""}}))
        with pytest.raises(LLMResponseError):
            self._provider(http).complete(CONVERSATION)


class TestNewProviders:
    @pytest.mark.parametrize(
        ("provider", "url"),
        [
            (ProviderName.OPENROUTER, "https://openrouter.ai/api/v1/chat/completions"),
            (ProviderName.HUGGINGFACE, "https://router.huggingface.co/v1/chat/completions"),
        ],
    )
    def test_openai_compatible_providers_use_their_base_url(
        self, provider: ProviderName, url: str
    ) -> None:
        http = FakeHttp().on("POST", url, _chat("feat: x"))
        config = GitCommitAIConfig(llm_provider=provider, llm_model="org/model")
        llm = create_provider(config, api_key=KEY, transport=http.transport)
        assert llm.complete(CONVERSATION) == "feat: x"
        assert http.calls[0].headers["authorization"] == f"Bearer {KEY}"
        assert http.calls[0].body["model"] == "org/model"

    def test_openrouter_sends_attribution_headers_only(self) -> None:
        url = "https://openrouter.ai/api/v1/chat/completions"
        http = FakeHttp().on("POST", url, _chat("x"))
        config = GitCommitAIConfig(llm_provider=ProviderName.OPENROUTER)
        create_provider(config, api_key=KEY, transport=http.transport).complete(CONVERSATION)
        headers = http.calls[0].headers
        assert headers["http-referer"] == "https://github.com/vaibhav-barman/gitcommit-ai"
        assert headers["x-title"] == "GitCommit AI"

    def test_hugging_face_sends_no_attribution_headers(self) -> None:
        url = "https://router.huggingface.co/v1/chat/completions"
        http = FakeHttp().on("POST", url, _chat("x"))
        config = GitCommitAIConfig(llm_provider=ProviderName.HUGGINGFACE)
        create_provider(config, api_key=KEY, transport=http.transport).complete(CONVERSATION)
        assert "x-title" not in http.calls[0].headers


class TestOllamaKey:
    def test_a_key_is_sent_as_a_bearer_token(self) -> None:
        reply = json_response(200, {"message": {"content": "x"}})
        http = FakeHttp().on("POST", "https://ollama.com/api/chat", reply)
        OllamaProvider(
            model="m",
            base_url="https://ollama.com",
            temperature=0.2,
            timeout=5,
            api_key=KEY,
            transport=http.transport,
        ).complete(CONVERSATION)
        assert http.calls[0].headers["authorization"] == f"Bearer {KEY}"

    def test_a_rejected_key_is_an_auth_error_without_the_key(self) -> None:
        body = {"error": f"unauthorized {KEY}"}
        http = FakeHttp().on("POST", "https://ollama.com/api/chat", json_response(401, body))
        provider = OllamaProvider(
            model="m",
            base_url="https://ollama.com",
            temperature=0.2,
            timeout=5,
            api_key=KEY,
            transport=http.transport,
        )
        with pytest.raises(AuthenticationError) as info:
            provider.complete(CONVERSATION)
        assert KEY not in str(info.value)

    @pytest.mark.parametrize(
        ("url", "insecure"),
        [
            ("http://localhost:11434", False),
            ("http://127.0.0.1:11434", False),
            ("http://[::1]:11434", False),
            ("https://ollama.internal.example:11434", False),
            ("http://ollama.internal.example:11434", True),
            ("http://192.168.1.20:11434", True),
        ],
    )
    def test_plain_http_to_another_host_is_flagged(self, url: str, insecure: bool) -> None:
        assert sends_key_unencrypted(url) is insecure
