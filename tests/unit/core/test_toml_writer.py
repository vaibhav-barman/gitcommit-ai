"""The restricted TOML writer round-trips everything our config files hold."""

from __future__ import annotations

import datetime
import random
import tomllib
from typing import Any

import pytest

from git_commit_ai.core.toml_writer import dumps


def _round_trip(data: dict[str, Any]) -> dict[str, Any]:
    return tomllib.loads(dumps(data))


@pytest.mark.parametrize(
    "value", ["text", "", 0, -7, 2**40, 0.3, -1.5, 1e-9, True, False, "ünïcødé ✓", "日本語"]
)
def test_scalars_round_trip(value: Any) -> None:
    assert _round_trip({"key": value}) == {"key": value}


@pytest.mark.parametrize(
    "value",
    ['say "hi"', "back\\slash", "line\nbreak", "tab\there", "cr\rlf", "\x00\x1f\x7f", "\b\f"],
)
def test_strings_are_escaped(value: str) -> None:
    assert _round_trip({"key": value}) == {"key": value}


def test_tables_follow_the_top_level_keys() -> None:
    data: dict[str, Any] = {
        "schema_version": 1,
        "providers": {"anthropic": {"model": "m"}, "ollama": {"base_url": "http://x"}},
        "generation": {"temperature": 0.5},
        "provider": "anthropic",
    }
    text = dumps(data)
    assert _round_trip(data) == data
    assert text.index("provider =") < text.index("[providers.anthropic]")
    assert "[generation]" in text


def test_empty_tables_are_omitted() -> None:
    assert dumps({"a": 1, "providers": {}, "generation": {}}) == "a = 1\n"


def test_keys_that_are_not_bare_are_quoted() -> None:
    data = {"has space": 1, "dotted.key": {"ünï": "x"}}
    assert _round_trip(data) == data


@pytest.mark.parametrize(
    "value",
    [[1, 2], None, datetime.date(2026, 1, 1), float("nan"), float("inf"), {"a": {"b": {}}}],
)
def test_rejects_unsupported_values(value: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        dumps({"key": {"inner": value}} if isinstance(value, dict) else {"key": value})


def test_random_strings_round_trip() -> None:
    rng = random.Random(20261102)
    alphabet = [chr(c) for c in range(0x0, 0x3000) if not 0xD800 <= c <= 0xDFFF]
    for _ in range(300):
        value = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        assert _round_trip({"k": value, "t": {"v": value}}) == {"k": value, "t": {"v": value}}
