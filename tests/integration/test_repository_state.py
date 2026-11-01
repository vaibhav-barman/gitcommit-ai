"""Detection of merge, rebase, cherry-pick, revert and detached-HEAD states."""

from __future__ import annotations

from pathlib import Path

import pytest

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.schemas import RepositoryInfo, RepositoryOperation
from git_commit_ai.git.repository import RepositoryInspector
from tests.fixtures.git_repo import diverged_repo, run_git, start_operation


def _info(repo: Path) -> RepositoryInfo:
    return RepositoryInspector(repo_path=str(repo), config=GitCommitAIConfig()).inspect()


def test_ordinary_repository_has_no_operation(populated_repo: Path) -> None:
    assert _info(populated_repo).operation is RepositoryOperation.NONE


def test_empty_repository_has_no_operation(empty_repo: Path) -> None:
    assert _info(empty_repo).operation is RepositoryOperation.NONE


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("merge", RepositoryOperation.MERGE),
        ("rebase", RepositoryOperation.REBASE),
        ("cherry-pick", RepositoryOperation.CHERRY_PICK),
        ("revert", RepositoryOperation.REVERT),
    ],
)
def test_paused_operation_is_detected(
    tmp_path: Path, operation: str, expected: RepositoryOperation
) -> None:
    repo = diverged_repo(tmp_path / "repo")
    start_operation(repo, operation)
    assert _info(repo).operation is expected


def test_operation_is_gone_once_the_merge_is_aborted(tmp_path: Path) -> None:
    repo = diverged_repo(tmp_path / "repo")
    start_operation(repo, "merge")
    run_git(repo, "merge", "--abort")
    assert _info(repo).operation is RepositoryOperation.NONE


def test_detached_head_is_reported(repo_with_initial_commit: Path) -> None:
    run_git(repo_with_initial_commit, "checkout", "-q", "--detach")
    info = _info(repo_with_initial_commit)
    assert info.is_detached_head
    assert info.operation is RepositoryOperation.NONE


def test_operation_is_detected_from_a_linked_worktree(tmp_path: Path) -> None:
    repo = diverged_repo(tmp_path / "repo")
    worktree = tmp_path / "wt"
    run_git(repo, "worktree", "add", "-q", str(worktree), "-b", "other", "main")
    run_git(worktree, "merge", "feature", check=False)
    assert _info(worktree).operation is RepositoryOperation.MERGE
    assert _info(repo).operation is RepositoryOperation.NONE
