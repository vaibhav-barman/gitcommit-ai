"""Commit convention detection using statistical distribution analysis."""

from __future__ import annotations

from git_commit_ai.core.schemas import (
    CommitConvention,
    ConventionDetectionResult,
    NormalizedCommit,
)


class CommitConventionDetector:
    """Detects repository-level commit conventions from normalized commits.

    Uses statistical distribution analysis to identify whether a repository
    predominantly adheres to Conventional Commits, Scoped Conventional Commits,
    Gitmoji, Bracket-prefixed commits, or Plain descriptive commits.
    """

    def __init__(
        self,
        dominance_threshold: float = 0.65,
        scoped_threshold: float = 0.50,
    ) -> None:
        self._dominance_threshold = dominance_threshold
        self._scoped_threshold = scoped_threshold

    def detect(self, commits: list[NormalizedCommit]) -> ConventionDetectionResult:
        """Analyze normalized commits and determine the dominant convention.

        Args:
            commits: List of normalized historical commits.

        Returns:
            A ``ConventionDetectionResult`` with classification, confidence,
            supporting evidence, and format counts.
        """
        total = len(commits)
        if total == 0:
            return ConventionDetectionResult(
                detected_convention=CommitConvention.UNKNOWN,
                confidence=0.0,
                supporting_evidence=["No commits available for convention analysis."],
                analyzed_commits=0,
                format_distribution={},
            )

        # Count frequencies of each format
        distribution: dict[str, int] = {
            CommitConvention.CONVENTIONAL.value: 0,
            CommitConvention.SCOPED_CONVENTIONAL.value: 0,
            CommitConvention.GITMOJI.value: 0,
            CommitConvention.BRACKET_PREFIX.value: 0,
            CommitConvention.PLAIN.value: 0,
        }

        for c in commits:
            key = c.convention.value
            distribution[key] = distribution.get(key, 0) + 1

        unscoped_conv = distribution[CommitConvention.CONVENTIONAL.value]
        scoped_conv = distribution[CommitConvention.SCOPED_CONVENTIONAL.value]
        total_conv = unscoped_conv + scoped_conv
        gitmoji_count = distribution[CommitConvention.GITMOJI.value]
        bracket_count = distribution[CommitConvention.BRACKET_PREFIX.value]
        plain_count = distribution[CommitConvention.PLAIN.value]

        conv_ratio = total_conv / total
        gitmoji_ratio = gitmoji_count / total
        bracket_ratio = bracket_count / total
        plain_ratio = plain_count / total

        evidence: list[str] = []
        low_sample_penalty = 1.0
        if total < 5:
            low_sample_penalty = max(0.4, total / 5.0)
            evidence.append(
                f"Low sample size: only {total} commit(s) analyzed; confidence adjusted."
            )

        # 1. Conventional Commits (scoped or unscoped)
        if conv_ratio >= self._dominance_threshold:
            pct = conv_ratio * 100.0
            evidence.append(
                f"{total_conv} of {total} commits ({pct:.1f}%) follow Conventional Commits format."
            )

            scoped_ratio_within_conv = scoped_conv / total_conv if total_conv > 0 else 0.0
            if scoped_ratio_within_conv >= self._scoped_threshold:
                scoped_pct = scoped_ratio_within_conv * 100.0
                evidence.append(
                    f"{scoped_conv} of {total_conv} conventional commits "
                    f"({scoped_pct:.1f}%) specify a scope."
                )
                confidence = round(conv_ratio * low_sample_penalty, 2)
                return ConventionDetectionResult(
                    detected_convention=CommitConvention.SCOPED_CONVENTIONAL,
                    confidence=confidence,
                    supporting_evidence=evidence,
                    analyzed_commits=total,
                    format_distribution=distribution,
                )

            confidence = round(conv_ratio * low_sample_penalty, 2)
            evidence.append("Conventional commits are predominantly unscoped.")
            return ConventionDetectionResult(
                detected_convention=CommitConvention.CONVENTIONAL,
                confidence=confidence,
                supporting_evidence=evidence,
                analyzed_commits=total,
                format_distribution=distribution,
            )

        # 2. Gitmoji
        if gitmoji_ratio >= self._dominance_threshold:
            pct = gitmoji_ratio * 100.0
            evidence.append(
                f"{gitmoji_count} of {total} commits ({pct:.1f}%) use Gitmoji prefixes."
            )
            confidence = round(gitmoji_ratio * low_sample_penalty, 2)
            return ConventionDetectionResult(
                detected_convention=CommitConvention.GITMOJI,
                confidence=confidence,
                supporting_evidence=evidence,
                analyzed_commits=total,
                format_distribution=distribution,
            )

        # 3. Bracket prefix
        if bracket_ratio >= self._dominance_threshold:
            pct = bracket_ratio * 100.0
            evidence.append(
                f"{bracket_count} of {total} commits ({pct:.1f}%) use bracketed prefix tags."
            )
            confidence = round(bracket_ratio * low_sample_penalty, 2)
            return ConventionDetectionResult(
                detected_convention=CommitConvention.BRACKET_PREFIX,
                confidence=confidence,
                supporting_evidence=evidence,
                analyzed_commits=total,
                format_distribution=distribution,
            )

        # 4. Plain descriptive messages
        if plain_ratio >= self._dominance_threshold:
            pct = plain_ratio * 100.0
            evidence.append(
                f"{plain_count} of {total} commits ({pct:.1f}%) are plain descriptive messages."
            )
            confidence = round(plain_ratio * low_sample_penalty, 2)
            return ConventionDetectionResult(
                detected_convention=CommitConvention.PLAIN,
                confidence=confidence,
                supporting_evidence=evidence,
                analyzed_commits=total,
                format_distribution=distribution,
            )

        # 5. Mixed / Unknown convention
        max_ratio = max(conv_ratio, gitmoji_ratio, bracket_ratio, plain_ratio)
        evidence.append(
            "Repository convention is mixed: no single format achieves dominant consensus "
            f"(highest format represented at {max_ratio * 100.0:.1f}%)."
        )
        confidence = round(max_ratio * 0.6 * low_sample_penalty, 2)
        return ConventionDetectionResult(
            detected_convention=CommitConvention.UNKNOWN,
            confidence=confidence,
            supporting_evidence=evidence,
            analyzed_commits=total,
            format_distribution=distribution,
        )
