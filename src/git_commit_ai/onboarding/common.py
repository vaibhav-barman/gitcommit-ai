"""Pieces the wizard and the Ollama flow share."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from git_commit_ai.core.exceptions import SetupCancelledError
from git_commit_ai.core.settings import SettingSource, mask_secret
from git_commit_ai.llm.catalog import ModelCatalog, OllamaServer

if TYPE_CHECKING:
    from git_commit_ai.core.config import ProviderName
    from git_commit_ai.core.settings import Settings
    from git_commit_ai.onboarding.prompts import Prompter

CatalogFactory = Callable[["ProviderName", "str | None", str], ModelCatalog]
OllamaDetector = Callable[[str, "str | None"], OllamaServer]


class ChooseProvider(Exception):  # noqa: N818  (control flow, not an error)
    """Raised by a step when the user picks "Choose a different provider"."""


@dataclass
class Credentials:
    """Where a provider is reached, with which key, and whether that key is new.

    Attributes:
        key: The key requests use (``None`` for a local Ollama).
        new_key: A key the user typed, to be saved; ``None`` when the key came from the
            environment, ``.env`` or the store, or when there is none.
    """

    provider: ProviderName
    base_url: str
    key: str | None = field(repr=False)
    new_key: str | None = field(repr=False)
    catalog: ModelCatalog = field(repr=False)


def cancel() -> SetupCancelledError:
    """The error for "Cancel setup"."""
    return SetupCancelledError()


def offer_existing_key(
    prompter: Prompter, settings: Settings, provider: ProviderName, label: str
) -> str | None:
    """Offer a key already in the environment, ``.env`` or the store. Returns it when the
    user accepts, so it is used but never saved again."""
    found = settings.api_key_for(provider)
    if found is None:
        return None
    if found.source in (SettingSource.ENVIRONMENT, SettingSource.DOTENV):
        where = "environment" if found.source is SettingSource.ENVIRONMENT else ".env file"
        prompter.note(f"Found {found.variable} in your {where}.")
        question = "Use it? It will not be saved, so it must stay set."
    else:
        prompter.note(f"A key for {label} is already saved ({mask_secret(found.value)}).")
        question = "Use it?"
    return found.value if prompter.confirm(question, default=True) else None
