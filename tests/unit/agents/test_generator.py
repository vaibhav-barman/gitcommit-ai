"""Unit tests for the generator node and its prompt."""

from __future__ import annotations

import pytest

from git_commit_ai.agents.generator import (
    build_generator_messages,
    clean_message,
    make_generator,
)
from git_commit_ai.agents.state import AgentState, Critique, Draft, Severity, Violation
from git_commit_ai.core.exceptions import (
    AgentStateError,
    LLMError,
    LLMResponseError,
    RateLimitError,
    TransientLLMError,
)
from git_commit_ai.core.schemas import CommitConvention
from git_commit_ai.llm import FakeLLM, Role
from tests.fixtures.agent_inputs import (
    BRACKET_HISTORY,
    GITMOJI_HISTORY,
    PLAIN_HISTORY,
    SCOPED_HISTORY,
    make_analysis,
    make_state,
)


def _user_prompt(state: AgentState) -> str:
    messages = build_generator_messages(state)
    assert messages[0].role is Role.SYSTEM
    return messages[1].content


@pytest.mark.parametrize(
    ("history", "convention", "expected"),
    [
        (SCOPED_HISTORY, CommitConvention.SCOPED_CONVENTIONAL, "<type>(<scope>): <description>"),
        (GITMOJI_HISTORY, CommitConvention.GITMOJI, "a gitmoji"),
        (BRACKET_HISTORY, CommitConvention.BRACKET_PREFIX, "[PREFIX] description"),
        (PLAIN_HISTORY, CommitConvention.PLAIN, "plain subject line"),
    ],
)
def test_format_rule_follows_detected_convention(
    history: list[str], convention: CommitConvention, expected: str
) -> None:
    state = make_state(history)
    assert state.context is not None
    assert state.context.convention_result.detected_convention is convention
    assert expected in _user_prompt(state)


def test_empty_history_defaults_to_conventional_commits() -> None:
    assert "Conventional Commits" in _user_prompt(make_state([]))


def test_scoped_prompt_lists_repository_scopes_and_hint() -> None:
    prompt = _user_prompt(make_state(SCOPED_HISTORY))
    assert "Scopes used here:" in prompt
    assert "api" in prompt
    assert "Suggested scope: api" in prompt


def test_examples_are_included_verbatim() -> None:
    prompt = _user_prompt(make_state(SCOPED_HISTORY))
    assert "Relevant commits from this repository" in prompt
    assert "feat(api): add pagination" in prompt


def test_no_examples_section_without_history() -> None:
    assert "Relevant commits" not in _user_prompt(make_state([]))


def test_style_rules_come_from_the_profile() -> None:
    prompt = _user_prompt(make_state(PLAIN_HISTORY))
    assert "Start the description with a capital letter." in prompt
    assert "End the subject with a period." in prompt
    assert "under 72 characters" in prompt
    assert "imperative mood" in prompt


def test_lowercase_repository_without_periods() -> None:
    prompt = _user_prompt(make_state(SCOPED_HISTORY))
    assert "lowercase letter" in prompt
    assert "Do not end the subject with a period." in prompt


def test_breaking_change_adds_footer_rule() -> None:
    state = make_state(SCOPED_HISTORY, analysis=make_analysis(is_breaking=True))
    assert "BREAKING CHANGE:" in _user_prompt(state)
    assert "BREAKING CHANGE:" not in _user_prompt(make_state(SCOPED_HISTORY))


def test_analysis_and_diff_are_in_the_prompt() -> None:
    prompt = _user_prompt(make_state(SCOPED_HISTORY))
    assert "Add page size option to the API client" in prompt
    assert "src/api/client.py" in prompt


def test_revision_prompt_carries_previous_draft_and_violations() -> None:
    state = make_state(SCOPED_HISTORY)
    critique = Critique(
        violations=[
            Violation(rule="subject-length", message="Shorten it", severity=Severity.ERROR)
        ]
    )
    state = state.model_copy(
        update={"drafts": [Draft(message="feat(api): too long", iteration=1, critique=critique)]}
    )
    prompt = _user_prompt(state)
    assert "feat(api): too long" in prompt
    assert "[subject-length] Shorten it" in prompt


def test_clean_draft_adds_no_revision_section() -> None:
    state = make_state(SCOPED_HISTORY)
    state = state.model_copy(
        update={"drafts": [Draft(message="feat(api): ok", iteration=1, critique=Critique())]}
    )
    assert "previous draft" not in _user_prompt(state)


def test_node_appends_a_draft_and_advances_the_iteration() -> None:
    llm = FakeLLM(["feat(api): add page size option"])
    update = make_generator(llm)(make_state(SCOPED_HISTORY))
    drafts = update["drafts"]
    assert isinstance(drafts, list)
    assert [(d.message, d.iteration) for d in drafts] == [("feat(api): add page size option", 1)]
    assert update["iteration"] == 1


def test_node_keeps_earlier_drafts() -> None:
    state = make_state(SCOPED_HISTORY).model_copy(
        update={"drafts": [Draft(message="old", iteration=1)], "iteration": 1}
    )
    update = make_generator(FakeLLM(["new"]))(state)
    drafts = update["drafts"]
    assert isinstance(drafts, list)
    assert [d.message for d in drafts] == ["old", "new"]
    assert update["iteration"] == 2


def test_node_requires_upstream_results() -> None:
    state = make_state(SCOPED_HISTORY).model_copy(update={"analysis": None})
    with pytest.raises(AgentStateError, match="analysis"):
        make_generator(FakeLLM(["x"]))(state)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("  feat: add x \n", "feat: add x"),
        ("```\nfeat: add x\n\nbody\n```", "feat: add x\n\nbody"),
        ("```text\nfix: y\n```", "fix: y"),
        ("fix: keep `code` spans", "fix: keep `code` spans"),
    ],
)
def test_clean_message(reply: str, expected: str) -> None:
    assert clean_message(reply) == expected


_PROVIDER_ERRORS = [
    RateLimitError("openai"),
    TransientLLMError("openai", "request timed out"),
    LLMResponseError("empty response"),
]


class _Failing:
    name = "failing"
    model = "failing-1"

    def __init__(self, error: LLMError) -> None:
        self._error = error

    def complete(self, messages: object) -> str:
        raise self._error


@pytest.mark.parametrize("error", _PROVIDER_ERRORS, ids=["rate-limit", "timeout", "empty"])
def test_first_round_provider_failure_aborts(error: LLMError) -> None:
    with pytest.raises(type(error)):
        make_generator(_Failing(error))(make_state(SCOPED_HISTORY))


@pytest.mark.parametrize("error", _PROVIDER_ERRORS, ids=["rate-limit", "timeout", "empty"])
def test_later_round_provider_failure_is_recorded_not_raised(error: LLMError) -> None:
    state = make_state(SCOPED_HISTORY).model_copy(
        update={"drafts": [Draft(message="old", iteration=1, critique=Critique())], "iteration": 1}
    )
    update = make_generator(_Failing(error))(state)
    assert set(update) == {"interruption"}
    assert "round 2 could not be generated" in str(update["interruption"])
    assert str(error) in str(update["interruption"])


class TestHint:
    def test_hint_is_quoted_in_the_prompt(self) -> None:
        state = make_state(SCOPED_HISTORY).model_copy(
            update={"hint": "retry flaky uploads, see ticket 12"}
        )
        prompt = _user_prompt(state)
        assert "retry flaky uploads, see ticket 12" in prompt
        assert "still describe only what the diff shows" in prompt

    def test_no_hint_section_without_a_hint(self) -> None:
        assert "The author describes the intent" not in _user_prompt(make_state(SCOPED_HISTORY))


class TestBodyInstructions:
    def test_subject_only_in_a_subject_only_repo(self) -> None:
        prompt = _user_prompt(make_state(SCOPED_HISTORY))
        assert "Write a subject line only" in prompt

    def test_body_requested_where_bodies_are_common(self) -> None:
        prompt = _user_prompt(make_state(SCOPED_HISTORY, with_bodies=True))
        assert "Add a short body after a blank line" in prompt
        assert "under 72 characters" in prompt

    def test_body_requested_for_a_large_diff_even_in_a_subject_only_repo(self) -> None:
        files = {f"src/api/mod{i}.py": 3 for i in range(6)}
        prompt = _user_prompt(make_state(SCOPED_HISTORY, files=files))
        assert "Add a short body after a blank line" in prompt


def test_generated_body_is_wrapped_and_separated() -> None:
    reply = (
        "feat(api): add page size option\n"
        "This change lets callers bound the size of each response so that slow "
        "connections do not stall while a very large page is transferred."
    )
    update = make_generator(FakeLLM([reply]))(make_state(SCOPED_HISTORY, with_bodies=True))
    drafts = update["drafts"]
    assert isinstance(drafts, list)
    lines = drafts[0].message.splitlines()
    assert lines[0] == "feat(api): add page size option"
    assert lines[1] == ""
    assert all(len(line) <= 72 for line in lines)
