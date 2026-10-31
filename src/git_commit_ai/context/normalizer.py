"""Commit message normalization and metadata extraction."""

from __future__ import annotations

import re

from git_commit_ai.core.schemas import (
    CommitConvention,
    HistoricalCommit,
    NormalizedCommit,
)

# Standard Conventional Commits: type(scope)!: subject
_CONVENTIONAL_RE = re.compile(
    r"^([a-zA-Z][a-zA-Z0-9_-]*)(?:\(([^)]+)\))?(!)?:\s*(.*)$",
    re.DOTALL,
)

# Bracketed prefixes: [AUTH] subject or [core/api]: subject
_BRACKET_RE = re.compile(
    r"^\[([a-zA-Z0-9_./-]+)\]\s*(.*)$",
    re.DOTALL,
)

# Gitmoji text code: :sparkles: subject
_GITMOJI_CODE_RE = re.compile(
    r"^(:[a-zA-Z0-9_]+:)\s*(.*)$",
    re.DOTALL,
)

# Gitmoji unicode emoji character at start of subject
_GITMOJI_UNICODE_RE = re.compile(
    r"^([\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]|\u2705|\u2728|\u274c)\s*(.*)$",
    re.DOTALL,
)

# Breaking change footer or paragraph indicator in body or subject
_BREAKING_CHANGE_RE = re.compile(
    r"(?m)^BREAKING[- ]CHANGE:\s*(.*)$",
    re.IGNORECASE,
)


class CommitNormalizer:
    """Normalizes raw historical commits into structured domain models.

    Extracts commit types, scopes, breaking changes, gitmoji indicators,
    and bracket prefixes, while strictly preserving original commit content.
    """

    def normalize(self, commit: HistoricalCommit) -> NormalizedCommit:
        """Normalize a single historical commit.

        Args:
            commit: Raw historical commit data.

        Returns:
            A populated ``NormalizedCommit`` model.
        """
        raw_subject = commit.subject or ""
        raw_body = commit.body or ""

        # Normalize whitespace
        cleaned_body = self._clean_body(raw_body)
        raw_subject_clean = raw_subject.strip()

        # Check for breaking change markers in body
        is_breaking = False
        breaking_description: str | None = None
        breaking_match = _BREAKING_CHANGE_RE.search(raw_body)
        if breaking_match:
            is_breaking = True
            breaking_description = breaking_match.group(1).strip() or None

        # 1. Check Conventional Commits: type(scope)!: subject
        conv_match = _CONVENTIONAL_RE.match(raw_subject_clean)
        if conv_match:
            commit_type = conv_match.group(1).lower()
            scope = conv_match.group(2).strip() if conv_match.group(2) else None
            exclamation = conv_match.group(3) is not None
            remainder_subject = conv_match.group(4).strip()

            if exclamation:
                is_breaking = True

            convention = (
                CommitConvention.SCOPED_CONVENTIONAL if scope else CommitConvention.CONVENTIONAL
            )

            return NormalizedCommit(
                hash=commit.hash,
                original_subject=raw_subject,
                original_body=raw_body,
                cleaned_subject=remainder_subject or raw_subject_clean,
                cleaned_body=cleaned_body,
                convention=convention,
                commit_type=commit_type,
                scope=scope,
                is_breaking=is_breaking,
                breaking_description=breaking_description,
            )

        # 2. Check Bracket prefix: [TAG] subject
        bracket_match = _BRACKET_RE.match(raw_subject_clean)
        if bracket_match:
            prefix = bracket_match.group(1).strip()
            remainder_subject = bracket_match.group(2).strip()
            # If colon follows bracket like [AUTH]: subject, strip leading colon
            if remainder_subject.startswith(":"):
                remainder_subject = remainder_subject[1:].strip()

            return NormalizedCommit(
                hash=commit.hash,
                original_subject=raw_subject,
                original_body=raw_body,
                cleaned_subject=remainder_subject or raw_subject_clean,
                cleaned_body=cleaned_body,
                convention=CommitConvention.BRACKET_PREFIX,
                bracket_prefix=prefix,
                is_breaking=is_breaking,
                breaking_description=breaking_description,
            )

        # 3. Check Gitmoji (code or unicode)
        gitmoji_code_match = _GITMOJI_CODE_RE.match(raw_subject_clean)
        if gitmoji_code_match:
            emoji_code = gitmoji_code_match.group(1)
            remainder = gitmoji_code_match.group(2).strip()
            return NormalizedCommit(
                hash=commit.hash,
                original_subject=raw_subject,
                original_body=raw_body,
                cleaned_subject=remainder or raw_subject_clean,
                cleaned_body=cleaned_body,
                convention=CommitConvention.GITMOJI,
                gitmoji=emoji_code,
                is_breaking=is_breaking,
                breaking_description=breaking_description,
            )

        gitmoji_unicode_match = _GITMOJI_UNICODE_RE.match(raw_subject_clean)
        if gitmoji_unicode_match:
            emoji_char = gitmoji_unicode_match.group(1)
            remainder = gitmoji_unicode_match.group(2).strip()
            return NormalizedCommit(
                hash=commit.hash,
                original_subject=raw_subject,
                original_body=raw_body,
                cleaned_subject=remainder or raw_subject_clean,
                cleaned_body=cleaned_body,
                convention=CommitConvention.GITMOJI,
                gitmoji=emoji_char,
                is_breaking=is_breaking,
                breaking_description=breaking_description,
            )

        # 4. Default: Plain descriptive commit
        return NormalizedCommit(
            hash=commit.hash,
            original_subject=raw_subject,
            original_body=raw_body,
            cleaned_subject=raw_subject_clean,
            cleaned_body=cleaned_body,
            convention=CommitConvention.PLAIN,
            is_breaking=is_breaking,
            breaking_description=breaking_description,
        )

    def normalize_many(self, commits: list[HistoricalCommit]) -> list[NormalizedCommit]:
        """Normalize a collection of historical commits."""
        return [self.normalize(c) for c in commits]

    @staticmethod
    def _clean_body(body: str) -> str:
        """Clean and normalize whitespace in commit bodies."""
        if not body:
            return ""
        lines = [line.rstrip() for line in body.splitlines()]
        # Strip leading and trailing empty lines
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)
