"""Repository commit style analysis and profiling."""

from __future__ import annotations

import statistics
from collections import Counter

from git_commit_ai.core.schemas import (
    CommitConvention,
    CommitStyleProfile,
    NormalizedCommit,
)

# Common imperative verbs frequently starting commit subjects
COMMON_IMPERATIVE_VERBS = frozenset(
    {
        "add",
        "adjust",
        "align",
        "allow",
        "avoid",
        "bump",
        "change",
        "check",
        "clean",
        "clear",
        "configure",
        "convert",
        "correct",
        "create",
        "delete",
        "deprecate",
        "disable",
        "document",
        "drop",
        "enable",
        "enforce",
        "ensure",
        "exclude",
        "export",
        "expose",
        "extend",
        "extract",
        "fix",
        "format",
        "handle",
        "implement",
        "improve",
        "include",
        "initialize",
        "install",
        "integrate",
        "introduce",
        "limit",
        "load",
        "make",
        "migrate",
        "move",
        "normalize",
        "optimize",
        "parse",
        "patch",
        "prevent",
        "refactor",
        "release",
        "remove",
        "rename",
        "replace",
        "require",
        "reset",
        "resolve",
        "restore",
        "restrict",
        "revert",
        "revise",
        "set",
        "setup",
        "simplify",
        "split",
        "standardize",
        "start",
        "stop",
        "strip",
        "support",
        "switch",
        "test",
        "track",
        "update",
        "upgrade",
        "use",
        "validate",
        "verify",
        "warn",
        "wrap",
    }
)


class RepositoryStyleAnalyzer:
    """Analyzes historical commits to produce an empirical style profile."""

    def analyze(
        self,
        commits: list[NormalizedCommit],
        dominant_convention: CommitConvention | None = None,
    ) -> CommitStyleProfile:
        """Construct a style profile from normalized commits.

        Args:
            commits: Collection of normalized historical commits.
            dominant_convention: Predetermined convention if already detected,
                                 or determined automatically if None.

        Returns:
            A populated ``CommitStyleProfile`` model.
        """
        if not commits:
            return CommitStyleProfile(
                dominant_convention=dominant_convention or CommitConvention.UNKNOWN,
            )

        total = len(commits)

        # 1. Convention
        effective_convention = dominant_convention or self._infer_convention(commits)

        # 2. Type frequencies
        types = [c.commit_type for c in commits if c.commit_type]
        type_counts = dict(Counter(types).most_common())

        # 3. Scope analysis
        scopes = [c.scope for c in commits if c.scope]
        scope_usage_freq = round(len(scopes) / total, 2)
        common_scopes = [s for s, _ in Counter(scopes).most_common(10)]

        # 4. Subject lengths (measured on cleaned subjects)
        lengths = [len(c.cleaned_subject) for c in commits if c.cleaned_subject]
        if lengths:
            avg_len = round(sum(lengths) / len(lengths), 1)
            med_len = float(statistics.median(lengths))
        else:
            avg_len = 0.0
            med_len = 0.0

        # 5. Capitalization
        capitalization = self._analyze_capitalization(commits)

        # 6. Punctuation
        punctuation = self._analyze_punctuation(commits)

        # 7. Imperative mood frequency
        imperative_freq = self._analyze_imperative_mood(commits)

        # 8. Body and breaking change usage
        body_count = sum(1 for c in commits if c.original_body.strip())
        body_freq = round(body_count / total, 2)

        breaking_count = sum(1 for c in commits if c.is_breaking)
        breaking_freq = round(breaking_count / total, 2)

        # 9. Recent shifts
        shifts = self._detect_shifts(commits)

        return CommitStyleProfile(
            dominant_convention=effective_convention,
            type_frequencies=type_counts,
            scope_usage_frequency=scope_usage_freq,
            common_scopes=common_scopes,
            average_subject_length=avg_len,
            median_subject_length=med_len,
            typical_capitalization=capitalization,
            punctuation_patterns=punctuation,
            imperative_mood_frequency=imperative_freq,
            body_usage_frequency=body_freq,
            breaking_change_frequency=breaking_freq,
            recent_convention_shifts=shifts,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_convention(commits: list[NormalizedCommit]) -> CommitConvention:
        counts = Counter(c.convention for c in commits)
        most_common, _ = counts.most_common(1)[0]
        return most_common

    @staticmethod
    def _analyze_capitalization(commits: list[NormalizedCommit]) -> str:
        """Determine predominant subject capitalization."""
        first_chars: list[str] = []
        for c in commits:
            subj = c.cleaned_subject.strip()
            if subj and subj[0].isalpha():
                first_chars.append(subj[0])

        if not first_chars:
            return "unknown"

        lower_count = sum(1 for ch in first_chars if ch.islower())
        upper_count = sum(1 for ch in first_chars if ch.isupper())
        total = len(first_chars)

        if lower_count / total >= 0.65:
            return "lowercase"
        if upper_count / total >= 0.65:
            # Distinguish sentence case vs title case vs all uppercase
            all_upper = sum(1 for c in commits if c.cleaned_subject.isupper())
            if all_upper / len(commits) >= 0.65:
                return "uppercase"

            title_count = sum(1 for c in commits if c.cleaned_subject.istitle())
            if title_count / len(commits) >= 0.50:
                return "titlecase"
            return "sentencecase"

        return "mixed"

    @staticmethod
    def _analyze_punctuation(commits: list[NormalizedCommit]) -> list[str]:
        """Detect trailing punctuation patterns in subjects."""
        trailing_period_count = 0
        total = 0

        for c in commits:
            subj = c.cleaned_subject.strip()
            if subj:
                total += 1
                if subj.endswith("."):
                    trailing_period_count += 1

        if total == 0:
            return []

        period_ratio = trailing_period_count / total
        if period_ratio >= 0.70:
            return ["trailing_period"]
        if period_ratio <= 0.20:
            return ["no_trailing_period"]
        return ["mixed_trailing_period"]

    @staticmethod
    def _analyze_imperative_mood(commits: list[NormalizedCommit]) -> float | None:
        """Estimate the proportion of subjects beginning with an imperative verb."""
        valid_subjects = 0
        imperative_count = 0

        for c in commits:
            words = c.cleaned_subject.strip().split()
            if not words:
                continue
            first_word = words[0].lower().rstrip(":,.-_")
            valid_subjects += 1
            if first_word in COMMON_IMPERATIVE_VERBS:
                imperative_count += 1

        if valid_subjects < 3:
            return None

        return round(imperative_count / valid_subjects, 2)

    @staticmethod
    def _detect_shifts(commits: list[NormalizedCommit]) -> list[str]:
        """Identify recent shifts in conventions compared to older history."""
        # Need at least 6 commits to detect a temporal shift meaningfully
        if len(commits) < 6:
            return []

        # Most recent first
        split_idx = max(3, len(commits) // 3)
        recent_commits = commits[:split_idx]
        older_commits = commits[split_idx:]

        shifts: list[str] = []

        # Conventional shift check
        recent_conv = sum(
            1
            for c in recent_commits
            if c.convention
            in (CommitConvention.CONVENTIONAL, CommitConvention.SCOPED_CONVENTIONAL)
        )
        older_conv = sum(
            1
            for c in older_commits
            if c.convention
            in (CommitConvention.CONVENTIONAL, CommitConvention.SCOPED_CONVENTIONAL)
        )

        recent_conv_ratio = recent_conv / len(recent_commits)
        older_conv_ratio = older_conv / len(older_commits)

        if recent_conv_ratio >= 0.75 and older_conv_ratio <= 0.35:
            shifts.append(
                "Recent commits adopted Conventional Commits formatting "
                f"({recent_conv_ratio * 100:.0f}% recent vs "
                f"{older_conv_ratio * 100:.0f}% historical)."
            )

        # Scope adoption shift
        recent_scoped = sum(1 for c in recent_commits if c.scope)
        older_scoped = sum(1 for c in older_commits if c.scope)

        recent_scope_ratio = recent_scoped / len(recent_commits)
        older_scope_ratio = older_scoped / len(older_commits)

        if recent_scope_ratio >= 0.65 and older_scope_ratio <= 0.25:
            shifts.append(
                "Recent commits adopted scoped commit convention "
                f"({recent_scope_ratio * 100:.0f}% recent vs "
                f"{older_scope_ratio * 100:.0f}% historical)."
            )

        return shifts
