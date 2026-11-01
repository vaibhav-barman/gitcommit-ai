"""Deterministic layout of a generated commit message.

Models are unreliable at hard-wrapping text, so the layout rules that can be
applied mechanically are applied here rather than left to the prompt.
"""

from __future__ import annotations

import re
import textwrap

from git_commit_ai.agents.conventions import BODY_WIDTH

_LIST_ITEM_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)")
_FOOTER_RE = re.compile(
    r"^(?:BREAKING[ -]CHANGE|Refs?|Fixes|Closes|Resolves|See|[A-Za-z]+(?:-[A-Za-z]+)+)(?::\s|\s#)"
)
_CODE_INDENT = ("    ", "\t")


def split_message(message: str) -> tuple[str, str]:
    """Split a message into its subject line and the body that follows."""
    lines = message.strip().splitlines()
    if not lines:
        return "", ""
    return lines[0].rstrip(), _trim_blank_lines(lines[1:])


def _trim_blank_lines(lines: list[str]) -> str:
    """Join ``lines`` without leading or trailing blank lines, keeping indentation."""
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return "\n".join(line.rstrip() for line in lines[start:end])


def format_message(message: str) -> str:
    """Normalize ``message``: blank line after the subject, body wrapped at 72 columns.

    Paragraphs are re-flowed, list items keep a hanging indent, trailers
    (``Key: value``, ``BREAKING CHANGE: ...``) continue with a one-space indent,
    indented code blocks are left alone, and a single unbreakable token (such
    as a URL) is never split. The function is idempotent.
    """
    subject, body = split_message(message)
    if not body:
        return subject
    paragraphs = re.split(r"\n\s*\n", body)
    wrapped = "\n".join(_wrap_paragraph(p) + "\n" for p in paragraphs).rstrip("\n")
    return f"{subject}\n\n{wrapped}"


def _wrap_paragraph(paragraph: str) -> str:
    lines = paragraph.splitlines()
    if any(line.startswith(_CODE_INDENT) for line in lines):
        return paragraph
    if _LIST_ITEM_RE.match(lines[0]):
        items = _group(lines, _LIST_ITEM_RE)
        return "\n".join(_wrap_list_item(item) for item in items)
    if _FOOTER_RE.match(lines[0]):
        return "\n".join(
            _fill(entry, subsequent_indent=" ") for entry in _group(lines, _FOOTER_RE)
        )
    return _fill(" ".join(line.strip() for line in lines))


def _group(lines: list[str], starts_entry: re.Pattern[str]) -> list[str]:
    """Join continuation lines onto the entry (list item or trailer) they belong to."""
    entries: list[str] = []
    for line in lines:
        if starts_entry.match(line) or not entries:
            entries.append(line.rstrip())
        else:
            entries[-1] += " " + line.strip()
    return entries


def _wrap_list_item(item: str) -> str:
    marker = _LIST_ITEM_RE.match(item)
    prefix = marker.group(1) if marker else ""
    return _fill(item[len(prefix) :], initial_indent=prefix, subsequent_indent=" " * len(prefix))


def _fill(text: str, initial_indent: str = "", subsequent_indent: str = "") -> str:
    return textwrap.fill(
        text,
        width=BODY_WIDTH,
        initial_indent=initial_indent,
        subsequent_indent=subsequent_indent,
        break_long_words=False,
        break_on_hyphens=False,
    )
