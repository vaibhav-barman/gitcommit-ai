"""Whether this session can ask questions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import TextIO


def is_interactive(env: Mapping[str, str], stdin: TextIO, stdout: TextIO) -> bool:
    """True when stdin and stdout are terminals, ``TERM`` is not ``dumb`` and ``CI`` is
    unset or empty. Requiring stdout keeps menus out of ``--dry-run | pbcopy``."""
    if env.get("CI") or env.get("TERM") == "dumb":
        return False
    try:
        return stdin.isatty() and stdout.isatty()
    except ValueError:  # a closed stream
        return False
