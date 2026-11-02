"""Prompt adapters: the plain numbered fallback, the questionary menus, and the choice
between them. Neither needs a real terminal here."""

from __future__ import annotations

import getpass
import io
from collections.abc import Iterator

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.output import DummyOutput

from git_commit_ai.core.exceptions import SetupCancelledError
from git_commit_ai.onboarding import prompts
from git_commit_ai.onboarding.prompts import (
    Choice,
    PlainPrompter,
    QuestionaryPrompter,
    make_prompter,
)

CHOICES = [Choice("a", "Apple", "red"), Choice("b", "Banana"), Choice("c", "Cherry", "dark")]


def _plain(text: str, secret: str = "") -> tuple[PlainPrompter, io.StringIO]:
    err = io.StringIO()

    def fake_getpass(prompt: str, stream: object = None) -> str:
        err.write(prompt)
        return secret

    return PlainPrompter(stdin=io.StringIO(text), err=err, getpass=fake_getpass), err


class TestPlainPrompter:
    def test_numbered_selection(self) -> None:
        prompter, err = _plain("2\n")
        assert prompter.select("Pick one", CHOICES) == "b"
        shown = err.getvalue()
        assert "Pick one" in shown
        assert "1) Apple" in shown
        assert "red" in shown
        assert "3) Cherry" in shown

    def test_out_of_range_and_junk_are_asked_again(self) -> None:
        prompter, err = _plain("9\nzz\n0\n3\n")
        assert prompter.select("Pick", CHOICES) == "c"
        assert err.getvalue().count("Enter a number from 1 to 3") == 3

    def test_empty_input_picks_the_default(self) -> None:
        prompter, err = _plain("\n")
        assert prompter.select("Pick", CHOICES, default="c") == "c"
        assert "[3]" in err.getvalue()

    def test_empty_input_without_a_default_picks_the_first(self) -> None:
        prompter, _ = _plain("\n")
        assert prompter.select("Pick", CHOICES) == "a"

    def test_searchable_lists_filter_by_text(self) -> None:
        prompter, err = _plain("an\n1\n")
        assert prompter.select("Pick", CHOICES, searchable=True) == "b"
        assert "type text to filter" in err.getvalue()

    def test_a_filter_matching_nothing_shows_everything_again(self) -> None:
        prompter, err = _plain("zzz\n2\n")
        assert prompter.select("Pick", CHOICES, searchable=True) == "b"
        assert "Nothing matches" in err.getvalue()

    @pytest.mark.parametrize("method", ["select", "text", "confirm"])
    def test_eof_cancels(self, method: str) -> None:
        prompter, _ = _plain("")
        with pytest.raises(SetupCancelledError):
            if method == "select":
                prompter.select("Pick", CHOICES)
            elif method == "text":
                prompter.text("Name")
            else:
                prompter.confirm("Sure?")

    def test_text_uses_the_default_and_validates(self) -> None:
        prompter, err = _plain("\nbad\ngood\n")
        assert prompter.text("Name", default="dflt") == "dflt"
        assert prompter.text("Name", validate=lambda v: "nope" if v == "bad" else None) == "good"
        assert "nope" in err.getvalue()

    @pytest.mark.parametrize(
        ("answer", "default", "expected"),
        [("y\n", False, True), ("no\n", True, False), ("\n", True, True), ("\n", False, False)],
    )
    def test_confirm(self, answer: str, default: bool, expected: bool) -> None:
        prompter, _ = _plain(answer)
        assert prompter.confirm("Sure?", default=default) is expected

    def test_confirm_asks_again_on_junk(self) -> None:
        prompter, err = _plain("maybe\ny\n")
        assert prompter.confirm("Sure?") is True
        assert "Answer y or n" in err.getvalue()

    def test_secret_uses_getpass(self) -> None:
        prompter, err = _plain("", secret="  hidden  ")
        assert prompter.secret("Key:") == "hidden"
        assert "Key:" in err.getvalue()

    def test_secret_warns_when_input_would_echo(self) -> None:
        err = io.StringIO()

        def echoing(prompt: str, stream: object = None) -> str:
            import warnings

            warnings.warn("Can not control echo", getpass.GetPassWarning, stacklevel=2)
            return "unreachable"

        prompter = PlainPrompter(stdin=io.StringIO("y\ntyped-key\n"), err=err, getpass=echoing)
        assert prompter.secret("Key:") == "typed-key"
        assert "can't hide what you type" in err.getvalue()
        assert "--stdin" in err.getvalue()

    def test_declining_an_echoing_secret_cancels(self) -> None:
        def echoing(prompt: str, stream: object = None) -> str:
            import warnings

            warnings.warn("Can not control echo", getpass.GetPassWarning, stacklevel=2)
            return "unreachable"

        prompter = PlainPrompter(stdin=io.StringIO("n\n"), err=io.StringIO(), getpass=echoing)
        with pytest.raises(SetupCancelledError):
            prompter.secret("Key:")

    def test_ctrl_c_in_getpass_cancels(self) -> None:
        def interrupted(prompt: str, stream: object = None) -> str:
            raise KeyboardInterrupt

        prompter = PlainPrompter(stdin=io.StringIO(), err=io.StringIO(), getpass=interrupted)
        with pytest.raises(SetupCancelledError):
            prompter.secret("Key:")

    def test_note_and_status_go_to_the_error_stream(self) -> None:
        prompter, err = _plain("")
        prompter.note("hello")
        with prompter.status("Checking the key") as done:
            done("ok")
        with prompter.status("Silent"):
            pass
        assert err.getvalue() == "hello\nChecking the key... ok\nSilent...\n"


@pytest.fixture
def pipe() -> Iterator[PipeInput]:
    with create_pipe_input() as pipe_input:
        yield pipe_input


def _questionary(pipe: PipeInput) -> tuple[QuestionaryPrompter, io.StringIO]:
    err = io.StringIO()
    return QuestionaryPrompter(input=pipe, output=DummyOutput(), err=err), err


class TestQuestionaryPrompter:
    def test_arrow_and_enter(self, pipe: PipeInput) -> None:
        pipe.send_text("\x1b[B\r")
        assert _questionary(pipe)[0].select("Pick", CHOICES) == "b"

    def test_default_row(self, pipe: PipeInput) -> None:
        pipe.send_text("\r")
        assert _questionary(pipe)[0].select("Pick", CHOICES, default="c") == "c"

    def test_number_shortcut(self, pipe: PipeInput) -> None:
        pipe.send_text("3\r")
        assert _questionary(pipe)[0].select("Pick", CHOICES) == "c"

    def test_type_to_filter(self, pipe: PipeInput) -> None:
        pipe.send_text("cher\r")
        assert _questionary(pipe)[0].select("Pick", CHOICES, searchable=True) == "c"

    def test_password(self, pipe: PipeInput) -> None:
        pipe.send_text("s3cret\r")
        assert _questionary(pipe)[0].secret("Key:") == "s3cret"

    def test_text_and_confirm(self, pipe: PipeInput) -> None:
        pipe.send_text("value\r")
        prompter, _ = _questionary(pipe)
        assert prompter.text("Name") == "value"
        pipe.send_text("y")
        assert prompter.confirm("Sure?") is True

    def test_ctrl_c_cancels_a_menu(self, pipe: PipeInput) -> None:
        pipe.send_text("\x03")
        with pytest.raises(SetupCancelledError):
            _questionary(pipe)[0].select("Pick", CHOICES)

    @pytest.mark.parametrize("key", ["\x03", "\x04"])
    def test_ctrl_c_and_ctrl_d_cancel_text_input(self, pipe: PipeInput, key: str) -> None:
        pipe.send_text(key)
        with pytest.raises(SetupCancelledError):
            _questionary(pipe)[0].secret("Key:")

    def test_more_choices_than_shortcut_keys_still_work(self, pipe: PipeInput) -> None:
        many = [Choice(i, f"Item {i}") for i in range(40)]
        pipe.send_text("\x1b[B\x1b[B\r")
        assert _questionary(pipe)[0].select("Pick", many) == 2


class TestMakePrompter:
    def test_falls_back_when_the_terminal_cannot_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_console(*args: object, **kwargs: object) -> object:
            raise OSError("No Windows console found. Are you running cmd.exe?")

        monkeypatch.setattr(prompts, "create_output", no_console)
        assert isinstance(make_prompter(), PlainPrompter)

    def test_uses_questionary_when_it_can(
        self, monkeypatch: pytest.MonkeyPatch, pipe: PipeInput
    ) -> None:
        monkeypatch.setattr(prompts, "create_output", lambda **_: DummyOutput())
        monkeypatch.setattr(prompts, "create_input", lambda: pipe)
        assert isinstance(make_prompter(), QuestionaryPrompter)
