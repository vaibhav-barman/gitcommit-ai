"""Unit tests for the context retrieval node."""

from __future__ import annotations

from unittest.mock import MagicMock

from git_commit_ai.agents.context_retrieval import make_context_retrieval
from git_commit_ai.agents.state import AgentState
from git_commit_ai.context.engine import RepositoryContextEngine
from git_commit_ai.core.schemas import CommitConvention, HistoricalCommit, RepositoryContext
from tests.fixtures.diffs import make_diff


def _history() -> list[HistoricalCommit]:
    subjects = [
        "feat(api): add pagination",
        "fix(api): handle empty page",
        "docs(readme): describe setup",
        "feat(cli): add verbose flag",
        "fix(cli): exit non-zero on error",
        "test(api): cover pagination",
    ]
    return [HistoricalCommit(hash=f"{i:040d}", subject=s) for i, s in enumerate(subjects)]


def test_builds_context_from_diff_and_history() -> None:
    state = AgentState(diff_result=make_diff({"src/api/page.py": 5}), history=_history())
    update = make_context_retrieval()(state)
    context = update["context"]
    assert isinstance(context, RepositoryContext)
    assert context.convention_result.detected_convention is CommitConvention.SCOPED_CONVENTIONAL
    assert context.analyzed_commit_count == 6
    assert any("api" in ex.subject for ex in context.historical_examples)


def test_empty_history_yields_unknown_convention() -> None:
    state = AgentState(diff_result=make_diff({"a.py": 1}))
    context = make_context_retrieval()(state)["context"]
    assert isinstance(context, RepositoryContext)
    assert context.convention_result.detected_convention is CommitConvention.UNKNOWN
    assert context.historical_examples == []


def test_delegates_to_the_injected_engine() -> None:
    diff = make_diff({"a.py": 1})
    engine = MagicMock(spec=RepositoryContextEngine)
    engine.build_context.return_value = RepositoryContextEngine().build_context(diff, [])
    history = _history()

    update = make_context_retrieval(engine)(AgentState(diff_result=diff, history=history))

    engine.build_context.assert_called_once_with(diff, history)
    assert update["context"] is engine.build_context.return_value
