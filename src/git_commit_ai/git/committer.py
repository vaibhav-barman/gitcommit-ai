"""Creating a commit from a generated message, guarded against stale staging."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_commit_ai.core.exceptions import StaleStagingError

if TYPE_CHECKING:
    from git_commit_ai.core.schemas import RepositorySnapshot
    from git_commit_ai.git.repository import RepositoryInspector
    from git_commit_ai.git.snapshots import SnapshotManager


class GitCommitter:
    """Commits the index, but only if it is still the one that was analyzed.

    This is the only component that writes to the repository, and it never
    stages or unstages anything: it commits exactly what is already staged.
    Git hooks run as usual unless ``no_verify`` is set.

    Args:
        inspector: A ``RepositoryInspector`` bound to the target repository.
        snapshots: Snapshot manager used for the staleness check.
    """

    def __init__(self, inspector: RepositoryInspector, snapshots: SnapshotManager) -> None:
        self._inspector = inspector
        self._snapshots = snapshots

    def commit(
        self,
        message: str,
        expected: RepositorySnapshot,
        *,
        amend: bool = False,
        no_verify: bool = False,
    ) -> str:
        """Commit the staged changes with ``message``.

        Args:
            message: Full commit message (subject, optional body and footers).
            expected: Snapshot captured before the message was generated. It
                includes ``HEAD``, so amending a commit that moved is refused too.
            amend: Replace ``HEAD`` instead of adding a commit.
            no_verify: Skip the pre-commit and commit-msg hooks.

        Returns:
            The abbreviated hash of the new commit.

        Raises:
            StaleStagingError: If the staged tree no longer matches ``expected``.
            GitCommandError: If Git or a commit hook rejects the commit.
        """
        if self._snapshots.has_changed_since(expected):
            raise StaleStagingError()
        args = ["commit", "-m", message]
        if amend:
            args.append("--amend")
        if no_verify:
            args.append("--no-verify")
        self._inspector._run_git(args)
        head = self._inspector._run_git(["rev-parse", "--short", "HEAD"])
        return head.stdout.strip()
