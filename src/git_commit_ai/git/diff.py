"""Diff extraction: staged and unstaged diffs with per-file statistics."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.schemas import (
    DiffMode,
    DiffResult,
    DiffStatistics,
    FileChange,
    FileStatus,
)

if TYPE_CHECKING:
    from git_commit_ai.git.repository import RepositoryInspector


class DiffExtractor:
    """Extracts and parses diffs from a Git repository.

    Default behavior analyses **staged** changes only.
    Never automatically stages, unstages, discards, or modifies files.

    Args:
        inspector: A ``RepositoryInspector`` bound to the target repository.
        config: Application configuration (provides timeout defaults).
    """

    _STAT_LINE_RE = re.compile(
        r"^\s*(\d+|-)\s+(\d+|-)\s+(.+)$",
    )

    def __init__(
        self,
        inspector: RepositoryInspector,
        config: GitCommitAIConfig | None = None,
    ) -> None:
        self._inspector = inspector
        self._config = config or GitCommitAIConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, mode: DiffMode | None = None) -> DiffResult:
        """Extract diff output, per-file changes, and statistics.

        Args:
            mode: ``DiffMode.STAGED`` or ``DiffMode.UNSTAGED``.
                  Defaults to the configured default diff mode.

        Returns:
            A fully populated ``DiffResult``.
        """
        effective_mode = mode or self._config.default_diff_mode
        raw_diff = self._get_raw_diff(effective_mode)
        file_changes = self._get_file_changes(effective_mode)
        stats = self._compute_statistics(file_changes)

        return DiffResult(
            raw_diff=raw_diff,
            file_changes=file_changes,
            statistics=stats,
            mode=effective_mode,
        )

    def get_staged_diff(self) -> DiffResult:
        """Convenience method: extract staged changes only."""
        return self.extract(DiffMode.STAGED)

    def get_unstaged_diff(self) -> DiffResult:
        """Convenience method: extract unstaged changes only."""
        return self.extract(DiffMode.UNSTAGED)

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _get_raw_diff(self, mode: DiffMode) -> str:
        """Retrieve raw unified diff output."""
        args = [*self._diff_args(mode), "-M", "--no-color"]
        result = self._inspector._run_git(args, check=False)
        return result.stdout

    def _get_file_changes(self, mode: DiffMode) -> list[FileChange]:
        """Parse per-file change details from numstat and name-status."""
        numstat = self._get_numstat(mode)
        name_status = self._get_name_status(mode)

        # Build a lookup of status and rename info from name-status
        status_map: dict[str, tuple[FileStatus, str | None]] = {}
        for entry in name_status:
            status, old_path, new_path = self._parse_name_status_line(entry)
            target = new_path or old_path
            if target is not None:
                status_map[target] = (status, old_path if new_path else None)

        changes: list[FileChange] = []
        for line in numstat:
            parsed = self._parse_numstat_line(line)
            if parsed is None:
                continue
            additions, deletions, raw_path, is_binary = parsed
            old_p, new_p = self._resolve_rename_paths(raw_path)
            target_path = new_p if " => " in raw_path else raw_path
            detected_old = old_p if " => " in raw_path else None

            status_info = status_map.get(
                target_path,
                (FileStatus.RENAMED if detected_old else FileStatus.MODIFIED, detected_old),
            )

            changes.append(
                FileChange(
                    path=target_path,
                    status=status_info[0],
                    additions=additions,
                    deletions=deletions,
                    is_binary=is_binary,
                    old_path=status_info[1] or detected_old,
                )
            )
        return changes

    def _get_numstat(self, mode: DiffMode) -> list[str]:
        """Run ``git diff --numstat`` for addition/deletion counts."""
        args = [*self._diff_args(mode), "--numstat", "-M"]
        result = self._inspector._run_git(args, check=False)
        return self._split_lines(result.stdout)

    def _get_name_status(self, mode: DiffMode) -> list[str]:
        """Run ``git diff --name-status`` for file status codes."""
        args = [*self._diff_args(mode), "--name-status", "-M"]
        result = self._inspector._run_git(args, check=False)
        return self._split_lines(result.stdout)

    def _diff_args(self, mode: DiffMode) -> list[str]:
        """Build the base diff command arguments for the given mode."""
        if mode == DiffMode.STAGED:
            return ["diff", "--cached"]
        return ["diff"]

    @staticmethod
    def _resolve_rename_paths(path: str) -> tuple[str, str]:
        """Resolve old and new paths from a Git numstat rename representation."""
        if " => " not in path:
            return path, path
        if "{" in path and "}" in path:
            pre = path[: path.index("{")]
            post = path[path.index("}") + 1 :]
            inside = path[path.index("{") + 1 : path.index("}")]
            old_part, new_part = inside.split(" => ")
            old_path = f"{pre}{old_part}{post}"
            new_path = f"{pre}{new_part}{post}"
        else:
            old_path, new_path = path.split(" => ")
        while "//" in old_path:
            old_path = old_path.replace("//", "/")
        while "//" in new_path:
            new_path = new_path.replace("//", "/")
        return old_path, new_path

    @staticmethod
    def _parse_numstat_line(line: str) -> tuple[int, int, str, bool] | None:
        """Parse a single numstat output line.

        Returns:
            ``(additions, deletions, path, is_binary)`` or ``None`` if unparseable.
        """
        parts = line.split("\t")
        if len(parts) < 3:
            return None

        add_str, del_str, path = parts[0], parts[1], parts[-1]

        # Binary files show "-" for additions/deletions
        if add_str == "-" and del_str == "-":
            return 0, 0, path, True

        try:
            return int(add_str), int(del_str), path, False
        except ValueError:
            return None

    @staticmethod
    def _parse_name_status_line(
        line: str,
    ) -> tuple[FileStatus, str | None, str | None]:
        """Parse a name-status output line.

        Returns:
            ``(status, primary_path, rename_target)``
        """
        parts = line.split("\t")
        if not parts:
            return FileStatus.MODIFIED, None, None

        status_code = parts[0][0] if parts[0] else "M"
        status_mapping: dict[str, FileStatus] = {
            "A": FileStatus.ADDED,
            "D": FileStatus.DELETED,
            "M": FileStatus.MODIFIED,
            "R": FileStatus.RENAMED,
            "C": FileStatus.COPIED,
            "T": FileStatus.TYPE_CHANGED,
        }
        status = status_mapping.get(status_code, FileStatus.MODIFIED)

        if status in (FileStatus.RENAMED, FileStatus.COPIED) and len(parts) >= 3:
            return status, parts[1], parts[2]

        primary = parts[1] if len(parts) >= 2 else None
        return status, primary, None

    @staticmethod
    def _compute_statistics(changes: list[FileChange]) -> DiffStatistics:
        """Aggregate per-file changes into summary statistics."""
        return DiffStatistics(
            files_changed=len(changes),
            total_additions=sum(c.additions for c in changes),
            total_deletions=sum(c.deletions for c in changes),
        )

    @staticmethod
    def _split_lines(output: str) -> list[str]:
        """Split command output into non-empty lines."""
        return [line for line in output.strip().split("\n") if line]
