"""Unit tests for critique scoring."""

from __future__ import annotations

from git_commit_ai.agents.state import Critique, Severity, Violation


def _v(severity: Severity) -> Violation:
    return Violation(rule="r", message="m", severity=severity)


def test_empty_critique_is_approved() -> None:
    critique = Critique()
    assert critique.approved
    assert critique.penalty == 0


def test_warnings_do_not_block_approval() -> None:
    critique = Critique(violations=[_v(Severity.WARNING), _v(Severity.WARNING)])
    assert critique.approved
    assert critique.penalty == 2


def test_any_error_blocks_approval_and_outweighs_warnings() -> None:
    critique = Critique(violations=[_v(Severity.ERROR), _v(Severity.WARNING)])
    assert not critique.approved
    assert critique.penalty == 11
