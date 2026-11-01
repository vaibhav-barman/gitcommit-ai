"""Replay a repository's own commits: each commit's diff, and the history before it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import GitCommitAIError
from git_commit_ai.core.schemas import DiffMode
from git_commit_ai.git.diff import DiffExtractor
from git_commit_ai.git.history import CommitHistoryExtractor
from git_commit_ai.git.repository import RepositoryInspector

if TYPE_CHECKING:
    from git_commit_ai.core.schemas import DiffResult, HistoricalCommit


@dataclass(frozen=True)
class Replay:
    """Everything the agent graph is given for one historical commit.

    Attributes:
        commit: The real commit, whose message is the reference.
        diff: The commit's change against its parent (the empty tree for a root commit).
        history: Non-merge commits older than ``commit``, newest first.
    """

    commit: HistoricalCommit
    diff: DiffResult
    history: list[HistoricalCommit]


class CommitDiffExtractor(DiffExtractor):
    """Extracts the diff a commit introduced, straight from the object store.

    Nothing is checked out and the index is not touched: ``git diff <parent> <commit>``
    reads the two trees directly. The result is labelled ``DiffMode.AMEND``, which
    already means "this commit's change against its parent".
    """

    def __init__(
        self,
        inspector: RepositoryInspector,
        commit: str,
        config: GitCommitAIConfig | None = None,
    ) -> None:
        super().__init__(inspector, config)
        self._commit = commit

    def extract_commit(self) -> DiffResult:
        """Return the diff of the commit against its first parent."""
        return self.extract(DiffMode.AMEND)

    def _diff_args(self, mode: DiffMode) -> list[str]:
        return ["diff", self._base(), self._commit]

    def _base(self) -> str:
        parent = self._inspector._run_git(
            ["rev-parse", "--verify", "--quiet", f"{self._commit}^"], check=False
        )
        return (
            parent.stdout.strip() if parent.returncode == 0 else self._inspector.empty_tree_hash()
        )


def replay_commits(
    repo_path: str,
    count: int,
    config: GitCommitAIConfig | None = None,
) -> list[Replay]:
    """Prepare the last ``count`` non-merge commits of a repository, newest first.

    For each one, the history is limited to commits older than the commit itself,
    so the generator sees only what its real author could have seen.

    Raises:
        NotAGitRepositoryError: If ``repo_path`` is not inside a Git repository.
        GitCommitAIError: If the repository has no commits to evaluate.
    """
    config = config or GitCommitAIConfig()
    inspector = RepositoryInspector(repo_path=repo_path, config=config)
    inspector.get_repository_root()
    history = CommitHistoryExtractor(inspector=inspector, config=config)

    targets = history.get_history(count=count)
    if not targets:
        raise GitCommitAIError("The repository has no non-merge commits to evaluate.")

    return [
        Replay(
            commit=target,
            diff=CommitDiffExtractor(inspector, target.hash, config).extract_commit(),
            history=history.get_history(revision=target.hash, exclude_head=True),
        )
        for target in targets
    ]
