"""Pytest configuration and shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.git_repo import commit_file, init_repo


@pytest.fixture
def empty_repo(tmp_path: Path) -> Path:
    """Provide a newly initialized, empty Git repository (zero commits)."""
    return init_repo(tmp_path / "empty_repo")


@pytest.fixture
def repo_with_initial_commit(tmp_path: Path) -> Path:
    """Provide a Git repository with a single initial commit."""
    repo = init_repo(tmp_path / "repo_with_commit")
    commit_file(repo, "README.md", "# Hello World\n", "Initial commit")
    return repo


@pytest.fixture
def populated_repo(tmp_path: Path) -> Path:
    """Provide a repository with staged, unstaged, untracked, and committed files."""
    repo = init_repo(tmp_path / "populated_repo")
    # Initial commit
    commit_file(repo, "README.md", "# Main\n", "docs: initial readme")
    commit_file(repo, "src/app.py", "print('v1')\n", "feat: initial app")

    # Staged modification and new staged file
    app_py = repo / "src" / "app.py"
    app_py.write_text("print('v2 staged')\n", encoding="utf-8")
    staged_new = repo / "src" / "staged_new.py"
    staged_new.write_text("def staged(): pass\n", encoding="utf-8")

    import subprocess

    subprocess.run(
        ["git", "-C", str(repo), "add", "src/app.py", "src/staged_new.py"],
        check=True,
        capture_output=True,
    )

    # Unstaged modification
    readme = repo / "README.md"
    readme.write_text("# Main\nUnstaged edit line\n", encoding="utf-8")

    # Untracked file
    untracked = repo / "scratch.txt"
    untracked.write_text("untracked notes\n", encoding="utf-8")

    return repo
