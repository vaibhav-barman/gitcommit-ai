"""Run the agent graph over every replayed commit and collect the scores."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from git_commit_ai.agents.graph import CommitMessageGraph
from git_commit_ai.core.exceptions import GitCommitAIError
from scripts.eval.metrics import score
from scripts.eval.replay import replay_commits
from scripts.eval.report import CommitRecord, Report, aggregate

if TYPE_CHECKING:
    from collections.abc import Callable

    from git_commit_ai.core.config import GitCommitAIConfig
    from git_commit_ai.llm import LLMProvider
    from scripts.eval.replay import Replay

    LLMFactory = Callable[[Replay], LLMProvider]
    Progress = Callable[[int, int, CommitRecord], None]


def evaluate(
    repo_path: str,
    count: int,
    make_llm: LLMFactory,
    config: GitCommitAIConfig,
    mode: str,
    on_progress: Progress | None = None,
) -> Report:
    """Replay the last ``count`` non-merge commits and score what the graph writes.

    A commit the graph cannot handle (no file changes, or a provider error before
    any draft exists) is recorded with its error and left out of the aggregate;
    it never stops the run.

    Args:
        repo_path: Any directory inside the repository to evaluate.
        count: How many of the newest non-merge commits to replay.
        make_llm: Builds the provider for one replay; offline runs tailor it to the diff.
        config: Supplies the iteration cap and history size.
        mode: ``"offline"`` or ``"live"``, recorded in the report.
        on_progress: Called after each commit with ``(done, total, record)``.
    """
    replays = replay_commits(repo_path, count, config)
    records: list[CommitRecord] = []
    provider, model = config.llm_provider.value, config.llm_model or ""
    named = False

    for position, replay in enumerate(replays, start=1):
        if not replay.diff.file_changes:
            record = _failed(replay, "the commit has no file changes")
        else:
            llm = make_llm(replay)
            if not named:
                provider, model, named = llm.name, llm.model, True
            record = _run_one(replay, llm, config)
        records.append(record)
        if on_progress:
            on_progress(position, len(replays), record)

    return Report(
        generated_at=datetime.now(UTC),
        repository=repo_path,
        mode=mode,
        provider=provider,
        model=model,
        requested=count,
        max_iterations=config.max_iterations,
        commits=records,
        aggregate=aggregate(records),
    )


def _run_one(replay: Replay, llm: LLMProvider, config: GitCommitAIConfig) -> CommitRecord:
    try:
        result = CommitMessageGraph(llm, config).run(replay.diff, replay.history)
    except GitCommitAIError as exc:
        return _failed(replay, str(exc))
    return score(replay.commit, result)


def _failed(replay: Replay, reason: str) -> CommitRecord:
    return CommitRecord(hash=replay.commit.hash, real_subject=replay.commit.subject, error=reason)
