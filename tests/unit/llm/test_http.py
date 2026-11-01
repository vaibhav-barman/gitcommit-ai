"""The shared HTTP layer: error mapping (plan §4.5) and secret redaction."""

from __future__ import annotations

import httpx
import pytest

from git_commit_ai.core.exceptions import (
    AuthenticationError,
    LLMError,
    LLMResponseError,
    ModelNotAvailableError,
    OllamaUnreachableError,
    RateLimitError,
    RequestRejectedError,
    TransientLLMError,
)
from git_commit_ai.llm.http import HttpClient, map_http_error, redact
from tests.fixtures.http import FakeHttp, json_response

BASE = "https://api.example.test/v1"
KEY = "FAKE-" + "k" * 24 + "9z9z"


def _client(http: FakeHttp, **kwargs: object) -> HttpClient:
    return HttpClient(
        provider="example",
        base_url=BASE,
        headers={"Authorization": f"Bearer {KEY}"},
        timeout=5,
        transport=http.transport,
        secrets=(KEY,),
        **kwargs,  # type: ignore[arg-type]
    )


def _mapped(status: int, payload: object = None, **headers: str) -> LLMError:
    response = httpx.Response(status, json=payload, headers=headers)
    return map_http_error("example", response, (KEY,), model="m-1")


class TestMapping:
    @pytest.mark.parametrize("status", [408, 500, 502, 503, 504, 529])
    def test_server_side_statuses_are_transient(self, status: int) -> None:
        error = _mapped(status)
        assert isinstance(error, TransientLLMError)
        assert f"HTTP {status}" in str(error)

    def test_429_is_a_rate_limit_with_retry_after(self) -> None:
        error = _mapped(429, **{"retry-after": "7"})
        assert isinstance(error, RateLimitError)
        assert error.retry_after == 7

    def test_429_without_a_numeric_retry_after(self) -> None:
        error = _mapped(429, **{"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
        assert isinstance(error, RateLimitError)
        assert error.retry_after is None

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failures_use_fixed_text_and_ignore_the_body(self, status: int) -> None:
        body = {"error": {"message": f"Incorrect API key provided: {KEY}"}}
        error = _mapped(status, body)
        assert isinstance(error, AuthenticationError)
        assert KEY not in str(error)
        assert "Incorrect" not in str(error)
        assert f"HTTP {status}" in str(error)

    def test_gemini_api_key_invalid_is_an_auth_failure(self) -> None:
        body = {
            "error": {
                "code": 400,
                "message": "API key not valid. Please pass a valid API key.",
                "details": [{"reason": "API_KEY_INVALID"}],
            }
        }
        assert isinstance(_mapped(400, body), AuthenticationError)

    def test_404_is_model_not_available(self) -> None:
        error = _mapped(404, {"error": "model 'm-1' not found"})
        assert isinstance(error, ModelNotAvailableError)
        assert error.model == "m-1"
        assert "m-1" in str(error)

    @pytest.mark.parametrize(
        "message",
        [
            "The model `m-1` does not exist or you do not have access to it.",
            "model: m-1 not found",
            "This model is not supported by this endpoint",
            "Invalid model name passed in model=m-1",
        ],
    )
    def test_400_naming_a_missing_model_is_model_not_available(self, message: str) -> None:
        assert isinstance(_mapped(400, {"error": {"message": message}}), ModelNotAvailableError)

    def test_other_4xx_carry_the_redacted_provider_message(self) -> None:
        error = _mapped(400, {"error": {"message": f"bad field, key {KEY} sk-" + "x" * 20}})
        assert isinstance(error, RequestRejectedError)
        assert "example request failed (HTTP 400)" in str(error)
        assert "bad field" in str(error)
        assert KEY not in str(error)
        assert "x" * 20 not in str(error)

    def test_the_message_is_truncated(self) -> None:
        error = _mapped(422, {"error": {"message": "a" * 1000}})
        assert len(str(error)) < 400

    def test_param_is_kept_for_the_caller(self) -> None:
        body = {"error": {"message": "Unsupported value: 'temperature'", "param": "temperature"}}
        error = _mapped(400, body)
        assert isinstance(error, RequestRejectedError)
        assert error.param == "temperature"

    def test_a_body_that_is_not_json_still_maps(self) -> None:
        response = httpx.Response(418, text=f"<html>{KEY}</html>")
        error = map_http_error("example", response, (KEY,))
        assert "HTTP 418" in str(error)
        assert KEY not in str(error)

    def test_404_without_a_model_is_a_plain_error(self) -> None:
        error = map_http_error("example", httpx.Response(404), ())
        assert not isinstance(error, ModelNotAvailableError)
        assert "HTTP 404" in str(error)


class TestRedact:
    def test_known_secrets_are_removed(self) -> None:
        assert redact(f"key={KEY}.", (KEY,)) == "key=[redacted]."

    @pytest.mark.parametrize(
        "token",
        [
            "sk-" + "A1b2C3d4E5",
            "sk-proj-" + "abcdefgh1234",
            "sk-ant-" + "api03-abcdefgh",
            "sk-or-" + "v1-0123456789ab",
            "hf_" + "AbCdEfGhIjKl",
            "AIza" + "S" * 35,
        ],
    )
    def test_key_shaped_tokens_are_removed(self, token: str) -> None:
        assert redact(f"got {token} here", ()) == "got [redacted] here"

    @pytest.mark.parametrize(
        "text",
        [
            "risk_assessment failed",
            "task_scheduler_backlog",
            "use the desk-reference_manual",
            "the whf_parameters field",
            "sk-short",
        ],
    )
    def test_ordinary_words_are_left_alone(self, text: str) -> None:
        assert redact(text, ()) == text

    def test_empty_secrets_are_ignored(self) -> None:
        assert redact("abc", ("",)) == "abc"


class TestClient:
    def test_sends_headers_and_json_and_returns_json(self) -> None:
        http = FakeHttp().on("POST", f"{BASE}/chat", json_response(200, {"ok": True}))
        assert _client(http).post_json("/chat", {"a": 1}) == {"ok": True}
        call = http.calls[0]
        assert call.body == {"a": 1}
        assert call.headers["authorization"] == f"Bearer {KEY}"

    def test_get_sends_params(self) -> None:
        http = FakeHttp().on("GET", f"{BASE}/models", json_response(200, {"data": []}))
        _client(http).get_json("/models", {"limit": 5})
        assert http.calls[0].url.params["limit"] == "5"

    def test_error_statuses_are_mapped(self) -> None:
        http = FakeHttp().on("POST", f"{BASE}/chat", json_response(503))
        with pytest.raises(TransientLLMError):
            _client(http).post_json("/chat", {})

    def test_timeouts_are_transient(self) -> None:
        http = FakeHttp().on("POST", f"{BASE}/chat", httpx.ReadTimeout("slow"))
        with pytest.raises(TransientLLMError, match="timed out"):
            _client(http).post_json("/chat", {})

    def test_dropped_connections_are_transient(self) -> None:
        http = FakeHttp().on("POST", f"{BASE}/chat", httpx.RemoteProtocolError("dropped"))
        with pytest.raises(TransientLLMError):
            _client(http).post_json("/chat", {})

    def test_a_refused_connection_can_be_reported_as_unreachable(self) -> None:
        http = FakeHttp().on("POST", f"{BASE}/chat", httpx.ConnectError("refused"))
        client = _client(http, unreachable=lambda: OllamaUnreachableError(BASE))
        with pytest.raises(OllamaUnreachableError):
            client.post_json("/chat", {})

    def test_a_refused_connection_is_otherwise_transient(self) -> None:
        http = FakeHttp().on("POST", f"{BASE}/chat", httpx.ConnectError("refused"))
        with pytest.raises(TransientLLMError):
            _client(http).post_json("/chat", {})

    def test_a_success_that_is_not_json_is_a_response_error(self) -> None:
        http = FakeHttp().on("POST", f"{BASE}/chat", httpx.Response(200, text="<html>"))
        with pytest.raises(LLMResponseError):
            _client(http).post_json("/chat", {})

    def test_the_model_reaches_the_mapping(self) -> None:
        http = FakeHttp().on("POST", f"{BASE}/chat", json_response(404))
        with pytest.raises(ModelNotAvailableError, match="gone-model"):
            _client(http).post_json("/chat", {}, model="gone-model")
