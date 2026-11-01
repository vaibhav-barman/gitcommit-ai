"""Load settings for a CLI command, searching the working directory and repo root for ``.env``."""

from __future__ import annotations

import os
from pathlib import Path

from git_commit_ai.core.exceptions import GitNotFoundError
from git_commit_ai.core.settings import Settings, load_settings
from git_commit_ai.git.repository import RepositoryInspector


def load_cli_settings(repo_path: str = ".") -> Settings:
    """Load settings, looking for ``.env`` in the current directory and the repo root.

    ``repo_path`` is any directory inside the repository being worked on. Outside
    a repository, or without Git installed, only the current directory is searched.
    """
    return load_settings(os.environ, repo_root=_repository_root(repo_path))


def _repository_root(repo_path: str) -> Path | None:
    try:
        root = RepositoryInspector(repo_path=repo_path).find_repository_root()
    except GitNotFoundError:
        return None
    return Path(root) if root else None
