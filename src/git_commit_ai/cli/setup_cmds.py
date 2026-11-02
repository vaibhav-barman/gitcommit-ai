"""Commands that change the saved setup: ``setup``, ``provider``, ``model`` and ``key``.

Each asks through the wizard in a terminal and also has a form that never asks
(``provider NAME``, ``model NAME``, ``key --stdin``) for scripts.
"""

from __future__ import annotations

import contextlib
import os
import sys
from typing import TYPE_CHECKING, NoReturn

import typer

from git_commit_ai.cli.interaction import Interaction, current, run_wizard
from git_commit_ai.cli.settings_loader import load_cli_settings
from git_commit_ai.core.config import ProviderName  # noqa: TC001  (Typer reads it at runtime)
from git_commit_ai.core.credentials import open_credential_store
from git_commit_ai.core.exceptions import GitCommitAIError, UnsupportedConfigVersionError
from git_commit_ai.core.paths import config_dir
from git_commit_ai.core.user_config import (
    CONFIG_FILE,
    CommitMode,
    CredentialStoreKind,
    UserConfig,
    load_user_config,
    save_user_config,
)
from git_commit_ai.llm.catalog import KeyCheck, catalog_for
from git_commit_ai.llm.registry import spec
from git_commit_ai.onboarding.wizard import SetupWizard

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from git_commit_ai.core.settings import Settings
    from git_commit_ai.onboarding.wizard import SetupResult

MODE_SUMMARY = {
    CommitMode.CONFIRM: "ask first",
    CommitMode.PREVIEW: "print only",
    CommitMode.AUTO: "commit without asking",
}
CHANGE_HINT = "Change any of this later: git ai-commit provider | model | key | config"


def setup(ctx: typer.Context) -> None:
    """Choose a provider, key, model and commit mode, checking each with the provider."""
    interaction = _terminal(
        ctx,
        "setup needs a terminal. Without one, set GITCOMMIT_AI_PROVIDER and that "
        "provider's key variable.",
    )
    with _errors():
        settings = load_cli_settings()
        result, saved = run_wizard(interaction, settings, SetupWizard.run_full)
    report_setup(result, saved)


def provider(
    ctx: typer.Context,
    name: ProviderName | None = typer.Argument(
        None, help="Save this provider without asking (no key check)."
    ),
) -> None:
    """Switch provider. Keys and models saved for other providers are kept."""
    with _errors():
        settings = load_cli_settings()
        if name is not None:
            saved = update_config(lambda config: config.model_copy(update={"provider": name}))
            model = saved.preferences(name).model or spec(name).default_model
            _note(f"Saved. Provider: {name.value} · model: {model}")
            return
        interaction = _terminal(ctx, "provider needs a terminal, or pass a provider name.")
        before = settings.config.llm_provider
        result, saved = run_wizard(interaction, settings, SetupWizard.run_provider)
    _note(f"Saved. Provider: {result.provider.value} · model: {result.model}")
    if before is not result.provider and settings.api_key_for(before) is not None:
        _note(
            f"Your {spec(before).label} key is still saved; "
            "switch back with `git ai-commit provider`."
        )


def model(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Save this model without listing or testing it."),
    for_provider: ProviderName | None = typer.Option(
        None, "--provider", help="Change the model of this provider (default: the active one)."
    ),
) -> None:
    """Choose the model for the active provider (or --provider), from the provider's list."""
    with _errors():
        settings = load_cli_settings().with_flags(llm_provider=for_provider)
        target = settings.config.llm_provider
        if name is not None:
            update_config(lambda config: _with_model(config, target, name))
            _note(
                f"Saved. Model for {target.value}: {name} (not tested; run "
                "`git ai-commit model` without a name to pick from the list and test it)"
            )
        else:
            interaction = _terminal(ctx, "model needs a terminal, or pass a model name.")
            result, _ = run_wizard(interaction, settings, SetupWizard.run_model)
            _note(f"Saved. Model for {result.provider.value}: {result.model}")
    pinned = settings.environment.get("GITCOMMIT_AI_MODEL")
    if pinned:
        where = "environment" if settings.process_env.get("GITCOMMIT_AI_MODEL") else ".env file"
        _note(f"GITCOMMIT_AI_MODEL={pinned} in your {where} overrides this setting.")


def key(
    ctx: typer.Context,
    for_provider: ProviderName | None = typer.Option(
        None, "--provider", help="Whose key to replace (default: the active provider)."
    ),
    stdin: bool = typer.Option(
        False, "--stdin", help="Read the key from standard input instead of asking."
    ),
) -> None:
    """Replace a provider's saved API key. The key is checked before it is saved."""
    with _errors():
        settings = load_cli_settings()
        target = for_provider or settings.config.llm_provider
        if stdin:
            _save_key_from_stdin(current(ctx), settings, target)
        else:
            interaction = _terminal(ctx, "key needs a terminal, or pass --stdin.")
            run_wizard(interaction, settings, lambda wizard: wizard.run_key(target))
    _note("Saved.")


def report_setup(result: SetupResult, saved: UserConfig) -> None:
    """The summary printed after a full setup."""
    model = result.model or spec(result.provider).default_model
    mode = MODE_SUMMARY[saved.commit_mode or CommitMode.CONFIRM]
    _note(f"Saved. Provider: {result.provider.value} · model: {model} · commits: {mode}")
    if result.key:
        if saved.credential_store is CredentialStoreKind.KEYRING:
            _note("Key stored in this computer's keychain.")
        else:
            path = config_dir(os.environ) / "credentials.toml"
            _note(f"Key stored in {path} (readable only by you).")
    _note(CHANGE_HINT)


def update_config(change: Callable[[UserConfig], UserConfig]) -> UserConfig:
    """Load ``config.toml``, apply ``change`` and save it.

    Raises:
        UnsupportedConfigVersionError: If a newer release wrote the file.
    """
    directory = config_dir(os.environ)
    previous = load_user_config(directory)
    if previous is not None and previous.written_by_newer_version:
        raise UnsupportedConfigVersionError(str(directory / CONFIG_FILE))
    updated = change(previous or UserConfig())
    save_user_config(directory, updated)
    return updated


def _with_model(config: UserConfig, provider: ProviderName, name: str) -> UserConfig:
    preferences = config.preferences(provider).model_copy(update={"model": name})
    return config.model_copy(update={"providers": {**config.providers, provider: preferences}})


def _save_key_from_stdin(
    interaction: Interaction, settings: Settings, provider: ProviderName
) -> None:
    value = sys.stdin.read().strip()
    if not value:
        _fail("No key was given on stdin.")
    entry = spec(provider)
    if entry.requires_key:
        catalog = catalog_for(provider, value, entry.base_url, interaction.transport)
        typer.echo("Checking the key...", nl=False, err=True)
        check = catalog.check_key()
        if check is KeyCheck.INVALID:
            typer.echo(f" {entry.label} rejected this key.", err=True)
            raise typer.Exit(code=1)
        detail = (
            "ok" if check is KeyCheck.VALID else f"couldn't check it ({catalog.check_failure})"
        )
        typer.echo(f" {detail}", err=True)
    saved = settings.user_config
    kind = saved.credential_store if saved else CredentialStoreKind.FILE
    open_credential_store(config_dir(os.environ), kind).set(provider, value)


def _terminal(ctx: typer.Context, message: str) -> Interaction:
    interaction = current(ctx)
    if not interaction.interactive:
        _fail(message)
    return interaction


@contextlib.contextmanager
def _errors() -> Iterator[None]:
    """Turn ``GitCommitAIError`` into ``Error: ...`` and exit code 1."""
    try:
        yield
    except GitCommitAIError as exc:
        _fail(str(exc))


def _note(text: str) -> None:
    typer.echo(text, err=True)


def _fail(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
