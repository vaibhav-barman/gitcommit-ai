"""Pydantic data models and enums for Git-related data."""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field


class FileStatus(enum.StrEnum):
    """Status of a file in the Git working tree or index."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    UNTRACKED = "untracked"
    TYPE_CHANGED = "type_changed"


class DiffMode(enum.StrEnum):
    """Which changes to diff.

    ``AMEND`` is the staged index against the parent of ``HEAD``: what the
    commit would contain if ``HEAD`` were replaced by ``git commit --amend``.
    """

    STAGED = "staged"
    UNSTAGED = "unstaged"
    AMEND = "amend"


class RepositoryOperation(enum.StrEnum):
    """A multi-step Git operation that is paused and waiting for a commit."""

    NONE = "none"
    MERGE = "merge"
    REBASE = "rebase"
    CHERRY_PICK = "cherry-pick"
    REVERT = "revert"


class RepositoryInfo(BaseModel):
    """Snapshot of repository-level metadata."""

    root_path: str = Field(description="Absolute path to the repository root")
    branch: str | None = Field(
        default=None,
        description="Current branch name, or None if in detached HEAD state",
    )
    is_detached_head: bool = Field(
        default=False,
        description="Whether HEAD is detached",
    )
    is_empty: bool = Field(
        default=False,
        description="Whether the repository has zero commits",
    )
    operation: RepositoryOperation = Field(
        default=RepositoryOperation.NONE,
        description="Merge, rebase, cherry-pick or revert in progress, if any",
    )
    staged_files: list[str] = Field(
        default_factory=list,
        description="Paths of files staged for commit",
    )
    unstaged_files: list[str] = Field(
        default_factory=list,
        description="Paths of modified but unstaged files",
    )
    untracked_files: list[str] = Field(
        default_factory=list,
        description="Paths of untracked files",
    )


class FileChange(BaseModel):
    """Per-file change information from a diff."""

    path: str = Field(description="File path relative to repository root")
    status: FileStatus = Field(description="Type of change")
    additions: int = Field(default=0, description="Number of lines added")
    deletions: int = Field(default=0, description="Number of lines deleted")
    is_binary: bool = Field(default=False, description="Whether the file is binary")
    old_path: str | None = Field(
        default=None,
        description="Original path if the file was renamed or copied",
    )


class DiffStatistics(BaseModel):
    """Aggregate statistics for a diff."""

    files_changed: int = Field(default=0, description="Total number of files changed")
    total_additions: int = Field(default=0, description="Total lines added")
    total_deletions: int = Field(default=0, description="Total lines deleted")


class DiffResult(BaseModel):
    """Complete diff output including raw diff, per-file changes, and statistics."""

    raw_diff: str = Field(default="", description="Raw unified diff output")
    file_changes: list[FileChange] = Field(
        default_factory=list,
        description="Per-file change details",
    )
    statistics: DiffStatistics = Field(
        default_factory=DiffStatistics,
        description="Aggregate diff statistics",
    )
    mode: DiffMode = Field(
        default=DiffMode.STAGED,
        description="Whether this diff covers staged or unstaged changes",
    )


class HistoricalCommit(BaseModel):
    """Metadata for a single historical commit."""

    hash: str = Field(description="Full commit hash")
    subject: str = Field(description="First line of the commit message")
    body: str = Field(default="", description="Commit message body (may be multiline)")


class RepositorySnapshot(BaseModel):
    """Snapshot of the staged tree identity for staleness detection."""

    tree_hash: str = Field(description="Hash of the staged tree object")
    head_hash: str | None = Field(
        default=None,
        description="Commit HEAD pointed to at snapshot time (None in an empty repository)",
    )
    staged_files: list[str] = Field(
        default_factory=list,
        description="Sorted list of staged file paths at snapshot time",
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="When the snapshot was captured",
    )

    def matches(self, other: RepositorySnapshot) -> bool:
        """Return True if both snapshots represent the same staged state.

        The same staged tree is not the same change if ``HEAD`` moved, since the
        diff against ``HEAD`` (or its parent, when amending) would differ.
        """
        return self.tree_hash == other.tree_hash and self.head_hash == other.head_hash


# ---------------------------------------------------------------------------
# Phase 2: Context Engine Models
# ---------------------------------------------------------------------------


class CommitConvention(enum.StrEnum):
    """Categorization of commit message convention."""

    CONVENTIONAL = "conventional"
    SCOPED_CONVENTIONAL = "scoped_conventional"
    GITMOJI = "gitmoji"
    BRACKET_PREFIX = "bracket_prefix"
    PLAIN = "plain"
    UNKNOWN = "unknown"


class NormalizedCommit(BaseModel):
    """Normalized metadata extracted from a historical commit."""

    hash: str = Field(description="Full commit hash")
    original_subject: str = Field(description="Original subject line")
    original_body: str = Field(default="", description="Original commit body")
    cleaned_subject: str = Field(description="Subject with prefixes/whitespace normalized")
    cleaned_body: str = Field(default="", description="Cleaned body text")
    convention: CommitConvention = Field(
        default=CommitConvention.PLAIN,
        description="Identified convention for this individual commit",
    )
    commit_type: str | None = Field(
        default=None,
        description="Extracted Conventional Commit type (e.g. feat, fix, chore)",
    )
    scope: str | None = Field(
        default=None,
        description="Extracted Conventional Commit scope (e.g. auth, api)",
    )
    is_breaking: bool = Field(
        default=False,
        description="Whether this commit introduces a breaking change",
    )
    breaking_description: str | None = Field(
        default=None,
        description="Description of the breaking change if present",
    )
    gitmoji: str | None = Field(
        default=None,
        description="Extracted Gitmoji symbol or code if present",
    )
    bracket_prefix: str | None = Field(
        default=None,
        description="Extracted bracket prefix tag if present (e.g. [AUTH])",
    )

    @property
    def full_original_message(self) -> str:
        """Return the unparsed full original message."""
        if self.original_body:
            return f"{self.original_subject}\n\n{self.original_body}"
        return self.original_subject


class ConventionDetectionResult(BaseModel):
    """Statistical summary of repository-level commit convention analysis."""

    detected_convention: CommitConvention = Field(
        description="Predominant commit convention across analyzed commits",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score based on observed commit distribution",
    )
    supporting_evidence: list[str] = Field(
        default_factory=list,
        description="Human-readable rationales and observations",
    )
    analyzed_commits: int = Field(
        default=0,
        description="Total number of commits analyzed",
    )
    format_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Counts of commits matching each convention category",
    )


class CommitTypeStatistics(BaseModel):
    """Frequency metrics for a specific commit type."""

    type_name: str = Field(description="Commit type identifier (e.g. feat, fix)")
    count: int = Field(default=0, description="Number of occurrences")
    percentage: float = Field(default=0.0, description="Percentage of typed commits")
    breaking_count: int = Field(default=0, description="Count of breaking commits of this type")


class CommitStyleProfile(BaseModel):
    """Structured stylistic profile of repository commit habits."""

    dominant_convention: CommitConvention = Field(
        description="Dominant commit convention",
    )
    type_frequencies: dict[str, int] = Field(
        default_factory=dict,
        description="Counts per commit type",
    )
    scope_usage_frequency: float = Field(
        default=0.0,
        description="Ratio of commits utilizing a scope (0.0 to 1.0)",
    )
    common_scopes: list[str] = Field(
        default_factory=list,
        description="Most frequently used scopes in descending order",
    )
    average_subject_length: float = Field(
        default=0.0,
        description="Mean character length of commit subjects",
    )
    median_subject_length: float = Field(
        default=0.0,
        description="Median character length of commit subjects",
    )
    typical_capitalization: str = Field(
        default="unknown",
        description=(
            "Predominant subject capitalization: "
            "lowercase, uppercase, titlecase, sentencecase, mixed"
        ),
    )
    punctuation_patterns: list[str] = Field(
        default_factory=list,
        description="Observed subject punctuation styles",
    )
    imperative_mood_frequency: float | None = Field(
        default=None,
        description=(
            "Estimated ratio of subjects starting with imperative verbs, or None if uncertain"
        ),
    )
    body_usage_frequency: float = Field(
        default=0.0,
        description="Ratio of commits that include a non-empty body (0.0 to 1.0)",
    )
    breaking_change_frequency: float = Field(
        default=0.0,
        description="Ratio of commits marked as breaking changes (0.0 to 1.0)",
    )
    recent_convention_shifts: list[str] = Field(
        default_factory=list,
        description="Detected shifts in style between older and recent commits",
    )


class HistoricalExample(BaseModel):
    """Selected historical commit recommended as few-shot context."""

    hash: str = Field(description="Commit hash")
    subject: str = Field(description="Commit subject")
    body: str = Field(default="", description="Commit body")
    original_message: str = Field(description="Full original message text")
    relevance_score: float = Field(
        default=0.0,
        description="Composite relevance score for current diff",
    )
    selection_reasons: list[str] = Field(
        default_factory=list,
        description="Explanations of why this example was selected",
    )
    matched_scope: str | None = Field(default=None, description="Scope that matched the change")
    matched_type: str | None = Field(default=None, description="Type that matched the change")


class ExampleRetrievalResult(BaseModel):
    """Result of historical example selection."""

    examples: list[HistoricalExample] = Field(
        default_factory=list,
        description="Ranked list of relevant historical examples",
    )
    total_candidates: int = Field(default=0, description="Total candidates evaluated")
    selection_metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Metadata and tuning parameters used during retrieval",
    )


class RepositoryContext(BaseModel):
    """Aggregated repository context bundle for consumption by AI generators."""

    style_profile: CommitStyleProfile = Field(
        description="Repository commit style profile",
    )
    convention_result: ConventionDetectionResult = Field(
        description="Convention detection summary",
    )
    historical_examples: list[HistoricalExample] = Field(
        default_factory=list,
        description="Selected relevant historical examples",
    )
    analyzed_commit_count: int = Field(
        default=0,
        description="Number of historical commits inspected to build this context",
    )
    retrieval_metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Additional context generation metadata",
    )
