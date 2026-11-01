"""Guards that hold for the whole repository and the whole test run."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from git_commit_ai.llm.http import KEY_SHAPED

ROOT = Path(__file__).resolve().parents[1]


def _repository_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    ).stdout.decode()
    return [ROOT / name for name in listed.split("\0") if name and (ROOT / name).is_file()]


def test_no_file_holds_a_string_shaped_like_a_real_api_key() -> None:
    found = []
    for path in _repository_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found += [
            f"{path.relative_to(ROOT)}: {m.group(0)[:12]}..." for m in KEY_SHAPED.finditer(text)
        ]
    assert found == []


def test_tests_cannot_reach_the_network() -> None:
    with pytest.raises(AssertionError, match="network access in a test"):
        httpx.Client().get("https://example.invalid/")
