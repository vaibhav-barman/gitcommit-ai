"""Context retrieval node: derive repository conventions from history."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_commit_ai.context.engine import RepositoryContextEngine

if TYPE_CHECKING:
    from git_commit_ai.agents.state import AgentNode, AgentState


def make_context_retrieval(
    engine: RepositoryContextEngine | None = None,
) -> AgentNode:
    """Create the context retrieval node.

    The node makes no LLM call. It delegates to ``RepositoryContextEngine``
    so convention detection, style profiling and example selection stay in
    the deterministic context layer.

    Args:
        engine: Context engine to use; a default one is created if omitted.
    """
    context_engine = engine or RepositoryContextEngine()

    def context_retrieval(state: AgentState) -> dict[str, object]:
        context = context_engine.build_context(state.diff_result, state.history)
        return {"context": context}

    return context_retrieval
