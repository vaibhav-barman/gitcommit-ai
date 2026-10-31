"""Unit tests for CommitConventionDetector."""

from __future__ import annotations

from git_commit_ai.context.convention_detector import CommitConventionDetector
from git_commit_ai.context.normalizer import CommitNormalizer
from git_commit_ai.core.schemas import CommitConvention, HistoricalCommit, NormalizedCommit


def _normalize(subjects: list[str]) -> list[NormalizedCommit]:
    normalizer = CommitNormalizer()
    return normalizer.normalize_many(
        [HistoricalCommit(hash=f"h{i}", subject=s, body="") for i, s in enumerate(subjects)]
    )


def test_detect_conventional_repository() -> None:
    """Verify detection of predominantly unscoped Conventional Commits."""
    detector = CommitConventionDetector()
    subjects = [
        "feat: add feature a",
        "fix: resolve bug b",
        "docs: update readme",
        "refactor: clean up utils",
        "feat: add feature c",
        "fix: correct edge case",
        "chore: bump version",
        "test: add unit tests",
        "Update dependencies",  # 1 plain
    ]
    result = detector.detect(_normalize(subjects))

    assert result.detected_convention == CommitConvention.CONVENTIONAL
    assert result.confidence >= 0.80
    assert result.analyzed_commits == 9
    assert result.format_distribution["conventional"] == 8


def test_detect_scoped_conventional_repository() -> None:
    """Verify detection of predominantly scoped Conventional Commits."""
    detector = CommitConventionDetector()
    subjects = [
        "feat(auth): add login endpoint",
        "fix(api): handle timeout error",
        "refactor(core): simplify config loader",
        "feat(cli): add inspect command",
        "docs(readme): add installation guide",
        "test(auth): add mock token tests",
        "feat: unscoped commit",
    ]
    result = detector.detect(_normalize(subjects))

    assert result.detected_convention == CommitConvention.SCOPED_CONVENTIONAL
    assert result.confidence >= 0.85
    assert result.format_distribution["scoped_conventional"] == 6


def test_detect_gitmoji_repository() -> None:
    """Verify detection of Gitmoji style repository."""
    detector = CommitConventionDetector()
    subjects = [
        ":sparkles: Introduce new auth service",
        ":bug: Fix critical race condition",
        ":recycle: Refactor repository inspector",
        ":memo: Document architecture decisions",
        ":white_check_mark: Add unit tests for normalizer",
        "Plain commit message",
    ]
    result = detector.detect(_normalize(subjects))

    assert result.detected_convention == CommitConvention.GITMOJI
    assert result.confidence >= 0.70


def test_detect_bracket_prefix_repository() -> None:
    """Verify detection of bracketed prefix tags."""
    detector = CommitConventionDetector()
    subjects = [
        "[AUTH] Implement OAuth2 flow",
        "[CORE] Add error hierarchy",
        "[CLI] Format diff output",
        "[DOCS] Update API specs",
        "[TESTS] Cover edge cases",
    ]
    result = detector.detect(_normalize(subjects))

    assert result.detected_convention == CommitConvention.BRACKET_PREFIX
    assert result.confidence == 1.0


def test_detect_plain_repository() -> None:
    """Verify detection of plain descriptive messages."""
    detector = CommitConventionDetector()
    subjects = [
        "Initial commit",
        "Added user management views",
        "Fixed login issue with empty passwords",
        "Updated documentation and deployment scripts",
        "Refactored database models",
    ]
    result = detector.detect(_normalize(subjects))

    assert result.detected_convention == CommitConvention.PLAIN
    assert result.confidence == 1.0


def test_detect_mixed_repository() -> None:
    """Verify detection of mixed convention repository returns UNKNOWN."""
    detector = CommitConventionDetector()
    subjects = [
        "feat: add feature",
        "feat(api): add endpoint",
        ":sparkles: gitmoji feature",
        "[CORE] bracket commit",
        "Plain descriptive message",
        "Another plain message",
    ]
    result = detector.detect(_normalize(subjects))

    assert result.detected_convention == CommitConvention.UNKNOWN
    assert result.confidence < 0.60
    assert any("mixed" in ev for ev in result.supporting_evidence)


def test_detect_empty_commits() -> None:
    """Verify empty commits return UNKNOWN with zero confidence."""
    detector = CommitConventionDetector()
    result = detector.detect([])

    assert result.detected_convention == CommitConvention.UNKNOWN
    assert result.confidence == 0.0
    assert result.analyzed_commits == 0


def test_low_sample_size_penalty() -> None:
    """Verify confidence is penalized when sample size is very small."""
    detector = CommitConventionDetector()
    # 2 commits: 100% conventional, but low sample size
    subjects = [
        "feat: initial commit",
        "fix: quick fix",
    ]
    result = detector.detect(_normalize(subjects))

    assert result.detected_convention == CommitConvention.CONVENTIONAL
    assert result.confidence <= 0.60  # penalized
    assert any("Low sample size" in ev for ev in result.supporting_evidence)
