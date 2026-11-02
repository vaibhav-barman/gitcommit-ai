"""Load settings for a CLI command: the environment, ``.env``, and the saved config."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from git_commit_ai.core.credentials import (
    CredentialStore,
    FileCredentialStore,
    open_credential_store,
)
from git_commit_ai.core.exceptions import GitNotFoundError
from git_commit_ai.core.paths import CONFIG_DIR_VARIABLE, config_dir
from git_commit_ai.core.settings import Settings, load_settings
from git_commit_ai.core.user_config import (
    NEWER_VERSION_NOTE,
    CredentialStoreKind,
    UserConfig,
    load_user_config,
)
from git_commit_ai.git.repository import RepositoryInspector


def load_cli_settings(repo_path: str = ".") -> Settings:
    """Load settings, looking for ``.env`` in the current directory and the repo root.

    ``repo_path`` is any directory inside the repository being worked on. Outside
    a repository, or without Git installed, only the current directory is searched.
    The saved ``config.toml`` and credential store from the per-user config
    directory rank below both.
    """
    user_config, credentials = load_saved_layers()
    root = _repository_root(repo_path)
    if root is not None and os.environ.get(CONFIG_DIR_VARIABLE):
        directory = config_dir(os.environ).resolve()
        if directory.is_relative_to(root.resolve()):
            _warn(
                f"{CONFIG_DIR_VARIABLE} ({directory}) is inside this repository, so saved "
                "keys could be committed. Point it somewhere outside any repository."
            )
    return load_settings(
        os.environ,
        repo_root=root,
        user_config=user_config,
        credentials=credentials,
    )


def load_saved_layers() -> tuple[UserConfig | None, CredentialStore]:
    """Read ``config.toml`` and open the credential store it names.

    Problems that do not stop the run (a config file from a newer release, a
    credentials file other users can read) are printed on stderr.
    """
    directory = config_dir(os.environ)
    user_config = load_user_config(directory)
    if user_config is not None and user_config.written_by_newer_version:
        _warn(NEWER_VERSION_NOTE)
    kind = user_config.credential_store if user_config else CredentialStoreKind.FILE
    credentials = open_credential_store(directory, kind)
    if isinstance(credentials, FileCredentialStore):
        problem = credentials.permission_problem()
        if problem:
            _warn(problem)
    return user_config, credentials


def _warn(message: str) -> None:
    typer.secho(f"Warning: {message}", fg=typer.colors.YELLOW, err=True)


def _repository_root(repo_path: str) -> Path | None:
    try:
        root = RepositoryInspector(repo_path=repo_path).find_repository_root()
    except GitNotFoundError:
        return None
    return Path(root) if root else None
