"""A FakeLLM that answers each agent role from its own script."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from git_commit_ai.core.exceptions import LLMResponseError
from git_commit_ai.llm import FakeLLM

if TYPE_CHECKING:
    from collections.abc import Sequence

    from git_commit_ai.llm import LLMMessage

ANALYSIS_REPLY = json.dumps(
    {
        "change_type": "feat",
        "summary": "Add page size option to the API client",
        "intent": "Let callers bound response size",
        "is_breaking": False,
        "scope_hint": "api",
    }
)
FAITHFUL = json.dumps({"faithful": True, "problems": []})


def scripted_llm(
    drafts: Sequence[str],
    verdicts: Sequence[str] | None = None,
    analysis: str = ANALYSIS_REPLY,
) -> FakeLLM:
    """Build a FakeLLM that routes by the system prompt.

    Args:
        drafts: Replies for successive generator calls.
        verdicts: Replies for successive faithfulness checks; defaults to
            "faithful" every time.
        analysis: Reply for the diff analyst.
    """
    draft_queue = list(drafts)
    verdict_queue = list(verdicts) if verdicts is not None else None

    def respond(messages: Sequence[LLMMessage]) -> str:
        system = messages[0].content
        if "analyze staged git diffs" in system:
            return analysis
        if "verify a commit message" in system:
            if verdict_queue is None:
                return FAITHFUL
            if not verdict_queue:
                raise LLMResponseError("verdict script exhausted")
            return verdict_queue.pop(0)
        if not draft_queue:
            raise LLMResponseError("draft script exhausted")
        return draft_queue.pop(0)

    return FakeLLM(respond)
