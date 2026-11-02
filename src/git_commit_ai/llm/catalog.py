"""What setup asks each provider: is this key good, which models can it use, does a
chosen model answer? Every rule here is described in the plan's provider table (§5).

Listings come from the provider's API. Nothing here presents a hardcoded model as
available; the registry's fallback list is only for when listing fails.
"""

from __future__ import annotations

import datetime
import enum
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from git_commit_ai.core.config import ProviderName
from git_commit_ai.core.exceptions import (
    AuthenticationError,
    LLMError,
    OllamaUnreachableError,
    RateLimitError,
    TransientLLMError,
)
from git_commit_ai.llm._common import dig
from git_commit_ai.llm.anthropic_provider import anthropic_headers
from git_commit_ai.llm.gemini_provider import gemini_headers
from git_commit_ai.llm.http import HttpClient
from git_commit_ai.llm.ollama_provider import ollama_headers
from git_commit_ai.llm.registry import spec

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import httpx

SETUP_TIMEOUT = 10.0
PROBE_PROMPT = "Reply with OK"
PROBE_MAX_TOKENS = 16
_MAX_RATE_LIMIT_WAIT = 10.0
_T = TypeVar("_T")


class KeyCheck(enum.Enum):
    """Result of a cheap key check. ``UNKNOWN`` (timeout, 5xx) means: carry on, and let
    the probe be the real check."""

    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModelInfo:
    """One model a provider offers.

    Attributes:
        label: Display name, or the id when the provider has none.
        detail: A short hint for the menu ("free", "4.3B · Q4_K_M · 3.3 GB", "deprecated").
        shortlisted: Whether the model may appear in the short menu before "Show all".
    """

    id: str
    label: str
    detail: str = ""
    deprecated: bool = False
    shortlisted: bool = True


@dataclass(frozen=True)
class ModelListing:
    """Usable models, best first, plus the names of models hidden as unusable for chat."""

    models: list[ModelInfo]
    hidden: tuple[str, ...] = ()


class ModelCatalog(Protocol):
    """Setup-time questions for one provider and key."""

    check_failure: str | None
    """Why the last ``check_key`` returned ``UNKNOWN``; ``None`` otherwise."""

    def check_key(self) -> KeyCheck:
        """Ask the provider whether the key is accepted."""
        ...

    def list_models(self) -> ModelListing:
        """Models the key can use, filtered and sorted.

        Raises:
            LLMError: If listing fails.
        """
        ...

    def probe(self, model: str) -> None:
        """Send one tiny request to ``model``. Any 2xx passes, even an empty reply.

        Raises:
            AuthenticationError: The key was rejected or lacks inference permission.
            ModelNotAvailableError: The provider does not serve ``model`` here.
            LLMError: Anything else.
        """
        ...


@dataclass(frozen=True)
class OllamaServer:
    """What ``GET /api/version`` said about an Ollama server."""

    reachable: bool
    version: str | None
    needs_key: bool
    error: str | None


class _Catalog:
    """Shared plumbing: one HTTP client, one retry on a short rate limit, and a cached
    first listing response for providers whose key check is the listing call."""

    def __init__(
        self,
        provider: ProviderName,
        client: HttpClient,
        sleep: Callable[[float], None],
    ) -> None:
        self.provider = provider
        self._http = client
        self._sleep = sleep
        self._first_page: Any = None
        self.check_failure: str | None = None

    def _call(self, request: Callable[[], _T]) -> _T:
        try:
            return request()
        except RateLimitError as exc:
            wait = exc.retry_after
            if wait is None or wait > _MAX_RATE_LIMIT_WAIT:
                raise
            self._sleep(wait)
            return request()

    def _check(self, request: Callable[[], object]) -> KeyCheck:
        self.check_failure = None
        try:
            self._call(request)
        except AuthenticationError:
            return KeyCheck.INVALID
        except LLMError as exc:
            self.check_failure = failure_reason(exc)
            return KeyCheck.UNKNOWN
        return KeyCheck.VALID

    def _get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        return self._call(lambda: self._http.get_json(path, params))

    def _post(self, path: str, body: Mapping[str, Any], model: str) -> None:
        self._call(lambda: self._http.post_json(path, body, model=model))


class _OpenAICompatibleCatalog(_Catalog):
    """OpenAI, OpenRouter and Hugging Face: ``POST /chat/completions`` for the probe."""

    cap_field = "max_tokens"

    def probe(self, model: str) -> None:
        """Send one capped request to ``model``."""
        body = {
            "model": model,
            "messages": [{"role": "user", "content": PROBE_PROMPT}],
            self.cap_field: PROBE_MAX_TOKENS,
        }
        self._post("/chat/completions", body, model)


_OPENAI_KEEP = re.compile(r"^(gpt-|o\d|chatgpt-)")
_OPENAI_DROP = (
    "embedding",
    "tts",
    "whisper",
    "dall-e",
    "image",
    "audio",
    "realtime",
    "transcribe",
    "search",
    "moderation",
    "instruct",
    "codex",
)
_DATED = re.compile(r"-\d{4}-\d{2}-\d{2}$")


class OpenAICatalog(_OpenAICompatibleCatalog):
    """``GET /models`` both checks the key and lists the models."""

    cap_field = "max_completion_tokens"

    def check_key(self) -> KeyCheck:
        """Fetch the listing; its status is the key check."""
        return self._check(self._models)

    def list_models(self) -> ModelListing:
        """Chat models, newest first; dated snapshots only under "Show all"."""
        entries = [e for e in _list(self._models(), "data") if isinstance(e.get("id"), str)]
        kept = [
            e
            for e in entries
            if _OPENAI_KEEP.match(e["id"]) and not any(w in e["id"] for w in _OPENAI_DROP)
        ]
        kept.sort(key=lambda e: _number(e.get("created")), reverse=True)
        return ModelListing(
            [ModelInfo(e["id"], e["id"], shortlisted=not _DATED.search(e["id"])) for e in kept]
        )

    def _models(self) -> Any:
        if self._first_page is None:
            self._first_page = self._get("/models")
        return self._first_page


class OpenRouterCatalog(_OpenAICompatibleCatalog):
    """``GET /key`` checks the key; ``GET /models`` is public and lists everything."""

    def check_key(self) -> KeyCheck:
        """Ask ``/key`` about the key."""
        return self._check(lambda: self._get("/key"))

    def list_models(self) -> ModelListing:
        """Text-in, text-out models that have not expired, newest first."""
        today = datetime.datetime.now(datetime.UTC).date().isoformat()
        models: list[tuple[float, ModelInfo]] = []
        for entry in _list(self._get("/models"), "data"):
            model_id = entry.get("id")
            if not isinstance(model_id, str):
                continue
            architecture = entry.get("architecture") or {}
            if "text" not in (architecture.get("output_modalities") or []):
                continue
            if "text" not in (architecture.get("input_modalities") or []):
                continue
            if model_id.endswith(":batch") or "guard" in model_id or "embed" in model_id:
                continue
            expiry = entry.get("expiration_date")
            if isinstance(expiry, str) and expiry[:10] < today:
                continue
            pricing = entry.get("pricing") or {}
            free = pricing.get("prompt") == "0" and pricing.get("completion") == "0"
            name = entry.get("name")
            label = name if isinstance(name, str) else model_id
            models.append(
                (_number(entry.get("created")), ModelInfo(model_id, label, "free" if free else ""))
            )
        models.sort(key=lambda pair: pair[0], reverse=True)
        return ModelListing([info for _, info in models])


class HuggingFaceCatalog(_OpenAICompatibleCatalog):
    """``whoami-v2`` checks the token (``/v1/models`` is public and accepts any token)."""

    WHOAMI_URL = "https://huggingface.co/api/whoami-v2"

    def check_key(self) -> KeyCheck:
        """Ask the Hub who the token belongs to."""
        return self._check(lambda: self._get(self.WHOAMI_URL))

    def list_models(self) -> ModelListing:
        """Text models with at least one live inference provider, newest first."""
        models: list[tuple[float, ModelInfo]] = []
        for entry in _list(self._get("/models"), "data"):
            model_id = entry.get("id")
            if not isinstance(model_id, str):
                continue
            outputs = dig(entry, "architecture", "output_modalities")
            if not isinstance(outputs, list) or "text" not in outputs:
                continue
            live = [
                p
                for p in entry.get("providers") or []
                if isinstance(p, dict) and p.get("status") == "live"
            ]
            if not live:
                continue
            models.append(
                (_number(entry.get("created")), ModelInfo(model_id, model_id, _hf_detail(live)))
            )
        models.sort(key=lambda pair: pair[0], reverse=True)
        return ModelListing([info for _, info in models])


class AnthropicCatalog(_Catalog):
    """``GET /v1/models`` checks the key and lists models, following ``after_id``."""

    def check_key(self) -> KeyCheck:
        """Fetch the first page; its status is the key check."""
        return self._check(self._page_one)

    def list_models(self) -> ModelListing:
        """Every model, in API order (newest first), deprecated ones last."""
        page = self._page_one()
        entries = list(_list(page, "data"))
        while page.get("has_more") and isinstance(page.get("last_id"), str):
            page = self._get("/models", {"limit": 1000, "after_id": page["last_id"]})
            entries += _list(page, "data")
        models = []
        for entry in entries:
            model_id = entry.get("id")
            if not isinstance(model_id, str):
                continue
            deprecated = _lifecycle(entry) == "deprecated"
            label = entry.get("display_name")
            models.append(
                ModelInfo(
                    model_id,
                    label if isinstance(label, str) else model_id,
                    "deprecated" if deprecated else "",
                    deprecated=deprecated,
                    shortlisted=not deprecated,
                )
            )
        models.sort(key=lambda m: m.deprecated)
        return ModelListing(models)

    def probe(self, model: str) -> None:
        """Send one capped request to ``model``."""
        body = {
            "model": model,
            "max_tokens": PROBE_MAX_TOKENS,
            "messages": [{"role": "user", "content": PROBE_PROMPT}],
        }
        self._post("/messages", body, model)

    def _page_one(self) -> Any:
        if self._first_page is None:
            self._first_page = self._get("/models", {"limit": 1000})
        return self._first_page


_GEMINI_DROP = ("embedding", "imagen", "veo", "-tts", "image", "native-audio", "live", "aqa")
_GEMINI_VERSION = re.compile(r"gemini-(\d+(?:\.\d+)?)")
_GEMINI_UNSTABLE = re.compile(r"preview|exp")


class GeminiCatalog(_Catalog):
    """``GET /v1beta/models`` checks the key and lists models, following ``nextPageToken``.
    A bad key gets HTTP 400 with reason ``API_KEY_INVALID``."""

    def check_key(self) -> KeyCheck:
        """Fetch the first page; its status is the key check."""
        return self._check(self._page_one)

    def list_models(self) -> ModelListing:
        """Text models: stable before preview, newest version first."""
        page = self._page_one()
        entries = list(_list(page, "models"))
        while isinstance(page.get("nextPageToken"), str) and page["nextPageToken"]:
            page = self._get("/models", {"pageSize": 1000, "pageToken": page["nextPageToken"]})
            entries += _list(page, "models")
        models = []
        for entry in entries:
            name = entry.get("name")
            if not isinstance(name, str):
                continue
            model_id = name.removeprefix("models/")
            if "generateContent" not in (entry.get("supportedGenerationMethods") or []):
                continue
            if any(word in model_id for word in _GEMINI_DROP):
                continue
            label = entry.get("displayName")
            stable = not _GEMINI_UNSTABLE.search(model_id)
            models.append(
                ModelInfo(
                    model_id, label if isinstance(label, str) else model_id, shortlisted=stable
                )
            )
        models.sort(key=lambda m: (not m.shortlisted, -_gemini_version(m.id), m.id))
        return ModelListing(models)

    def probe(self, model: str) -> None:
        """Send one capped request to ``model``."""
        body = {
            "contents": [{"role": "user", "parts": [{"text": PROBE_PROMPT}]}],
            "generationConfig": {"maxOutputTokens": PROBE_MAX_TOKENS},
        }
        self._post(f"/models/{model.removeprefix('models/')}:generateContent", body, model)

    def _page_one(self) -> Any:
        if self._first_page is None:
            self._first_page = self._get("/models", {"pageSize": 1000})
        return self._first_page


class OllamaCatalog(_Catalog):
    """``GET /api/tags`` lists pulled models. It is public even on ollama.com, so it cannot
    check a key; the probe does."""

    def check_key(self) -> KeyCheck:
        """Always ``UNKNOWN``: Ollama has no endpoint that checks a key on its own."""
        return KeyCheck.UNKNOWN

    def list_models(self) -> ModelListing:
        """Chat-capable models, most recently changed first; embedding models hidden."""
        models: list[tuple[str, ModelInfo]] = []
        hidden: list[str] = []
        for entry in _list(self._get("/api/tags"), "models"):
            name = entry.get("name")
            if not isinstance(name, str):
                continue
            if not _ollama_can_chat(entry):
                hidden.append(name)
                continue
            modified = entry.get("modified_at")
            models.append(
                (
                    modified if isinstance(modified, str) else "",
                    ModelInfo(name, name, _ollama_detail(entry)),
                )
            )
        models.sort(key=lambda pair: pair[0], reverse=True)
        return ModelListing([info for _, info in models], tuple(hidden))

    def probe(self, model: str) -> None:
        """Send one capped request to ``model``."""
        body = {
            "model": model,
            "messages": [{"role": "user", "content": PROBE_PROMPT}],
            "stream": False,
            "options": {"num_predict": PROBE_MAX_TOKENS},
        }
        self._post("/api/chat", body, model)


def catalog_for(
    provider: ProviderName,
    api_key: str | None,
    base_url: str,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ModelCatalog:
    """The catalogue for ``provider``, talking to ``base_url`` with ``api_key``."""
    key = api_key or ""
    entry = spec(provider)
    headers: dict[str, str]
    if entry.backend == "anthropic":
        headers = anthropic_headers(key)
    elif entry.backend == "gemini":
        headers = gemini_headers(key)
    elif entry.backend == "ollama":
        headers = ollama_headers(api_key)
    else:
        headers = {"Authorization": f"Bearer {key}", **dict(entry.extra_headers)}
    client = HttpClient(
        provider.value,
        base_url,
        headers,
        SETUP_TIMEOUT,
        transport,
        secrets=(key,) if key else (),
        unreachable=(lambda: OllamaUnreachableError(base_url))
        if entry.backend == "ollama"
        else None,
    )
    classes: dict[
        ProviderName, Callable[[ProviderName, HttpClient, Callable[[float], None]], ModelCatalog]
    ] = {
        ProviderName.OPENAI: OpenAICatalog,
        ProviderName.OPENROUTER: OpenRouterCatalog,
        ProviderName.HUGGINGFACE: HuggingFaceCatalog,
        ProviderName.ANTHROPIC: AnthropicCatalog,
        ProviderName.GEMINI: GeminiCatalog,
        ProviderName.OLLAMA: OllamaCatalog,
    }
    return classes[provider](provider, client, sleep)


def detect_ollama(
    base_url: str, api_key: str | None, transport: httpx.BaseTransport | None = None
) -> OllamaServer:
    """Look for an Ollama server at ``base_url`` with ``GET /api/version``."""
    client = HttpClient(
        "ollama",
        base_url,
        ollama_headers(api_key),
        SETUP_TIMEOUT,
        transport,
        secrets=(api_key,) if api_key else (),
        unreachable=lambda: OllamaUnreachableError(base_url),
    )
    try:
        body = client.get_json("/api/version")
    except OllamaUnreachableError:
        return OllamaServer(False, None, False, "connection refused")
    except AuthenticationError:
        return OllamaServer(True, None, True, None)
    except TransientLLMError as exc:
        return OllamaServer(False, None, False, failure_reason(exc))
    except LLMError as exc:
        return OllamaServer(False, None, False, str(exc))
    version = dig(body, "version")
    return OllamaServer(True, version if isinstance(version, str) else None, False, None)


def failure_reason(error: LLMError) -> str:
    """A short reason for a failed setup call, for "failed (...)" status lines."""
    if isinstance(error, RateLimitError):
        return "rate limited"
    if isinstance(error, TransientLLMError):
        detail = str(error).split(": ", 1)[-1]
        return f"timed out after {SETUP_TIMEOUT:.0f}s" if "timed out" in detail else detail
    return str(error)


def _list(payload: Any, field: str) -> list[dict[str, Any]]:
    items = payload.get(field) if isinstance(payload, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _lifecycle(entry: Mapping[str, Any]) -> str:
    value = entry.get("lifecycle")
    if isinstance(value, dict):
        value = value.get("status")
    return value.lower() if isinstance(value, str) else ""


def _gemini_version(model_id: str) -> float:
    match = _GEMINI_VERSION.search(model_id)
    return float(match.group(1)) if match else 0.0


def _hf_detail(live: list[dict[str, Any]]) -> str:
    count = f"{len(live)} provider{'s' if len(live) != 1 else ''}"
    prices = [
        p["pricing"]["output"]
        for p in live
        if isinstance(p.get("pricing"), dict)
        and isinstance(p["pricing"].get("output"), int | float)
    ]
    return f"{count} · from ${min(prices):.2f}/1M output tokens" if prices else count


def _ollama_can_chat(entry: Mapping[str, Any]) -> bool:
    capabilities = entry.get("capabilities")
    if isinstance(capabilities, list):
        return "completion" in capabilities
    details = _details(entry)
    families = [details.get("family"), *(details.get("families") or [])]
    names = [entry.get("name"), *families]
    return not any(isinstance(n, str) and ("embed" in n or "bert" in n) for n in names)


def _ollama_detail(entry: Mapping[str, Any]) -> str:
    details = _details(entry)
    parts = [details.get("parameter_size"), details.get("quantization_level")]
    size = entry.get("size")
    if isinstance(size, int | float) and size > 0:
        parts.append(f"{size / 1e9:.1f} GB")
    return " · ".join(p for p in parts if isinstance(p, str) and p)


def _details(entry: Mapping[str, Any]) -> dict[str, Any]:
    details = entry.get("details")
    return details if isinstance(details, dict) else {}
