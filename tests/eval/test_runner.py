"""The evaluation loop over a real temporary repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts.eval.offline import offline_llm
from scripts.eval.runner import evaluate

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import GitCommitAIError, RateLimitError
from tests.fixtures.git_repo import init_repo, run_git, scoped_history_repo
from tests.fixtures.scripted_llm import scripted_llm

if TYPE_CHECKING:
    from pathlib import Path

    from scripts.eval.replay import Replay
    from scripts.eval.report import CommitRecord

    from git_commit_ai.llm import LLMProvider


def _offline(replay: Replay) -> LLMProvider:
    return offline_llm(replay.diff)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return scoped_history_repo(tmp_path / "repo")


def test_offline_run_scores_every_requested_commit(repo: Path) -> None:
    report = evaluate(str(repo), 3, _offline, GitCommitAIConfig(), "offline")

    assert report.mode == "offline"
    assert (report.provider, report.model) == ("fake", "offline-heuristic")
    assert report.requested == 3
    assert report.max_iterations == 3
    assert len(report.commits) == 3
    assert [c.real_subject for c in report.commits] == [
        "fix(api): handle empty list",
        "docs(readme): add setup guide",
        "feat(cli): add entry point",
    ]
    assert report.aggregate.evaluated == 3
    assert report.aggregate.failed == 0
    assert all(c.outcome is not None and c.error is None for c in report.commits)


def test_asking_for_more_commits_than_exist_evaluates_them_all(repo: Path) -> None:
    report = evaluate(str(repo), 30, _offline, GitCommitAIConfig(), "offline")
    assert report.requested == 30
    assert len(report.commits) == 5
    assert report.aggregate.evaluated == 5


def test_the_aggregate_agrees_with_the_per_commit_records(repo: Path) -> None:
    report = evaluate(str(repo), 5, _offline, GitCommitAIConfig(), "offline")
    outcomes = [c.outcome for c in report.commits if c.outcome]

    metric = report.aggregate.metrics["convention_match"]
    assert metric.passed == sum(o.checks.convention_match for o in outcomes)
    assert metric.applicable == 5
    assert metric.rate == pytest.approx(metric.passed / 5)

    assert report.aggregate.mean_iterations == pytest.approx(
        sum(o.iterations for o in outcomes) / 5
    )
    assert report.aggregate.metrics["critic_clean_after"].passed == sum(
        o.after.errors == 0 for o in outcomes
    )


def test_reflection_is_visible_in_the_scores(repo: Path) -> None:
    report = evaluate(str(repo), 1, _offline, GitCommitAIConfig(), "offline")
    outcome = report.commits[0].outcome
    assert outcome is not None
    assert outcome.iterations == 2
    assert outcome.before.errors > 0
    assert outcome.after.errors == 0
    assert outcome.generated_message.startswith("fix(api): ")


def test_the_oldest_commit_has_no_history_and_is_still_scored(repo: Path) -> None:
    report = evaluate(str(repo), 5, _offline, GitCommitAIConfig(), "offline")
    root = report.commits[-1]
    assert root.real_subject == "feat(api): add users endpoint"
    assert root.outcome is not None


def test_a_commit_without_file_changes_is_recorded_and_skipped(repo: Path) -> None:
    run_git(repo, "commit", "--allow-empty", "-m", "chore: empty")

    report = evaluate(str(repo), 2, _offline, GitCommitAIConfig(), "offline")

    empty = report.commits[0]
    assert empty.real_subject == "chore: empty"
    assert empty.outcome is None
    assert empty.error == "the commit has no file changes"
    assert report.aggregate.failed == 1
    assert report.aggregate.evaluated == 1


def test_a_provider_error_fails_one_commit_and_the_run_goes_on(repo: Path) -> None:
    calls = {"n": 0}

    def make_llm(replay: Replay) -> LLMProvider:
        calls["n"] += 1
        if calls["n"] == 1:
            return scripted_llm([], generator_errors={1: RateLimitError("openai")})
        return offline_llm(replay.diff)

    report = evaluate(str(repo), 3, make_llm, GitCommitAIConfig(), "offline")

    first = report.commits[0]
    assert first.outcome is None
    assert first.error is not None
    assert "rate limit" in first.error
    assert report.aggregate.failed == 1
    assert report.aggregate.evaluated == 2


def test_a_provider_error_after_the_first_draft_is_scored_as_interrupted(repo: Path) -> None:
    def make_llm(_: Replay) -> LLMProvider:
        return scripted_llm(
            ["fix(api): handle empty list"], verdict_errors={1: RateLimitError("openai")}
        )

    report = evaluate(str(repo), 2, make_llm, GitCommitAIConfig(), "live")

    assert report.aggregate.interrupted == 2
    assert all(c.outcome is not None and c.outcome.interrupted for c in report.commits)
    assert report.aggregate.evaluated == 2


def test_progress_is_reported_after_each_commit(repo: Path) -> None:
    seen: list[tuple[int, int, str]] = []

    def on_progress(done: int, total: int, record: CommitRecord) -> None:
        seen.append((done, total, record.real_subject))

    evaluate(str(repo), 2, _offline, GitCommitAIConfig(), "offline", on_progress)

    assert seen == [
        (1, 2, "fix(api): handle empty list"),
        (2, 2, "docs(readme): add setup guide"),
    ]


def test_the_iteration_cap_is_honoured(repo: Path) -> None:
    config = GitCommitAIConfig(max_iterations=1)
    report = evaluate(str(repo), 1, _offline, config, "offline")
    outcome = report.commits[0].outcome
    assert report.max_iterations == 1
    assert outcome is not None
    assert outcome.iterations == 1
    assert outcome.approved is False
    assert outcome.after.errors > 0


def test_the_repository_is_not_modified(repo: Path) -> None:
    before = (run_git(repo, "status", "--porcelain").stdout, run_git(repo, "rev-parse", "HEAD"))
    evaluate(str(repo), 5, _offline, GitCommitAIConfig(), "offline")
    after = (run_git(repo, "status", "--porcelain").stdout, run_git(repo, "rev-parse", "HEAD"))
    assert before[0] == after[0]
    assert before[1].stdout == after[1].stdout


def test_an_empty_repository_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(GitCommitAIError, match="no non-merge commits"):
        evaluate(str(init_repo(tmp_path / "empty")), 5, _offline, GitCommitAIConfig(), "offline")
