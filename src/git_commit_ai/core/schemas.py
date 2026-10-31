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
    """Which changes to diff."""

    STAGED = "staged"
    UNSTAGED = "unstaged"


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
    staged_files: list[str] = Field(
        default_factory=list,
        description="Sorted list of staged file paths at snapshot time",
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="When the snapshot was captured",
    )

    def matches(self, other: RepositorySnapshot) -> bool:
        """Return True if both snapshots represent the same staged state."""
        return self.tree_hash == other.tree_hash
