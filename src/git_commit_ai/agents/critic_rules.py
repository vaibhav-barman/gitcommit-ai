"""Deterministic checks of a draft against repository convention and style."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_commit_ai.agents.conventions import (
    BODY_WIDTH,
    HARD_SUBJECT_LIMIT,
    MAX_BODY_LINES,
    body_expected,
    effective_convention,
)
from git_commit_ai.agents.message_format import split_message
from git_commit_ai.agents.state import CommitType, Severity, Violation
from git_commit_ai.context.normalizer import CommitNormalizer
from git_commit_ai.context.style_analyzer import COMMON_IMPERATIVE_VERBS
from git_commit_ai.core.schemas import CommitConvention, HistoricalCommit

if TYPE_CHECKING:
    from git_commit_ai.agents.state import DiffAnalysis
    from git_commit_ai.core.schemas import (
        CommitStyleProfile,
        DiffResult,
        NormalizedCommit,
        RepositoryContext,
    )

_TYPED = {CommitConvention.CONVENTIONAL, CommitConvention.SCOPED_CONVENTIONAL}
_IMPERATIVE_ENFORCED_AT = 0.5
_LENGTH_SLACK = 1.5
_LENGTH_FLOOR = 50
_KNOWN_TYPES = {t.value for t in CommitType}

_normalizer = CommitNormalizer()


def check_draft(
    message: str,
    analysis: DiffAnalysis,
    context: RepositoryContext,
    diff_result: DiffResult,
) -> list[Violation]:
    """Return every rule violation found in ``message``.

    Only checks a rule can decide are made here; whether the message is
    faithful to the diff is left to the LLM-backed critic.
    """
    text = message.strip()
    if not text:
        return [Violation(rule="empty-message", message="The commit message is empty.")]

    lines = text.splitlines()
    subject, body = split_message(text)
    parsed = _normalizer.normalize(HistoricalCommit(hash="draft", subject=subject, body=body))
    convention = effective_convention(context)
    profile = context.style_profile

    found: list[Violation | None] = [
        _format(parsed, convention, profile),
        _type_matches_analysis(parsed, convention, analysis),
        _subject_length(subject, parsed, profile),
        _imperative(parsed, profile),
        _capitalization(parsed, profile),
        _trailing_period(parsed, profile),
        _scope(parsed, convention, profile),
        _breaking(parsed, convention, analysis),
        _blank_line(lines),
    ]
    found.extend(_body(body, parsed, body_expected(context, diff_result)))
    return [v for v in found if v is not None]


def _error(rule: str, message: str) -> Violation:
    return Violation(rule=rule, message=message, severity=Severity.ERROR)


def _warning(rule: str, message: str) -> Violation:
    return Violation(rule=rule, message=message, severity=Severity.WARNING)


def _format(
    parsed: NormalizedCommit,
    convention: CommitConvention,
    profile: CommitStyleProfile,
) -> Violation | None:
    has_prefix = parsed.convention is not CommitConvention.PLAIN
    if has_prefix and parsed.cleaned_subject == parsed.original_subject.strip():
        return _error("format", "The subject has a prefix but no description after it.")

    match convention:
        case CommitConvention.CONVENTIONAL | CommitConvention.SCOPED_CONVENTIONAL:
            if parsed.convention not in _TYPED or parsed.commit_type is None:
                return _error("format", "Use the form '<type>(<scope>): <description>'.")
            known = _KNOWN_TYPES | set(profile.type_frequencies)
            if parsed.commit_type not in known:
                return _error(
                    "format",
                    f"Unknown type '{parsed.commit_type}'; use one of {', '.join(sorted(known))}.",
                )
            if convention is CommitConvention.SCOPED_CONVENTIONAL and parsed.scope is None:
                return _error(
                    "scope", "This repository always uses a scope: '<type>(<scope>): ...'."
                )
        case CommitConvention.GITMOJI:
            if parsed.gitmoji is None:
                return _error(
                    "format", "Start the subject with a gitmoji, as the repository does."
                )
        case CommitConvention.BRACKET_PREFIX:
            if parsed.bracket_prefix is None:
                return _error("format", "Start the subject with a bracketed prefix: '[AREA] ...'.")
        case _:
            if parsed.convention is not CommitConvention.PLAIN:
                return _error(
                    "format",
                    "Use a plain subject line without a type, scope, bracket or emoji prefix.",
                )
    return None


def _type_matches_analysis(
    parsed: NormalizedCommit,
    convention: CommitConvention,
    analysis: DiffAnalysis,
) -> Violation | None:
    if convention not in _TYPED or parsed.commit_type not in _KNOWN_TYPES:
        return None
    if parsed.commit_type == analysis.change_type.value:
        return None
    return _warning(
        "type-mismatch",
        f"The diff looks like '{analysis.change_type.value}' but the draft uses "
        f"'{parsed.commit_type}'. Keep it only if the draft's type is more accurate.",
    )


def _subject_length(
    subject: str,
    parsed: NormalizedCommit,
    profile: CommitStyleProfile,
) -> Violation | None:
    if len(subject) > HARD_SUBJECT_LIMIT:
        return _error(
            "subject-length",
            f"The subject is {len(subject)} characters; keep it under {HARD_SUBJECT_LIMIT}.",
        )
    typical = profile.average_subject_length
    limit = max(_LENGTH_FLOOR, typical * _LENGTH_SLACK)
    if typical and len(parsed.cleaned_subject) > limit:
        return _warning(
            "subject-length",
            f"The description is {len(parsed.cleaned_subject)} characters; this repository "
            f"averages {typical:.0f}. Consider tightening it.",
        )
    return None


def _imperative(parsed: NormalizedCommit, profile: CommitStyleProfile) -> Violation | None:
    frequency = profile.imperative_mood_frequency
    if frequency is not None and frequency < _IMPERATIVE_ENFORCED_AT:
        return None
    words = parsed.cleaned_subject.split()
    if not words:
        return None
    first = words[0].lower().strip(":,.-_")
    if first not in COMMON_IMPERATIVE_VERBS and _is_inflected_verb(first):
        return _error(
            "imperative-mood",
            f"'{words[0]}' is not imperative; start with the base verb (for example 'add').",
        )
    return None


def _is_inflected_verb(word: str) -> bool:
    """True if ``word`` is a past, gerund or third-person form of a known verb."""
    stems: set[str] = set()
    if word.endswith("ing") and len(word) > 4:
        stem = word[:-3]
        stems |= {stem, stem + "e", stem[:-1] if stem[-1:] == stem[-2:-1] else stem}
    if word.endswith("ed") and len(word) > 3:
        stem = word[:-2]
        stems |= {stem, stem + "e", word[:-1], stem[:-1] if stem[-1:] == stem[-2:-1] else stem}
        if stem.endswith("i"):
            stems.add(stem[:-1] + "y")
    if word.endswith("ies") and len(word) > 4:
        stems.add(word[:-3] + "y")
    if word.endswith("es") and len(word) > 3:
        stems.add(word[:-2])
    if word.endswith("s") and len(word) > 2:
        stems.add(word[:-1])
    return bool(stems & COMMON_IMPERATIVE_VERBS)


def _capitalization(parsed: NormalizedCommit, profile: CommitStyleProfile) -> Violation | None:
    description = parsed.cleaned_subject.strip()
    if not description or not description[0].isalpha():
        return None
    first_is_upper = description[0].isupper()
    match profile.typical_capitalization:
        case "lowercase" if first_is_upper:
            return _error("capitalization", "Start the description with a lowercase letter.")
        case "sentencecase" | "titlecase" if not first_is_upper:
            return _error("capitalization", "Start the description with a capital letter.")
        case "uppercase" if not description.isupper():
            return _error("capitalization", "Write the description in uppercase.")
        case _:
            return None


def _trailing_period(parsed: NormalizedCommit, profile: CommitStyleProfile) -> Violation | None:
    ends_with_period = parsed.cleaned_subject.rstrip().endswith(".")
    if "trailing_period" in profile.punctuation_patterns and not ends_with_period:
        return _error("trailing-period", "End the subject with a period, as the repository does.")
    if "no_trailing_period" in profile.punctuation_patterns and ends_with_period:
        return _error("trailing-period", "Remove the period at the end of the subject.")
    return None


def _scope(
    parsed: NormalizedCommit,
    convention: CommitConvention,
    profile: CommitStyleProfile,
) -> Violation | None:
    if convention not in _TYPED or parsed.scope is None or not profile.common_scopes:
        return None
    if parsed.scope in profile.common_scopes:
        return None
    return _warning(
        "scope",
        f"Scope '{parsed.scope}' has not been used in this repository. "
        f"Known scopes: {', '.join(profile.common_scopes[:8])}.",
    )


def _breaking(
    parsed: NormalizedCommit,
    convention: CommitConvention,
    analysis: DiffAnalysis,
) -> Violation | None:
    if convention not in _TYPED:
        return None
    if analysis.is_breaking and not parsed.is_breaking:
        return _error(
            "breaking-change",
            "The change is breaking: add '!' before the colon and a 'BREAKING CHANGE:' footer.",
        )
    if parsed.is_breaking and not analysis.is_breaking:
        return _warning(
            "breaking-change",
            "The draft is marked breaking but the diff does not appear to break anything.",
        )
    return None


def _body(body: str, parsed: NormalizedCommit, expected: bool) -> list[Violation]:
    if not body:
        if expected:
            return [
                _error(
                    "body-missing",
                    "Add a short body after a blank line explaining what changed and why; "
                    "this repository's commits (or this large diff) call for one.",
                )
            ]
        return []

    found: list[Violation] = []
    if not expected and not parsed.is_breaking:
        found.append(
            _warning("body-unexpected", "A subject line alone is enough here; drop the body.")
        )
    if wrapped := _overlong_lines(body):
        found.append(
            _error(
                "body-wrap",
                f"Wrap the body at {BODY_WIDTH} columns; {len(wrapped)} line(s) are longer.",
            )
        )
    non_blank = sum(1 for line in body.splitlines() if line.strip())
    if non_blank > MAX_BODY_LINES:
        found.append(
            _warning("body-length", f"The body is {non_blank} lines; keep it to {MAX_BODY_LINES}.")
        )
    return found


def _overlong_lines(body: str) -> list[str]:
    """Body lines over the width, ignoring code blocks and single unbreakable tokens."""
    return [
        line
        for line in body.splitlines()
        if len(line) > BODY_WIDTH and not line.startswith(("    ", "\t")) and len(line.split()) > 1
    ]


def _blank_line(lines: list[str]) -> Violation | None:
    if len(lines) > 1 and lines[1].strip():
        return _error("blank-line", "Separate the subject from the body with a blank line.")
    return None
