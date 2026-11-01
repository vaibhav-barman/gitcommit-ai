"""The generate -> critique -> regenerate graph and its termination logic."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from git_commit_ai.agents.context_retrieval import make_context_retrieval
from git_commit_ai.agents.critic import make_critic
from git_commit_ai.agents.diff_analyst import make_diff_analyst
from git_commit_ai.agents.generator import make_generator
from git_commit_ai.agents.state import AgentState, Draft
from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import AgentStateError

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from git_commit_ai.context.engine import RepositoryContextEngine
    from git_commit_ai.core.schemas import DiffResult, HistoricalCommit
    from git_commit_ai.llm.base import LLMProvider

_GENERATOR = "generator"
_CRITIC = "critic"
_STEPS_BEFORE_LOOP = 2
_STEPS_PER_ROUND = 2
_RECURSION_HEADROOM = 5


class GenerationResult(BaseModel):
    """Outcome of one graph run."""

    message: str
    approved: bool
    iterations: int
    interruption: str | None = None
    state: AgentState


def route_after_generator(state: AgentState) -> str:
    """Go on to the critic, unless the generator was interrupted by a provider error."""
    return END if state.interruption else _CRITIC


def route_after_critic(state: AgentState, max_iterations: int) -> str:
    """Decide whether to stop or regenerate once the critic has spoken.

    The loop ends as soon as a draft is approved, when a provider error
    interrupted the run, and unconditionally once ``max_iterations`` generation
    rounds have run, so it always terminates.
    """
    if state.interruption:
        return END
    critique = state.drafts[-1].critique if state.drafts else None
    if critique is not None and critique.approved:
        return END
    if state.iteration >= max_iterations:
        return END
    return _GENERATOR


def select_best_draft(drafts: list[Draft]) -> Draft:
    """Pick the draft to present: lowest penalty, latest on ties.

    An unreviewed draft ranks below any reviewed one. Preferring the latest of
    equally good drafts favours the one that has seen the most feedback.

    Raises:
        AgentStateError: If there are no drafts.
    """
    if not drafts:
        raise AgentStateError("drafts")
    unreviewed = float("inf")
    return min(
        drafts,
        key=lambda d: (d.critique.penalty if d.critique else unreviewed, -d.iteration),
    )


def build_graph(
    llm: LLMProvider,
    config: GitCommitAIConfig,
    engine: RepositoryContextEngine | None = None,
) -> CompiledStateGraph[AgentState]:
    """Compile the commit message graph.

    Flow: diff_analyst -> context_retrieval -> generator -> critic, then back
    to generator until the critic approves, the iteration cap is reached, or a
    provider error interrupts a later step.
    """
    graph = StateGraph(AgentState)
    graph.add_node("diff_analyst", make_diff_analyst(llm, config.max_prompt_diff_chars))
    graph.add_node("context_retrieval", make_context_retrieval(engine))
    graph.add_node(_GENERATOR, make_generator(llm))
    graph.add_node(_CRITIC, make_critic(llm))

    graph.add_edge(START, "diff_analyst")
    graph.add_edge("diff_analyst", "context_retrieval")
    graph.add_edge("context_retrieval", _GENERATOR)
    graph.add_conditional_edges(
        _GENERATOR,
        route_after_generator,
        {_CRITIC: _CRITIC, END: END},
    )
    graph.add_conditional_edges(
        _CRITIC,
        lambda state: route_after_critic(state, config.max_iterations),
        {_GENERATOR: _GENERATOR, END: END},
    )
    return graph.compile()


class CommitMessageGraph:
    """Runs the compiled graph and picks the message to present.

    Args:
        llm: Provider used by the analyst, generator and critic.
        config: Supplies the iteration cap and prompt budget.
        engine: Optional context engine override.
    """

    def __init__(
        self,
        llm: LLMProvider,
        config: GitCommitAIConfig | None = None,
        engine: RepositoryContextEngine | None = None,
    ) -> None:
        self._config = config or GitCommitAIConfig()
        self._graph = build_graph(llm, self._config, engine)

    def run(
        self,
        diff_result: DiffResult,
        history: list[HistoricalCommit],
        hint: str | None = None,
    ) -> GenerationResult:
        """Generate a commit message for ``diff_result``.

        ``hint`` is the author's own statement of intent; it steers the analyst
        and the generator, but the message must still match the diff.

        If no draft is approved within the cap, the best draft is returned
        with ``approved=False`` rather than failing. A provider error after the
        first draft ends the run the same way, with the reason in
        ``interruption``. An error in the first generation propagates.
        """
        steps = _STEPS_BEFORE_LOOP + _STEPS_PER_ROUND * self._config.max_iterations
        raw = self._graph.invoke(
            AgentState(diff_result=diff_result, history=history, hint=hint),
            {"recursion_limit": steps + _RECURSION_HEADROOM},
        )
        final = AgentState.model_validate(raw)
        best = select_best_draft(final.drafts)
        reviewed_ok = best.critique is not None and best.critique.approved
        return GenerationResult(
            message=best.message,
            approved=reviewed_ok and final.interruption is None,
            iterations=final.iteration,
            interruption=final.interruption,
            state=final,
        )
