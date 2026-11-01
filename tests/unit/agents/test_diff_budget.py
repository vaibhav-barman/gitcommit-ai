"""Unit tests for fitting diffs into a prompt budget."""

from __future__ import annotations

from git_commit_ai.agents.diff_budget import build_diff_digest
from tests.fixtures.diffs import make_diff


def test_small_diff_is_included_whole() -> None:
    digest = build_diff_digest(make_diff({"a.py": 3, "b.py": 2}), max_chars=10_000)
    assert not digest.was_truncated
    assert "+line 2 of a.py" in digest.text
    assert "+line 1 of b.py" in digest.text


def test_summary_lists_every_file_with_stats() -> None:
    digest = build_diff_digest(make_diff({"a.py": 3}), max_chars=10_000)
    assert "- added a.py (+3/-0)" in digest.text


def test_large_patch_is_truncated_at_a_line_boundary() -> None:
    digest = build_diff_digest(make_diff({"big.py": 500}), max_chars=1_000)
    assert digest.was_truncated
    assert "more lines truncated]" in digest.text
    assert len(digest.text) < 1_400
    assert not digest.omitted_files


def test_small_files_survive_while_large_ones_are_cut() -> None:
    digest = build_diff_digest(make_diff({"tiny.py": 2, "huge.py": 800}), max_chars=2_000)
    assert "+line 1 of tiny.py" in digest.text
    assert "more lines truncated]" in digest.text


def test_files_with_no_room_are_listed_but_their_patches_omitted() -> None:
    files = {f"mod{i}.py": 100 for i in range(30)}
    digest = build_diff_digest(make_diff(files), max_chars=1_500)
    assert digest.was_truncated
    assert digest.omitted_files
    assert "Patches omitted for size" in digest.text
    for path in files:
        assert path in digest.text


def test_binary_files_are_summarised_without_patch_text() -> None:
    digest = build_diff_digest(make_diff({"a.py": 1}, binary=("logo.png",)), max_chars=10_000)
    assert "logo.png [binary]" in digest.text
    assert "Binary files" not in digest.text
    assert not digest.was_truncated


def test_binary_only_diff_yields_summary_only() -> None:
    digest = build_diff_digest(make_diff({}, binary=("a.png", "b.png")), max_chars=10_000)
    assert digest.text.startswith("Changed files:")
    assert "diff --git" not in digest.text


def test_empty_diff_does_not_crash() -> None:
    digest = build_diff_digest(make_diff({}), max_chars=1_000)
    assert not digest.was_truncated
