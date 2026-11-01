"""Deterministic LLM double for tests and offline runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_commit_ai.core.exceptions import LLMResponseError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from git_commit_ai.llm.base import LLMMessage


class FakeLLM:
    """Replays scripted replies without any network access.

    Args:
        script: Either a sequence of replies returned in order, or a callable
            that maps the incoming messages to a reply. A sequence that runs
            out raises ``LLMResponseError`` so tests fail loudly instead of
            silently reusing a reply.
    """

    name = "fake"

    def __init__(
        self,
        script: Sequence[str] | Callable[[Sequence[LLMMessage]], str],
        model: str = "fake-model",
    ) -> None:
        self._script = script
        self._model = model
        self._cursor = 0
        self.calls: list[list[LLMMessage]] = []

    @property
    def model(self) -> str:
        """Model identifier reported by the fake."""
        return self._model

    def complete(self, messages: Sequence[LLMMessage]) -> str:
        """Record the call and return the next scripted reply."""
        self.calls.append(list(messages))
        if callable(self._script):
            return self._script(messages)
        if self._cursor >= len(self._script):
            raise LLMResponseError("FakeLLM script exhausted")
        reply = self._script[self._cursor]
        self._cursor += 1
        return reply
