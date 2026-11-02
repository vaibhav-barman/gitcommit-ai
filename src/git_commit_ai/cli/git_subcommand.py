"""Entry point for ``git ai-commit``.

Git runs any executable named ``git-<name>`` as ``git <name>``. This app makes
a bare ``git ai-commit`` behave like ``gitcommit-ai generate`` (so its flags
work directly) while still offering the other subcommands.
"""

from __future__ import annotations

import typer

from git_commit_ai.cli.config_cmd import config
from git_commit_ai.cli.generate import generate
from git_commit_ai.cli.hook import hook_app
from git_commit_ai.cli.setup_cmds import key, model, provider, setup

git_app = typer.Typer(
    name="git-ai-commit",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
git_app.callback(invoke_without_command=True)(generate)
git_app.command("config")(config)
git_app.add_typer(hook_app, name="hook")
for _command in (setup, provider, model, key):
    git_app.command()(_command)


def git_ai_commit() -> None:
    """Run ``git ai-commit``.

    Git intercepts ``--help`` for external subcommands and looks for a man
    page, so ``-h`` is accepted as well.
    """
    git_app()
