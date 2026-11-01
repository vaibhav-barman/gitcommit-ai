"""Unit tests for the diff analyst node."""

from __future__ import annotations

import json

import pytest

from git_commit_ai.agents.diff_analyst import make_diff_analyst
from git_commit_ai.agents.state import AgentState, CommitType, DiffAnalysis
from git_commit_ai.core.exceptions import LLMResponseError
from git_commit_ai.llm import FakeLLM, Role
from tests.fixtures.diffs import make_diff

REPLY = json.dumps(
    {
        "change_type": "feat",
        "summary": "Add retry support to the client",
        "intent": "Survive flaky networks",
        "is_breaking": False,
        "scope_hint": "client",
    }
)


def test_stores_digest_and_analysis() -> None:
    state = AgentState(diff_result=make_diff({"client.py": 4}))
    update = make_diff_analyst(FakeLLM([REPLY]), max_diff_chars=10_000)(state)
    analysis = update["analysis"]
    assert isinstance(analysis, DiffAnalysis)
    assert analysis.change_type is CommitType.FEAT
    assert analysis.scope_hint == "client"
    assert "client.py" in str(update["diff_digest"])


def test_prompt_contains_the_diff_and_respects_the_budget() -> None:
    llm = FakeLLM([REPLY])
    state = AgentState(diff_result=make_diff({"big.py": 600}))
    make_diff_analyst(llm, max_diff_chars=1_000)(state)
    system, user = llm.calls[0]
    assert system.role is Role.SYSTEM
    assert "big.py" in user.content
    assert "truncated" in user.content


def test_unknown_change_type_is_rejected_after_retry() -> None:
    bad = json.dumps({"change_type": "feature", "summary": "x"})
    llm = FakeLLM([bad, bad])
    state = AgentState(diff_result=make_diff({"a.py": 1}))
    with pytest.raises(LLMResponseError):
        make_diff_analyst(llm, max_diff_chars=10_000)(state)


def test_hint_reaches_the_analyst_prompt() -> None:
    llm = FakeLLM([REPLY])
    state = AgentState(diff_result=make_diff({"a.py": 1}), hint="speed up cold starts")
    make_diff_analyst(llm, max_diff_chars=10_000)(state)
    assert "speed up cold starts" in llm.calls[0][1].content


def test_prompt_is_just_the_digest_without_a_hint() -> None:
    llm = FakeLLM([REPLY])
    state = AgentState(diff_result=make_diff({"a.py": 1}))
    make_diff_analyst(llm, max_diff_chars=10_000)(state)
    assert llm.calls[0][1].content.startswith("Changed files:")
