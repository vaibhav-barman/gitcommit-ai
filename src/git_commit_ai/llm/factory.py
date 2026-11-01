"""Provider selection from configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_commit_ai.core.config import PROVIDER_KEY_VARIABLES, ProviderName
from git_commit_ai.core.exceptions import MissingAPIKeyError
from git_commit_ai.llm.anthropic_provider import AnthropicProvider
from git_commit_ai.llm.gemini_provider import GeminiProvider
from git_commit_ai.llm.ollama_provider import OllamaProvider
from git_commit_ai.llm.openai_compatible import OpenAICompatibleBackend
from git_commit_ai.llm.retry import RetryingProvider, RetryPolicy

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from git_commit_ai.core.config import GitCommitAIConfig
    from git_commit_ai.llm.base import LLMProvider
    from git_commit_ai.llm.retry import RetryEvent

OPENAI_BASE_URL = "https://api.openai.com/v1"

_DEFAULT_MODELS: dict[ProviderName, str] = {
    ProviderName.OPENAI: "gpt-4o-mini",
    ProviderName.ANTHROPIC: "claude-sonnet-5-5",
    ProviderName.GEMINI: "gemini-2.5-flash",
    ProviderName.OLLAMA: "llama3.1",
}


def default_model(provider: ProviderName) -> str:
    """Model used when none is configured for ``provider``."""
    return _DEFAULT_MODELS[provider]


def api_key_variable(provider: ProviderName) -> str | None:
    """Environment variable holding ``provider``'s API key, or ``None`` if it needs none."""
    variables = PROVIDER_KEY_VARIABLES[provider]
    return variables[0] if variables else None


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
            the credential store; ignored by providers that need none.
        on_retry: Called before each retry wait, for progress reporting.
        transport: ``httpx`` transport for every request; tests pass a fake one.

    Raises:
        MissingAPIKeyError: If the provider needs a key and none is given.
    """
    provider = config.llm_provider
    model = config.llm_model or default_model(provider)
    key_variable = api_key_variable(provider)
    if key_variable is not None and not api_key:
        raise MissingAPIKeyError(provider.value, key_variable)
    key = api_key or ""
    temperature, timeout = config.llm_temperature, config.llm_timeout_seconds

    backend: LLMProvider
    if provider is ProviderName.OPENAI:
        backend = OpenAICompatibleBackend(
            provider.value, OPENAI_BASE_URL, model, key, temperature, timeout, transport=transport
        )
    elif provider is ProviderName.ANTHROPIC:
        backend = AnthropicProvider(model, key, temperature, timeout, transport)
    elif provider is ProviderName.GEMINI:
        backend = GeminiProvider(model, key, temperature, timeout, transport)
    else:
        backend = OllamaProvider(model, config.ollama_base_url, temperature, timeout, transport)

    policy = RetryPolicy(
        max_retries=config.llm_max_retries,
        base_delay=config.llm_retry_base_delay_seconds,
        max_delay=config.llm_retry_max_delay_seconds,
    )
    return RetryingProvider(backend, policy, on_retry)
