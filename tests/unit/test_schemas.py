"""Unit tests for core Pydantic schemas and enums."""

from __future__ import annotations

from datetime import datetime

from git_commit_ai.core.schemas import (
    DiffMode,
    DiffResult,
    DiffStatistics,
    FileChange,
    FileStatus,
    HistoricalCommit,
    RepositoryInfo,
    RepositorySnapshot,
)


def test_file_status_enum_values() -> None:
    """Verify FileStatus StrEnum members and values."""
    assert FileStatus.ADDED.value == "added"
    assert FileStatus.MODIFIED.value == "modified"
    assert FileStatus.DELETED.value == "deleted"
    assert FileStatus.RENAMED.value == "renamed"
    assert FileStatus.COPIED.value == "copied"
    assert FileStatus.UNTRACKED.value == "untracked"
    assert FileStatus.TYPE_CHANGED.value == "type_changed"


def test_diff_mode_enum_values() -> None:
    """Verify DiffMode StrEnum members and values."""
    assert DiffMode.STAGED.value == "staged"
    assert DiffMode.UNSTAGED.value == "unstaged"


def test_repository_info_defaults() -> None:
    """Verify RepositoryInfo defaults and fields."""
    info = RepositoryInfo(root_path="/repo")
    assert info.root_path == "/repo"
    assert info.branch is None
    assert info.is_detached_head is False
    assert info.is_empty is False
    assert info.staged_files == []
    assert info.unstaged_files == []
    assert info.untracked_files == []


def test_file_change_model() -> None:
    """Verify FileChange attributes and defaults."""
    change = FileChange(
        path="src/main.py",
        status=FileStatus.MODIFIED,
        additions=12,
        deletions=3,
    )
    assert change.path == "src/main.py"
    assert change.status == FileStatus.MODIFIED
    assert change.additions == 12
    assert change.deletions == 3
    assert change.is_binary is False
    assert change.old_path is None

    # Binary file change
    bin_change = FileChange(
        path="assets/logo.png",
        status=FileStatus.ADDED,
        is_binary=True,
    )
    assert bin_change.is_binary is True
    assert bin_change.additions == 0

    # Renamed file change
    renamed = FileChange(
        path="new.py",
        status=FileStatus.RENAMED,
        old_path="old.py",
    )
    assert renamed.status == FileStatus.RENAMED
    assert renamed.old_path == "old.py"


def test_diff_statistics_model() -> None:
    """Verify DiffStatistics computation and fields."""
    stats = DiffStatistics(files_changed=3, total_additions=45, total_deletions=10)
    assert stats.files_changed == 3
    assert stats.total_additions == 45
    assert stats.total_deletions == 10


def test_diff_result_model() -> None:
    """Verify DiffResult defaults and aggregation."""
    result = DiffResult(
        raw_diff="diff --git a/f b/f\n...",
        file_changes=[
            FileChange(path="f", status=FileStatus.MODIFIED, additions=5, deletions=2),
        ],
        statistics=DiffStatistics(files_changed=1, total_additions=5, total_deletions=2),
        mode=DiffMode.STAGED,
    )
    assert result.mode == DiffMode.STAGED
    assert len(result.file_changes) == 1
    assert result.statistics.files_changed == 1


def test_historical_commit_model() -> None:
    """Verify HistoricalCommit fields."""
    commit = HistoricalCommit(
        hash="abcdef1234567890",
        subject="feat: add awesome feature",
        body="Detailed description\nSecond paragraph",
    )
    assert commit.hash == "abcdef1234567890"
    assert commit.subject == "feat: add awesome feature"
    assert commit.body == "Detailed description\nSecond paragraph"


def test_repository_snapshot_matching() -> None:
    """Verify RepositorySnapshot matches method compares tree hashes."""
    now = datetime.now()
    s1 = RepositorySnapshot(tree_hash="hash1", staged_files=["a.py"], timestamp=now)
    s2 = RepositorySnapshot(tree_hash="hash1", staged_files=["a.py"], timestamp=now)
    s3 = RepositorySnapshot(tree_hash="hash2", staged_files=["a.py"], timestamp=now)

    assert s1.matches(s2) is True
    assert s1.matches(s3) is False
