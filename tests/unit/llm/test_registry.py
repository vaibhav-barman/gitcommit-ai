"""The provider registry: one entry per provider, consistent with the key variables."""

from __future__ import annotations

import pytest

from git_commit_ai.core.config import PROVIDER_KEY_VARIABLES, ProviderName
from git_commit_ai.llm.registry import FALLBACKS_AS_OF, PROVIDERS, spec


def test_every_provider_has_a_spec() -> None:
    assert set(PROVIDERS) == set(ProviderName)
    for provider in ProviderName:
        assert spec(provider).name is provider


@pytest.mark.parametrize("provider", list(ProviderName))
def test_key_variables_come_from_core(provider: ProviderName) -> None:
    assert spec(provider).key_variables == PROVIDER_KEY_VARIABLES[provider]


@pytest.mark.parametrize("provider", [p for p in ProviderName if p is not ProviderName.OLLAMA])
def test_hosted_providers_need_a_key_and_have_fallbacks(provider: ProviderName) -> None:
    entry = spec(provider)
    assert entry.requires_key
    assert entry.key_variables
    assert entry.key_url
    assert entry.fallback_models
    assert all(model.strip() for model in entry.fallback_models)


def test_ollama_needs_no_key_and_has_no_fallbacks() -> None:
    entry = spec(ProviderName.OLLAMA)
    assert not entry.requires_key
    assert entry.key_variables == ("OLLAMA_API_KEY",)
    assert entry.fallback_models == ()


def test_gemini_accepts_both_key_variables_with_google_first() -> None:
    assert spec(ProviderName.GEMINI).key_variables == ("GOOGLE_API_KEY", "GEMINI_API_KEY")


@pytest.mark.parametrize(
    ("provider", "base_url", "backend"),
    [
        (ProviderName.OPENAI, "https://api.openai.com/v1", "openai_compatible"),
        (ProviderName.OPENROUTER, "https://openrouter.ai/api/v1", "openai_compatible"),
        (ProviderName.HUGGINGFACE, "https://router.huggingface.co/v1", "openai_compatible"),
        (ProviderName.ANTHROPIC, "https://api.anthropic.com/v1", "anthropic"),
        (
            ProviderName.GEMINI,
            "https://generativelanguage.googleapis.com/v1beta",
            "gemini",
        ),
        (ProviderName.OLLAMA, "http://localhost:11434", "ollama"),
    ],
)
def test_endpoints(provider: ProviderName, base_url: str, backend: str) -> None:
    assert (spec(provider).base_url, spec(provider).backend) == (base_url, backend)


def test_labels_are_unique_and_menu_order_is_alphabetical_with_ollama_last() -> None:
    labels = [entry.label for entry in PROVIDERS.values()]
    assert len(set(labels)) == len(labels)
    hosted = [e.label for e in PROVIDERS.values() if e.name is not ProviderName.OLLAMA]
    assert hosted == sorted(hosted)
    assert list(PROVIDERS)[-1] is ProviderName.OLLAMA


def test_fallbacks_carry_a_date() -> None:
    assert FALLBACKS_AS_OF == "November 2026"
