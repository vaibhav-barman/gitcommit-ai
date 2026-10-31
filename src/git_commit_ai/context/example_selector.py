"""Heuristic, recency-aware historical commit example selection."""

from __future__ import annotations

import re
from pathlib import Path

from git_commit_ai.core.schemas import (
    CommitStyleProfile,
    DiffResult,
    ExampleRetrievalResult,
    FileStatus,
    HistoricalExample,
    NormalizedCommit,
)

# Common words to ignore during keyword extraction
_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
}


class HistoricalExampleSelector:
    """Selects and ranks historical commit examples relevant to a staged diff.

    Combines scope alignment, path/filename overlap, change-type inference,
    keyword matching, and explainable recency decay.
    """

    def __init__(
        self,
        max_examples: int = 5,
        recency_decay: float = 0.05,
        min_score: float = 0.5,
    ) -> None:
        self._max_examples = max_examples
        self._recency_decay = recency_decay
        self._min_score = min_score

    def select(
        self,
        diff_result: DiffResult,
        historical_commits: list[NormalizedCommit],
        style_profile: CommitStyleProfile | None = None,
    ) -> ExampleRetrievalResult:
        """Select top relevant historical examples for the given diff.

        Args:
            diff_result: Extracted diff information.
            historical_commits: Normalized commits ordered most-recent first.
            style_profile: Optional repository style profile for context.

        Returns:
            An ``ExampleRetrievalResult`` containing ranked examples and metadata.
        """
        if not historical_commits or not diff_result.file_changes:
            return ExampleRetrievalResult(
                examples=[],
                total_candidates=len(historical_commits),
                selection_metadata={
                    "max_examples": self._max_examples,
                    "recency_decay": self._recency_decay,
                },
            )

        # 1. Extract context clues from current diff
        diff_scopes = self._extract_diff_scopes(diff_result)
        diff_paths = {Path(fc.path).stem.lower() for fc in diff_result.file_changes}
        diff_extensions = {Path(fc.path).suffix.lower() for fc in diff_result.file_changes}
        inferred_types = self._infer_diff_types(diff_result)
        keywords = self._extract_keywords(diff_result)

        scored_examples: list[HistoricalExample] = []
        seen_hashes: set[str] = set()

        for rank, commit in enumerate(historical_commits):
            if commit.hash in seen_hashes:
                continue

            base_score = 0.0
            reasons: list[str] = []
            matched_scope: str | None = None
            matched_type: str | None = None

            # Factor A: Scope match (strongest signal)
            if commit.scope and commit.scope.lower() in diff_scopes:
                base_score += 3.0
                matched_scope = commit.scope
                reasons.append(f"Matches scope '{commit.scope}' found in changed files")

            # Factor B: Path or module name match
            subject_words = set(re.findall(r"[a-zA-Z0-9_-]+", commit.cleaned_subject.lower()))
            common_paths = subject_words.intersection(diff_paths)
            if common_paths:
                matched_names = ", ".join(sorted(common_paths))
                base_score += 2.0
                reasons.append(f"References changed file name(s): {matched_names}")

            # Factor C: Change type alignment
            if commit.commit_type and commit.commit_type.lower() in inferred_types:
                base_score += 1.5
                matched_type = commit.commit_type
                reasons.append(f"Matches inferred change type '{commit.commit_type}'")

            # Factor D: Keyword overlap
            common_keywords = subject_words.intersection(keywords)
            if common_keywords:
                kw_score = min(1.5, len(common_keywords) * 0.5)
                base_score += kw_score
                kw_str = ", ".join(sorted(common_keywords))
                reasons.append(f"Shares keywords with change: {kw_str}")

            if base_score < self._min_score:
                continue

            # Factor E: Recency decay weighting
            # Rank 0 (most recent) has weight 1.0; older ranks decay gradually
            recency_weight = 1.0 / (1.0 + self._recency_decay * rank)
            final_score = round(base_score * recency_weight, 2)

            seen_hashes.add(commit.hash)
            scored_examples.append(
                HistoricalExample(
                    hash=commit.hash,
                    subject=commit.original_subject,
                    body=commit.original_body,
                    original_message=commit.full_original_message,
                    relevance_score=final_score,
                    selection_reasons=reasons,
                    matched_scope=matched_scope,
                    matched_type=matched_type,
                )
            )

        # Sort descending by relevance score, then take top N
        scored_examples.sort(key=lambda ex: ex.relevance_score, reverse=True)
        selected = scored_examples[: self._max_examples]

        return ExampleRetrievalResult(
            examples=selected,
            total_candidates=len(historical_commits),
            selection_metadata={
                "max_examples": self._max_examples,
                "recency_decay": self._recency_decay,
                "diff_scopes": sorted(diff_scopes),
                "inferred_types": sorted(inferred_types),
                "diff_extensions": sorted(diff_extensions),
            },
        )

    # ------------------------------------------------------------------
    # Heuristic extractors
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_diff_scopes(diff_result: DiffResult) -> set[str]:
        """Extract candidate scopes from file paths in the diff."""
        scopes: set[str] = set()
        for fc in diff_result.file_changes:
            parts = Path(fc.path).parts
            for p in parts[:-1]:
                clean = p.lower().strip()
                if clean not in ("src", "tests", "lib", "app", "pkg"):
                    scopes.add(clean)
            # Also filename stem
            stem = Path(fc.path).stem.lower().strip()
            if stem and len(stem) > 2 and stem not in ("__init__", "main", "base", "utils"):
                scopes.add(stem)
        return scopes

    @staticmethod
    def _infer_diff_types(diff_result: DiffResult) -> set[str]:
        """Infer probable Conventional Commit types from diff changes."""
        types: set[str] = set()
        paths = [fc.path.lower() for fc in diff_result.file_changes]
        statuses = [fc.status for fc in diff_result.file_changes]

        # All test files
        if paths and all("test" in p for p in paths):
            types.add("test")

        # Documentation changes
        if paths and all(p.endswith((".md", ".rst", ".txt")) or "doc" in p for p in paths):
            types.add("docs")

        # Build / CI changes
        build_indicators = (
            "pyproject.toml",
            "package.json",
            ".github",
            "docker",
            "makefile",
        )
        if paths and all(any(k in p for k in build_indicators) for p in paths):
            types.add("build")
            types.add("ci")
            types.add("chore")

        # Added new files
        if any(st == FileStatus.ADDED for st in statuses):
            types.add("feat")

        # Modified files
        if any(st == FileStatus.MODIFIED for st in statuses):
            types.add("fix")
            types.add("refactor")
            types.add("feat")

        return types

    @staticmethod
    def _extract_keywords(diff_result: DiffResult) -> set[str]:
        """Extract meaningful keywords from changed file paths."""
        words: set[str] = set()
        for fc in diff_result.file_changes:
            tokens = re.split(r"[/._-]+", fc.path.lower())
            for t in tokens:
                if len(t) > 2 and t not in _STOPWORDS and not t.isdigit():
                    words.add(t)
        return words
