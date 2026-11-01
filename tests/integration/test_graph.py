"""Run the whole graph against a real temporary Git repository."""

from __future__ import annotations

from pathlib import Path

from git_commit_ai.agents.graph import CommitMessageGraph, GenerationResult
from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.schemas import CommitConvention
from git_commit_ai.git.diff import DiffExtractor
from git_commit_ai.git.history import CommitHistoryExtractor
from git_commit_ai.git.repository import RepositoryInspector
from tests.fixtures.git_repo import init_repo, scoped_history_repo, stage_file
from tests.fixtures.scripted_llm import scripted_llm


def _stage_new_file(repo: Path) -> None:
    stage_file(repo, "src/api/orders.py", "def orders(): ...\n")


def _generate(repo: Path, llm_drafts: list[str]) -> GenerationResult:
    config = GitCommitAIConfig()
    inspector = RepositoryInspector(repo_path=str(repo), config=config)
    diff = DiffExtractor(inspector=inspector, config=config).extract()
    history = CommitHistoryExtractor(inspector=inspector, config=config).get_history()
    return CommitMessageGraph(scripted_llm(llm_drafts), config).run(diff, history)


def test_generates_a_message_in_the_repository_convention(tmp_path: Path) -> None:
    repo = scoped_history_repo(tmp_path / "repo")
    _stage_new_file(repo)

    result = _generate(repo, ["feat(api): add orders endpoint"])

    assert result.approved
    assert result.message == "feat(api): add orders endpoint"
    assert result.state.context is not None
    assert (
        result.state.context.convention_result.detected_convention
        is CommitConvention.SCOPED_CONVENTIONAL
    )
    assert any("api" in ex.subject for ex in result.state.context.historical_examples)


def test_critic_corrects_a_draft_that_ignores_the_convention(tmp_path: Path) -> None:
    repo = scoped_history_repo(tmp_path / "repo")
    _stage_new_file(repo)

    result = _generate(repo, ["Add orders endpoint", "feat(api): add orders endpoint"])

    assert result.iterations == 2
    assert result.message == "feat(api): add orders endpoint"
    first = result.state.drafts[0].critique
    assert first is not None
    assert {v.rule for v in first.violations} >= {"format"}


def test_works_in_a_repository_with_no_commits(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "fresh")
    stage_file(repo, "README.md", "# Fresh\n")

    result = _generate(repo, ["docs: add readme"])

    assert result.message == "docs: add readme"
    assert result.state.context is not None
    assert result.state.context.convention_result.detected_convention is CommitConvention.UNKNOWN
