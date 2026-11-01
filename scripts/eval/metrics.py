"""Deterministic scoring of one generated message against the real commit it replays.

No model is involved. Agreement checks compare with the real commit; the length
and mood checks compare with the repository's style profile, which was built
from the commits older than the one being replayed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_commit_ai.agents.conventions import HARD_SUBJECT_LIMIT
from git_commit_ai.agents.message_format import split_message
from git_commit_ai.agents.state import Severity
from git_commit_ai.context.normalizer import CommitNormalizer
from git_commit_ai.context.style_analyzer import COMMON_IMPERATIVE_VERBS
from git_commit_ai.core.exceptions import AgentStateError
from git_commit_ai.core.schemas import CommitConvention, HistoricalCommit
from scripts.eval.report import Checks, CommitRecord, Outcome, ViolationCounts

if TYPE_CHECKING:
    from git_commit_ai.agents.graph import GenerationResult
    from git_commit_ai.agents.state import Critique
    from git_commit_ai.core.schemas import CommitStyleProfile, NormalizedCommit

_LENGTH_LOWER_RATIO = 0.5
_LENGTH_UPPER_RATIO = 1.5
_LENGTH_UPPER_FLOOR = 50
_CONVENTIONAL = {CommitConvention.CONVENTIONAL, CommitConvention.SCOPED_CONVENTIONAL}

_normalizer = CommitNormalizer()


def score(reference: HistoricalCommit, result: GenerationResult) -> CommitRecord:
    """Score the graph's final message for ``reference``.

    Raises:
        AgentStateError: If the run left no style profile or no critiqued first draft
            (the graph always produces both for a completed run).
    """
    context = result.state.context
    if context is None or not result.state.drafts:
        raise AgentStateError("context")
    generated = _normalize(result.message)
    real = _normalizer.normalize(reference)

    kept = next(d for d in result.state.drafts if d.message == result.message)
    return CommitRecord(
        hash=reference.hash,
        real_subject=reference.subject,
        outcome=Outcome(
            generated_message=result.message,
            real_convention=real.convention.value,
            generated_convention=generated.convention.value,
            iterations=result.iterations,
            approved=result.approved,
            interrupted=result.interruption is not None,
            checks=check_message(generated, real, context.style_profile),
            before=_counts(result.state.drafts[0].critique),
            after=_counts(kept.critique),
        ),
    )


def check_message(
    generated: NormalizedCommit,
    real: NormalizedCommit,
    profile: CommitStyleProfile,
) -> Checks:
    """Run every deterministic check; the style profile comes from older history."""
    return Checks(
        convention_match=_family(generated.convention) == _family(real.convention),
        subject_length_ok=_length_ok(generated.cleaned_subject, profile),
        imperative_mood=_is_imperative(generated.cleaned_subject),
        capitalization_agrees=_capitalization_agrees(generated, real),
        punctuation_agrees=_ends_with_period(generated) == _ends_with_period(real),
        scope_agrees=(
            (generated.scope is not None) == (real.scope is not None)
            if real.convention in _CONVENTIONAL
            else None
        ),
        body_agrees=bool(generated.cleaned_body) == bool(real.cleaned_body),
    )


def _normalize(message: str) -> NormalizedCommit:
    subject, body = split_message(message)
    return _normalizer.normalize(HistoricalCommit(hash="generated", subject=subject, body=body))


def _family(convention: CommitConvention) -> CommitConvention:
    """Scoped and unscoped Conventional Commits are one convention; scope has its own check."""
    return CommitConvention.CONVENTIONAL if convention in _CONVENTIONAL else convention


def _length_ok(description: str, profile: CommitStyleProfile) -> bool:
    """Within half to one and a half times the repository's average, never past the hard limit.

    The upper bound is at least 50 characters, so a repository of terse subjects
    does not make a clear one fail. With no history only the hard limit applies.
    """
    length = len(description)
    average = profile.average_subject_length
    if not average:
        return length <= HARD_SUBJECT_LIMIT
    upper = min(HARD_SUBJECT_LIMIT, max(_LENGTH_UPPER_FLOOR, average * _LENGTH_UPPER_RATIO))
    return average * _LENGTH_LOWER_RATIO <= length <= upper


def _is_imperative(description: str) -> bool:
    """The profile's own definition: the first word is a known base-form verb."""
    words = description.split()
    return bool(words) and words[0].lower().rstrip(":,.-_") in COMMON_IMPERATIVE_VERBS


def _capitalization_agrees(generated: NormalizedCommit, real: NormalizedCommit) -> bool | None:
    first_generated = generated.cleaned_subject.strip()[:1]
    first_real = real.cleaned_subject.strip()[:1]
    if not (first_generated.isalpha() and first_real.isalpha()):
        return None
    return first_generated.isupper() == first_real.isupper()


def _ends_with_period(commit: NormalizedCommit) -> bool:
    return commit.cleaned_subject.rstrip().endswith(".")


def _counts(critique: Critique | None) -> ViolationCounts:
    violations = critique.violations if critique else []
    errors = sum(1 for v in violations if v.severity is Severity.ERROR)
    return ViolationCounts(
        errors=errors,
        warnings=len(violations) - errors,
        rules=sorted({v.rule for v in violations}),
    )
