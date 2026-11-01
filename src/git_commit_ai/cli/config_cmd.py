"""The ``config`` command: show the effective settings and where they come from."""

from __future__ import annotations

import typer

from git_commit_ai.cli.settings_loader import load_cli_settings
from git_commit_ai.core.config import ENV_FIELDS, ProviderName
from git_commit_ai.core.exceptions import GitCommitAIError
from git_commit_ai.core.settings import ENV_FILE_VARIABLE, mask_secret
from git_commit_ai.llm.factory import api_key_variable, default_model


def config() -> None:
    """Show the effective settings, their sources, and masked API keys.

    Precedence is: command-line flags, then environment variables, then the
    nearest .env file, then defaults. Flags are per-invocation, so they are not
    shown here.
    """
    try:
        settings = load_cli_settings()
    except GitCommitAIError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    values = settings.config.model_dump(mode="json")
    typer.secho("Effective settings", bold=True)
    typer.echo("  Precedence: flags > environment > .env > defaults")
    typer.echo(f"  .env file:  {settings.dotenv_path or 'none found'}")
    typer.echo()

    rows: list[tuple[str, str, str, str]] = []
    for variable, field_name in ENV_FIELDS.items():
        value = values[field_name]
        if value is None:
            provider = ProviderName(values["llm_provider"])
            shown = f"(provider default: {default_model(provider)})"
        else:
            shown = str(value)
        rows.append((variable, shown, settings.source_of_variable(variable).value, field_name))

    name_width = max(len(row[0]) for row in rows)
    value_width = max(len(row[1]) for row in rows)
    for variable, shown, source, _ in rows:
        typer.echo(f"  {variable:<{name_width}}  {shown:<{value_width}}  [{source}]")

    typer.echo()
    typer.secho("API keys", bold=True)
    for backend in ProviderName:
        key_variable = api_key_variable(backend)
        if key_variable is None:
            typer.echo(f"  {backend.value:<9} no key needed")
            continue
        secret = settings.environment.get(key_variable, "")
        status = mask_secret(secret) if secret else "not set"
        origin = f"  [{settings.source_of_variable(key_variable).value}]" if secret else ""
        typer.echo(f"  {backend.value:<9} {key_variable} = {status}{origin}")

    typer.echo()
    typer.echo(f"  Set {ENV_FILE_VARIABLE} to choose a .env file, or to '' to skip loading one.")
