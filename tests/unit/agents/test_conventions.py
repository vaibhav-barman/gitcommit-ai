"""Unit tests for shared convention rules."""

from __future__ import annotations

import pytest

from git_commit_ai.agents.conventions import body_expected, diff_is_large
from tests.fixtures.agent_inputs import SCOPED_HISTORY, make_state
from tests.fixtures.diffs import make_diff


@pytest.mark.parametrize(
    ("files", "large"),
    [
        ({"a.py": 3}, False),
        ({f"m{i}.py": 1 for i in range(5)}, False),
        ({f"m{i}.py": 1 for i in range(6)}, True),
        ({"a.py": 199}, False),
        ({"a.py": 200}, True),
    ],
)
def test_large_diff_thresholds(files: dict[str, int], large: bool) -> None:
    assert diff_is_large(make_diff(files)) is large


def test_bodies_expected_when_the_repository_uses_them() -> None:
    state = make_state(SCOPED_HISTORY, with_bodies=True)
    _, _, context = state.drafting_inputs()
    assert body_expected(context, state.diff_result)


def test_subject_only_when_bodies_are_rare_and_the_diff_is_small() -> None:
    state = make_state(SCOPED_HISTORY)
    _, _, context = state.drafting_inputs()
    assert not body_expected(context, state.diff_result)


def test_large_diff_expects_a_body_regardless_of_history() -> None:
    state = make_state(SCOPED_HISTORY, files={"big.py": 300})
    _, _, context = state.drafting_inputs()
    assert body_expected(context, state.diff_result)


def test_no_history_and_small_diff_means_subject_only() -> None:
    state = make_state([])
    _, _, context = state.drafting_inputs()
    assert not body_expected(context, state.diff_result)
