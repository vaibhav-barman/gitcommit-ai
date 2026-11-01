"""Unit tests for the critic node (rules first, LLM for faithfulness only)."""

from __future__ import annotations

import json

import pytest

from git_commit_ai.agents.critic import make_critic
from git_commit_ai.agents.state import AgentState, Draft, Severity
from git_commit_ai.core.exceptions import (
    AgentStateError,
    LLMError,
    LLMResponseError,
    RateLimitError,
    TransientLLMError,
)
from git_commit_ai.llm import FakeLLM
from tests.fixtures.agent_inputs import SCOPED_HISTORY, make_state
from tests.fixtures.scripted_llm import scripted_llm

GOOD = "feat(api): add page size option"
FAITHFUL = json.dumps({"faithful": True, "problems": []})


def _with_drafts(state: AgentState, *messages: str) -> AgentState:
    drafts = [Draft(message=m, iteration=i) for i, m in enumerate(messages, start=1)]
    return state.model_copy(update={"drafts": drafts, "iteration": len(drafts)})


def _review(llm: FakeLLM, state: AgentState) -> list[Draft]:
    drafts = make_critic(llm)(state)["drafts"]
    assert isinstance(drafts, list)
    return drafts


def test_clean_draft_is_approved_after_one_faithfulness_call() -> None:
    llm = FakeLLM([FAITHFUL])
    drafts = _review(llm, _with_drafts(make_state(SCOPED_HISTORY), GOOD))
    critique = drafts[-1].critique
    assert critique is not None
    assert critique.approved
    assert critique.violations == []
    assert len(llm.calls) == 1


def test_rule_errors_skip_the_llm_entirely() -> None:
    llm = FakeLLM([])
    drafts = _review(llm, _with_drafts(make_state(SCOPED_HISTORY), "Added page size"))
    critique = drafts[-1].critique
    assert critique is not None
    assert not critique.approved
    assert {v.rule for v in critique.violations} >= {"format"}
    assert llm.calls == []


def test_unfaithful_draft_gets_one_violation_per_problem() -> None:
    reply = json.dumps({"faithful": False, "problems": ["Mentions caching", "Misses retries"]})
    drafts = _review(FakeLLM([reply]), _with_drafts(make_state(SCOPED_HISTORY), GOOD))
    critique = drafts[-1].critique
    assert critique is not None
    assert not critique.approved
    assert [v.message for v in critique.violations] == ["Mentions caching", "Misses retries"]
    assert {v.rule for v in critique.violations} == {"faithfulness"}


def test_unfaithful_without_reasons_still_blocks_approval() -> None:
    reply = json.dumps({"faithful": False})
    drafts = _review(FakeLLM([reply]), _with_drafts(make_state(SCOPED_HISTORY), GOOD))
    critique = drafts[-1].critique
    assert critique is not None
    assert not critique.approved
    assert "does not accurately describe" in critique.violations[0].message


def test_unparsable_verdict_warns_but_does_not_block() -> None:
    drafts = _review(FakeLLM(["no", "still no"]), _with_drafts(make_state(SCOPED_HISTORY), GOOD))
    critique = drafts[-1].critique
    assert critique is not None
    assert critique.approved
    assert [(v.rule, v.severity) for v in critique.violations] == [
        ("faithfulness-unchecked", Severity.WARNING)
    ]


def test_warnings_keep_the_faithfulness_check_running() -> None:
    llm = FakeLLM([FAITHFUL])
    drafts = _review(
        llm, _with_drafts(make_state(SCOPED_HISTORY), "fix(api): add page size option")
    )
    critique = drafts[-1].critique
    assert critique is not None
    assert critique.approved
    assert {v.rule for v in critique.violations} == {"type-mismatch"}
    assert len(llm.calls) == 1


def test_prompt_contains_the_diff_and_the_message() -> None:
    llm = FakeLLM([FAITHFUL])
    _review(llm, _with_drafts(make_state(SCOPED_HISTORY), GOOD))
    user = llm.calls[0][1].content
    assert "src/api/client.py" in user
    assert GOOD in user


def test_truncated_digest_is_disclosed_to_the_judge() -> None:
    llm = FakeLLM([FAITHFUL, FAITHFUL])
    big = make_state(SCOPED_HISTORY, files={"src/api/big.py": 800}, max_diff_chars=1_000)
    _review(llm, _with_drafts(big, GOOD))
    _review(llm, _with_drafts(make_state(SCOPED_HISTORY), GOOD))
    assert "truncated for size" in llm.calls[0][0].content
    assert "truncated for size" not in llm.calls[1][0].content


def test_only_the_latest_draft_is_reviewed() -> None:
    drafts = _review(FakeLLM([FAITHFUL]), _with_drafts(make_state(SCOPED_HISTORY), "old", GOOD))
    assert drafts[0].critique is None
    assert drafts[1].critique is not None


def test_requires_a_draft() -> None:
    with pytest.raises(AgentStateError, match="drafts"):
        make_critic(FakeLLM([]))(make_state(SCOPED_HISTORY))


@pytest.mark.parametrize(
    "error",
    [
        RateLimitError("openai", retry_after=30),
        TransientLLMError("openai", "request timed out"),
        LLMResponseError("Provider 'openai' returned an empty response"),
        LLMError("rejected the API key"),
    ],
    ids=["rate-limit", "timeout", "empty-response", "other"],
)
def test_provider_failure_keeps_the_draft_and_records_an_interruption(error: LLMError) -> None:
    llm = scripted_llm([], verdict_errors={1: error})
    update = make_critic(llm)(_with_drafts(make_state(SCOPED_HISTORY), GOOD))

    drafts = update["drafts"]
    assert isinstance(drafts, list)
    critique = drafts[-1].critique
    assert critique is not None
    assert critique.approved
    assert [(v.rule, v.severity) for v in critique.violations] == [
        ("faithfulness-unchecked", Severity.WARNING)
    ]
    assert str(error) in critique.violations[0].message
    assert "could not be checked against the diff" in str(update["interruption"])


def test_no_interruption_when_the_check_succeeds() -> None:
    update = make_critic(FakeLLM([FAITHFUL]))(_with_drafts(make_state(SCOPED_HISTORY), GOOD))
    assert "interruption" not in update
