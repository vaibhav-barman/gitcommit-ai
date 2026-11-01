"""Provider selection from configuration and environment."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from git_commit_ai.core.config import PROVIDER_KEY_VARIABLES, ProviderName
from git_commit_ai.core.exceptions import MissingAPIKeyError, ProviderNotInstalledError
from git_commit_ai.llm.retry import RetryingProvider, RetryPolicy

if TYPE_CHECKING:
    from collections.abc import Callable

    from git_commit_ai.core.config import GitCommitAIConfig
    from git_commit_ai.llm.base import LLMProvider
    from git_commit_ai.llm.retry import RetryEvent


@dataclass(frozen=True)
class _BackendSpec:
    module: str
    class_name: str
    extra: str
    default_model: str
    credential_arg: str


_BACKENDS: dict[ProviderName, _BackendSpec] = {
    ProviderName.OPENAI: _BackendSpec(
        "git_commit_ai.llm.openai_provider",
        "OpenAIProvider",
        "openai",
        "gpt-4o-mini",
        "api_key",
    ),
    ProviderName.ANTHROPIC: _BackendSpec(
        "git_commit_ai.llm.anthropic_provider",
        "AnthropicProvider",
        "anthropic",
        "claude-sonnet-5-5",
        "api_key",
    ),
    ProviderName.GEMINI: _BackendSpec(
        "git_commit_ai.llm.gemini_provider",
        "GeminiProvider",
        "gemini",
        "gemini-2.5-flash",
        "api_key",
    ),
    ProviderName.OLLAMA: _BackendSpec(
        "git_commit_ai.llm.ollama_provider",
        "OllamaProvider",
        "ollama",
        "llama3.1",
        "base_url",
    ),
}


def default_model(provider: ProviderName) -> str:
    """Model used when none is configured for ``provider``."""
    return _BACKENDS[provider].default_model


def api_key_variable(provider: ProviderName) -> str | None:
    """Environment variable holding ``provider``'s API key, or ``None`` if it needs none."""
    variables = PROVIDER_KEY_VARIABLES[provider]
    return variables[0] if variables else None


def create_provider(
    config: GitCommitAIConfig,
    api_key: str | None,
    on_retry: Callable[[RetryEvent], None] | None = None,
) -> LLMProvider:
    """Instantiate the backend selected by ``config.llm_provider``.

    The backend is wrapped so rate limits and transient errors are retried
    with bounded exponential backoff (see the ``llm_*retr*`` settings).

    Backend modules are imported lazily so that a missing optional SDK only
    affects the provider that needs it.

    Args:
        config: Application configuration (provider, model, temperature, timeout).
        api_key: The provider's key, already resolved from the environment, ``.env`` or
            the credential store; ignored by providers that need none.
        on_retry: Called before each retry wait, for progress reporting.

    Raises:
        MissingAPIKeyError: If the provider needs a key and none is set.
        ProviderNotInstalledError: If the provider's SDK extra is not installed.
    """
    spec = _BACKENDS[config.llm_provider]
    model = config.llm_model or spec.default_model

    credential = config.ollama_base_url
    key_variable = api_key_variable(config.llm_provider)
    if key_variable is not None:
        if not api_key:
            raise MissingAPIKeyError(config.llm_provider.value, key_variable)
        credential = api_key

    try:
        module = importlib.import_module(spec.module)
    except ImportError as exc:
        raise ProviderNotInstalledError(config.llm_provider.value, spec.extra) from exc

    backend_class = getattr(module, spec.class_name)
    backend: LLMProvider = backend_class(
        model=model,
        temperature=config.llm_temperature,
        timeout=config.llm_timeout_seconds,
        **{spec.credential_arg: credential},
    )
    policy = RetryPolicy(
        max_retries=config.llm_max_retries,
        base_delay=config.llm_retry_base_delay_seconds,
        max_delay=config.llm_retry_max_delay_seconds,
    )
    return RetryingProvider(backend, policy, on_retry)
