"""Repository snapshot capture and comparison for staleness detection."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.schemas import RepositorySnapshot

if TYPE_CHECKING:
    from git_commit_ai.git.repository import RepositoryInspector


class SnapshotManager:
    """Captures and compares staged-tree snapshots.

    A snapshot records the Git tree hash of the current index (staged area)
    along with the list of staged file paths.  Comparing two snapshots
    reveals whether staged content has changed between captures — for
    example, while an AI model is generating a commit message.

    Args:
        inspector: A ``RepositoryInspector`` bound to the target repository.
        config: Application configuration.
    """

    def __init__(
        self,
        inspector: RepositoryInspector,
        config: GitCommitAIConfig | None = None,
    ) -> None:
        self._inspector = inspector
        self._config = config or GitCommitAIConfig()

    def capture(self) -> RepositorySnapshot:
        """Capture the current staged-tree identity.

        Returns:
            A ``RepositorySnapshot`` containing the tree hash and staged files.

        Note:
            In a repository with no staged changes the tree hash will be the
            hash of an empty tree, and ``staged_files`` will be empty.
        """
        tree_hash = self._get_staged_tree_hash()
        staged_files = sorted(self._inspector.get_staged_files())
        return RepositorySnapshot(
            tree_hash=tree_hash,
            staged_files=staged_files,
            timestamp=datetime.now(),
        )

    @staticmethod
    def compare(before: RepositorySnapshot, after: RepositorySnapshot) -> bool:
        """Check whether two snapshots represent the same staged state.

        Args:
            before: The earlier snapshot.
            after: The later snapshot.

        Returns:
            ``True`` if the staged state is **unchanged** between snapshots.
        """
        return before.matches(after)

    def has_changed_since(self, snapshot: RepositorySnapshot) -> bool:
        """Return ``True`` if the staged tree has changed since *snapshot*.

        This is a convenience wrapper that captures a fresh snapshot and
        compares it against the provided one.
        """
        current = self.capture()
        return not self.compare(snapshot, current)

    def _get_staged_tree_hash(self) -> str:
        """Compute the tree hash of the current index.

        Uses ``git write-tree`` which writes the index as a tree object
        and returns its hash, without creating a commit.
        """
        result = self._inspector._run_git(
            ["write-tree"],
            check=False,
        )
        if result.returncode != 0:
            # Fallback: hash of the empty tree
            return "4b825dc642cb6eb9a060e54bf899d69f82cf7149"
        return result.stdout.strip()
