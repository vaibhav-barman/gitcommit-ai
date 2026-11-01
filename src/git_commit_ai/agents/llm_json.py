"""Structured JSON replies from a text-only LLM provider."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, ValidationError

from git_commit_ai.core.exceptions import LLMResponseError
from git_commit_ai.llm.base import LLMMessage, Role

if TYPE_CHECKING:
    from git_commit_ai.llm.base import LLMProvider

ModelT = TypeVar("ModelT", bound=BaseModel)


def complete_json(
    llm: LLMProvider,
    messages: list[LLMMessage],
    model_type: type[ModelT],
    retries: int = 1,
) -> ModelT:
    """Ask the LLM for a JSON object and validate it against ``model_type``.

    On an unparsable or invalid reply the validation error is fed back to the
    model for up to ``retries`` corrective attempts.

    Raises:
        LLMResponseError: If no valid object is produced within the retries.
    """
    conversation = list(messages)
    problem = ""
    for _ in range(retries + 1):
        reply = llm.complete(conversation)
        try:
            return model_type.model_validate(json.loads(_extract_object(reply)))
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            problem = str(exc)
            conversation += [
                LLMMessage(role=Role.ASSISTANT, content=reply),
                LLMMessage(
                    role=Role.USER,
                    content=f"That reply was not valid ({problem}). "
                    "Reply again with only the corrected JSON object.",
                ),
            ]
    raise LLMResponseError(f"Model did not return valid {model_type.__name__} JSON: {problem}")


def _extract_object(reply: str) -> str:
    """Return the outermost ``{...}`` span, tolerating prose and code fences."""
    start = reply.find("{")
    end = reply.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object found in reply")
    return reply[start : end + 1]
