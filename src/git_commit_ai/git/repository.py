"""Repository inspection: detect, introspect, and validate Git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import (
    GitCommandError,
    GitNotFoundError,
    GitTimeoutError,
    NotAGitRepositoryError,
)
from git_commit_ai.core.schemas import RepositoryInfo, RepositoryOperation

# Checked in order; the first marker present names the operation.
_OPERATION_MARKERS: tuple[tuple[str, RepositoryOperation], ...] = (
    ("rebase-merge", RepositoryOperation.REBASE),
    ("rebase-apply", RepositoryOperation.REBASE),
    ("MERGE_HEAD", RepositoryOperation.MERGE),
    ("CHERRY_PICK_HEAD", RepositoryOperation.CHERRY_PICK),
    ("REVERT_HEAD", RepositoryOperation.REVERT),
)


class RepositoryInspector:
    """Inspects a Git repository's state using subprocess-based Git commands.

    All Git interactions use ``subprocess.run`` with argument lists (never
    ``shell=True``), explicit timeouts, and captured stdout/stderr. Every
    failure is surfaced through application-specific exceptions.

    Args:
        repo_path: Path to a directory inside the target repository.
        config: Application configuration (provides timeout defaults).
    """

    def __init__(
        self,
        repo_path: str = ".",
        config: GitCommitAIConfig | None = None,
    ) -> None:
        self._repo_path = repo_path
        self._config = config or GitCommitAIConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inspect(self) -> RepositoryInfo:
        """Collect a full snapshot of repository metadata.

        Returns:
            A populated ``RepositoryInfo`` model.

        Raises:
            GitNotFoundError: If Git is not installed.
            NotAGitRepositoryError: If the path is not inside a Git repo.
        """
        self._ensure_git_installed()
        root = self.get_repository_root()
        is_empty = self._is_empty_repository()
        branch, is_detached = self._get_branch_info()

        return RepositoryInfo(
            root_path=root,
            branch=branch,
            is_detached_head=is_detached,
            is_empty=is_empty,
            operation=self.get_operation_in_progress(),
            staged_files=self.get_staged_files(),
            unstaged_files=self.get_unstaged_files(),
            untracked_files=self.get_untracked_files(),
        )

    def get_repository_root(self) -> str:
        """Resolve the absolute path to the repository root.

        Raises:
            NotAGitRepositoryError: If the path is not inside a Git repo.
        """
        result = self._run_git(
            ["rev-parse", "--show-toplevel"],
            check=False,
        )
        if result.returncode != 0:
            raise NotAGitRepositoryError(self._repo_path)
        return result.stdout.strip()

    def get_branch_name(self) -> str | None:
        """Return the current branch name, or ``None`` if HEAD is detached."""
        branch, _ = self._get_branch_info()
        return branch

    def is_detached_head(self) -> bool:
        """Return ``True`` if HEAD is detached (not on any branch)."""
        _, detached = self._get_branch_info()
        return detached

    def get_operation_in_progress(self) -> RepositoryOperation:
        """Detect a merge, rebase, cherry-pick or revert waiting to be finished.

        Git records these as files inside the git directory; ``--git-path``
        resolves them correctly for linked worktrees too. A rebase wins over
        the others because a conflicted rebase step also leaves
        ``CHERRY_PICK_HEAD`` behind.
        """
        names = [name for name, _ in _OPERATION_MARKERS]
        args = ["rev-parse"]
        for name in names:
            args += ["--git-path", name]
        paths = self._split_lines(self._run_git(args).stdout)
        base = Path(self._repo_path)
        for (_, operation), relative in zip(_OPERATION_MARKERS, paths, strict=True):
            if (base / relative).exists():
                return operation
        return RepositoryOperation.NONE

    def empty_tree_hash(self) -> str:
        """Return the hash of the empty tree in this repository's object format.

        Asked of Git rather than hard-coded, because the value differs between
        SHA-1 and SHA-256 repositories. Nothing is written to the object store.
        """
        return self._run_git(["hash-object", "-t", "tree", "/dev/null"]).stdout.strip()

    def get_staged_files(self) -> list[str]:
        """Return a list of file paths staged for the next commit."""
        if self._is_empty_repository():
            # In an empty repo, all tracked files are "staged" (initial add)
            result = self._run_git(
                ["diff", "--cached", "--name-only", "--diff-filter=ACDMRT"],
                check=False,
            )
            if result.returncode != 0:
                return []
            return self._split_lines(result.stdout)

        result = self._run_git(
            ["diff", "--cached", "--name-only", "--diff-filter=ACDMRT"],
        )
        return self._split_lines(result.stdout)

    def get_unstaged_files(self) -> list[str]:
        """Return a list of modified but unstaged file paths."""
        if self._is_empty_repository():
            return []
        result = self._run_git(["diff", "--name-only"])
        return self._split_lines(result.stdout)

    def get_untracked_files(self) -> list[str]:
        """Return a list of untracked file paths."""
        result = self._run_git(
            ["ls-files", "--others", "--exclude-standard"],
        )
        return self._split_lines(result.stdout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_branch_info(self) -> tuple[str | None, bool]:
        """Return ``(branch_name, is_detached)``."""
        result = self._run_git(
            ["symbolic-ref", "--short", "HEAD"],
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip(), False
        # Detached HEAD — symbolic-ref fails
        return None, True

    def _is_empty_repository(self) -> bool:
        """Return ``True`` if the repository has no commits."""
        result = self._run_git(
            ["rev-parse", "--verify", "HEAD"],
            check=False,
        )
        return result.returncode != 0

    def _ensure_git_installed(self) -> None:
        """Verify that ``git`` is available on the system PATH."""
        try:
            subprocess.run(
                ["git", "--version"],
                capture_output=True,
                text=True,
                timeout=self._config.git_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GitNotFoundError() from exc

    def _run_git(
        self,
        args: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a Git command safely via subprocess.

        Args:
            args: Git sub-command and arguments (without the leading ``git``).
            check: If ``True``, raise ``GitCommandError`` on non-zero exit.

        Returns:
            The completed process result.

        Raises:
            GitCommandError: If the command fails and *check* is ``True``.
            GitTimeoutError: If the command exceeds the configured timeout.
        """
        cmd = ["git", "-C", self._repo_path, *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._config.git_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitTimeoutError(cmd, self._config.git_timeout_seconds) from exc

        if check and result.returncode != 0:
            raise GitCommandError(cmd, result.returncode, result.stderr)
        return result

    @staticmethod
    def _split_lines(output: str) -> list[str]:
        """Split output into non-empty lines."""
        return [line for line in output.strip().split("\n") if line]
