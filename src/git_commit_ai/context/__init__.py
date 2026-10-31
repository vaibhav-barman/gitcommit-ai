"""Context engine module.

Handles normalization, convention detection, style analysis, and example selection.
"""

from __future__ import annotations

from git_commit_ai.context.convention_detector import CommitConventionDetector
from git_commit_ai.context.normalizer import CommitNormalizer
from git_commit_ai.core.schemas import (
    CommitConvention,
    CommitStyleProfile,
    CommitTypeStatistics,
    ConventionDetectionResult,
    ExampleRetrievalResult,
    HistoricalExample,
    NormalizedCommit,
    RepositoryContext,
)

__all__ = [
    "CommitConvention",
    "CommitConventionDetector",
    "CommitNormalizer",
    "CommitStyleProfile",
    "CommitTypeStatistics",
    "ConventionDetectionResult",
    "ExampleRetrievalResult",
    "HistoricalExample",
    "NormalizedCommit",
    "RepositoryContext",
]
