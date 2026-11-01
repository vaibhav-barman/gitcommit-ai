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
    analysis_error: Exception | None = None,
    generator_errors: dict[int, Exception] | None = None,
    verdict_errors: dict[int, Exception] | None = None,
) -> FakeLLM:
    """Build a FakeLLM that routes by the system prompt.

    Args:
        drafts: Replies for successive generator calls.
        verdicts: Replies for successive faithfulness checks; defaults to
            "faithful" every time.
        analysis: Reply for the diff analyst.
        analysis_error: Raised by the diff analyst instead of replying.
        generator_errors: Maps a 1-based generator call number to the error
            raised on that call.
        verdict_errors: Same, for faithfulness checks.
    """
    draft_queue = list(drafts)
    verdict_queue = list(verdicts) if verdicts is not None else None
    calls = {"generator": 0, "verdict": 0}

    def respond(messages: Sequence[LLMMessage]) -> str:
        system = messages[0].content
        if "analyze staged git diffs" in system:
            if analysis_error is not None:
                raise analysis_error
            return analysis
        if "verify a commit message" in system:
            calls["verdict"] += 1
            if verdict_errors and calls["verdict"] in verdict_errors:
                raise verdict_errors[calls["verdict"]]
            if verdict_queue is None:
                return FAITHFUL
            if not verdict_queue:
                raise LLMResponseError("verdict script exhausted")
            return verdict_queue.pop(0)
        calls["generator"] += 1
        if generator_errors and calls["generator"] in generator_errors:
            raise generator_errors[calls["generator"]]
        if not draft_queue:
            raise LLMResponseError("draft script exhausted")
        return draft_queue.pop(0)

    return FakeLLM(respond)
