"""Amend support across the diff, history, snapshot and committer layers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import GitCommandError, StaleStagingError
from git_commit_ai.core.schemas import DiffMode
from git_commit_ai.git.committer import GitCommitter
from git_commit_ai.git.diff import DiffExtractor
from git_commit_ai.git.history import CommitHistoryExtractor
from git_commit_ai.git.repository import RepositoryInspector
from git_commit_ai.git.snapshots import SnapshotManager
from tests.fixtures.git_repo import (
    commit_count,
    commit_file,
    head_hash,
    head_message,
    init_repo,
    install_hook,
    stage_file,
)


def _inspector(repo: Path) -> RepositoryInspector:
    return RepositoryInspector(repo_path=str(repo), config=GitCommitAIConfig())


@pytest.fixture
def two_commit_repo(tmp_path: Path) -> Path:
    repo = init_repo(tmp_path / "repo")
    commit_file(repo, "a.py", "a = 1\n", "feat: add a")
    commit_file(repo, "b.py", "b = 1\n", "feat: add b")
    return repo


class TestEmptyTree:
    def test_matches_the_well_known_sha1_empty_tree(self, empty_repo: Path) -> None:
        assert (
            _inspector(empty_repo).empty_tree_hash() == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        )

    def test_asking_for_it_writes_nothing_to_the_object_store(self, empty_repo: Path) -> None:
        objects = empty_repo / ".git" / "objects"
        before = sorted(p.name for p in objects.iterdir())
        _inspector(empty_repo).empty_tree_hash()
        assert sorted(p.name for p in objects.iterdir()) == before


class TestAmendDiff:
    def test_covers_head_plus_staged_changes(self, two_commit_repo: Path) -> None:
        stage_file(two_commit_repo, "c.py", "c = 1\n")
        extractor = DiffExtractor(_inspector(two_commit_repo))

        amend = extractor.extract(DiffMode.AMEND)
        staged = extractor.extract(DiffMode.STAGED)

        assert {fc.path for fc in amend.file_changes} == {"b.py", "c.py"}
        assert {fc.path for fc in staged.file_changes} == {"c.py"}
        assert amend.mode is DiffMode.AMEND

    def test_with_nothing_staged_describes_head_alone(self, two_commit_repo: Path) -> None:
        diff = DiffExtractor(_inspector(two_commit_repo)).extract(DiffMode.AMEND)
        assert [fc.path for fc in diff.file_changes] == ["b.py"]

    def test_root_commit_is_diffed_against_the_empty_tree(
        self, repo_with_initial_commit: Path
    ) -> None:
        stage_file(repo_with_initial_commit, "extra.py", "x = 1\n")
        diff = DiffExtractor(_inspector(repo_with_initial_commit)).extract(DiffMode.AMEND)
        assert {fc.path for fc in diff.file_changes} == {"README.md", "extra.py"}


class TestHistoryExcludingHead:
    def test_head_is_left_out(self, two_commit_repo: Path) -> None:
        extractor = CommitHistoryExtractor(_inspector(two_commit_repo))
        assert [c.subject for c in extractor.get_history()] == ["feat: add b", "feat: add a"]
        assert [c.subject for c in extractor.get_history(exclude_head=True)] == ["feat: add a"]

    def test_root_commit_has_no_history_to_learn_from(
        self, repo_with_initial_commit: Path
    ) -> None:
        extractor = CommitHistoryExtractor(_inspector(repo_with_initial_commit))
        assert extractor.get_history(exclude_head=True) == []

    def test_empty_repository(self, empty_repo: Path) -> None:
        extractor = CommitHistoryExtractor(_inspector(empty_repo))
        assert extractor.get_history(exclude_head=True) == []


class TestSnapshotHead:
    def test_records_head(self, two_commit_repo: Path) -> None:
        snapshot = SnapshotManager(_inspector(two_commit_repo)).capture()
        assert snapshot.head_hash == head_hash(two_commit_repo)

    def test_empty_repository_has_no_head(self, empty_repo: Path) -> None:
        assert SnapshotManager(_inspector(empty_repo)).capture().head_hash is None

    def test_same_tree_under_a_new_head_is_stale(self, two_commit_repo: Path) -> None:
        snapshots = SnapshotManager(_inspector(two_commit_repo))
        before = snapshots.capture()
        subprocess.run(
            ["git", "-C", str(two_commit_repo), "commit", "--allow-empty", "-m", "chore: empty"],
            check=True,
            capture_output=True,
        )
        after = snapshots.capture()
        assert before.tree_hash == after.tree_hash
        assert snapshots.has_changed_since(before)


class TestCommitterFlags:
    def _committer(self, repo: Path) -> tuple[GitCommitter, SnapshotManager]:
        inspector = _inspector(repo)
        snapshots = SnapshotManager(inspector)
        return GitCommitter(inspector, snapshots), snapshots

    def test_amend_replaces_head_and_includes_staged_changes(self, two_commit_repo: Path) -> None:
        stage_file(two_commit_repo, "c.py", "c = 1\n")
        committer, snapshots = self._committer(two_commit_repo)

        committer.commit("feat: add b and c", snapshots.capture(), amend=True)

        assert commit_count(two_commit_repo) == 2
        assert head_message(two_commit_repo) == "feat: add b and c"
        files = subprocess.run(
            ["git", "-C", str(two_commit_repo), "show", "--name-only", "--format=", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        assert sorted(files) == ["b.py", "c.py"]

    def test_amend_with_nothing_staged_rewrites_only_the_message(
        self, two_commit_repo: Path
    ) -> None:
        committer, snapshots = self._committer(two_commit_repo)
        committer.commit("feat: add b (reworded)", snapshots.capture(), amend=True)
        assert commit_count(two_commit_repo) == 2
        assert head_message(two_commit_repo) == "feat: add b (reworded)"

    def test_amend_is_refused_if_head_moved_during_generation(self, two_commit_repo: Path) -> None:
        committer, snapshots = self._committer(two_commit_repo)
        snapshot = snapshots.capture()
        commit_file(two_commit_repo, "d.py", "d = 1\n", "feat: add d")

        with pytest.raises(StaleStagingError):
            committer.commit("feat: reworded", snapshot, amend=True)
        assert head_message(two_commit_repo) == "feat: add d"

    def test_hooks_run_by_default_and_no_verify_skips_them(self, two_commit_repo: Path) -> None:
        install_hook(two_commit_repo, "pre-commit", "#!/bin/sh\necho blocked >&2\nexit 1\n")
        stage_file(two_commit_repo, "c.py", "c = 1\n")
        committer, snapshots = self._committer(two_commit_repo)

        with pytest.raises(GitCommandError, match="blocked"):
            committer.commit("feat: add c", snapshots.capture())
        assert commit_count(two_commit_repo) == 2

        committer.commit("feat: add c", snapshots.capture(), no_verify=True)
        assert commit_count(two_commit_repo) == 3
