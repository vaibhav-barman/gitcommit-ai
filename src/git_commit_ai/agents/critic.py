"""Critic node: deterministic style rules first, an LLM only for faithfulness."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from git_commit_ai.agents.critic_rules import check_draft
from git_commit_ai.agents.llm_json import complete_json
from git_commit_ai.agents.state import AgentState, Critique, Severity, Violation
from git_commit_ai.core.exceptions import AgentStateError, LLMError
from git_commit_ai.llm.base import LLMMessage, Role

if TYPE_CHECKING:
    from git_commit_ai.agents.state import AgentNode, DiffDigest
    from git_commit_ai.llm.base import LLMProvider

_SYSTEM_PROMPT = """You verify a commit message against the staged diff it describes.
Reply with ONLY a JSON object:
{"faithful": true or false, "problems": [short strings]}
Mark it unfaithful if the message claims changes that are not in the diff,
misses the main change, or would mislead a reader. Do NOT judge format, tone,
length or style; other checks cover those."""

_TRUNCATION_NOTE = (
    "\nThe diff below was truncated for size. Do not call something invented "
    "merely because its patch is not shown."
)


class _Faithfulness(BaseModel):
    faithful: bool
    problems: list[str] = Field(default_factory=list)


def make_critic(llm: LLMProvider) -> AgentNode:
    """Create the critic node.

    Deterministic rules judge format, subject length, imperative mood, scope,
    capitalization and punctuation. Only when none of them raise an error does
    the node spend an LLM call on whether the message matches the diff, since
    a draft that must be rewritten anyway does not need that verdict yet.

    If that call fails for any reason (rate limit, timeout, empty or malformed
    reply) the draft keeps its rule-based verdict, gains a non-blocking
    ``faithfulness-unchecked`` warning, and the failure is recorded as an
    interruption rather than aborting the run.
    """

    def critic(state: AgentState) -> dict[str, object]:
        digest, analysis, context = state.drafting_inputs()
        if not state.drafts:
            raise AgentStateError("drafts")
        draft = state.drafts[-1]

        critique = Critique(
            violations=check_draft(draft.message, analysis, context, state.diff_result)
        )
        interruption: str | None = None
        if critique.approved:
            try:
                extra = _check_faithfulness(llm, digest, draft.message)
            except LLMError as exc:
                interruption = f"the message could not be checked against the diff: {exc}"
                extra = [
                    Violation(
                        rule="faithfulness-unchecked",
                        message=f"Not verified against the diff: {exc}",
                        severity=Severity.WARNING,
                    )
                ]
            critique = Critique(violations=[*critique.violations, *extra])

        reviewed = draft.model_copy(update={"critique": critique})
        update: dict[str, object] = {"drafts": [*state.drafts[:-1], reviewed]}
        if interruption is not None:
            update["interruption"] = interruption
        return update

    return critic


def _check_faithfulness(llm: LLMProvider, digest: DiffDigest, message: str) -> list[Violation]:
    system = _SYSTEM_PROMPT + (_TRUNCATION_NOTE if digest.was_truncated else "")
    verdict = complete_json(
        llm,
        [
            LLMMessage(role=Role.SYSTEM, content=system),
            LLMMessage(
                role=Role.USER,
                content=f"Staged diff:\n{digest.text}\n\nCommit message:\n{message}",
            ),
        ],
        _Faithfulness,
    )
    if verdict.faithful:
        return []
    problems = verdict.problems or ["The message does not accurately describe the diff."]
    return [Violation(rule="faithfulness", message=problem) for problem in problems]
