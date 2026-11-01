"""Convention and style rules shared by the generator and the critic."""

from __future__ import annotations

from git_commit_ai.core.schemas import CommitConvention, DiffResult, RepositoryContext

HARD_SUBJECT_LIMIT = 72
BODY_WIDTH = 72
MAX_BODY_LINES = 12
BODY_COMMON_FREQUENCY = 0.5
LARGE_DIFF_FILES = 6
LARGE_DIFF_CHANGED_LINES = 200


def effective_convention(context: RepositoryContext) -> CommitConvention:
    """Return the convention drafts must follow.

    A repository with no usable history has no convention to match, so
    Conventional Commits is the default.
    """
    detected = context.convention_result.detected_convention
    if detected is CommitConvention.UNKNOWN:
        return CommitConvention.CONVENTIONAL
    return detected


def diff_is_large(diff_result: DiffResult) -> bool:
    """Whether a diff is big enough that the reader needs a body to follow it."""
    stats = diff_result.statistics
    changed_lines = stats.total_additions + stats.total_deletions
    return stats.files_changed >= LARGE_DIFF_FILES or changed_lines >= LARGE_DIFF_CHANGED_LINES


def body_expected(context: RepositoryContext, diff_result: DiffResult) -> bool:
    """Whether the message should carry a body.

    Yes when the repository usually writes bodies, or when the diff is large;
    otherwise the message is a subject line only. The generator and the critic
    both use this, so they agree on what is expected.
    """
    common = context.style_profile.body_usage_frequency >= BODY_COMMON_FREQUENCY
    return common or diff_is_large(diff_result)
