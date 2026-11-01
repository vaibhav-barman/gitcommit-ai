"""Convention and style rules shared by the generator and the critic."""

from __future__ import annotations

from git_commit_ai.core.schemas import CommitConvention, RepositoryContext

HARD_SUBJECT_LIMIT = 72


def effective_convention(context: RepositoryContext) -> CommitConvention:
    """Return the convention drafts must follow.

    A repository with no usable history has no convention to match, so
    Conventional Commits is the default.
    """
    detected = context.convention_result.detected_convention
    if detected is CommitConvention.UNKNOWN:
        return CommitConvention.CONVENTIONAL
    return detected
