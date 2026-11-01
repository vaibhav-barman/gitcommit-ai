"""A ``Prompter`` that answers from a script, so the wizard runs with no terminal."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from git_commit_ai.core.exceptions import SetupCancelledError
from git_commit_ai.onboarding.prompts import Choice

T = TypeVar("T")
CTRL_C = object()  # an answer that presses Ctrl+C at that prompt


@dataclass
class Asked:
    """One prompt the code under test showed."""

    kind: str
    message: str
    choices: list[Choice[Any]] = field(default_factory=list)
    default: object = None

    @property
    def values(self) -> list[object]:
        """The values of the offered choices."""
        return [c.value for c in self.choices]

    @property
    def labels(self) -> list[str]:
        """The labels of the offered choices."""
        return [c.label for c in self.choices]


class ScriptedPrompter:
    """Returns ``answers`` in order. A select answer must be one of the offered values.
    ``CTRL_C`` raises ``SetupCancelledError``. Running out of answers fails the test, and
    so does ending with answers left over (``assert_done``)."""

    def __init__(self, answers: Sequence[object]) -> None:
        self._answers = list(answers)
        self.asked: list[Asked] = []
        self.lines: list[str] = []

    def select(
        self,
        message: str,
        choices: Sequence[Choice[T]],
        default: T | None = None,
        searchable: bool = False,
    ) -> T:
        answer = self._next(Asked("select", message, list(choices), default))
        values = [c.value for c in choices]
        assert answer in values, f"{answer!r} is not offered by {message!r}: {values}"
        return choices[values.index(answer)].value

    def secret(self, message: str) -> str:
        answer = self._next(Asked("secret", message))
        assert isinstance(answer, str)
        return answer.strip()

    def text(
        self,
        message: str,
        default: str | None = None,
        validate: Callable[[str], str | None] | None = None,
    ) -> str:
        while True:
            answer = self._next(Asked("text", message, default=default))
            assert isinstance(answer, str)
            problem = validate(answer.strip()) if validate else None
            if problem is None:
                return answer.strip() or (default or "")
            self.lines.append(problem)

    def confirm(self, message: str, default: bool = False) -> bool:
        answer = self._next(Asked("confirm", message, default=default))
        assert isinstance(answer, bool)
        return answer

    def note(self, message: str) -> None:
        self.lines.append(message)

    @contextlib.contextmanager
    def status(self, message: str) -> Iterator[Callable[[str], None]]:
        index = len(self.lines)
        self.lines.append(f"{message}...")

        def done(result: str) -> None:
            self.lines[index] = f"{message}... {result}"

        yield done

    @property
    def output(self) -> str:
        """Every note and status line, in order."""
        return "\n".join(self.lines)

    def messages(self, kind: str | None = None) -> list[str]:
        """Messages of the prompts shown, optionally of one kind."""
        return [a.message for a in self.asked if kind is None or a.kind == kind]

    def assert_done(self) -> None:
        """Fail if some scripted answers were never asked for."""
        assert self._answers == [], f"unused answers: {self._answers!r}"

    def _next(self, asked: Asked) -> object:
        self.asked.append(asked)
        self.lines.append(f"? {asked.message}")
        assert self._answers, f"unexpected prompt after the script ended: {asked.message!r}"
        answer = self._answers.pop(0)
        if answer is CTRL_C:
            raise SetupCancelledError
        return answer
