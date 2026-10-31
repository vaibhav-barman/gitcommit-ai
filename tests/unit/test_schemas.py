"""Unit tests for core Pydantic schemas and enums."""

from __future__ import annotations

from datetime import datetime

from git_commit_ai.core.schemas import (
    CommitConvention,
    CommitStyleProfile,
    ConventionDetectionResult,
    DiffMode,
    DiffResult,
    DiffStatistics,
    ExampleRetrievalResult,
    FileChange,
    FileStatus,
    HistoricalCommit,
    HistoricalExample,
    NormalizedCommit,
    RepositoryContext,
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


def test_commit_convention_enum_values() -> None:
    """Verify CommitConvention StrEnum values."""
    assert CommitConvention.CONVENTIONAL.value == "conventional"
    assert CommitConvention.SCOPED_CONVENTIONAL.value == "scoped_conventional"
    assert CommitConvention.GITMOJI.value == "gitmoji"
    assert CommitConvention.BRACKET_PREFIX.value == "bracket_prefix"
    assert CommitConvention.PLAIN.value == "plain"
    assert CommitConvention.UNKNOWN.value == "unknown"


def test_normalized_commit_model() -> None:
    """Verify NormalizedCommit attributes and full_original_message property."""
    commit = NormalizedCommit(
        hash="1234567890abcdef",
        original_subject="feat(api): add endpoint",
        original_body="Body line 1\nBody line 2",
        cleaned_subject="add endpoint",
        cleaned_body="Body line 1\nBody line 2",
        convention=CommitConvention.SCOPED_CONVENTIONAL,
        commit_type="feat",
        scope="api",
        is_breaking=False,
    )
    assert commit.commit_type == "feat"
    assert commit.scope == "api"
    assert commit.is_breaking is False
    assert commit.full_original_message == "feat(api): add endpoint\n\nBody line 1\nBody line 2"

    single_line = NormalizedCommit(
        hash="1234",
        original_subject="Initial commit",
        cleaned_subject="Initial commit",
        convention=CommitConvention.PLAIN,
    )
    assert single_line.full_original_message == "Initial commit"


def test_convention_detection_result_model() -> None:
    """Verify ConventionDetectionResult fields."""
    result = ConventionDetectionResult(
        detected_convention=CommitConvention.CONVENTIONAL,
        confidence=0.85,
        supporting_evidence=["85% of commits match conventional commit pattern"],
        analyzed_commits=20,
        format_distribution={"conventional": 17, "plain": 3},
    )
    assert result.confidence == 0.85
    assert result.analyzed_commits == 20
    assert result.format_distribution["conventional"] == 17


def test_commit_style_profile_model() -> None:
    """Verify CommitStyleProfile default and populated values."""
    profile = CommitStyleProfile(
        dominant_convention=CommitConvention.SCOPED_CONVENTIONAL,
        type_frequencies={"feat": 10, "fix": 5},
        scope_usage_frequency=0.8,
        common_scopes=["core", "git", "cli"],
        average_subject_length=48.5,
        median_subject_length=50.0,
        typical_capitalization="lowercase",
        punctuation_patterns=["no_trailing_period"],
        body_usage_frequency=0.4,
        breaking_change_frequency=0.05,
    )
    assert profile.dominant_convention == CommitConvention.SCOPED_CONVENTIONAL
    assert profile.type_frequencies["feat"] == 10
    assert profile.scope_usage_frequency == 0.8


def test_historical_example_and_retrieval_model() -> None:
    """Verify HistoricalExample and ExampleRetrievalResult models."""
    example = HistoricalExample(
        hash="abcdef1",
        subject="feat(auth): add token refresh",
        body="Expires in 15m",
        original_message="feat(auth): add token refresh\n\nExpires in 15m",
        relevance_score=0.92,
        selection_reasons=["Matching scope 'auth'", "Matching type 'feat'"],
        matched_scope="auth",
        matched_type="feat",
    )
    assert example.relevance_score == 0.92
    assert "Matching scope 'auth'" in example.selection_reasons

    retrieval = ExampleRetrievalResult(
        examples=[example],
        total_candidates=10,
        selection_metadata={"recency_decay": 0.1},
    )
    assert len(retrieval.examples) == 1
    assert retrieval.total_candidates == 10


def test_repository_context_model() -> None:
    """Verify RepositoryContext aggregates all subcomponents."""
    profile = CommitStyleProfile(dominant_convention=CommitConvention.PLAIN)
    detection = ConventionDetectionResult(
        detected_convention=CommitConvention.PLAIN,
        confidence=0.9,
        analyzed_commits=5,
    )
    context = RepositoryContext(
        style_profile=profile,
        convention_result=detection,
        historical_examples=[],
        analyzed_commit_count=5,
        retrieval_metadata={"source": "test"},
    )
    assert context.analyzed_commit_count == 5
    assert context.style_profile.dominant_convention == CommitConvention.PLAIN
