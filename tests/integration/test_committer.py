"""Integration tests for the staleness-guarded committer."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import GitCommandError, StaleStagingError
from git_commit_ai.git.committer import GitCommitter
from git_commit_ai.git.repository import RepositoryInspector
from git_commit_ai.git.snapshots import SnapshotManager
from tests.fixtures.git_repo import commit_count, head_message, init_repo, stage_file


def _committer(repo: Path) -> tuple[GitCommitter, SnapshotManager]:
    inspector = RepositoryInspector(repo_path=str(repo), config=GitCommitAIConfig())
    snapshots = SnapshotManager(inspector)
    return GitCommitter(inspector, snapshots), snapshots


def test_commits_the_staged_changes_and_returns_the_hash(repo_with_initial_commit: Path) -> None:
    repo = repo_with_initial_commit
    stage_file(repo, "app.py", "print('hi')\n")
    committer, snapshots = _committer(repo)

    short_hash = committer.commit("feat: add app", snapshots.capture())

    assert head_message(repo) == "feat: add app"
    assert short_hash
    assert commit_count(repo) == 2


def test_preserves_a_multiline_message(repo_with_initial_commit: Path) -> None:
    repo = repo_with_initial_commit
    stage_file(repo, "app.py", "x = 1\n")
    committer, snapshots = _committer(repo)
    message = "feat(app)!: add x\n\nWhy it matters.\n\nBREAKING CHANGE: x is required"

    committer.commit(message, snapshots.capture())

    assert head_message(repo) == message


def test_commits_into_a_repository_with_no_history(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "fresh")
    stage_file(repo, "README.md", "# Fresh\n")
    committer, snapshots = _committer(repo)

    committer.commit("docs: add readme", snapshots.capture())

    assert commit_count(repo) == 1


def test_refuses_when_staging_changed_after_the_snapshot(repo_with_initial_commit: Path) -> None:
    repo = repo_with_initial_commit
    stage_file(repo, "app.py", "x = 1\n")
    committer, snapshots = _committer(repo)
    snapshot = snapshots.capture()

    stage_file(repo, "extra.py", "y = 2\n")

    with pytest.raises(StaleStagingError):
        committer.commit("feat: add app", snapshot)
    assert commit_count(repo) == 1


def test_surfaces_git_failures(repo_with_initial_commit: Path) -> None:
    committer, snapshots = _committer(repo_with_initial_commit)
    with pytest.raises(GitCommandError):
        committer.commit("feat: nothing staged", snapshots.capture())
