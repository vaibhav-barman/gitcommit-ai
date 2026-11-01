"""The ``hook`` commands: a ``prepare-commit-msg`` hook that proposes messages."""

from __future__ import annotations

import os
import shutil
from pathlib import Path  # noqa: TC003  (Typer reads it at runtime)
from typing import NoReturn

import typer

from git_commit_ai.agents.graph import CommitMessageGraph
from git_commit_ai.cli.settings_loader import load_cli_settings
from git_commit_ai.core.exceptions import GitCommitAIError
from git_commit_ai.core.schemas import DiffMode, RepositoryOperation
from git_commit_ai.git.diff import DiffExtractor
from git_commit_ai.git.history import CommitHistoryExtractor
from git_commit_ai.git.hooks import HOOK_NAME, SKIP_VARIABLE, HookInstaller
from git_commit_ai.git.repository import RepositoryInspector
from git_commit_ai.llm import create_provider

hook_app = typer.Typer(
    name="hook",
    help="Install a prepare-commit-msg hook that proposes a message for `git commit`.",
    no_args_is_help=True,
)

# Sources for which Git already has a message, so the hook must not interfere.
_SOURCES_WITH_MESSAGE = frozenset({"message", "merge", "squash", "commit"})


@hook_app.command()
def install(
    path: str = typer.Option(".", "--path", "-p", help="Path to the Git repository."),
) -> None:
    """Install the hook, keeping (and chaining to) any hook that is already there."""
    try:
        installer = HookInstaller(RepositoryInspector(repo_path=path))
        result = installer.install()
    except GitCommitAIError as exc:
        _fail(str(exc))

    verb = "Updated" if result.already_installed else "Installed"
    typer.echo(f"{verb} {result.path}")
    if result.backup is not None:
        typer.echo(
            f"An existing hook was moved to {result.backup.name} and still runs first. "
            "`hook uninstall` puts it back."
        )
    typer.echo(f"Run `git commit` without -m to get a proposal; skip it with {SKIP_VARIABLE}=0.")
    if shutil.which("git-ai-commit") is None:
        typer.secho(
            "Warning: git-ai-commit is not on your PATH, so the hook does nothing until it is.",
            fg=typer.colors.YELLOW,
            err=True,
        )


@hook_app.command()
def uninstall(
    path: str = typer.Option(".", "--path", "-p", help="Path to the Git repository."),
) -> None:
    """Remove the hook and restore the one it replaced, if any."""
    try:
        result = HookInstaller(RepositoryInspector(repo_path=path)).uninstall()
    except GitCommitAIError as exc:
        _fail(str(exc))

    if not result.was_installed:
        typer.echo(f"No {HOOK_NAME} hook is installed.")
        return
    typer.echo(f"Removed {result.path}")
    if result.restored is not None:
        typer.echo("Restored the hook that was there before.")


@hook_app.command(hidden=True)
def run(
    message_file: Path = typer.Argument(..., help="File Git passes to prepare-commit-msg."),
    source: str = typer.Argument("", help="Message source Git passes to the hook."),
    path: str = typer.Option(".", "--path", "-p", help="Path to the Git repository."),
) -> None:
    """Write a proposed message into MESSAGE_FILE. Never blocks a commit.

    Any problem (no staged changes, no API key, provider failure, even an
    unexpected error) is reported on stderr and the commit carries on with its
    normal, empty message.
    """
    try:
        _propose(message_file, source, path)
    except Exception as exc:  # the hook must never be the reason a commit fails
        typer.secho(f"git-ai-commit: no proposal ({exc})", fg=typer.colors.YELLOW, err=True)


def _propose(message_file: Path, source: str, path: str) -> None:
    if source in _SOURCES_WITH_MESSAGE or os.environ.get(SKIP_VARIABLE) == "0":
        return
    existing = message_file.read_text(encoding="utf-8")
    if _has_user_content(existing):
        return

    settings = load_cli_settings(path)
    config = settings.config
    inspector = RepositoryInspector(repo_path=path, config=config)
    if inspector.inspect().operation is not RepositoryOperation.NONE:
        return
    diff = DiffExtractor(inspector=inspector, config=config).extract(DiffMode.STAGED)
    if not diff.file_changes:
        return
    history = CommitHistoryExtractor(inspector=inspector, config=config).get_history()

    typer.secho("git-ai-commit: writing a proposed message...", err=True)
    llm = create_provider(config, env=settings.environment)
    result = CommitMessageGraph(llm, config).run(diff, history)

    separator = "" if not existing or existing.startswith("\n") else "\n"
    message_file.write_text(f"{result.message}\n{separator}{existing}", encoding="utf-8")


def _has_user_content(text: str) -> bool:
    """True if the file already holds a message (comments and blanks do not count)."""
    return any(line.strip() and not line.startswith("#") for line in text.splitlines())


def _fail(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
