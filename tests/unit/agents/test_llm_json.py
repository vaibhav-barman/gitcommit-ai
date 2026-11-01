"""Unit tests for structured JSON replies."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from git_commit_ai.agents.llm_json import complete_json
from git_commit_ai.core.exceptions import LLMResponseError
from git_commit_ai.llm import FakeLLM, LLMMessage, Role


class Answer(BaseModel):
    value: int


PROMPT = [LLMMessage(role=Role.USER, content="go")]


def test_parses_plain_json() -> None:
    assert complete_json(FakeLLM(['{"value": 3}']), PROMPT, Answer).value == 3


def test_tolerates_code_fences_and_prose() -> None:
    reply = 'Sure!\n```json\n{"value": 4}\n```\nHope that helps.'
    assert complete_json(FakeLLM([reply]), PROMPT, Answer).value == 4


def test_retries_once_with_the_validation_error_fed_back() -> None:
    llm = FakeLLM(['{"value": "nope"}', '{"value": 5}'])
    assert complete_json(llm, PROMPT, Answer).value == 5
    retry = llm.calls[1]
    assert retry[-2].role is Role.ASSISTANT
    assert "not valid" in retry[-1].content


def test_gives_up_after_retries_are_spent() -> None:
    llm = FakeLLM(["not json", "still not json"])
    with pytest.raises(LLMResponseError, match="Answer"):
        complete_json(llm, PROMPT, Answer)
    assert len(llm.calls) == 2


def test_zero_retries_makes_a_single_attempt() -> None:
    llm = FakeLLM(["nope", '{"value": 1}'])
    with pytest.raises(LLMResponseError):
        complete_json(llm, PROMPT, Answer, retries=0)
    assert len(llm.calls) == 1
