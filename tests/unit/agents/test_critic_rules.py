"""Unit tests for the deterministic critic rules."""

from __future__ import annotations

import pytest

from git_commit_ai.agents.critic_rules import check_draft
from git_commit_ai.agents.state import Severity, Violation
from tests.fixtures.agent_inputs import (
    BRACKET_HISTORY,
    GITMOJI_HISTORY,
    PLAIN_HISTORY,
    SCOPED_HISTORY,
    make_analysis,
    make_state,
)


def _check(
    message: str,
    history: list[str] | None = SCOPED_HISTORY,
    with_bodies: bool = False,
    files: dict[str, int] | None = None,
    **analysis: object,
) -> list[Violation]:
    state = make_state(
        history, files=files, analysis=make_analysis(**analysis), with_bodies=with_bodies
    )
    _, parsed_analysis, context = state.drafting_inputs()
    return check_draft(message, parsed_analysis, context, state.diff_result)


def _rules(violations: list[Violation], severity: Severity) -> set[str]:
    return {v.rule for v in violations if v.severity is severity}


def test_conforming_message_has_no_violations() -> None:
    assert _check("feat(api): add page size option") == []


def test_empty_message_is_rejected() -> None:
    assert _rules(_check("  \n "), Severity.ERROR) == {"empty-message"}


class TestFormat:
    def test_scoped_repo_requires_type(self) -> None:
        assert "format" in _rules(_check("add page size option"), Severity.ERROR)

    def test_scoped_repo_requires_scope(self) -> None:
        assert "scope" in _rules(_check("feat: add page size option"), Severity.ERROR)

    def test_unknown_type_is_rejected(self) -> None:
        violations = _check("feature(api): add page size option")
        assert any(v.rule == "format" and "feature" in v.message for v in violations)

    def test_repository_specific_types_are_allowed(self) -> None:
        history = [s.replace("docs(readme)", "deps(readme)") for s in SCOPED_HISTORY]
        assert "format" not in _rules(
            _check("deps(readme): update setup guide", history), Severity.ERROR
        )

    def test_prefix_without_description_is_rejected(self) -> None:
        assert "format" in _rules(_check("feat(api):"), Severity.ERROR)

    def test_gitmoji_repo_requires_emoji(self) -> None:
        assert "format" in _rules(_check("add page size", GITMOJI_HISTORY), Severity.ERROR)
        assert _check("✨ add page size", GITMOJI_HISTORY) == []

    def test_bracket_repo_requires_prefix(self) -> None:
        assert "format" in _rules(_check("feat: add page size", BRACKET_HISTORY), Severity.ERROR)
        assert _check("[API] add page size", BRACKET_HISTORY) == []

    def test_plain_repo_rejects_prefixes(self) -> None:
        assert "format" in _rules(
            _check("feat(api): add page size.", PLAIN_HISTORY), Severity.ERROR
        )
        assert _check("Add page size option.", PLAIN_HISTORY) == []

    def test_no_history_defaults_to_conventional_commits(self) -> None:
        assert _check("feat: add page size", history=[]) == []
        assert "format" in _rules(_check("Add page size", history=[]), Severity.ERROR)


class TestSubjectLength:
    def test_over_hard_limit_is_an_error(self) -> None:
        message = "feat(api): " + "add " + "x" * 70
        assert "subject-length" in _rules(_check(message), Severity.ERROR)

    def test_much_longer_than_typical_is_a_warning(self) -> None:
        message = "feat(api): add " + "very " * 8 + "long option"
        violations = _check(message)
        assert "subject-length" in _rules(violations, Severity.WARNING)
        assert "subject-length" not in _rules(violations, Severity.ERROR)


class TestImperativeMood:
    @pytest.mark.parametrize("word", ["added", "adding", "adds", "fixed", "removes", "dropped"])
    def test_inflected_verbs_are_rejected(self, word: str) -> None:
        assert "imperative-mood" in _rules(_check(f"feat(api): {word} page size"), Severity.ERROR)

    @pytest.mark.parametrize("word", ["add", "remove", "access", "process", "support"])
    def test_base_verbs_and_nouns_pass(self, word: str) -> None:
        assert "imperative-mood" not in _rules(
            _check(f"feat(api): {word} page size"), Severity.ERROR
        )

    def test_not_enforced_where_the_repository_is_not_imperative(self) -> None:
        history = [
            "feat(api): added pagination",
            "fix(api): fixed empty page",
            "docs(readme): updated setup guide",
            "feat(cli): added verbose flag",
        ]
        assert "imperative-mood" not in _rules(
            _check("feat(api): added page size", history), Severity.ERROR
        )


class TestCapitalizationAndPunctuation:
    def test_lowercase_repo_rejects_capital(self) -> None:
        assert "capitalization" in _rules(_check("feat(api): Add page size"), Severity.ERROR)

    def test_sentence_case_repo_rejects_lowercase(self) -> None:
        assert "capitalization" in _rules(_check("add page size.", PLAIN_HISTORY), Severity.ERROR)

    def test_period_forbidden_where_repo_has_none(self) -> None:
        assert "trailing-period" in _rules(_check("feat(api): add page size."), Severity.ERROR)

    def test_period_required_where_repo_uses_it(self) -> None:
        assert "trailing-period" in _rules(_check("Add page size", PLAIN_HISTORY), Severity.ERROR)


class TestScope:
    def test_unfamiliar_scope_is_only_a_warning(self) -> None:
        violations = _check("feat(db): add page size option")
        assert _rules(violations, Severity.WARNING) == {"scope"}
        assert _rules(violations, Severity.ERROR) == set()


class TestTypeAgreement:
    def test_type_differing_from_analysis_warns(self) -> None:
        violations = _check("fix(api): add page size option")
        assert _rules(violations, Severity.WARNING) == {"type-mismatch"}


class TestBreakingChange:
    def test_breaking_change_must_be_marked(self) -> None:
        assert "breaking-change" in _rules(
            _check("feat(api): add page size option", is_breaking=True), Severity.ERROR
        )

    def test_exclamation_mark_satisfies_the_rule(self) -> None:
        assert _check("feat(api)!: add page size option", is_breaking=True) == []

    def test_footer_satisfies_the_rule(self) -> None:
        message = "feat(api): add page size option\n\nBREAKING CHANGE: page size is required"
        violations = _check(message, is_breaking=True)
        assert "breaking-change" not in _rules(violations, Severity.ERROR)

    def test_unneeded_marker_warns(self) -> None:
        violations = _check("feat(api)!: add page size option")
        assert "breaking-change" in _rules(violations, Severity.WARNING)


class TestBody:
    def test_missing_body_is_an_error_where_bodies_are_the_norm(self) -> None:
        violations = _check("feat(api): add page size option", with_bodies=True)
        assert _rules(violations, Severity.ERROR) == {"body-missing"}

    def test_missing_body_is_an_error_for_a_large_diff(self) -> None:
        files = {f"src/api/mod{i}.py": 3 for i in range(6)}
        violations = _check("feat(api): add page size option", files=files)
        assert _rules(violations, Severity.ERROR) == {"body-missing"}

    def test_large_diff_by_line_count_also_expects_a_body(self) -> None:
        violations = _check("feat(api): add page size option", files={"src/api/big.py": 250})
        assert _rules(violations, Severity.ERROR) == {"body-missing"}

    def test_subject_only_is_fine_in_a_subject_only_repo(self) -> None:
        assert _check("feat(api): add page size option") == []

    def test_unexpected_body_is_only_a_warning(self) -> None:
        violations = _check("feat(api): add page size option\n\nBecause callers asked.")
        assert _rules(violations, Severity.WARNING) == {"body-unexpected"}
        assert _rules(violations, Severity.ERROR) == set()

    def test_breaking_footer_is_not_an_unexpected_body(self) -> None:
        message = "feat(api)!: add page size option\n\nBREAKING CHANGE: page size is required"
        assert _check(message, is_breaking=True) == []

    def test_well_formed_body_is_clean(self) -> None:
        message = "feat(api): add page size option\n\nBecause callers asked."
        assert _check(message, with_bodies=True) == []

    def test_overlong_body_line_is_an_error(self) -> None:
        line = "word " * 20
        message = f"feat(api): add page size option\n\n{line.strip()}"
        assert _rules(_check(message, with_bodies=True), Severity.ERROR) == {"body-wrap"}

    def test_unbreakable_tokens_and_code_blocks_may_exceed_the_width(self) -> None:
        url = "https://example.com/" + "a" * 80
        code = "x = 1; " * 20
        message = f"feat(api): add page size option\n\n{url}\n\n    {code}"
        assert _check(message, with_bodies=True) == []

    def test_long_body_gets_a_length_warning(self) -> None:
        body = "\n".join(f"- point number {n}" for n in range(15))
        message = f"feat(api): add page size option\n\n{body}"
        assert _rules(_check(message, with_bodies=True), Severity.WARNING) == {"body-length"}

    def test_body_directly_under_subject_is_an_error(self) -> None:
        message = "feat(api): add page size option\nBecause callers asked."
        assert "blank-line" in _rules(_check(message, with_bodies=True), Severity.ERROR)
