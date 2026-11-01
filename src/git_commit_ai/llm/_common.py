"""Helpers shared by the provider backends."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_commit_ai.core.exceptions import LLMResponseError
from git_commit_ai.llm.base import LLMMessage, Role

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def split_system(messages: Sequence[LLMMessage]) -> tuple[str | None, list[LLMMessage]]:
    """Separate system instructions from the conversation turns.

    Anthropic and Gemini take the system prompt out-of-band, so backends
    that need it separated share this helper.
    """
    system_parts = [m.content for m in messages if m.role is Role.SYSTEM]
    turns = [m for m in messages if m.role is not Role.SYSTEM]
    return ("\n\n".join(system_parts) or None), turns


def require_text(provider: str, text: str | None) -> str:
    """Return stripped completion text or raise if the provider sent none."""
    if text is None or not text.strip():
        raise LLMResponseError(f"Provider '{provider}' returned an empty response")
    return text.strip()


def parse_retry_after(headers: Mapping[str, str] | None) -> float | None:
    """Read a numeric ``retry-after`` header, ignoring HTTP-date values."""
    if headers is None:
        return None
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def dig(data: object, *path: str | int) -> object:
    """Follow keys and list indexes into decoded JSON; ``None`` if any step is missing."""
    current = data
    for step in path:
        if isinstance(step, int):
            if not isinstance(current, list) or not -len(current) <= step < len(current):
                return None
            current = current[step]
        elif isinstance(current, dict):
            current = current.get(step)
        else:
            return None
    return current


def text_or_none(value: object) -> str | None:
    """``value`` if it is a string, else ``None``."""
    return value if isinstance(value, str) else None
