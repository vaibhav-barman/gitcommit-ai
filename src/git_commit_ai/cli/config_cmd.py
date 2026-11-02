"""The ``config`` commands and ``reset``.

``config`` alone shows the effective settings and where they come from;
``config get/set/unset/path`` read and change ``config.toml``. Keys never go through
``config`` (they would end up in shell history); ``git ai-commit key`` handles them.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any, NoReturn

import typer
from pydantic import ValidationError

from git_commit_ai.cli.interaction import current
from git_commit_ai.cli.settings_loader import load_cli_settings
from git_commit_ai.cli.setup_cmds import update_config
from git_commit_ai.core.config import (  # Typer reads ProviderName at runtime
    ENV_FIELDS,
    ProviderName,
    config_key,
)
from git_commit_ai.core.credentials import (
    CREDENTIALS_FILE,
    KeyringCredentialStore,
    keyring_available,
    keyring_backend,
    migrate_keys,
    open_credential_store,
)
from git_commit_ai.core.exceptions import (
    ConfigurationError,
    GitCommitAIError,
    UnsupportedConfigVersionError,
)
from git_commit_ai.core.paths import config_dir
from git_commit_ai.core.settings import ENV_FILE_VARIABLE, load_settings, mask_secret
from git_commit_ai.core.user_config import (
    CONFIG_FILE,
    CredentialStoreKind,
    GenerationOverrides,
    UserConfig,
    read_toml,
)
from git_commit_ai.llm.registry import spec
from git_commit_ai.onboarding.ollama_flow import normalize_url

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from git_commit_ai.core.settings import Settings

# Each key maps one-to-one to a variable: key ``x`` is ``GITCOMMIT_AI_X``.
FIELD_FOR_KEY: dict[str, str] = {config_key(var): field for var, field in ENV_FIELDS.items()}
GENERATION_KEYS = frozenset(GenerationOverrides.model_fields)
SETTING_KEYS = (*FIELD_FOR_KEY, "credential_store")
_SECRET_REFUSAL = "API keys aren't set with config; run git ai-commit key."

config_app = typer.Typer(
    name="config",
    help="Show the effective settings, or read and change the saved ones.",
)
_PROVIDER_OPTION = typer.Option(
    None, "--provider", help="For model: the provider whose model to use (default: active)."
)


@config_app.callback(invoke_without_command=True)
def show(ctx: typer.Context) -> None:
    """Show the effective settings, their sources, and masked API keys.

    Precedence is: command-line flags, then environment variables, then the
    nearest .env file, then the saved config file, then defaults. Flags are
    per-invocation, so they are not shown here.
    """
    if ctx.invoked_subcommand is not None:
        return
    with _errors():
        settings = load_cli_settings()

    values = settings.config.model_dump(mode="json")
    typer.secho("Effective settings", bold=True)
    config_path = config_dir(os.environ) / CONFIG_FILE
    typer.echo("  Precedence: flags > environment > .env > config file > defaults")
    typer.echo(f"  .env file:  {settings.dotenv_path or 'none found'}")
    typer.echo(f"  Config file: {config_path if settings.user_config else 'none'}")
    typer.echo()

    rows: list[tuple[str, str, str]] = []
    for variable, field_name in ENV_FIELDS.items():
        value = values[field_name]
        if value is None:
            provider = ProviderName(values["llm_provider"])
            shown = f"(provider default: {spec(provider).default_model})"
        else:
            shown = str(value)
        rows.append((variable, shown, settings.source_of_field(field_name).value))

    name_width = max(len(row[0]) for row in rows)
    value_width = max(len(row[1]) for row in rows)
    for variable, shown, source in rows:
        typer.echo(f"  {variable:<{name_width}}  {shown:<{value_width}}  [{source}]")

    typer.echo()
    typer.secho("API keys", bold=True)
    width = max(len(provider.value) for provider in ProviderName)
    for backend in ProviderName:
        entry = spec(backend)
        key = settings.api_key_for(backend)
        variable = key.variable if key and key.variable else entry.key_variables[0]
        status = mask_secret(key.value) if key else "not set"
        origin = f"  [{key.source.value}]" if key else ""
        optional = (
            "" if entry.requires_key or key else " (only for Ollama Cloud or a protected server)"
        )
        typer.echo(f"  {backend.value:<{width}} {variable} = {status}{origin}{optional}")

    typer.echo()
    typer.echo(f"  Set {ENV_FILE_VARIABLE} to choose a .env file, or to '' to skip loading one.")


@config_app.command("get")
def get(
    key: str = typer.Argument(..., help="A setting, for example model or temperature."),
    for_provider: ProviderName | None = _PROVIDER_OPTION,
) -> None:
    """Print the effective value of a setting, wherever it comes from."""
    name = _known(key)
    with _errors():
        settings = load_cli_settings().with_flags(llm_provider=for_provider)
    typer.echo(_effective(settings, name))


@config_app.command("set")
def set_(
    key: str = typer.Argument(..., help="A setting, for example temperature."),
    value: str = typer.Argument(..., help="Its new value."),
    for_provider: ProviderName | None = _PROVIDER_OPTION,
) -> None:
    """Save a setting in config.toml (environment variables and .env still win)."""
    name = _known(key)
    if name == "ollama_url":
        url, problem = normalize_url(value)
        if problem:
            _fail(f"ollama_url: {problem}")
        value = url or value
    with _errors():
        _change(name, value, for_provider)
    typer.echo(f"Saved. {name} = {value}", err=True)


@config_app.command("unset")
def unset(
    key: str = typer.Argument(..., help="A setting to remove from config.toml."),
    for_provider: ProviderName | None = _PROVIDER_OPTION,
) -> None:
    """Remove a setting from config.toml, so the next layer down applies."""
    name = _known(key)
    with _errors():
        _change(name, None, for_provider)
    typer.echo(f"Removed {name} from {CONFIG_FILE}.", err=True)


@config_app.command("path")
def path() -> None:
    """Print where config.toml lives (credentials.toml is next to it)."""
    typer.echo(config_dir(os.environ) / CONFIG_FILE)


def reset(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Remove the saved settings and keys (config file, credentials file and keychain).

    Environment variables and .env files are not touched. Works even when config.toml
    is damaged or was written by a newer release.
    """
    directory = config_dir(os.environ)
    files = [p for p in (directory / CONFIG_FILE, directory / CREDENTIALS_FILE) if p.exists()]
    keychain = KeyringCredentialStore(backend=keyring_backend()) if keyring_available() else None
    in_keychain = keychain.saved_providers() if keychain else []
    if not files and not in_keychain:
        typer.echo("Nothing is saved, so there is nothing to remove.", err=True)
        return
    typer.echo("This removes:", err=True)
    for file in files:
        detail = ""
        if file.name == CREDENTIALS_FILE:
            saved_keys = _file_key_names(file)
            detail = f" (keys for: {', '.join(saved_keys)})" if saved_keys else ""
        typer.echo(f"  {file}{detail}", err=True)
    if in_keychain:
        listed = ", ".join(p.value for p in in_keychain)
        typer.echo(f"  keychain entries for: {listed}", err=True)
    typer.echo("Environment variables and .env files are not touched.", err=True)
    if not yes:
        interaction = current(ctx)
        if not interaction.interactive:
            _fail("reset needs --yes when there is no terminal to confirm in.")
        with _errors():
            agreed = interaction.prompter_factory().confirm(
                "Remove all saved settings and keys?", default=False
            )
        if not agreed:
            typer.echo("Nothing was removed.", err=True)
            raise typer.Exit(code=1)
    if keychain is not None:
        with _errors():
            for provider in in_keychain:
                keychain.delete(provider)
    for file in files:
        file.unlink()
    typer.echo("Removed. Run `git ai-commit` to set up again.", err=True)


def _known(key: str) -> str:
    name = key.lower()
    if name in ("api_key", "key") or name.endswith("_key"):
        _fail(_SECRET_REFUSAL)
    if name not in SETTING_KEYS:
        _fail(f"Unknown setting '{key}'. Settings: {', '.join(SETTING_KEYS)}.")
    return name


def _effective(settings: Settings, name: str) -> str:
    if name == "credential_store":
        saved = settings.user_config
        return (saved.credential_store if saved else CredentialStoreKind.FILE).value
    value = getattr(settings.config, FIELD_FOR_KEY[name])
    if name == "model" and value is None:
        return spec(settings.config.llm_provider).default_model
    return str(getattr(value, "value", value))


def _change(name: str, value: str | None, for_provider: ProviderName | None) -> None:
    """Validate the change against the full settings, move keys if the store changes,
    then save."""
    settings = load_cli_settings()
    directory = config_dir(os.environ)
    if settings.user_config is not None and settings.user_config.written_by_newer_version:
        raise UnsupportedConfigVersionError(str(directory / CONFIG_FILE))
    previous = settings.user_config or UserConfig()
    provider = for_provider or settings.config.llm_provider
    data: dict[str, Any] = previous.model_dump(mode="json")
    if name == "model":
        data["providers"].setdefault(provider.value, {})["model"] = value
    elif name == "ollama_url":
        data["providers"].setdefault(ProviderName.OLLAMA.value, {})["base_url"] = value
    elif name in GENERATION_KEYS:
        data["generation"][name] = value
    elif name == "credential_store":
        data[name] = value or CredentialStoreKind.FILE.value
    else:
        data[name] = value
    try:
        updated = UserConfig.model_validate(data)
    except ValidationError as exc:
        problem = "; ".join(err["msg"] for err in exc.errors())
        raise ConfigurationError(f"{CONFIG_FILE}: {name}: {problem}") from None
    load_settings({ENV_FILE_VARIABLE: ""}, user_config=updated)
    if updated.credential_store is not previous.credential_store:
        migrate_keys(
            open_credential_store(directory, previous.credential_store),
            open_credential_store(directory, updated.credential_store),
        )
    update_config(lambda _old: updated)


def _file_key_names(file: Path) -> list[str]:
    try:
        data = read_toml(file) or {}
    except ConfigurationError:
        return []
    keys = data.get("keys")
    return sorted(keys) if isinstance(keys, dict) else []


@contextlib.contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except GitCommitAIError as exc:
        _fail(str(exc))


def _fail(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
