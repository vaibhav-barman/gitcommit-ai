"""A small TOML writer for the flat, fully known schema of our config files.

It writes strings, integers, finite floats and booleans, at the top level and in
tables nested at most two deep (``[generation]``, ``[providers.ollama]``).
Anything else raises, and every result is parsed back to prove it round-trips.
"""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Mapping
from typing import Any

TomlValue = str | int | float | bool
TomlTable = Mapping[str, TomlValue | Mapping[str, TomlValue | Mapping[str, TomlValue]]]

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}
_MAX_DEPTH = 2


def dumps(data: TomlTable) -> str:
    """Serialise ``data`` as TOML.

    Raises:
        TypeError: For a value that is not a string, int, float, bool or table, or
            for tables nested more than two deep.
        ValueError: For a non-finite float, or output that does not round-trip.
    """
    lines: list[str] = []
    _write_table(lines, (), data)
    text = "\n".join(lines).lstrip("\n") + "\n" if lines else ""
    if tomllib.loads(text) != _plain(data):
        raise ValueError("TOML output did not round-trip")
    return text


def _write_table(lines: list[str], path: tuple[str, ...], table: Mapping[str, Any]) -> None:
    scalars = [(k, v) for k, v in table.items() if not isinstance(v, Mapping)]
    tables = [(k, v) for k, v in table.items() if isinstance(v, Mapping)]
    if path and scalars:
        lines.append("")
        lines.append("[" + ".".join(_key(part) for part in path) + "]")
    lines.extend(f"{_key(key)} = {_value(value)}" for key, value in scalars)
    for key, sub in tables:
        if len(path) >= _MAX_DEPTH:
            raise TypeError(f"tables nested deeper than {_MAX_DEPTH} levels are not supported")
        _write_table(lines, (*path, key), sub)


def _key(key: str) -> str:
    return key if _BARE_KEY.match(key) else _string(key)


def _value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats are not supported")
        return repr(value)
    if isinstance(value, str):
        return _string(value)
    raise TypeError(f"unsupported TOML value of type {type(value).__name__}")


def _string(text: str) -> str:
    out = []
    for char in text:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _plain(table: Mapping[str, Any]) -> dict[str, Any]:
    plain: dict[str, Any] = {}
    for key, value in table.items():
        if isinstance(value, Mapping):
            if nested := _plain(value):
                plain[key] = nested
        else:
            plain[key] = value
    return plain
