"""Unit tests for the provider protocol and FakeLLM."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from git_commit_ai.core.exceptions import LLMResponseError
from git_commit_ai.llm import FakeLLM, LLMMessage, LLMProvider, Role


def _user(text: str) -> list[LLMMessage]:
    return [LLMMessage(role=Role.USER, content=text)]


def test_fake_satisfies_provider_protocol() -> None:
    assert isinstance(FakeLLM(["x"]), LLMProvider)


def test_sequence_replies_in_order_and_records_calls() -> None:
    llm = FakeLLM(["first", "second"])
    assert llm.complete(_user("a")) == "first"
    assert llm.complete(_user("b")) == "second"
    assert [call[0].content for call in llm.calls] == ["a", "b"]


def test_exhausted_sequence_raises() -> None:
    llm = FakeLLM(["only"])
    llm.complete(_user("a"))
    with pytest.raises(LLMResponseError, match="exhausted"):
        llm.complete(_user("b"))


def test_callable_script_sees_messages() -> None:
    def echo(messages: Sequence[LLMMessage]) -> str:
        return messages[-1].content.upper()

    llm = FakeLLM(echo, model="custom")
    assert llm.complete(_user("hi")) == "HI"
    assert llm.model == "custom"
    assert llm.name == "fake"
