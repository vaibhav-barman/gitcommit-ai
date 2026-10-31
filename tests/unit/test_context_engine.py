"""Unit tests for RepositoryContextEngine."""

from __future__ import annotations

from git_commit_ai.context.engine import RepositoryContextEngine
from git_commit_ai.core.schemas import (
    CommitConvention,
    DiffMode,
    DiffResult,
    DiffStatistics,
    FileChange,
    FileStatus,
    HistoricalCommit,
)


def _make_sample_diff() -> DiffResult:
    changes = [
        FileChange(
            path="src/auth/service.py",
            status=FileStatus.MODIFIED,
            additions=20,
            deletions=5,
        ),
        FileChange(
            path="tests/test_auth.py",
            status=FileStatus.ADDED,
            additions=30,
            deletions=0,
        ),
    ]
    return DiffResult(
        raw_diff="diff --git ...",
        file_changes=changes,
        statistics=DiffStatistics(files_changed=2, total_additions=50, total_deletions=5),
        mode=DiffMode.STAGED,
    )


def test_build_context_full_pipeline() -> None:
    """Verify complete repository context generation across all pipeline components."""
    engine = RepositoryContextEngine()
    diff = _make_sample_diff()

    historical_commits = [
        HistoricalCommit(
            hash="c1",
            subject="feat(auth): implement JWT refresh token",
            body="Expires in 15m",
        ),
        HistoricalCommit(hash="c2", subject="fix(auth): handle expired token error", body=""),
        HistoricalCommit(hash="c3", subject="docs(readme): add auth documentation", body=""),
        HistoricalCommit(hash="c4", subject="refactor(core): streamline config loader", body=""),
        HistoricalCommit(hash="c5", subject="test(auth): add token expiry unit tests", body=""),
        HistoricalCommit(hash="c6", subject="feat(api): expose user profile endpoint", body=""),
    ]

    context = engine.build_context(diff, historical_commits)

    # 1. Analyzed commits count
    assert context.analyzed_commit_count == 6

    # 2. Convention detection
    assert context.convention_result.detected_convention == CommitConvention.SCOPED_CONVENTIONAL
    assert context.convention_result.confidence >= 0.80

    # 3. Style profile
    assert context.style_profile.dominant_convention == CommitConvention.SCOPED_CONVENTIONAL
    assert context.style_profile.scope_usage_frequency == 1.0
    assert "auth" in context.style_profile.common_scopes
    assert context.style_profile.type_frequencies["feat"] == 2
    assert context.style_profile.type_frequencies["fix"] == 1

    # 4. Historical examples retrieval
    assert len(context.historical_examples) >= 1
    # Top example should be an auth commit
    assert any(ex.matched_scope == "auth" for ex in context.historical_examples)
    top = context.historical_examples[0]
    assert top.relevance_score > 0
    assert len(top.selection_reasons) > 0

    # 5. Metadata
    assert context.retrieval_metadata["total_historical_commits"] == 6
    assert context.retrieval_metadata["files_in_diff"] == 2


def test_build_context_empty_repository() -> None:
    """Verify build_context on empty repository with no commits and empty diff."""
    engine = RepositoryContextEngine()
    empty_diff = DiffResult()

    context = engine.build_context(empty_diff, [])

    assert context.analyzed_commit_count == 0
    assert context.convention_result.detected_convention == CommitConvention.UNKNOWN
    assert context.convention_result.confidence == 0.0
    assert context.style_profile.dominant_convention == CommitConvention.UNKNOWN
    assert context.historical_examples == []


def test_build_context_with_extra_metadata() -> None:
    """Verify custom metadata injection in context bundle."""
    engine = RepositoryContextEngine()
    diff = _make_sample_diff()
    commits = [HistoricalCommit(hash="c1", subject="feat: init", body="")]

    context = engine.build_context(diff, commits, extra_metadata={"run_id": "test-123"})
    assert context.retrieval_metadata["run_id"] == "test-123"
