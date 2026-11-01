"""Historical commit extraction with robust multiline body parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.schemas import HistoricalCommit

if TYPE_CHECKING:
    from git_commit_ai.git.repository import RepositoryInspector

# Delimiters chosen to be extremely unlikely in real commit messages.
_COMMIT_DELIM = "---GCAI_COMMIT_DELIM---"
_FIELD_DELIM = "---GCAI_FIELD_DELIM---"


class CommitHistoryExtractor:
    """Extracts historical commit metadata from a Git repository.

    Uses explicit delimiters in the ``--format`` string to robustly parse
    multiline commit bodies and messages containing special characters.

    Args:
        inspector: A ``RepositoryInspector`` bound to the target repository.
        config: Application configuration (provides default commit count).
    """

    def __init__(
        self,
        inspector: RepositoryInspector,
        config: GitCommitAIConfig | None = None,
    ) -> None:
        self._inspector = inspector
        self._config = config or GitCommitAIConfig()

    def get_history(
        self,
        count: int | None = None,
        exclude_head: bool = False,
        revision: str = "HEAD",
    ) -> list[HistoricalCommit]:
        """Retrieve recent non-merge commits.

        Args:
            count: Maximum number of commits to retrieve.
                   Defaults to ``config.history_commit_count``.
            exclude_head: Start from the parent of ``revision`` (``HEAD`` unless
                   given), so the commit about to be amended is not used as an
                   example of itself.
            revision: Where the walk starts, inclusive. Pass a commit's parent to
                   see only what that commit's author could have seen.

        Returns:
            A list of ``HistoricalCommit`` models, most-recent first.
            Returns an empty list if the repository has no commits.
        """
        if self._inspector._is_empty_repository():
            return []

        effective_count = count or self._config.history_commit_count
        start = f"{revision}^" if exclude_head else revision
        if not self._resolves(start):
            return []

        # Build a format string with explicit delimiters:
        # <hash>FIELD<subject>FIELD<body>COMMIT
        fmt = f"%H{_FIELD_DELIM}%s{_FIELD_DELIM}%b{_COMMIT_DELIM}"

        result = self._inspector._run_git(
            [
                "log",
                f"-{effective_count}",
                "--no-merges",
                f"--format={fmt}",
                start,
            ],
            check=False,
        )

        if result.returncode != 0 or not result.stdout.strip():
            return []

        return self._parse_log_output(result.stdout)

    def _resolves(self, revision: str) -> bool:
        result = self._inspector._run_git(
            ["rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"], check=False
        )
        return result.returncode == 0

    def _parse_log_output(self, raw: str) -> list[HistoricalCommit]:
        """Parse raw ``git log`` output into structured commits.

        Splitting on the commit delimiter first, then on the field delimiter
        within each commit block, ensures that multiline bodies and special
        characters are handled correctly.
        """
        commits: list[HistoricalCommit] = []
        blocks = raw.split(_COMMIT_DELIM)

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            parts = block.split(_FIELD_DELIM)
            if len(parts) < 2:
                continue

            commit_hash = parts[0].strip()
            subject = parts[1].strip()
            body = parts[2].strip() if len(parts) >= 3 else ""

            if not commit_hash:
                continue

            commits.append(
                HistoricalCommit(
                    hash=commit_hash,
                    subject=subject,
                    body=body,
                )
            )

        return commits
