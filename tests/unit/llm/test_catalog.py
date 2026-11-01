"""Key checks, model listings and probes for every provider (plan §5), over a fake transport."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from git_commit_ai.core.config import ProviderName
from git_commit_ai.core.exceptions import (
    AuthenticationError,
    ModelNotAvailableError,
    RateLimitError,
    TransientLLMError,
)
from git_commit_ai.llm.catalog import (
    PROBE_PROMPT,
    SETUP_TIMEOUT,
    KeyCheck,
    ModelCatalog,
    catalog_for,
    detect_ollama,
)
from git_commit_ai.llm.registry import spec
from tests.fixtures.http import FakeHttp, json_response
from tests.fixtures.provider_payloads import (
    ANTHROPIC_PAGES,
    GEMINI_PAGES,
    HUGGINGFACE_MODELS,
    OLLAMA_OLD_TAGS,
    OLLAMA_TAGS,
    OPENAI_MODELS,
    OPENROUTER_MODELS,
)

KEY = "FAKE-" + "c" * 24 + "5b5b"
OPENAI = "https://api.openai.com/v1"
ANTHROPIC = "https://api.anthropic.com/v1"
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
OPENROUTER = "https://openrouter.ai/api/v1"
HF = "https://router.huggingface.co/v1"
OLLAMA = "http://localhost:11434"


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def _catalog(
    provider: ProviderName, http: FakeHttp, base_url: str | None = None, key: str | None = KEY
) -> ModelCatalog:
    return catalog_for(
        provider,
        key,
        base_url or spec(provider).base_url,
        transport=http.transport,
        sleep=Sleeps(),
    )


def _ids(catalog: ModelCatalog) -> list[str]:
    return [m.id for m in catalog.list_models().models]


def _shortlist(catalog: ModelCatalog) -> list[str]:
    return [m.id for m in catalog.list_models().models if m.shortlisted]


class TestKeyChecks:
    @pytest.mark.parametrize(
        ("provider", "url"),
        [
            (ProviderName.OPENAI, f"{OPENAI}/models"),
            (ProviderName.ANTHROPIC, f"{ANTHROPIC}/models"),
            (ProviderName.GEMINI, f"{GEMINI}/models"),
            (ProviderName.OPENROUTER, f"{OPENROUTER}/key"),
            (ProviderName.HUGGINGFACE, "https://huggingface.co/api/whoami-v2"),
        ],
    )
    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            (json_response(200, {"data": [], "models": []}), KeyCheck.VALID),
            (json_response(401), KeyCheck.INVALID),
            (json_response(403), KeyCheck.INVALID),
            (json_response(503), KeyCheck.UNKNOWN),
            (httpx.ReadTimeout("slow"), KeyCheck.UNKNOWN),
        ],
    )
    def test_status_maps_to_a_key_check(
        self,
        provider: ProviderName,
        url: str,
        response: httpx.Response | Exception,
        expected: KeyCheck,
    ) -> None:
        http = FakeHttp().on("GET", url, response)
        assert _catalog(provider, http).check_key() is expected

    def test_gemini_api_key_invalid_400_is_invalid(self) -> None:
        body = {"error": {"code": 400, "details": [{"reason": "API_KEY_INVALID"}]}}
        http = FakeHttp().on("GET", f"{GEMINI}/models", json_response(400, body))
        assert _catalog(ProviderName.GEMINI, http).check_key() is KeyCheck.INVALID

    @pytest.mark.parametrize(
        ("provider", "header", "value"),
        [
            (ProviderName.OPENAI, "authorization", f"Bearer {KEY}"),
            (ProviderName.ANTHROPIC, "x-api-key", KEY),
            (ProviderName.GEMINI, "x-goog-api-key", KEY),
            (ProviderName.OPENROUTER, "authorization", f"Bearer {KEY}"),
            (ProviderName.HUGGINGFACE, "authorization", f"Bearer {KEY}"),
        ],
    )
    def test_keys_travel_in_headers_never_in_urls(
        self, provider: ProviderName, header: str, value: str
    ) -> None:
        http = FakeHttp()
        for url in (
            f"{OPENAI}/models",
            f"{ANTHROPIC}/models",
            f"{GEMINI}/models",
            f"{OPENROUTER}/key",
            "https://huggingface.co/api/whoami-v2",
        ):
            http.on("GET", url, json_response(200, {"data": [], "models": []}))
        _catalog(provider, http).check_key()
        assert http.calls[0].headers[header] == value
        assert KEY not in str(http.calls[0].url)

    def test_anthropic_sends_the_version_header(self) -> None:
        http = FakeHttp().on("GET", f"{ANTHROPIC}/models", json_response(200, {"data": []}))
        _catalog(ProviderName.ANTHROPIC, http).check_key()
        assert http.calls[0].headers["anthropic-version"] == "2023-06-01"

    def test_one_retry_on_a_short_rate_limit(self) -> None:
        http = FakeHttp().on(
            "GET",
            f"{OPENAI}/models",
            json_response(429, headers={"retry-after": "3"}),
            json_response(200, OPENAI_MODELS),
        )
        sleeps = Sleeps()
        catalog = catalog_for(ProviderName.OPENAI, KEY, OPENAI, http.transport, sleep=sleeps)
        assert catalog.check_key() is KeyCheck.VALID
        assert sleeps.waits == [3.0]

    @pytest.mark.parametrize("headers", [{"retry-after": "30"}, {}])
    def test_no_retry_on_a_long_or_unknown_rate_limit(self, headers: dict[str, str]) -> None:
        http = FakeHttp().on("GET", f"{OPENAI}/models", json_response(429, headers=headers))
        sleeps = Sleeps()
        catalog = catalog_for(ProviderName.OPENAI, KEY, OPENAI, http.transport, sleep=sleeps)
        assert catalog.check_key() is KeyCheck.UNKNOWN
        assert sleeps.waits == []
        assert len(http.calls) == 1

    def test_only_one_retry(self) -> None:
        limited = json_response(429, headers={"retry-after": "1"})
        http = FakeHttp().on("GET", f"{OPENAI}/models", limited)
        catalog = _catalog(ProviderName.OPENAI, http)
        with pytest.raises(RateLimitError):
            catalog.list_models()
        assert len(http.calls) == 2

    def test_setup_calls_use_a_ten_second_timeout(self) -> None:
        assert SETUP_TIMEOUT == 10.0


class TestOpenAI:
    def _catalog(self) -> tuple[ModelCatalog, FakeHttp]:
        http = FakeHttp().on("GET", f"{OPENAI}/models", json_response(200, OPENAI_MODELS))
        return _catalog(ProviderName.OPENAI, http), http

    def test_filters_and_sorts_newest_first(self) -> None:
        catalog, _ = self._catalog()
        assert _ids(catalog) == [
            "gpt-5-mini",
            "gpt-5-2026-08-07",
            "gpt-5",
            "o4-mini",
            "chatgpt-4o-latest",
        ]

    def test_dated_snapshots_are_kept_out_of_the_shortlist(self) -> None:
        catalog, _ = self._catalog()
        assert "gpt-5-2026-08-07" not in _shortlist(catalog)
        assert _shortlist(catalog)[0] == "gpt-5-mini"

    def test_the_key_check_and_listing_share_one_request(self) -> None:
        catalog, http = self._catalog()
        catalog.check_key()
        catalog.list_models()
        assert len(http.calls) == 1


class TestAnthropic:
    def _http(self) -> FakeHttp:
        pages = iter(ANTHROPIC_PAGES)
        return FakeHttp().on(
            "GET", f"{ANTHROPIC}/models", lambda _request: json_response(200, next(pages))
        )

    def test_follows_after_id_through_every_page(self) -> None:
        http = self._http()
        ids = _ids(_catalog(ProviderName.ANTHROPIC, http))
        assert len(http.calls) == 3
        assert http.calls[0].url.params["limit"] == "1000"
        assert "after_id" not in http.calls[0].url.params
        assert http.calls[1].url.params["after_id"] == "claude-opus-5-5"
        assert http.calls[2].url.params["after_id"] == "claude-haiku-5-5"
        assert ids == [
            "claude-sonnet-5-5",
            "claude-opus-5-5",
            "claude-haiku-5-5",
            "claude-sonnet-4-5",
            "claude-3-haiku-20240307",
        ]

    def test_display_names_and_deprecation(self) -> None:
        models = _catalog(ProviderName.ANTHROPIC, self._http()).list_models().models
        assert models[0].label == "Claude Sonnet 5.5"
        old = models[-1]
        assert old.deprecated
        assert old.detail == "deprecated"
        assert not old.shortlisted


class TestGemini:
    def _http(self) -> FakeHttp:
        pages = iter(GEMINI_PAGES)
        return FakeHttp().on(
            "GET", f"{GEMINI}/models", lambda _request: json_response(200, next(pages))
        )

    def test_follows_page_tokens_filters_and_sorts(self) -> None:
        http = self._http()
        ids = _ids(_catalog(ProviderName.GEMINI, http))
        assert [c.url.params.get("pageToken") for c in http.calls] == [None, "page-2", "page-3"]
        assert http.calls[0].url.params["pageSize"] == "1000"
        assert ids == [
            "gemini-3.0-flash",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemma-3-27b-it",
            "gemini-3.0-pro-preview",
            "gemini-2.0-flash-exp",
        ]

    def test_previews_are_kept_out_of_the_shortlist(self) -> None:
        shortlist = _shortlist(_catalog(ProviderName.GEMINI, self._http()))
        assert "gemini-3.0-pro-preview" not in shortlist
        assert "gemini-2.0-flash-exp" not in shortlist

    def test_a_later_page_failure_raises_after_a_valid_key(self) -> None:
        responses = iter([json_response(200, GEMINI_PAGES[0]), json_response(503)])
        http = FakeHttp().on("GET", f"{GEMINI}/models", lambda _request: next(responses))
        catalog = _catalog(ProviderName.GEMINI, http)
        assert catalog.check_key() is KeyCheck.VALID
        with pytest.raises(TransientLLMError):
            catalog.list_models()


class TestOpenRouter:
    def _catalog(self) -> ModelCatalog:
        http = FakeHttp().on("GET", f"{OPENROUTER}/models", json_response(200, OPENROUTER_MODELS))
        return _catalog(ProviderName.OPENROUTER, http)

    def test_filters_and_sorts_newest_first(self) -> None:
        assert _ids(self._catalog()) == [
            "anthropic/claude-sonnet-5.5",
            "qwen/qwen3.8-flash",
            "qwen/qwen3.8-27b",
            "still/valid",
        ]

    def test_free_models_are_marked(self) -> None:
        models = {m.id: m for m in self._catalog().list_models().models}
        assert models["qwen/qwen3.8-27b"].detail == "free"
        assert models["qwen/qwen3.8-flash"].detail == ""
        assert models["anthropic/claude-sonnet-5.5"].label == "Anthropic: Claude Sonnet 5.5"


class TestHuggingFace:
    def test_keeps_live_text_models_with_provider_detail(self) -> None:
        http = FakeHttp().on("GET", f"{HF}/models", json_response(200, HUGGINGFACE_MODELS))
        models = _catalog(ProviderName.HUGGINGFACE, http).list_models().models
        assert [m.id for m in models] == ["Qwen/Qwen3-Coder", "deepseek-ai/DeepSeek-V4.1-Flash"]
        assert models[0].detail == "1 provider"
        assert models[1].detail == "2 providers · from $0.40/1M output tokens"


class TestOllama:
    def test_lists_chat_models_newest_first_and_names_hidden_ones(self) -> None:
        http = FakeHttp().on("GET", f"{OLLAMA}/api/tags", json_response(200, OLLAMA_TAGS))
        listing = _catalog(ProviderName.OLLAMA, http, OLLAMA, key=None).list_models()
        assert [m.id for m in listing.models] == ["qwen3:8b", "gemma3:4b"]
        assert listing.models[1].detail == "4.3B · Q4_K_M · 3.3 GB"
        assert listing.hidden == ("nomic-embed-text:latest",)
        assert all(m.shortlisted for m in listing.models)

    def test_old_servers_without_capabilities_drop_embedding_families(self) -> None:
        http = FakeHttp().on("GET", f"{OLLAMA}/api/tags", json_response(200, OLLAMA_OLD_TAGS))
        listing = _catalog(ProviderName.OLLAMA, http, OLLAMA, key=None).list_models()
        assert [m.id for m in listing.models] == ["llama3.1:latest"]
        assert set(listing.hidden) == {"all-minilm:latest", "mxbai-embed-large:latest"}

    def test_a_key_is_sent_as_a_bearer_token(self) -> None:
        http = FakeHttp().on("GET", "https://ollama.com/api/tags", json_response(200, {}))
        _catalog(ProviderName.OLLAMA, http, "https://ollama.com").list_models()
        assert http.calls[0].headers["authorization"] == f"Bearer {KEY}"

    def test_the_key_cannot_be_checked_by_listing(self) -> None:
        http = FakeHttp()
        assert _catalog(ProviderName.OLLAMA, http, "https://ollama.com").check_key() is (
            KeyCheck.UNKNOWN
        )
        assert http.calls == []


class TestProbe:
    @pytest.mark.parametrize(
        ("provider", "url", "reply", "cap"),
        [
            (
                ProviderName.OPENAI,
                f"{OPENAI}/chat/completions",
                {"choices": []},
                ("max_completion_tokens",),
            ),
            (ProviderName.OPENROUTER, f"{OPENROUTER}/chat/completions", {}, ("max_tokens",)),
            (ProviderName.HUGGINGFACE, f"{HF}/chat/completions", {}, ("max_tokens",)),
            (ProviderName.ANTHROPIC, f"{ANTHROPIC}/messages", {"content": []}, ("max_tokens",)),
            (
                ProviderName.GEMINI,
                f"{GEMINI}/models/m-1:generateContent",
                {},
                ("generationConfig", "maxOutputTokens"),
            ),
            (ProviderName.OLLAMA, f"{OLLAMA}/api/chat", {}, ("options", "num_predict")),
        ],
    )
    def test_any_2xx_passes_and_output_is_capped(
        self, provider: ProviderName, url: str, reply: dict[str, Any], cap: tuple[str, ...]
    ) -> None:
        http = FakeHttp().on("POST", url, json_response(200, reply))
        base = OLLAMA if provider is ProviderName.OLLAMA else None
        _catalog(provider, http, base).probe("m-1")
        body: Any = http.calls[0].body
        for part in cap:
            body = body[part]
        assert body == 16
        assert PROBE_PROMPT in str(http.calls[0].body)
        assert "temperature" not in str(http.calls[0].body)

    def test_a_retired_model_is_reported(self) -> None:
        http = FakeHttp().on("POST", f"{OPENAI}/chat/completions", json_response(404))
        with pytest.raises(ModelNotAvailableError, match="m-1"):
            _catalog(ProviderName.OPENAI, http).probe("m-1")

    def test_a_token_without_inference_permission_is_an_auth_error(self) -> None:
        http = FakeHttp().on("POST", f"{HF}/chat/completions", json_response(403))
        with pytest.raises(AuthenticationError):
            _catalog(ProviderName.HUGGINGFACE, http).probe("m-1")


class TestDetectOllama:
    def test_found_with_its_version(self) -> None:
        http = FakeHttp().on(
            "GET", f"{OLLAMA}/api/version", json_response(200, {"version": "0.40.12"})
        )
        server = detect_ollama(OLLAMA, None, http.transport)
        assert (server.reachable, server.version, server.needs_key, server.error) == (
            True,
            "0.40.12",
            False,
            None,
        )

    def test_refused(self) -> None:
        http = FakeHttp().on("GET", f"{OLLAMA}/api/version", httpx.ConnectError("refused"))
        server = detect_ollama(OLLAMA, None, http.transport)
        assert not server.reachable
        assert server.error == "connection refused"

    def test_timed_out(self) -> None:
        http = FakeHttp().on("GET", f"{OLLAMA}/api/version", httpx.ConnectTimeout("slow"))
        server = detect_ollama(OLLAMA, None, http.transport)
        assert not server.reachable
        assert server.error == "timed out after 10s"

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_protected_server_needs_a_key(self, status: int) -> None:
        url = "https://ollama.internal.example:11434"
        http = FakeHttp().on("GET", f"{url}/api/version", json_response(status))
        server = detect_ollama(url, None, http.transport)
        assert server.reachable
        assert server.needs_key

    def test_the_key_is_sent_when_given(self) -> None:
        url = "https://ollama.internal.example:11434"
        http = FakeHttp().on("GET", f"{url}/api/version", json_response(200, {"version": "1"}))
        detect_ollama(url, KEY, http.transport)
        assert http.calls[0].headers["authorization"] == f"Bearer {KEY}"

    def test_another_status_is_reported(self) -> None:
        http = FakeHttp().on("GET", f"{OLLAMA}/api/version", json_response(500))
        server = detect_ollama(OLLAMA, None, http.transport)
        assert not server.reachable
        assert server.error == "HTTP 500"
