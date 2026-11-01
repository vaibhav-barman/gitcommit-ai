"""The deterministic checks, one rule at a time, and scoring a real graph run."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from scripts.eval.metrics import check_message, score
from scripts.eval.replay import replay_commits

from git_commit_ai.agents.graph import CommitMessageGraph
from git_commit_ai.context.normalizer import CommitNormalizer
from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import AgentStateError
from git_commit_ai.core.schemas import CommitConvention, CommitStyleProfile, HistoricalCommit
from tests.fixtures.git_repo import scoped_history_repo
from tests.fixtures.scripted_llm import scripted_llm

if TYPE_CHECKING:
    from pathlib import Path

    from scripts.eval.replay import Replay
    from scripts.eval.report import Checks

    from git_commit_ai.core.schemas import NormalizedCommit

_normalizer = CommitNormalizer()


def _parse(message: str) -> NormalizedCommit:
    subject, _, body = message.partition("\n\n")
    return _normalizer.normalize(HistoricalCommit(hash="x", subject=subject, body=body))


def _profile(average: float = 40.0) -> CommitStyleProfile:
    return CommitStyleProfile(
        dominant_convention=CommitConvention.SCOPED_CONVENTIONAL,
        average_subject_length=average,
    )


def _checks(generated: str, real: str, profile: CommitStyleProfile | None = None) -> Checks:
    return check_message(_parse(generated), _parse(real), profile or _profile())


class TestConvention:
    @pytest.mark.parametrize(
        ("generated", "real", "expected"),
        [
            ("feat(api): add x", "fix(cli): add y", True),
            ("feat(api): add x", "feat: add y", True),
            ("feat: add x", "Add y", False),
            ("✨ add x", "feat: add y", False),
            ("✨ add x", "✨ add y", True),
            ("[API] add x", "[CLI] add y", True),
            ("Add x", "Add y", True),
        ],
    )
    def test_scope_does_not_change_the_family(
        self, generated: str, real: str, expected: bool
    ) -> None:
        assert _checks(generated, real).convention_match is expected


class TestSubjectLength:
    """The description must be 0.5x to 1.5x the average, at most 72, and 50 is always allowed."""

    @pytest.mark.parametrize(
        ("average", "length", "expected"),
        [
            (40.0, 20, True),
            (40.0, 19, False),
            (40.0, 60, True),
            (40.0, 61, False),
            (20.0, 50, True),
            (20.0, 51, False),
            (20.0, 10, True),
            (20.0, 9, False),
            (100.0, 72, True),
            (100.0, 73, False),
            (0.0, 72, True),
            (0.0, 73, False),
        ],
    )
    def test_bounds(self, average: float, length: int, expected: bool) -> None:
        description = "a" * length
        result = _checks(f"feat: {description}", "feat: add y", _profile(average))
        assert result.subject_length_ok is expected

    def test_the_prefix_is_not_counted(self) -> None:
        description = "a" * 60
        result = _checks(f"feat(some-long-scope): {description}", "feat: x", _profile(40.0))
        assert result.subject_length_ok is True


class TestImperativeMood:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("feat: add x", True),
            ("feat: Add x", True),
            ("feat: added x", False),
            ("feat: adds x", False),
            ("feat: adding x", False),
            ("Fix: handle x", True),
        ],
    )
    def test_first_word(self, message: str, expected: bool) -> None:
        assert _checks(message, "feat: add y").imperative_mood is expected


class TestAgreementWithTheRealCommit:
    @pytest.mark.parametrize(
        ("generated", "real", "expected"),
        [
            ("feat: add x", "fix: handle y", True),
            ("feat: Add x", "fix: Handle y", True),
            ("feat: add x", "fix: Handle y", False),
            ("feat: Add x", "fix: handle y", False),
            ("feat: 3 things", "fix: handle y", None),
            ("feat: add x", "fix: 3 things", None),
        ],
    )
    def test_capitalization(self, generated: str, real: str, expected: bool | None) -> None:
        assert _checks(generated, real).capitalization_agrees is expected

    @pytest.mark.parametrize(
        ("generated", "real", "expected"),
        [
            ("feat: add x.", "fix: handle y.", True),
            ("feat: add x", "fix: handle y", True),
            ("feat: add x.", "fix: handle y", False),
            ("feat: add x", "fix: handle y.", False),
        ],
    )
    def test_trailing_period(self, generated: str, real: str, expected: bool) -> None:
        assert _checks(generated, real).punctuation_agrees is expected

    @pytest.mark.parametrize(
        ("generated", "real", "expected"),
        [
            ("feat(api): add x", "fix(cli): add y", True),
            ("feat: add x", "fix: add y", True),
            ("feat: add x", "fix(cli): add y", False),
            ("feat(api): add x", "fix: add y", False),
            ("Add x", "fix(cli): add y", False),
        ],
    )
    def test_scope_presence(self, generated: str, real: str, expected: bool) -> None:
        assert _checks(generated, real).scope_agrees is expected

    @pytest.mark.parametrize("real", ["Add y", "✨ add y", "[API] add y"])
    def test_scope_presence_only_applies_to_conventional_commits(self, real: str) -> None:
        assert _checks("feat(api): add x", real).scope_agrees is None

    @pytest.mark.parametrize(
        ("generated", "real", "expected"),
        [
            ("feat: add x\n\nwhy", "fix: add y\n\nbecause", True),
            ("feat: add x", "fix: add y", True),
            ("feat: add x\n\nwhy", "fix: add y", False),
            ("feat: add x", "fix: add y\n\nbecause", False),
        ],
    )
    def test_body_presence(self, generated: str, real: str, expected: bool) -> None:
        assert _checks(generated, real).body_agrees is expected


class TestScoringARun:
    @pytest.fixture
    def replay(self, tmp_path: Path) -> Replay:
        repo = scoped_history_repo(tmp_path / "repo")
        return replay_commits(str(repo), 1)[0]

    def test_a_message_that_needed_a_second_round(self, replay: Replay) -> None:
        llm = scripted_llm(["Added empty list handling", "fix(api): handle empty list"])
        result = CommitMessageGraph(llm, GitCommitAIConfig()).run(replay.diff, replay.history)

        record = score(replay.commit, result)

        assert record.hash == replay.commit.hash
        assert record.real_subject == "fix(api): handle empty list"
        outcome = record.outcome
        assert outcome is not None
        assert outcome.generated_message == "fix(api): handle empty list"
        assert outcome.iterations == 2
        assert outcome.interrupted is False
        assert outcome.before.errors >= 3
        assert {"format", "imperative-mood", "capitalization"} <= set(outcome.before.rules)
        assert outcome.after.errors == 0
        assert outcome.after.warnings == 1
        assert outcome.after.rules == ["type-mismatch"]
        assert outcome.real_convention == outcome.generated_convention == "scoped_conventional"
        assert outcome.checks.model_dump() == {
            "convention_match": True,
            "subject_length_ok": True,
            "imperative_mood": True,
            "capitalization_agrees": True,
            "punctuation_agrees": True,
            "scope_agrees": True,
            "body_agrees": True,
        }

    def test_a_draft_the_critic_never_approved_keeps_its_errors(self, replay: Replay) -> None:
        config = GitCommitAIConfig(max_iterations=1)
        llm = scripted_llm(["Added empty list handling"])
        result = CommitMessageGraph(llm, config).run(replay.diff, replay.history)

        outcome = score(replay.commit, result).outcome

        assert outcome is not None
        assert outcome.approved is False
        assert outcome.before == outcome.after
        assert outcome.after.errors >= 3
        assert outcome.checks.convention_match is False
        assert outcome.checks.imperative_mood is False
        assert outcome.checks.scope_agrees is False

    def test_a_run_without_context_cannot_be_scored(self, replay: Replay) -> None:
        llm = scripted_llm(["fix(api): handle empty list"])
        result = CommitMessageGraph(llm, GitCommitAIConfig()).run(replay.diff, replay.history)
        result.state.context = None
        with pytest.raises(AgentStateError):
            score(replay.commit, result)
