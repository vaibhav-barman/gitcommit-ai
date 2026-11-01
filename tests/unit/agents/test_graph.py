"""Unit tests for loop termination, draft selection, and the compiled graph."""

from __future__ import annotations

import json

import pytest
from langgraph.graph import END

from git_commit_ai.agents.graph import (
    CommitMessageGraph,
    GenerationResult,
    route_after_critic,
    route_after_generator,
    select_best_draft,
)
from git_commit_ai.agents.state import AgentState, Critique, Draft, Severity, Violation
from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import (
    AgentStateError,
    LLMError,
    LLMResponseError,
    RateLimitError,
    TransientLLMError,
)
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

    def test_interruption_ends_the_loop_even_with_rounds_left(self) -> None:
        state = _state_with([_draft(1, Severity.ERROR)]).model_copy(
            update={"interruption": "provider down"}
        )
        assert route_after_critic(state, max_iterations=5) == END

    def test_generator_hands_over_to_the_critic_unless_interrupted(self) -> None:
        state = _state_with([_draft(1, reviewed=False)])
        assert route_after_generator(state) == "critic"
        assert route_after_generator(state.model_copy(update={"interruption": "x"})) == END

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


_ERRORS = [
    RateLimitError("openai", retry_after=30),
    TransientLLMError("openai", "request timed out"),
    LLMResponseError("empty response"),
]
_ERROR_IDS = ["rate-limit", "timeout", "empty-response"]


class TestDegradation:
    def _run(self, llm: FakeLLM) -> GenerationResult:
        graph = CommitMessageGraph(llm, GitCommitAIConfig(max_iterations=3))
        return graph.run(make_diff({"src/api/client.py": 6}), HISTORY)

    @pytest.mark.parametrize("error", _ERRORS, ids=_ERROR_IDS)
    def test_critic_failure_returns_the_draft_with_an_interruption(self, error: LLMError) -> None:
        result = self._run(scripted_llm([GOOD], verdict_errors={1: error}))
        assert result.message == GOOD
        assert not result.approved
        assert result.iterations == 1
        assert result.interruption is not None
        assert "could not be checked against the diff" in result.interruption

    @pytest.mark.parametrize("error", _ERRORS, ids=_ERROR_IDS)
    def test_later_generation_failure_returns_the_best_draft_so_far(self, error: LLMError) -> None:
        llm = scripted_llm(["feat(api): added page size"], generator_errors={2: error})
        result = self._run(llm)
        assert result.message == "feat(api): added page size"
        assert not result.approved
        assert result.iterations == 1
        assert result.interruption is not None
        assert "round 2 could not be generated" in result.interruption

    def test_failure_in_a_later_round_keeps_the_better_earlier_draft(self) -> None:
        unfaithful = json.dumps({"faithful": False, "problems": ["Mentions caching"]})
        llm = scripted_llm(
            [GOOD, "feat(api): add page size limit"],
            verdicts=[unfaithful],
            verdict_errors={2: RateLimitError("openai")},
        )
        result = self._run(llm)
        # Round 2 passed every rule and only lacks the faithfulness verdict, so it
        # outranks round 1, which the verdict rejected.
        assert result.message == "feat(api): add page size limit"
        assert result.iterations == 2
        assert result.interruption is not None

    @pytest.mark.parametrize("error", _ERRORS, ids=_ERROR_IDS)
    def test_first_generation_failure_aborts(self, error: LLMError) -> None:
        with pytest.raises(type(error)):
            self._run(scripted_llm([], generator_errors={1: error}))

    @pytest.mark.parametrize("error", _ERRORS, ids=_ERROR_IDS)
    def test_diff_analyst_failure_aborts(self, error: LLMError) -> None:
        with pytest.raises(type(error)):
            self._run(scripted_llm([GOOD], analysis_error=error))

    def test_a_healthy_run_reports_no_interruption(self) -> None:
        result = self._run(scripted_llm([GOOD]))
        assert result.interruption is None
        assert result.approved
