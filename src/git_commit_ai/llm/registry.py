"""Everything that differs between providers, in one table."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from git_commit_ai.core.config import PROVIDER_KEY_VARIABLES, ProviderName

FALLBACKS_AS_OF = "November 2026"
PROJECT_URL = "https://github.com/vaibhav-barman/gitcommit-ai"
HUGGING_FACE_TOKEN_URL = (
    "https://huggingface.co/settings/tokens/new"
    "?ownUserPermissions=inference.serverless.write&tokenType=fineGrained"
)

Backend = Literal["openai_compatible", "anthropic", "gemini", "ollama"]


@dataclass(frozen=True)
class ProviderSpec:
    """What the wizard, the factory and the docs need to know about one provider.

    Attributes:
        key_variables: Variables read for the key, canonical first.
        requires_key: False only for Ollama, where a key is optional.
        key_url: Where to create a key.
        default_model: Used by environment-only setups when no model is set.
        fallback_models: Suggested only when listing the provider's models fails.
        extra_headers: Sent with every request; never secrets.
    """

    name: ProviderName
    label: str
    blurb: str
    key_variables: tuple[str, ...]
    requires_key: bool
    key_url: str | None
    base_url: str
    default_model: str
    fallback_models: tuple[str, ...]
    backend: Backend
    extra_headers: tuple[tuple[str, str], ...] = ()


# Menu order: alphabetical by label, Ollama last, no vendor favoured.
_SPECS = (
    ProviderSpec(
        name=ProviderName.ANTHROPIC,
        key_variables=PROVIDER_KEY_VARIABLES[ProviderName.ANTHROPIC],
        label="Anthropic",
        blurb="Claude models · API key",
        requires_key=True,
        key_url="https://console.anthropic.com/settings/keys",
        base_url="https://api.anthropic.com/v1",
        default_model="claude-sonnet-5-5",
        fallback_models=("claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"),
        backend="anthropic",
    ),
    ProviderSpec(
        name=ProviderName.GEMINI,
        key_variables=PROVIDER_KEY_VARIABLES[ProviderName.GEMINI],
        label="Google Gemini",
        blurb="Gemini models · API key",
        requires_key=True,
        key_url="https://aistudio.google.com/apikey",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        default_model="gemini-2.5-flash",
        fallback_models=("gemini-2.5-flash", "gemini-2.5-pro"),
        backend="gemini",
    ),
    ProviderSpec(
        name=ProviderName.HUGGINGFACE,
        key_variables=PROVIDER_KEY_VARIABLES[ProviderName.HUGGINGFACE],
        label="Hugging Face",
        blurb="Open models via Inference Providers · access token",
        requires_key=True,
        key_url=HUGGING_FACE_TOKEN_URL,
        base_url="https://router.huggingface.co/v1",
        default_model="deepseek-ai/DeepSeek-V4.1-Flash",
        fallback_models=("deepseek-ai/DeepSeek-V4.1-Flash",),
        backend="openai_compatible",
    ),
    ProviderSpec(
        name=ProviderName.OPENAI,
        key_variables=PROVIDER_KEY_VARIABLES[ProviderName.OPENAI],
        label="OpenAI",
        blurb="GPT models · API key",
        requires_key=True,
        key_url="https://platform.openai.com/api-keys",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        fallback_models=("gpt-5-mini", "gpt-5"),
        backend="openai_compatible",
    ),
    ProviderSpec(
        name=ProviderName.OPENROUTER,
        key_variables=PROVIDER_KEY_VARIABLES[ProviderName.OPENROUTER],
        label="OpenRouter",
        blurb="Hundreds of models from many vendors · API key",
        requires_key=True,
        key_url="https://openrouter.ai/settings/keys",
        base_url="https://openrouter.ai/api/v1",
        default_model="anthropic/claude-sonnet-5.5",
        fallback_models=("anthropic/claude-sonnet-5.5", "anthropic/claude-haiku-5.5"),
        backend="openai_compatible",
        extra_headers=(("HTTP-Referer", PROJECT_URL), ("X-Title", "GitCommit AI")),
    ),
    ProviderSpec(
        name=ProviderName.OLLAMA,
        key_variables=PROVIDER_KEY_VARIABLES[ProviderName.OLLAMA],
        label="Ollama",
        blurb="Run models on this computer or your own server · no key for local",
        requires_key=False,
        key_url="https://ollama.com/settings/keys",
        base_url="http://localhost:11434",
        default_model="llama3.1",
        fallback_models=(),
        backend="ollama",
    ),
)
PROVIDERS: dict[ProviderName, ProviderSpec] = {entry.name: entry for entry in _SPECS}


def spec(provider: ProviderName) -> ProviderSpec:
    """The registry entry for ``provider``."""
    return PROVIDERS[provider]
