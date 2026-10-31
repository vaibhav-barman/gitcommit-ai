"""Unit tests for RepositoryStyleAnalyzer."""

from __future__ import annotations

from git_commit_ai.context.normalizer import CommitNormalizer
from git_commit_ai.context.style_analyzer import RepositoryStyleAnalyzer
from git_commit_ai.core.schemas import CommitConvention, HistoricalCommit, NormalizedCommit


def _norm(subject: str, body: str = "") -> NormalizedCommit:
    return CommitNormalizer().normalize(HistoricalCommit(hash="h1", subject=subject, body=body))


def test_style_analyzer_empty_commits() -> None:
    """Verify analyzer handles zero commits without raising an error."""
    analyzer = RepositoryStyleAnalyzer()
    profile = analyzer.analyze([])

    assert profile.dominant_convention == CommitConvention.UNKNOWN
    assert profile.average_subject_length == 0.0
    assert profile.median_subject_length == 0.0
    assert profile.type_frequencies == {}
    assert profile.common_scopes == []


def test_style_analyzer_subject_lengths() -> None:
    """Verify mean and median subject length calculation."""
    analyzer = RepositoryStyleAnalyzer()
    # Cleaned subjects: "add feature" (11), "fix bug now" (11), "update auth docs" (16)
    commits = [
        _norm("feat: add feature"),
        _norm("fix: fix bug now"),
        _norm("docs: update auth docs"),
    ]
    profile = analyzer.analyze(commits)

    assert profile.average_subject_length == round((11 + 11 + 16) / 3, 1)
    assert profile.median_subject_length == 11.0


def test_style_analyzer_types_and_scopes() -> None:
    """Verify type frequency and scope extraction."""
    analyzer = RepositoryStyleAnalyzer()
    commits = [
        _norm("feat(auth): login"),
        _norm("feat(auth): signup"),
        _norm("fix(api): timeout"),
        _norm("refactor(core): cleanup"),
        _norm("feat: new landing"),
    ]
    profile = analyzer.analyze(commits)

    assert profile.type_frequencies["feat"] == 3
    assert profile.type_frequencies["fix"] == 1
    assert profile.type_frequencies["refactor"] == 1
    assert profile.common_scopes[0] == "auth"
    assert profile.scope_usage_frequency == 0.8  # 4 of 5 scoped


def test_style_analyzer_capitalization() -> None:
    """Verify detection of lowercase, sentence case, and title case."""
    analyzer = RepositoryStyleAnalyzer()

    # Lowercase
    lower_commits = [_norm("feat: add a"), _norm("fix: fix b"), _norm("chore: update c")]
    assert analyzer.analyze(lower_commits).typical_capitalization == "lowercase"

    # Sentencecase
    sentence_commits = [
        _norm("Add user authentication view"),
        _norm("Fix database connection retry"),
        _norm("Update documentation guide"),
    ]
    assert analyzer.analyze(sentence_commits).typical_capitalization == "sentencecase"


def test_style_analyzer_punctuation() -> None:
    """Verify detection of trailing punctuation."""
    analyzer = RepositoryStyleAnalyzer()

    no_period = [_norm("feat: add item"), _norm("fix: resolve bug")]
    assert analyzer.analyze(no_period).punctuation_patterns == ["no_trailing_period"]

    with_period = [_norm("feat: add item."), _norm("fix: resolve bug.")]
    assert analyzer.analyze(with_period).punctuation_patterns == ["trailing_period"]


def test_style_analyzer_imperative_mood() -> None:
    """Verify imperative mood heuristic frequency."""
    analyzer = RepositoryStyleAnalyzer()
    commits = [
        _norm("feat: add feature"),
        _norm("fix: resolve crash"),
        _norm("refactor: clean models"),
        _norm("feat: adding something bad"),  # gerund, not imperative
    ]
    profile = analyzer.analyze(commits)

    assert profile.imperative_mood_frequency is not None
    assert profile.imperative_mood_frequency == 0.75  # 3 of 4 imperative


def test_style_analyzer_body_and_breaking_frequency() -> None:
    """Verify body usage and breaking change frequencies."""
    analyzer = RepositoryStyleAnalyzer()
    commits = [
        _norm("feat: feature 1", body="Detailed description"),
        _norm("fix!: breaking bug fix", body=""),
        _norm("chore: simple task", body=""),
        _norm("docs: simple doc", body=""),
    ]
    profile = analyzer.analyze(commits)

    assert profile.body_usage_frequency == 0.25  # 1 of 4
    assert profile.breaking_change_frequency == 0.25  # 1 of 4


def test_style_analyzer_recent_shift_detection() -> None:
    """Verify detecting recent adoption of Conventional Commits."""
    analyzer = RepositoryStyleAnalyzer()
    # 3 recent conventional commits + 5 older plain commits
    recent = [
        _norm("feat(auth): new login flow"),
        _norm("fix(core): resolve null pointer"),
        _norm("refactor(api): clean up endpoints"),
    ]
    older = [
        _norm("Initial commit"),
        _norm("Added database support"),
        _norm("Fixed layout issues"),
        _norm("Updated readme"),
        _norm("More bug fixes"),
    ]
    all_commits = recent + older
    profile = analyzer.analyze(all_commits)

    assert len(profile.recent_convention_shifts) > 0
    assert any("Conventional Commits" in shift for shift in profile.recent_convention_shifts)
