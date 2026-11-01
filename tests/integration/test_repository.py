"""Integration tests for RepositoryInspector using isolated Git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import (
    GitCommandError,
    GitNotFoundError,
    GitTimeoutError,
    NotAGitRepositoryError,
)
from git_commit_ai.git.repository import RepositoryInspector
from tests.fixtures.git_repo import commit_file


def test_detect_valid_repository(populated_repo: Path) -> None:
    """Verify repository inspection on a valid Git repository."""
    inspector = RepositoryInspector(repo_path=str(populated_repo))
    info = inspector.inspect()

    assert info.root_path == str(populated_repo.resolve())
    assert info.branch == "main"
    assert info.is_detached_head is False
    assert info.is_empty is False
    assert "src/app.py" in info.staged_files
    assert "src/staged_new.py" in info.staged_files
    assert "README.md" in info.unstaged_files
    assert "scratch.txt" in info.untracked_files


def test_non_git_directory_raises_error(tmp_path: Path) -> None:
    """Verify NotAGitRepositoryError when target path is not in a Git repository."""
    non_git_dir = tmp_path / "not_a_repo"
    non_git_dir.mkdir()

    inspector = RepositoryInspector(repo_path=str(non_git_dir))
    with pytest.raises(NotAGitRepositoryError) as exc_info:
        inspector.inspect()

    assert str(non_git_dir) in str(exc_info.value)


def test_repository_root_resolution(populated_repo: Path) -> None:
    """Verify root resolution from a subdirectory inside the repository."""
    subdir = populated_repo / "src"
    inspector = RepositoryInspector(repo_path=str(subdir))
    root = inspector.get_repository_root()

    assert root == str(populated_repo.resolve())


def test_find_repository_root_returns_the_root_from_a_subdirectory(populated_repo: Path) -> None:
    inspector = RepositoryInspector(repo_path=str(populated_repo / "src"))
    assert inspector.find_repository_root() == str(populated_repo.resolve())


def test_find_repository_root_is_none_outside_a_repository(tmp_path: Path) -> None:
    assert RepositoryInspector(repo_path=str(tmp_path)).find_repository_root() is None


def test_empty_repository_handling(empty_repo: Path) -> None:
    """Verify inspection on a repository with zero commits."""
    inspector = RepositoryInspector(repo_path=str(empty_repo))
    info = inspector.inspect()

    assert info.is_empty is True
    assert info.staged_files == []
    assert info.unstaged_files == []
    assert info.untracked_files == []

    # Add a file and stage it in empty repo
    new_file = empty_repo / "init.txt"
    new_file.write_text("initial", encoding="utf-8")
    subprocess.run(["git", "-C", str(empty_repo), "add", "init.txt"], check=True)

    info_after_add = inspector.inspect()
    assert info_after_add.is_empty is True
    assert "init.txt" in info_after_add.staged_files


def test_detached_head_detection(repo_with_initial_commit: Path) -> None:
    """Verify detection of detached HEAD state."""
    repo = repo_with_initial_commit
    commit_file(repo, "second.txt", "2", "feat: second commit")

    # Checkout previous commit by hash to detach HEAD
    res = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD~1"],
        check=True,
        capture_output=True,
        text=True,
    )
    first_commit_hash = res.stdout.strip()
    subprocess.run(
        ["git", "-C", str(repo), "checkout", first_commit_hash],
        check=True,
        capture_output=True,
    )

    inspector = RepositoryInspector(repo_path=str(repo))
    info = inspector.inspect()

    assert info.is_detached_head is True
    assert info.branch is None


def test_git_command_error_on_invalid_invocation(populated_repo: Path) -> None:
    """Verify GitCommandError when a git command exits with non-zero status."""
    inspector = RepositoryInspector(repo_path=str(populated_repo))
    with pytest.raises(GitCommandError) as exc_info:
        inspector._run_git(["invalid-subcommand-xyz"], check=True)

    assert exc_info.value.returncode != 0
    stderr = exc_info.value.stderr
    assert "invalid-subcommand-xyz" in stderr or "is not a git command" in stderr


def test_git_command_timeout(populated_repo: Path) -> None:
    """Verify GitTimeoutError when a command exceeds configured timeout."""
    # Use very low timeout with a sleep or mock timeout
    config = GitCommitAIConfig(git_timeout_seconds=0.001)
    inspector = RepositoryInspector(repo_path=str(populated_repo), config=config)

    timeout_exc = subprocess.TimeoutExpired(cmd=["git"], timeout=0.001)
    with (
        patch("subprocess.run", side_effect=timeout_exc),
        pytest.raises(GitTimeoutError) as exc_info,
    ):
        inspector.get_repository_root()

    assert exc_info.value.timeout == 0.001


def test_git_not_found_raises_error(populated_repo: Path) -> None:
    """Verify GitNotFoundError when git executable is not available."""
    inspector = RepositoryInspector(repo_path=str(populated_repo))
    with (
        patch("subprocess.run", side_effect=FileNotFoundError("No git")),
        pytest.raises(GitNotFoundError),
    ):
        inspector.inspect()
