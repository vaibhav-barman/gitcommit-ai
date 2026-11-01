"""Generator node: draft a commit message that matches repository style."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from git_commit_ai.agents.conventions import HARD_SUBJECT_LIMIT, effective_convention
from git_commit_ai.agents.state import AgentState, CommitType, Draft
from git_commit_ai.core.schemas import CommitConvention
from git_commit_ai.llm.base import LLMMessage, Role

if TYPE_CHECKING:
    from collections.abc import Callable

    from git_commit_ai.agents.state import DiffAnalysis, DiffDigest
    from git_commit_ai.core.schemas import CommitStyleProfile, RepositoryContext
    from git_commit_ai.llm.base import LLMProvider

_SYSTEM_PROMPT = """You write git commit messages that read as if the repository's own
maintainers wrote them. Follow the format and style rules exactly. Describe only
what the diff shows. Reply with ONLY the commit message: no quotes, no code
fences, no commentary."""

_BODY_COMMON = 0.5
_BODY_RARE = 0.15
_TYPED = {CommitConvention.CONVENTIONAL, CommitConvention.SCOPED_CONVENTIONAL}
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*?)\n?```$", re.DOTALL)


def make_generator(llm: LLMProvider) -> Callable[[AgentState], dict[str, object]]:
    """Create the generator node.

    Each call appends one ``Draft`` and advances the iteration counter. When
    earlier drafts were critiqued, the latest draft and its violations are
    included so the model revises instead of starting over.
    """

    def generator(state: AgentState) -> dict[str, object]:
        digest, analysis, context = state.drafting_inputs()
        reply = llm.complete(build_generator_messages(digest, analysis, context, state.drafts))
        iteration = state.iteration + 1
        draft = Draft(message=clean_message(reply), iteration=iteration)
        return {"drafts": [*state.drafts, draft], "iteration": iteration}

    return generator


def build_generator_messages(
    digest: DiffDigest,
    analysis: DiffAnalysis,
    context: RepositoryContext,
    drafts: list[Draft],
) -> list[LLMMessage]:
    """Assemble the generator prompt from deterministic context.

    Pure so the prompt can be tested without an LLM.
    """
    rules = "\n".join(f"- {rule}" for rule in _rules(analysis, context))
    sections = [f"Format and style rules:\n{rules}"]

    if context.historical_examples:
        shown = "\n\n".join(
            f"```\n{example.original_message.strip()}\n```"
            for example in context.historical_examples
        )
        sections.append(f"Relevant commits from this repository (match their voice):\n{shown}")

    sections.append(
        "What the change does:\n"
        f"- type: {analysis.change_type.value}\n"
        f"- summary: {analysis.summary}\n"
        f"- intent: {analysis.intent or 'not stated'}"
    )
    sections.append(f"Staged diff:\n{digest.text}")

    if drafts and drafts[-1].critique is not None and drafts[-1].critique.violations:
        problems = "\n".join(f"- [{v.rule}] {v.message}" for v in drafts[-1].critique.violations)
        sections.append(
            f"Your previous draft:\n```\n{drafts[-1].message}\n```\n"
            f"A reviewer found these problems; fix all of them:\n{problems}"
        )

    return [
        LLMMessage(role=Role.SYSTEM, content=_SYSTEM_PROMPT),
        LLMMessage(role=Role.USER, content="\n\n".join(sections)),
    ]


def clean_message(reply: str) -> str:
    """Strip code fences and surrounding whitespace from a model reply."""
    text = reply.strip()
    fenced = _FENCE_RE.match(text)
    return fenced.group(1).strip() if fenced else text


def _rules(analysis: DiffAnalysis, context: RepositoryContext) -> list[str]:
    convention = effective_convention(context)
    profile = context.style_profile
    rules = [_format_rule(convention, analysis, profile)]
    rules.append(
        f"Keep the subject line under {HARD_SUBJECT_LIMIT} characters"
        + (
            f" (this repository averages {profile.average_subject_length:.0f})."
            if profile.average_subject_length
            else "."
        )
    )
    rules.append("Use the imperative mood: 'add', 'fix', 'remove', not 'added' or 'adds'.")
    if capitalization := _capitalization_rule(profile.typical_capitalization):
        rules.append(capitalization)
    if "trailing_period" in profile.punctuation_patterns:
        rules.append("End the subject with a period.")
    elif "no_trailing_period" in profile.punctuation_patterns:
        rules.append("Do not end the subject with a period.")
    rules.append(_body_rule(profile.body_usage_frequency))
    if analysis.is_breaking and convention in _TYPED:
        rules.append(
            "This change is breaking: mark it with '!' before the colon and add a "
            "'BREAKING CHANGE: <what breaks>' footer."
        )
    return rules


def _format_rule(
    convention: CommitConvention,
    analysis: DiffAnalysis,
    profile: CommitStyleProfile,
) -> str:
    types = ", ".join(t.value for t in CommitType)
    scopes = ", ".join(profile.common_scopes[:8])
    hint = f" Suggested scope: {analysis.scope_hint}." if analysis.scope_hint else ""
    match convention:
        case CommitConvention.SCOPED_CONVENTIONAL:
            known = f" Scopes used here: {scopes}." if scopes else ""
            return (
                f"Format: '<type>(<scope>): <description>' with types from {types}. "
                f"A scope is required.{known}{hint}"
            )
        case CommitConvention.GITMOJI:
            return (
                "Format: a gitmoji, a space, then the description, in the same emoji "
                "notation (unicode or :shortcode:) as the examples."
            )
        case CommitConvention.BRACKET_PREFIX:
            return "Format: '[PREFIX] description' with a bracketed prefix naming the area."
        case CommitConvention.PLAIN:
            return "Format: a plain subject line with no type prefix, scope or emoji."
        case _:
            optional = f" Scopes used here: {scopes}." if scopes else ""
            return (
                f"Format: Conventional Commits '<type>: <description>' with types "
                f"from {types}; add '(<scope>)' only when it clarifies.{optional}{hint}"
            )


def _capitalization_rule(capitalization: str) -> str | None:
    match capitalization:
        case "lowercase":
            return "Start the description with a lowercase letter."
        case "sentencecase" | "titlecase":
            return "Start the description with a capital letter."
        case "uppercase":
            return "Write the description in uppercase."
        case _:
            return None


def _body_rule(frequency: float) -> str:
    if frequency >= _BODY_COMMON:
        return "Add a body after a blank line explaining why the change was made."
    if frequency <= _BODY_RARE:
        return "Write a subject line only; add a body just for breaking changes."
    return "Add a body after a blank line only when the reason is not obvious."
