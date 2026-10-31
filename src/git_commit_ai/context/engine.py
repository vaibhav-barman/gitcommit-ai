"""Repository context engine.

Orchestrates normalization, convention detection, style profiling, and example retrieval.
"""

from __future__ import annotations

from typing import Any

from git_commit_ai.context.convention_detector import CommitConventionDetector
from git_commit_ai.context.example_selector import HistoricalExampleSelector
from git_commit_ai.context.normalizer import CommitNormalizer
from git_commit_ai.context.style_analyzer import RepositoryStyleAnalyzer
from git_commit_ai.core.schemas import (
    DiffResult,
    HistoricalCommit,
    RepositoryContext,
)


class RepositoryContextEngine:
    """Orchestrates repository context extraction for downstream AI agents.

    Coordinates commit normalization, statistical convention detection,
    stylistic profiling, and relevant historical example selection without
    executing Git commands directly.

    Args:
        normalizer: Custom ``CommitNormalizer`` instance (optional).
        detector: Custom ``CommitConventionDetector`` instance (optional).
        analyzer: Custom ``RepositoryStyleAnalyzer`` instance (optional).
        selector: Custom ``HistoricalExampleSelector`` instance (optional).
    """

    def __init__(
        self,
        normalizer: CommitNormalizer | None = None,
        detector: CommitConventionDetector | None = None,
        analyzer: RepositoryStyleAnalyzer | None = None,
        selector: HistoricalExampleSelector | None = None,
    ) -> None:
        self._normalizer = normalizer or CommitNormalizer()
        self._detector = detector or CommitConventionDetector()
        self._analyzer = analyzer or RepositoryStyleAnalyzer()
        self._selector = selector or HistoricalExampleSelector()

    def build_context(
        self,
        diff_result: DiffResult,
        historical_commits: list[HistoricalCommit],
        extra_metadata: dict[str, Any] | None = None,
    ) -> RepositoryContext:
        """Construct a complete ``RepositoryContext`` bundle.

        Args:
            diff_result: Extracted staged (or unstaged) diff.
            historical_commits: Extracted raw historical commits.
            extra_metadata: Optional supplementary metadata for tracing.

        Returns:
            A populated ``RepositoryContext`` ready for consumption by
            AI agents, evaluation loops, or CLI commands.
        """
        # Step 1: Normalize all historical commits
        normalized_commits = self._normalizer.normalize_many(historical_commits)

        # Step 2: Detect repository-level commit convention
        convention_result = self._detector.detect(normalized_commits)

        # Step 3: Analyze repository writing style profile
        style_profile = self._analyzer.analyze(
            normalized_commits,
            dominant_convention=convention_result.detected_convention,
        )

        # Step 4: Select relevant historical examples for the current diff
        retrieval_result = self._selector.select(
            diff_result=diff_result,
            historical_commits=normalized_commits,
            style_profile=style_profile,
        )

        metadata: dict[str, Any] = {
            "total_historical_commits": len(historical_commits),
            "files_in_diff": len(diff_result.file_changes),
            "diff_mode": diff_result.mode.value,
            **retrieval_result.selection_metadata,
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        return RepositoryContext(
            style_profile=style_profile,
            convention_result=convention_result,
            historical_examples=retrieval_result.examples,
            analyzed_commit_count=len(normalized_commits),
            retrieval_metadata=metadata,
        )
