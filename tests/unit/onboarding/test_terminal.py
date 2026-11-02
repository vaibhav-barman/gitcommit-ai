"""When a session counts as interactive (plan D10)."""

from __future__ import annotations

import io

import pytest

from git_commit_ai.onboarding.terminal import is_interactive


class FakeTty(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_both_ttys_and_a_normal_terminal_are_interactive() -> None:
    assert is_interactive({"TERM": "xterm-256color"}, FakeTty(True), FakeTty(True))


@pytest.mark.parametrize(
    ("env", "stdin", "stdout"),
    [
        ({}, False, True),
        ({}, True, False),
        ({"TERM": "dumb"}, True, True),
        ({"CI": "true"}, True, True),
        ({"CI": "1"}, True, True),
    ],
)
def test_each_condition_alone_makes_it_non_interactive(
    env: dict[str, str], stdin: bool, stdout: bool
) -> None:
    assert not is_interactive(env, FakeTty(stdin), FakeTty(stdout))


def test_an_empty_ci_variable_does_not_count() -> None:
    assert is_interactive({"CI": ""}, FakeTty(True), FakeTty(True))


def test_a_closed_stream_is_not_interactive() -> None:
    stream = io.StringIO()
    stream.close()
    assert not is_interactive({}, stream, FakeTty(True))
