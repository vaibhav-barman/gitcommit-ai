"""Unit tests for loop termination, draft selection, and the compiled graph."""

from __future__ import annotations

import json

import pytest
from langgraph.graph import END

from git_commit_ai.agents.graph import (
    CommitMessageGraph,
    GenerationResult,
    route_after_critic,
    select_best_draft,
)
from git_commit_ai.agents.state import AgentState, Critique, Draft, Severity, Violation
from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import AgentStateError, LLMResponseError
from git_commit_ai.core.schemas import HistoricalCommit
from git_commit_ai.llm import FakeLLM
from tests.fixtures.agent_inputs import SCOPED_HISTORY, make_state
from tests.fixtures.diffs import make_diff
from tests.fixtures.scripted_llm import FAITHFUL, scripted_llm

GOOD = "feat(api): add page size option"
HISTORY = [
    HistoricalCommit(hash=f"{i:040d}", subject=subject) for i, subject in enumerate(SCOPED_HISTORY)
]


def _violation(severity: Severity) -> Violation:
    return Violation(rule="r", message="m", severity=severity)


def _draft(iteration: int, *severities: Severity, reviewed: bool = True) -> Draft:
    critique = Critique(violations=[_violation(s) for s in severities]) if reviewed else None
    return Draft(message=f"draft {iteration}", iteration=iteration, critique=critique)


def _state_with(drafts: list[Draft], iteration: int | None = None) -> AgentState:
    return make_state(SCOPED_HISTORY).model_copy(
        update={"drafts": drafts, "iteration": iteration or len(drafts)}
    )


class TestRouting:
    def test_approved_draft_ends_the_loop(self) -> None:
        assert route_after_critic(_state_with([_draft(1)]), max_iterations=3) == END

    def test_warnings_alone_still_count_as_approved(self) -> None:
        state = _state_with([_draft(1, Severity.WARNING)])
        assert route_after_critic(state, max_iterations=3) == END

    def test_rejected_draft_under_the_cap_regenerates(self) -> None:
        state = _state_with([_draft(1, Severity.ERROR)])
        assert route_after_critic(state, max_iterations=3) == "generator"

    def test_rejected_draft_at_the_cap_ends_the_loop(self) -> None:
        state = _state_with([_draft(n, Severity.ERROR) for n in (1, 2, 3)])
        assert route_after_critic(state, max_iterations=3) == END

    def test_cap_of_one_never_loops(self) -> None:
        state = _state_with([_draft(1, Severity.ERROR)])
        assert route_after_critic(state, max_iterations=1) == END

    def test_iteration_beyond_the_cap_still_ends(self) -> None:
        state = _state_with([_draft(1, Severity.ERROR)], iteration=9)
        assert route_after_critic(state, max_iterations=3) == END


class TestBestDraft:
    def test_approved_beats_rejected(self) -> None:
        drafts = [_draft(1, Severity.ERROR), _draft(2), _draft(3, Severity.ERROR)]
        assert select_best_draft(drafts).iteration == 2

    def test_fewer_violations_win(self) -> None:
        drafts = [
            _draft(1, Severity.ERROR, Severity.ERROR),
            _draft(2, Severity.ERROR),
            _draft(3, Severity.ERROR, Severity.ERROR),
        ]
        assert select_best_draft(drafts).iteration == 2

    def test_errors_outweigh_many_warnings(self) -> None:
        drafts = [_draft(1, Severity.ERROR), _draft(2, *[Severity.WARNING] * 5)]
        assert select_best_draft(drafts).iteration == 2

    def test_ties_go_to_the_latest_draft(self) -> None:
        drafts = [_draft(1, Severity.ERROR), _draft(2, Severity.ERROR)]
        assert select_best_draft(drafts).iteration == 2

    def test_unreviewed_drafts_rank_last(self) -> None:
        drafts = [_draft(1, Severity.ERROR), _draft(2, reviewed=False)]
        assert select_best_draft(drafts).iteration == 1

    def test_no_drafts_is_an_error(self) -> None:
        with pytest.raises(AgentStateError):
            select_best_draft([])


class TestGraphRuns:
    def _run(self, llm: FakeLLM, max_iterations: int = 3) -> GenerationResult:
        graph = CommitMessageGraph(llm, GitCommitAIConfig(max_iterations=max_iterations))
        return graph.run(make_diff({"src/api/client.py": 6}), HISTORY)

    def test_first_draft_accepted(self) -> None:
        llm = scripted_llm([GOOD])
        result = self._run(llm)
        assert (result.message, result.approved, result.iterations) == (GOOD, True, 1)
        assert len(llm.calls) == 3

    def test_critic_feedback_drives_a_second_round(self) -> None:
        llm = scripted_llm(["Added page size option", GOOD])
        result = self._run(llm)
        assert (result.message, result.approved, result.iterations) == (GOOD, True, 2)
        # analyst, generator, generator, faithfulness check: the first critique is
        # rule-only because a rule error already forces a rewrite.
        assert len(llm.calls) == 4
        regeneration_prompt = llm.calls[2][1].content
        assert "Added page size option" in regeneration_prompt
        assert "[imperative-mood]" in regeneration_prompt

    def test_unfaithful_draft_is_regenerated(self) -> None:
        unfaithful = json.dumps({"faithful": False, "problems": ["Mentions caching"]})
        llm = scripted_llm([GOOD, "feat(api): add page size limit"], [unfaithful, FAITHFUL])
        result = self._run(llm)
        assert result.iterations == 2
        assert "Mentions caching" in llm.calls[3][1].content

    def test_cap_reached_falls_back_to_the_best_draft(self) -> None:
        worst = "Added page size"
        middle = "feat(api): added page size option"
        also_bad = "feat(api): Added page size option"
        llm = scripted_llm([worst, middle, also_bad])
        result = self._run(llm)
        assert result.iterations == 3
        assert not result.approved
        assert result.message == middle

    def test_cap_of_one_makes_a_single_attempt(self) -> None:
        llm = scripted_llm(["Added page size option", GOOD])
        result = self._run(llm, max_iterations=1)
        assert result.iterations == 1
        assert not result.approved
        assert result.message == "Added page size option"

    def test_final_state_exposes_everything_for_verbose_output(self) -> None:
        result = self._run(scripted_llm([GOOD]))
        state = result.state
        assert state.analysis is not None
        assert state.context is not None
        assert state.context.convention_result.analyzed_commits == 6
        assert [d.iteration for d in state.drafts] == [1]

    def test_llm_failure_propagates(self) -> None:
        with pytest.raises(LLMResponseError):
            self._run(scripted_llm([]))
