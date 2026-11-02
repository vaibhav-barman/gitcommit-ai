"""Provider selection from configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_commit_ai.core.exceptions import MissingAPIKeyError
from git_commit_ai.llm.anthropic_provider import AnthropicProvider
from git_commit_ai.llm.gemini_provider import GeminiProvider
from git_commit_ai.llm.ollama_provider import OllamaProvider
from git_commit_ai.llm.openai_compatible import OpenAICompatibleBackend
from git_commit_ai.llm.registry import spec
from git_commit_ai.llm.retry import RetryingProvider, RetryPolicy

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from git_commit_ai.core.config import GitCommitAIConfig
    from git_commit_ai.llm.base import LLMProvider
    from git_commit_ai.llm.retry import RetryEvent


def create_provider(
    config: GitCommitAIConfig,
    api_key: str | None,
    on_retry: Callable[[RetryEvent], None] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> LLMProvider:
    """Instantiate the backend selected by ``config.llm_provider``.

    The backend is wrapped so rate limits and transient errors are retried
    with bounded exponential backoff (see the ``llm_*retr*`` settings).

    Args:
        config: Application configuration (provider, model, temperature, timeout).
        api_key: The provider's key, already resolved from the environment, ``.env`` or
            the credential store. Optional for Ollama, which sends it as a Bearer token.
        on_retry: Called before each retry wait, for progress reporting.
        transport: ``httpx`` transport for every request; tests pass a fake one.

    Raises:
        MissingAPIKeyError: If the provider needs a key and none is given.
    """
    entry = spec(config.llm_provider)
    model = config.llm_model or entry.default_model
    if entry.requires_key and not api_key:
        raise MissingAPIKeyError(entry.name.value, entry.key_variables[0])
    temperature, timeout = config.llm_temperature, config.llm_timeout_seconds

    backend: LLMProvider
    if entry.backend == "openai_compatible":
        backend = OpenAICompatibleBackend(
            entry.name.value,
            entry.base_url,
            model,
            api_key or "",
            temperature,
            timeout,
            extra_headers=dict(entry.extra_headers),
            transport=transport,
        )
    elif entry.backend == "anthropic":
        backend = AnthropicProvider(model, api_key or "", temperature, timeout, transport)
    elif entry.backend == "gemini":
        backend = GeminiProvider(model, api_key or "", temperature, timeout, transport)
    else:
        backend = OllamaProvider(
            model, config.ollama_base_url, temperature, timeout, api_key, transport
        )

    policy = RetryPolicy(
        max_retries=config.llm_max_retries,
        base_delay=config.llm_retry_base_delay_seconds,
        max_delay=config.llm_retry_max_delay_seconds,
    )
    return RetryingProvider(backend, policy, on_retry)
