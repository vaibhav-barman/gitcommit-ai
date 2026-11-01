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


def stage_file(repo_path: Path, filename: str, content: str | bytes) -> None:
    """Create or overwrite a file and stage it."""
    file_path = repo_path / filename
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        file_path.write_bytes(content)
    else:
        file_path.write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_path), "add", filename],
        check=True,
        capture_output=True,
    )


def head_message(repo_path: Path) -> str:
    """Return the full message of the latest commit."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "log", "-1", "--format=%B"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit_count(repo_path: Path) -> int:
    """Return the number of commits reachable from HEAD (0 for an empty repo)."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return int(result.stdout.strip()) if result.returncode == 0 else 0


def scoped_history_repo(path: Path, with_bodies: bool = False) -> Path:
    """Create a repository whose history uses scoped Conventional Commits."""
    repo = init_repo(path)
    suffix = "\n\nExplains why the change was needed." if with_bodies else ""
    history = [
        ("src/api/users.py", "def users(): ...\n", "feat(api): add users endpoint"),
        ("src/api/teams.py", "def teams(): ...\n", "feat(api): add teams endpoint"),
        ("src/cli/main.py", "def main(): ...\n", "feat(cli): add entry point"),
        ("docs/setup.md", "# Setup\n", "docs(readme): add setup guide"),
        ("src/api/users.py", "def users(): return []\n", "fix(api): handle empty list"),
    ]
    for filename, content, subject in history:
        commit_file(repo, filename, content, subject + suffix)
    return repo


def install_hook(repo_path: Path, name: str, script: str) -> Path:
    """Write an executable Git hook into the repository's hooks directory."""
    hook = repo_path / ".git" / "hooks" / name
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(script, encoding="utf-8")
    hook.chmod(0o755)
    return hook


def head_hash(repo_path: Path) -> str:
    """Return the full hash of HEAD."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
