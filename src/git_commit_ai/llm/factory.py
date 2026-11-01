"""Provider selection from configuration and environment."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from git_commit_ai.core.config import ProviderName
from git_commit_ai.core.exceptions import MissingAPIKeyError, ProviderNotInstalledError
from git_commit_ai.llm.retry import RetryingProvider, RetryPolicy

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

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
    key_env_var: str | None


_BACKENDS: dict[ProviderName, _BackendSpec] = {
    ProviderName.OPENAI: _BackendSpec(
        "git_commit_ai.llm.openai_provider",
        "OpenAIProvider",
        "openai",
        "gpt-4o-mini",
        "api_key",
        "OPENAI_API_KEY",
    ),
    ProviderName.ANTHROPIC: _BackendSpec(
        "git_commit_ai.llm.anthropic_provider",
        "AnthropicProvider",
        "anthropic",
        "claude-sonnet-5-5",
        "api_key",
        "ANTHROPIC_API_KEY",
    ),
    ProviderName.GEMINI: _BackendSpec(
        "git_commit_ai.llm.gemini_provider",
        "GeminiProvider",
        "gemini",
        "gemini-2.5-flash",
        "api_key",
        "GOOGLE_API_KEY",
    ),
    ProviderName.OLLAMA: _BackendSpec(
        "git_commit_ai.llm.ollama_provider",
        "OllamaProvider",
        "ollama",
        "llama3.1",
        "base_url",
        None,
    ),
}


def create_provider(
    config: GitCommitAIConfig,
    env: Mapping[str, str] | None = None,
    on_retry: Callable[[RetryEvent], None] | None = None,
) -> LLMProvider:
    """Instantiate the backend selected by ``config.llm_provider``.

    The backend is wrapped so rate limits and transient errors are retried
    with bounded exponential backoff (see the ``llm_*retr*`` settings).

    Backend modules are imported lazily so that a missing optional SDK only
    affects the provider that needs it.

    Args:
        config: Application configuration (provider, model, temperature, timeout).
        env: Environment to read API keys from; defaults to ``os.environ``.
        on_retry: Called before each retry wait, for progress reporting.

    Raises:
        MissingAPIKeyError: If the provider needs a key and none is set.
        ProviderNotInstalledError: If the provider's SDK extra is not installed.
    """
    environment = os.environ if env is None else env
    spec = _BACKENDS[config.llm_provider]
    model = config.llm_model or spec.default_model

    credential = config.ollama_base_url
    if spec.key_env_var is not None:
        credential = environment.get(spec.key_env_var, "")
        if not credential:
            raise MissingAPIKeyError(config.llm_provider.value, spec.key_env_var)

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
