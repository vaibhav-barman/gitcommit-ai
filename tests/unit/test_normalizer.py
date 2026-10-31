"""Unit tests for CommitNormalizer."""

from __future__ import annotations

from git_commit_ai.context.normalizer import CommitNormalizer
from git_commit_ai.core.schemas import CommitConvention, HistoricalCommit


def test_normalize_conventional_unscoped() -> None:
    """Verify normalizing an unscoped Conventional Commit."""
    normalizer = CommitNormalizer()
    commit = HistoricalCommit(hash="123", subject="feat: add user login", body="")

    norm = normalizer.normalize(commit)
    assert norm.convention == CommitConvention.CONVENTIONAL
    assert norm.commit_type == "feat"
    assert norm.scope is None
    assert norm.cleaned_subject == "add user login"
    assert norm.original_subject == "feat: add user login"
    assert norm.is_breaking is False


def test_normalize_conventional_scoped() -> None:
    """Verify normalizing a scoped Conventional Commit."""
    normalizer = CommitNormalizer()
    commit = HistoricalCommit(
        hash="456",
        subject="fix(auth): handle expired token gracefully",
        body="Tokens were causing 500 error",
    )

    norm = normalizer.normalize(commit)
    assert norm.convention == CommitConvention.SCOPED_CONVENTIONAL
    assert norm.commit_type == "fix"
    assert norm.scope == "auth"
    assert norm.cleaned_subject == "handle expired token gracefully"
    assert norm.is_breaking is False


def test_normalize_breaking_change_exclamation() -> None:
    """Verify breaking change marker (!) after type or scope."""
    normalizer = CommitNormalizer()
    commit = HistoricalCommit(
        hash="789",
        subject="refactor(api)!: redesign response payload",
        body="Removes deprecated v1 fields",
    )

    norm = normalizer.normalize(commit)
    assert norm.convention == CommitConvention.SCOPED_CONVENTIONAL
    assert norm.commit_type == "refactor"
    assert norm.scope == "api"
    assert norm.is_breaking is True


def test_normalize_breaking_change_in_body() -> None:
    """Verify breaking change detected from BREAKING CHANGE: paragraph in body."""
    normalizer = CommitNormalizer()
    commit = HistoricalCommit(
        hash="abc",
        subject="feat: upgrade database schema",
        body="Migrate to v2\n\nBREAKING CHANGE: drops legacy users table",
    )

    norm = normalizer.normalize(commit)
    assert norm.is_breaking is True
    assert norm.breaking_description == "drops legacy users table"


def test_normalize_bracket_prefix() -> None:
    """Verify bracketed prefix convention parsing."""
    normalizer = CommitNormalizer()

    commit1 = HistoricalCommit(hash="1", subject="[AUTH] Add session validation", body="")
    norm1 = normalizer.normalize(commit1)
    assert norm1.convention == CommitConvention.BRACKET_PREFIX
    assert norm1.bracket_prefix == "AUTH"
    assert norm1.cleaned_subject == "Add session validation"

    commit2 = HistoricalCommit(hash="2", subject="[core/config]: load environment", body="")
    norm2 = normalizer.normalize(commit2)
    assert norm2.convention == CommitConvention.BRACKET_PREFIX
    assert norm2.bracket_prefix == "core/config"
    assert norm2.cleaned_subject == "load environment"


def test_normalize_gitmoji() -> None:
    """Verify Gitmoji code and unicode parsing."""
    normalizer = CommitNormalizer()

    commit_code = HistoricalCommit(hash="3", subject=":sparkles: Add dark mode toggle", body="")
    norm_code = normalizer.normalize(commit_code)
    assert norm_code.convention == CommitConvention.GITMOJI
    assert norm_code.gitmoji == ":sparkles:"
    assert norm_code.cleaned_subject == "Add dark mode toggle"

    commit_unicode = HistoricalCommit(hash="4", subject="🐛 Fix memory leak in listener", body="")
    norm_unicode = normalizer.normalize(commit_unicode)
    assert norm_unicode.convention == CommitConvention.GITMOJI
    assert norm_unicode.gitmoji == "🐛"
    assert norm_unicode.cleaned_subject == "Fix memory leak in listener"


def test_normalize_plain_commit() -> None:
    """Verify plain descriptive commit message handling."""
    normalizer = CommitNormalizer()
    commit = HistoricalCommit(
        hash="5",
        subject="Updated dependencies and bumped version to 1.2.0",
        body="",
    )

    norm = normalizer.normalize(commit)
    assert norm.convention == CommitConvention.PLAIN
    assert norm.commit_type is None
    assert norm.scope is None
    assert norm.cleaned_subject == "Updated dependencies and bumped version to 1.2.0"


def test_normalize_whitespace_and_empty_body() -> None:
    """Verify whitespace cleaning in multiline commit bodies."""
    normalizer = CommitNormalizer()
    commit = HistoricalCommit(
        hash="6",
        subject="  chore: clean workspace   ",
        body="\n\n  Line 1   \n\n  Line 2   \n\n",
    )

    norm = normalizer.normalize(commit)
    assert norm.cleaned_subject == "clean workspace"
    assert norm.cleaned_body == "  Line 1\n\n  Line 2"
    assert norm.original_body == "\n\n  Line 1   \n\n  Line 2   \n\n"


def test_normalize_many() -> None:
    """Verify batch normalization."""
    normalizer = CommitNormalizer()
    commits = [
        HistoricalCommit(hash="1", subject="feat: add a", body=""),
        HistoricalCommit(hash="2", subject="fix: fix b", body=""),
    ]

    normalized = normalizer.normalize_many(commits)
    assert len(normalized) == 2
    assert normalized[0].commit_type == "feat"
    assert normalized[1].commit_type == "fix"
