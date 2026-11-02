"""Whether a command may ask questions, and how: the CLI's link to the setup wizard.

Commands read an ``Interaction`` from ``ctx.obj``; tests pass one with scripted answers
and a fake HTTP transport, so no test needs a terminal or the network.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from git_commit_ai.core.credentials import keyring_available
from git_commit_ai.core.exceptions import UnsupportedConfigVersionError
from git_commit_ai.core.paths import config_dir
from git_commit_ai.core.user_config import CONFIG_FILE, UserConfig
from git_commit_ai.llm.catalog import catalog_for, detect_ollama
from git_commit_ai.onboarding.prompts import make_prompter
from git_commit_ai.onboarding.terminal import is_interactive
from git_commit_ai.onboarding.wizard import SetupWizard, persist

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx
    import typer

    from git_commit_ai.core.settings import Settings
    from git_commit_ai.onboarding.prompts import Prompter
    from git_commit_ai.onboarding.wizard import SetupResult


@dataclass
class Interaction:
    """How this run talks to the user.

    Attributes:
        interactive: Whether questions may be asked at all.
        prompter_factory: Builds the prompter, only when a question is about to be asked.
        transport: ``httpx`` transport for setup's provider calls (tests inject a fake).
        keyring_ok: Whether the system keychain can be offered.
    """

    interactive: bool
    prompter_factory: Callable[[], Prompter]
    transport: httpx.BaseTransport | None = None
    keyring_ok: Callable[[], bool] = keyring_available


def current(ctx: typer.Context) -> Interaction:
    """The ``Interaction`` in ``ctx.obj``, or one built from the real terminal."""
    if isinstance(ctx.obj, Interaction):
        return ctx.obj
    return Interaction(is_interactive(os.environ, sys.stdin, sys.stdout), make_prompter)


def setup_required(settings: Settings) -> bool:
    """Plan D9: no ``config.toml``, no ``--provider`` flag, and nothing in the environment or
    ``.env`` that set a provider up the old way.

    An explicit ``--provider`` is a choice already made, like ``GITCOMMIT_AI_PROVIDER``:
    the run then needs only that provider's key, so the error names its variable (and a
    terminal asks for the key alone).
    """
    return (
        settings.user_config is None
        and "llm_provider" not in settings.flag_fields
        and not settings.provider_configured_by_environment()
    )


def run_wizard(
    interaction: Interaction,
    settings: Settings,
    step: Callable[[SetupWizard], SetupResult],
) -> tuple[SetupResult, UserConfig]:
    """Run one wizard flow and save its result.

    Raises:
        UnsupportedConfigVersionError: Before asking anything, if a newer release wrote
            ``config.toml``.
        SetupCancelledError: If the user leaves; nothing is written then.
    """
    directory = config_dir(os.environ)
    if settings.user_config is not None and settings.user_config.written_by_newer_version:
        raise UnsupportedConfigVersionError(str(directory / CONFIG_FILE))
    transport = interaction.transport
    wizard = SetupWizard(
        interaction.prompter_factory(),
        settings,
        catalogs=lambda provider, key, url: catalog_for(provider, key, url, transport),
        ollama_detector=lambda url, key: detect_ollama(url, key, transport),
        keyring_ok=interaction.keyring_ok(),
        directory=directory,
    )
    result = step(wizard)
    return result, persist(result, directory, settings.user_config)
