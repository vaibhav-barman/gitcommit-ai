"""Unit tests for HistoricalExampleSelector."""

from __future__ import annotations

from git_commit_ai.context.example_selector import HistoricalExampleSelector
from git_commit_ai.context.normalizer import CommitNormalizer
from git_commit_ai.core.schemas import (
    DiffMode,
    DiffResult,
    DiffStatistics,
    FileChange,
    FileStatus,
    HistoricalCommit,
    NormalizedCommit,
)


def _norm(hash_val: str, subject: str, body: str = "") -> NormalizedCommit:
    return CommitNormalizer().normalize(
        HistoricalCommit(hash=hash_val, subject=subject, body=body)
    )


def _make_diff(paths: list[tuple[str, FileStatus]]) -> DiffResult:
    changes = [FileChange(path=p, status=st, additions=5, deletions=1) for p, st in paths]
    stats = DiffStatistics(
        files_changed=len(changes),
        total_additions=10,
        total_deletions=2,
    )
    return DiffResult(
        raw_diff="diff",
        file_changes=changes,
        statistics=stats,
        mode=DiffMode.STAGED,
    )


def test_select_scope_matching() -> None:
    """Verify commits matching the scope of changed files receive higher scores."""
    selector = HistoricalExampleSelector()
    diff = _make_diff([("src/auth/service.py", FileStatus.MODIFIED)])

    commits = [
        _norm("h1", "feat(auth): add OAuth provider"),
        _norm("h2", "fix(database): resolve query lock"),
    ]

    result = selector.select(diff, commits)
    assert len(result.examples) == 2
    top = result.examples[0]
    assert top.hash == "h1"
    assert top.matched_scope == "auth"
    assert top.relevance_score > result.examples[1].relevance_score
    assert any("scope 'auth'" in reason for reason in top.selection_reasons)


def test_select_path_name_matching() -> None:
    """Verify commits referencing changed filenames get scored."""
    selector = HistoricalExampleSelector()
    diff = _make_diff([("src/core/config.py", FileStatus.MODIFIED)])

    commits = [
        _norm("h1", "feat(core): update config parser and validation"),
        _norm("h2", "fix(ui): adjust button padding"),
    ]

    result = selector.select(diff, commits)
    assert len(result.examples) >= 1
    assert result.examples[0].hash == "h1"
    assert any("config" in reason for reason in result.examples[0].selection_reasons)


def test_select_recency_decay() -> None:
    """Verify that among identical relevance commits, more recent ranks higher."""
    selector = HistoricalExampleSelector(recency_decay=0.2)
    diff = _make_diff([("src/api/routes.py", FileStatus.MODIFIED)])

    # Both commits have identical base matching criteria (scope=api)
    # h_recent is index 0 (rank 0); h_older is index 5 (rank 5)
    commits = [
        _norm("h_recent", "feat(api): recent endpoint addition"),
        _norm("other1", "chore: misc 1"),
        _norm("other2", "chore: misc 2"),
        _norm("other3", "chore: misc 3"),
        _norm("other4", "chore: misc 4"),
        _norm("h_older", "feat(api): older endpoint addition"),
    ]

    result = selector.select(diff, commits)
    matching = [ex for ex in result.examples if ex.hash in ("h_recent", "h_older")]
    assert len(matching) == 2
    assert matching[0].hash == "h_recent"
    assert matching[1].hash == "h_older"
    assert matching[0].relevance_score > matching[1].relevance_score


def test_select_max_examples_limit() -> None:
    """Verify selector respects max_examples parameter."""
    selector = HistoricalExampleSelector(max_examples=2)
    diff = _make_diff([("src/auth/token.py", FileStatus.MODIFIED)])

    commits = [
        _norm("h1", "feat(auth): token 1"),
        _norm("h2", "feat(auth): token 2"),
        _norm("h3", "feat(auth): token 3"),
    ]

    result = selector.select(diff, commits)
    assert len(result.examples) == 2


def test_select_empty_inputs() -> None:
    """Verify handling of empty diff or empty commits."""
    selector = HistoricalExampleSelector()
    empty_diff = DiffResult()

    res1 = selector.select(empty_diff, [_norm("h1", "feat: test")])
    assert res1.examples == []

    res2 = selector.select(_make_diff([("a.py", FileStatus.ADDED)]), [])
    assert res2.examples == []
