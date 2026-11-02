"""The CLI test helpers themselves."""

from __future__ import annotations

from tests.fixtures.cli import plain


def test_plain_removes_colour_and_cursor_codes() -> None:
    styled = "\x1b[1;36m-\x1b[0m\x1b[1;36m-dry\x1b[0m\x1b[1;36m-run\x1b[0m \x1b[?25h\x1b[2Kdone"
    assert plain(styled) == "--dry-run done"


def test_plain_leaves_plain_text_alone() -> None:
    assert plain("--dry-run [default]") == "--dry-run [default]"
