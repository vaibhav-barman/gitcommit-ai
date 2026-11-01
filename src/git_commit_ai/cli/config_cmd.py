"""The ``config`` command: show the effective settings and where they come from."""

from __future__ import annotations

import os

import typer

from git_commit_ai.cli.settings_loader import load_cli_settings
from git_commit_ai.core.config import ENV_FIELDS, ProviderName
from git_commit_ai.core.exceptions import GitCommitAIError
from git_commit_ai.core.paths import config_dir
from git_commit_ai.core.settings import ENV_FILE_VARIABLE, mask_secret
from git_commit_ai.core.user_config import CONFIG_FILE
from git_commit_ai.llm.registry import spec


def config() -> None:
    """Show the effective settings, their sources, and masked API keys.

    Precedence is: command-line flags, then environment variables, then the
    nearest .env file, then the saved config file, then defaults. Flags are
    per-invocation, so they are not shown here.
    """
    try:
        settings = load_cli_settings()
    except GitCommitAIError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    values = settings.config.model_dump(mode="json")
    typer.secho("Effective settings", bold=True)
    config_path = config_dir(os.environ) / CONFIG_FILE
    typer.echo("  Precedence: flags > environment > .env > config file > defaults")
    typer.echo(f"  .env file:  {settings.dotenv_path or 'none found'}")
    typer.echo(f"  Config file: {config_path if settings.user_config else 'none'}")
    typer.echo()

    rows: list[tuple[str, str, str, str]] = []
    for variable, field_name in ENV_FIELDS.items():
        value = values[field_name]
        if value is None:
            provider = ProviderName(values["llm_provider"])
            shown = f"(provider default: {spec(provider).default_model})"
        else:
            shown = str(value)
        rows.append((variable, shown, settings.source_of_field(field_name).value, field_name))

    name_width = max(len(row[0]) for row in rows)
    value_width = max(len(row[1]) for row in rows)
    for variable, shown, source, _ in rows:
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
