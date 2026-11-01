"""Provider-neutral LLM interface."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Sequence


class Role(enum.StrEnum):
    """Author of a chat message."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class LLMMessage(BaseModel):
    """A single chat message."""

    role: Role
    content: str


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal contract every LLM backend implements.

    Backends translate ``LLMMessage`` sequences to their SDK, return the
    text of the completion, and raise ``LLMError`` subclasses for failures.
    """

    @property
    def name(self) -> str:
        """Short provider identifier, for example ``"openai"``."""
        ...

    @property
    def model(self) -> str:
        """Model identifier the provider sends requests to."""
        ...

    def complete(self, messages: Sequence[LLMMessage]) -> str:
        """Return the assistant's reply for the given conversation."""
        ...
