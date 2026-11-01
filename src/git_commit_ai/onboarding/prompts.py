"""How setup asks questions: arrow-key menus with number shortcuts through questionary,
or a plain numbered prompt when prompt_toolkit cannot start (for example Git Bash
without winpty). Prompts and status lines go to stderr, so stdout stays clean."""

from __future__ import annotations

import contextlib
import getpass as getpass_module
import sys
import warnings
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Protocol, TextIO, TypeVar

import questionary
from prompt_toolkit.input import create_input
from prompt_toolkit.output import create_output

from git_commit_ai.core.exceptions import SetupCancelledError

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from prompt_toolkit.input import Input
    from prompt_toolkit.output import Output

T = TypeVar("T")
_MAX_SHORTCUTS = 36
_SEARCH_INSTRUCTION = "(Use arrow keys, type to filter)"
_ECHO_WARNING = (
    "This terminal can't hide what you type, so the key would be visible on screen. "
    "You can instead pipe it in: git ai-commit key --stdin"
)

Validator = Callable[[str], "str | None"]


@dataclass(frozen=True)
class Choice(Generic[T]):
    """One menu row: the value returned, its label, and a hint shown beside it."""

    value: T
    label: str
    hint: str = ""


class Prompter(Protocol):
    """Everything setup needs from a terminal. Every method raises
    ``SetupCancelledError`` on Ctrl+C, Ctrl+D or end of input."""

    def select(
        self,
        message: str,
        choices: Sequence[Choice[T]],
        default: T | None = None,
        searchable: bool = False,
    ) -> T:
        """Pick one choice; ``searchable`` lists accept type-to-filter."""
        ...

    def secret(self, message: str) -> str:
        """Read hidden input, stripped."""
        ...

    def text(
        self, message: str, default: str | None = None, validate: Validator | None = None
    ) -> str:
        """Read a line; ``validate`` returns an error message, or ``None`` to accept."""
        ...

    def confirm(self, message: str, default: bool = False) -> bool:
        """Ask yes or no."""
        ...

    def note(self, message: str) -> None:
        """Print a line to stderr."""
        ...

    def status(self, message: str) -> AbstractContextManager[Callable[[str], None]]:
        """Print ``message... `` and yield a callback that finishes the line."""
        ...


class _StatusLines:
    """``note`` and ``status``, shared by both prompters."""

    def __init__(self, err: TextIO) -> None:
        self._err = err

    def note(self, message: str) -> None:
        """Print a line to stderr."""
        print(message, file=self._err, flush=True)

    @contextlib.contextmanager
    def status(self, message: str) -> Iterator[Callable[[str], None]]:
        """Print ``message...`` then whatever result the callback is given."""
        self._err.write(f"{message}...")
        self._err.flush()
        finished = False

        def done(result: str) -> None:
            nonlocal finished
            finished = True
            self._err.write(f" {result}\n")
            self._err.flush()

        try:
            yield done
        finally:
            if not finished:
                self._err.write("\n")
                self._err.flush()


class QuestionaryPrompter(_StatusLines):
    """Menus with arrow keys and number shortcuts, hidden password input, and
    type-to-filter for long lists."""

    def __init__(
        self, input: Input | None = None, output: Output | None = None, err: TextIO | None = None
    ) -> None:
        super().__init__(err or sys.stderr)
        self._io: dict[str, Any] = {"output": output or create_output(stdout=sys.stderr)}
        if input is not None:
            self._io["input"] = input

    def select(
        self,
        message: str,
        choices: Sequence[Choice[T]],
        default: T | None = None,
        searchable: bool = False,
    ) -> T:
        """Arrow keys and Enter; numbers jump to a row when there are 36 rows or fewer."""
        width = max(len(c.label) for c in choices)
        rows = [
            questionary.Choice(
                title=f"{c.label:<{width}}   {c.hint}" if c.hint else c.label, value=index
            )
            for index, c in enumerate(choices)
        ]
        start = next((rows[i] for i, c in enumerate(choices) if c.value == default), None)
        shortcuts = not searchable and len(choices) <= _MAX_SHORTCUTS
        question = questionary.select(
            message,
            choices=rows,
            default=start,
            use_shortcuts=shortcuts,
            use_search_filter=searchable,
            use_jk_keys=False,
            instruction=_SEARCH_INSTRUCTION if searchable else None,
            **self._io,
        )
        index: int = _ask(question)
        return choices[index].value

    def secret(self, message: str) -> str:
        """Hidden input."""
        answer: str = _ask(questionary.password(message, **self._io))
        return answer.strip()

    def text(
        self, message: str, default: str | None = None, validate: Validator | None = None
    ) -> str:
        """A line of text."""

        def check(value: str) -> bool | str:
            problem = validate(value.strip()) if validate else None
            return problem or True

        answer: str = _ask(
            questionary.text(message, default=default or "", validate=check, **self._io)
        )
        return answer.strip()

    def confirm(self, message: str, default: bool = False) -> bool:
        """Yes or no."""
        answer: bool = _ask(questionary.confirm(message, default=default, **self._io))
        return answer


class PlainPrompter(_StatusLines):
    """Numbered menus read with ``input``-style lines, for terminals prompt_toolkit cannot
    drive. Secrets go through ``getpass``; if it would echo, the user is warned first."""

    def __init__(
        self,
        stdin: TextIO | None = None,
        err: TextIO | None = None,
        getpass: Callable[..., str] = getpass_module.getpass,
    ) -> None:
        super().__init__(err or sys.stderr)
        self._in = stdin or sys.stdin
        self._getpass = getpass

    def select(
        self,
        message: str,
        choices: Sequence[Choice[T]],
        default: T | None = None,
        searchable: bool = False,
    ) -> T:
        """Show numbered rows and read a number; searchable lists also take filter text."""
        shown = list(choices)
        default_index = next((i for i, c in enumerate(shown) if c.value == default), 0)
        while True:
            self._show(message, shown)
            hint = ", or type text to filter" if searchable else ""
            answer = self._line(f"Choose 1-{len(shown)}{hint} [{default_index + 1}]: ")
            if not answer:
                return shown[default_index].value
            if answer.isdigit() and 1 <= int(answer) <= len(shown):
                return shown[int(answer) - 1].value
            if searchable and not answer.isdigit():
                matches = [c for c in choices if answer.lower() in f"{c.label} {c.hint}".lower()]
                if matches:
                    shown, default_index = matches, 0
                else:
                    self.note(f"Nothing matches '{answer}'.")
                    shown, default_index = list(choices), 0
                continue
            self.note(f"Enter a number from 1 to {len(shown)}.")

    def secret(self, message: str) -> str:
        """Hidden input through ``getpass``."""
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass_module.GetPassWarning)
            try:
                return self._getpass(f"{message} ", stream=self._err).strip()
            except getpass_module.GetPassWarning:
                pass
            except (KeyboardInterrupt, EOFError):
                raise SetupCancelledError from None
        self.note(_ECHO_WARNING)
        if not self.confirm("Type the key anyway?", default=False):
            raise SetupCancelledError
        return self._line(f"{message} ")

    def text(
        self, message: str, default: str | None = None, validate: Validator | None = None
    ) -> str:
        """A line of text; empty input takes ``default``."""
        suffix = f" [{default}]" if default else ""
        while True:
            answer = self._line(f"{message}{suffix} ") or (default or "")
            problem = validate(answer) if validate else None
            if problem is None:
                return answer
            self.note(problem)

    def confirm(self, message: str, default: bool = False) -> bool:
        """``y`` or ``n``; empty input takes ``default``."""
        options = "Y/n" if default else "y/N"
        while True:
            answer = self._line(f"{message} ({options}) ").lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.note("Answer y or n.")

    def _show(self, message: str, choices: Sequence[Choice[Any]]) -> None:
        self.note(f"? {message}")
        width = max(len(c.label) for c in choices)
        for number, choice in enumerate(choices, 1):
            hint = f"   {choice.hint}" if choice.hint else ""
            self.note(f"  {number:>2}) {choice.label:<{width}}{hint}".rstrip())

    def _line(self, prompt: str) -> str:
        self._err.write(prompt)
        self._err.flush()
        try:
            line = self._in.readline()
        except KeyboardInterrupt:
            raise SetupCancelledError from None
        if not line:
            raise SetupCancelledError
        return line.strip()


def make_prompter() -> Prompter:
    """Questionary menus when prompt_toolkit can drive this terminal, else plain prompts."""
    try:
        return QuestionaryPrompter(input=create_input(), output=create_output(stdout=sys.stderr))
    except Exception:  # prompt_toolkit raises console-specific errors (e.g. on mintty)
        return PlainPrompter()


def _ask(question: questionary.Question) -> Any:
    try:
        return question.unsafe_ask()
    except (KeyboardInterrupt, EOFError):
        raise SetupCancelledError from None
