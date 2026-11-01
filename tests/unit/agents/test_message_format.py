"""Unit tests for deterministic message layout."""

from __future__ import annotations

import pytest

from git_commit_ai.agents.conventions import BODY_WIDTH
from git_commit_ai.agents.message_format import format_message, split_message

LONG = (
    "This change lets callers bound the size of each response so that slow "
    "connections do not stall while a very large page is transferred."
)


def _body_lines(message: str) -> list[str]:
    return message.split("\n\n", 1)[1].splitlines()


class TestSplit:
    def test_subject_and_body(self) -> None:
        assert split_message("feat: x\n\nwhy\nmore") == ("feat: x", "why\nmore")

    def test_subject_only(self) -> None:
        assert split_message("  feat: x  \n") == ("feat: x", "")

    def test_empty(self) -> None:
        assert split_message("  \n ") == ("", "")


class TestFormat:
    def test_subject_only_is_returned_trimmed(self) -> None:
        assert format_message("  feat: add x \n\n") == "feat: add x"

    def test_long_paragraph_is_wrapped_at_72_columns(self) -> None:
        lines = _body_lines(format_message(f"feat: add x\n\n{LONG}"))
        assert len(lines) > 1
        assert all(len(line) <= BODY_WIDTH for line in lines)
        assert " ".join(lines) == LONG

    def test_blank_line_is_inserted_after_the_subject(self) -> None:
        assert format_message("feat: add x\nBecause.") == "feat: add x\n\nBecause."

    def test_paragraph_breaks_are_kept(self) -> None:
        assert format_message("feat: x\n\nFirst.\n\n\nSecond.") == "feat: x\n\nFirst.\n\nSecond."

    def test_hard_wrapped_input_is_reflowed(self) -> None:
        assert format_message("feat: x\n\nOne\ntwo\nthree") == "feat: x\n\nOne two three"

    def test_list_items_get_a_hanging_indent(self) -> None:
        message = format_message(f"feat: x\n\n- {LONG}\n- short item")
        lines = _body_lines(message)
        assert lines[0].startswith("- This change")
        assert lines[1].startswith("  ")
        assert lines[-1] == "- short item"
        assert all(len(line) <= BODY_WIDTH for line in lines)

    def test_numbered_items_are_wrapped_with_matching_indent(self) -> None:
        lines = _body_lines(format_message(f"feat: x\n\n1. {LONG}"))
        assert lines[1].startswith("   ")

    def test_trailers_stay_one_per_line(self) -> None:
        body = "Co-authored-by: A <a@example.com>\nSigned-off-by: B <b@example.com>"
        assert format_message(f"feat: x\n\n{body}") == f"feat: x\n\n{body}"

    def test_breaking_change_footer_wraps_with_a_one_space_indent(self) -> None:
        message = format_message(f"feat!: x\n\nBREAKING CHANGE: {LONG}")
        lines = _body_lines(message)
        assert lines[0].startswith("BREAKING CHANGE: This change")
        assert lines[1].startswith(" ")
        assert all(len(line) <= BODY_WIDTH for line in lines)

    def test_prose_starting_with_a_word_and_colon_is_not_mistaken_for_a_trailer(self) -> None:
        lines = _body_lines(format_message(f"feat: x\n\nNote: {LONG}"))
        assert not lines[1].startswith(" ")

    def test_unbreakable_tokens_are_not_split(self) -> None:
        url = "https://example.com/" + "a" * 90
        assert format_message(f"feat: x\n\n{url}") == f"feat: x\n\n{url}"

    def test_indented_code_blocks_are_untouched(self) -> None:
        code = "    def very_long_function_name(argument_one, argument_two, argument_three): pass"
        assert format_message(f"feat: x\n\n{code}") == f"feat: x\n\n{code}"

    @pytest.mark.parametrize(
        "message",
        [
            f"feat: x\n\n{LONG}",
            f"feat: x\n\n- {LONG}\n- b",
            f"feat!: x\n\nBREAKING CHANGE: {LONG}",
            "feat: x\n\nIntro.\n\n- one\n- two\n\nRefs #12",
        ],
    )
    def test_formatting_is_idempotent(self, message: str) -> None:
        once = format_message(message)
        assert format_message(once) == once
