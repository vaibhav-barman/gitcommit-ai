"""Reusable Git repository fixtures and helper functions for testing."""

from __future__ import annotations

import subprocess
from pathlib import Path


def init_repo(path: Path) -> Path:
    """Initialize a clean Git repository with isolated user configuration.

    Args:
        path: Path where the repository will be initialized.

    Returns:
        The repository path.
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    # Configure user identity locally so tests run deterministically on any machine
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "commit.gpgsign", "false"],
        check=True,
        capture_output=True,
    )
    return path


def commit_file(
    repo_path: Path,
    filename: str,
    content: str,
    message: str,
) -> None:
    """Create a file, stage it, and commit it."""
    file_path = repo_path / filename
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_path), "add", filename],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", message],
        check=True,
        capture_output=True,
    )
