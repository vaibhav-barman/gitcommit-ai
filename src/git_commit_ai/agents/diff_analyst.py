"""Diff analyst node: decide what a staged diff does and why."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_commit_ai.agents.diff_budget import build_diff_digest
from git_commit_ai.agents.llm_json import complete_json
from git_commit_ai.agents.state import AgentState, CommitType, DiffAnalysis
from git_commit_ai.llm.base import LLMMessage, Role

if TYPE_CHECKING:
    from git_commit_ai.agents.state import AgentNode
    from git_commit_ai.llm.base import LLMProvider

_SYSTEM_PROMPT = f"""You analyze staged git diffs for a commit message generator.
Read the changed-file list and patches, then reply with ONLY a JSON object:
{{
  "change_type": one of {", ".join(t.value for t in CommitType)},
  "summary": one sentence stating what the change does,
  "intent": one sentence on why it was likely made, or "",
  "is_breaking": true only if public behaviour or interfaces break,
  "scope_hint": a short area name such as a package or module, or null
}}
Judge the dominant change. Ignore formatting-only noise and lockfile churn when
real changes exist. Do not invent changes that are not in the diff."""


def make_diff_analyst(
    llm: LLMProvider,
    max_diff_chars: int,
) -> AgentNode:
    """Create the diff analyst node.

    The node renders the diff within ``max_diff_chars`` (so every later node
    sees the same digest) and stores the analyst's structured reading.
    """

    def diff_analyst(state: AgentState) -> dict[str, object]:
        digest = build_diff_digest(state.diff_result, max_diff_chars)
        analysis = complete_json(
            llm,
            [
                LLMMessage(role=Role.SYSTEM, content=_SYSTEM_PROMPT),
                LLMMessage(role=Role.USER, content=digest.text),
            ],
            DiffAnalysis,
        )
        return {"diff_digest": digest, "analysis": analysis}

    return diff_analyst
