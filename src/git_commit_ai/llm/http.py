"""The HTTP layer every provider backend and model catalogue shares.

Requests carry keys in headers only, never in URLs. Error bodies are read for
a provider's own message, which is redacted before it reaches an exception;
auth failures ignore the body entirely because some providers echo the key.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import httpx

from git_commit_ai.core.exceptions import (
    AuthenticationError,
    LLMError,
    LLMResponseError,
    ModelNotAvailableError,
    RateLimitError,
    RequestRejectedError,
    TransientLLMError,
)
from git_commit_ai.llm._common import parse_retry_after

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

REDACTED = "[redacted]"
# Key-shaped tokens: OpenAI, Anthropic and OpenRouter (sk-...), Hugging Face (hf_...) and
# Google (AIza...). The word boundary and required separator keep words like
# "risk_assessment" intact.
KEY_SHAPED = re.compile(r"\b(?:sk-ant-|sk-or-|sk-|hf_)[A-Za-z0-9_-]{8,}|\bAIza[0-9A-Za-z_-]{35}")
_MODEL_MISSING = re.compile(
    r"model.*(not found|does not exist|not supported)|invalid model", re.IGNORECASE | re.DOTALL
)
_MAX_MESSAGE = 300
_AUTH_STATUSES = frozenset({401, 403})
_TRANSIENT_STATUSES = frozenset({408, 529})
_RATE_LIMITED = 429
_NOT_FOUND = 404
_BAD_REQUEST = 400
_SERVER_ERRORS = range(500, 600)


class HttpClient:
    """JSON over HTTP for one provider, with failures mapped to typed ``LLMError``s.

    Args:
        provider: Name used in error messages.
        base_url: Prefix for every request path.
        headers: Sent with every request (auth headers go here, never in URLs).
        timeout: Seconds per request.
        transport: An ``httpx`` transport; tests pass a fake one.
        secrets: Strings removed from any provider text that reaches an error.
        unreachable: Builds the error for a refused connection; by default it is
            transient, like any other dropped connection.
    """

    def __init__(
        self,
        provider: str,
        base_url: str,
        headers: Mapping[str, str],
        timeout: float,
        transport: httpx.BaseTransport | None = None,
        secrets: Sequence[str] = (),
        unreachable: Callable[[], LLMError] | None = None,
    ) -> None:
        self._provider = provider
        self._secrets = tuple(s for s in secrets if s)
        self._unreachable = unreachable
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=dict(headers),
            timeout=timeout,
            transport=transport,
        )

    def get_json(
        self, path: str, params: Mapping[str, str | int] | None = None, model: str | None = None
    ) -> Any:
        """GET ``path`` and return the decoded JSON body."""
        return self._send("GET", path, params=params, model=model)

    def post_json(self, path: str, body: Mapping[str, Any], model: str | None = None) -> Any:
        """POST ``body`` as JSON to ``path`` and return the decoded JSON body.

        ``model`` lets a 404 or "model not found" error name the model.
        """
        return self._send("POST", path, json=body, model=model)

    def _send(self, method: str, path: str, model: str | None, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise TransientLLMError(self._provider, "request timed out") from exc
        except httpx.ConnectError as exc:
            if self._unreachable is not None:
                raise self._unreachable() from exc
            raise TransientLLMError(self._provider, "could not connect") from exc
        except httpx.TransportError as exc:
            raise TransientLLMError(self._provider, type(exc).__name__) from exc
        if not response.is_success:
            raise map_http_error(self._provider, response, self._secrets, model=model)
        try:
            return response.json()
        except ValueError as exc:
            raise LLMResponseError(
                f"Provider '{self._provider}' returned a response that is not JSON"
            ) from exc


def map_http_error(
    provider: str, response: httpx.Response, secrets: Sequence[str], model: str | None = None
) -> LLMError:
    """Turn an unsuccessful response into the matching ``LLMError`` (plan §4.5)."""
    status = response.status_code
    if status in _TRANSIENT_STATUSES or status in _SERVER_ERRORS:
        return TransientLLMError(provider, f"HTTP {status}")
    if status == _RATE_LIMITED:
        return RateLimitError(provider, parse_retry_after(response.headers))
    if status in _AUTH_STATUSES:
        return AuthenticationError(provider, status)
    error = _error_object(response)
    if status == _BAD_REQUEST and _has_reason(error, "API_KEY_INVALID"):
        return AuthenticationError(provider, status)
    message = _message(error)
    param = error.get("param") if isinstance(error.get("param"), str) else None
    if model is not None and (
        status == _NOT_FOUND
        or (status == _BAD_REQUEST and param in (None, "model") and _MODEL_MISSING.search(message))
    ):
        return ModelNotAvailableError(provider, model)
    return RequestRejectedError(provider, status, redact(message, secrets)[:_MAX_MESSAGE], param)


def redact(text: str, secrets: Sequence[str]) -> str:
    """Remove every known secret, and anything shaped like an API key, from ``text``."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return KEY_SHAPED.sub(REDACTED, text)


def _error_object(response: httpx.Response) -> dict[str, Any]:
    """The ``error`` member of a JSON error body, normalised to a dict."""
    try:
        body = response.json()
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}
    error = body.get("error")
    if isinstance(error, dict):
        return error
    if isinstance(error, str):  # Ollama: {"error": "model 'x' not found"}
        return {"message": error}
    return {}


def _message(error: Mapping[str, Any]) -> str:
    message = error.get("message")
    return message if isinstance(message, str) else ""


def _has_reason(error: Mapping[str, Any], reason: str) -> bool:
    details = error.get("details")
    return isinstance(details, list) and any(
        isinstance(d, dict) and d.get("reason") == reason for d in details
    )
