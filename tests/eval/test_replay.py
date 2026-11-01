"""Replaying history: each commit's own diff, with only older commits as context."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts.eval.replay import replay_commits

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import GitCommitAIError, NotAGitRepositoryError
from git_commit_ai.core.schemas import DiffMode, FileStatus
from tests.fixtures.git_repo import commit_file, init_repo, run_git, stage_file

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = init_repo(tmp_path / "repo")
    commit_file(repo, "a.py", "a = 1\n", "feat: add a")
    commit_file(repo, "b.py", "b = 1\nb2 = 2\n", "feat(core): add b")
    commit_file(repo, "docs/c.md", "# c\n", "docs: add c")
    return repo


def test_takes_the_newest_commits_first(repo: Path) -> None:
    replays = replay_commits(str(repo), 2)
    assert [r.commit.subject for r in replays] == ["docs: add c", "feat(core): add b"]


def test_asking_for_more_than_exist_returns_them_all(repo: Path) -> None:
    assert len(replay_commits(str(repo), 30)) == 3


def test_diff_is_the_commit_against_its_parent(repo: Path) -> None:
    by_subject = {r.commit.subject: r for r in replay_commits(str(repo), 3)}

    middle = by_subject["feat(core): add b"].diff
    assert [fc.path for fc in middle.file_changes] == ["b.py"]
    assert middle.file_changes[0].additions == 2
    assert middle.file_changes[0].status is FileStatus.ADDED
    assert "+b2 = 2" in middle.raw_diff
    assert "a.py" not in middle.raw_diff
    assert middle.mode is DiffMode.AMEND
    assert middle.statistics.files_changed == 1


def test_root_commit_is_diffed_against_the_empty_tree(repo: Path) -> None:
    root = replay_commits(str(repo), 3)[-1]
    assert root.commit.subject == "feat: add a"
    assert [fc.path for fc in root.diff.file_changes] == ["a.py"]
    assert root.diff.file_changes[0].status is FileStatus.ADDED
    assert root.history == []


def test_history_holds_only_commits_older_than_the_target(repo: Path) -> None:
    replays = {r.commit.subject: r for r in replay_commits(str(repo), 3)}

    assert [c.subject for c in replays["docs: add c"].history] == [
        "feat(core): add b",
        "feat: add a",
    ]
    assert [c.subject for c in replays["feat(core): add b"].history] == ["feat: add a"]
    for replay in replays.values():
        assert replay.commit.hash not in {c.hash for c in replay.history}


def test_history_respects_the_configured_size(repo: Path) -> None:
    config = GitCommitAIConfig(history_commit_count=1)
    newest = replay_commits(str(repo), 1, config)[0]
    assert [c.subject for c in newest.history] == ["feat(core): add b"]


def test_merge_commits_are_not_evaluated(repo: Path) -> None:
    run_git(repo, "switch", "-c", "feature")
    commit_file(repo, "f.py", "f = 1\n", "feat: add f")
    run_git(repo, "switch", "main")
    commit_file(repo, "m.py", "m = 1\n", "fix: add m")
    run_git(repo, "merge", "--no-ff", "feature", "-m", "Merge branch 'feature'")

    subjects = [r.commit.subject for r in replay_commits(str(repo), 10)]

    assert "Merge branch 'feature'" not in subjects
    assert {"feat: add f", "fix: add m"} <= set(subjects)


def test_renames_are_detected(repo: Path) -> None:
    run_git(repo, "mv", "b.py", "renamed.py")
    run_git(repo, "commit", "-m", "refactor: rename b")

    newest = replay_commits(str(repo), 1)[0]

    assert newest.diff.file_changes[0].status is FileStatus.RENAMED
    assert newest.diff.file_changes[0].old_path == "b.py"


def test_the_working_tree_and_index_are_left_alone(repo: Path) -> None:
    stage_file(repo, "staged.py", "s = 1\n")
    (repo / "a.py").write_text("a = 'dirty'\n", encoding="utf-8")
    before = (run_git(repo, "status", "--porcelain").stdout, run_git(repo, "rev-parse", "HEAD"))

    replays = replay_commits(str(repo), 3)

    after = (run_git(repo, "status", "--porcelain").stdout, run_git(repo, "rev-parse", "HEAD"))
    assert before[0] == after[0]
    assert before[1].stdout == after[1].stdout
    for replay in replays:
        assert "staged.py" not in replay.diff.raw_diff
        assert "dirty" not in replay.diff.raw_diff


def test_a_directory_that_is_not_a_repository_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(NotAGitRepositoryError):
        replay_commits(str(tmp_path), 5)


def test_an_empty_repository_has_nothing_to_evaluate(tmp_path: Path) -> None:
    empty = init_repo(tmp_path / "empty")
    with pytest.raises(GitCommitAIError, match="no non-merge commits"):
        replay_commits(str(empty), 5)
