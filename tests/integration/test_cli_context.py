"""Integration tests for the context and examples CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from git_commit_ai.cli.main import app

runner = CliRunner()


def test_cli_context_command(populated_repo: Path) -> None:
    """Verify context command analyzes commit convention and style."""
    result = runner.invoke(app, ["context", "--path", str(populated_repo)])
    assert result.exit_code == 0
    assert "Repository Commit Context" in result.output
    assert "Convention:" in result.output
    assert "Analyzed:       2 commits" in result.output
    assert "Style Characteristics" in result.output
    assert "Subject length:" in result.output
    assert "Capitalization:" in result.output


def test_cli_context_empty_repo(empty_repo: Path) -> None:
    """Verify context command handles empty repository gracefully."""
    result = runner.invoke(app, ["context", "--path", str(empty_repo)])
    assert result.exit_code == 0
    assert "Repository Commit Context" in result.output
    assert "UNKNOWN" in result.output
    assert "Analyzed:       0 commits" in result.output


def test_cli_context_with_count(populated_repo: Path) -> None:
    """Verify context command respects --count parameter."""
    result = runner.invoke(app, ["context", "--path", str(populated_repo), "--count", "1"])
    assert result.exit_code == 0
    assert "Analyzed:       1 commits" in result.output


def test_cli_context_non_git_dir(tmp_path: Path) -> None:
    """Verify context command reports error on non-git directory."""
    non_git = tmp_path / "not_git"
    non_git.mkdir()
    result = runner.invoke(app, ["context", "--path", str(non_git)])
    assert result.exit_code == 1
    assert "Error: Not a Git repository" in result.output


def test_cli_examples_staged_changes(populated_repo: Path) -> None:
    """Verify examples command matches staged changes against history."""
    result = runner.invoke(app, ["examples", "--path", str(populated_repo)])
    assert result.exit_code == 0
    assert "Relevant Historical Examples for Staged Changes" in result.output
    assert "feat: initial app" in result.output


def test_cli_examples_unstaged_changes(populated_repo: Path) -> None:
    """Verify examples command matches unstaged changes with --unstaged."""
    result = runner.invoke(app, ["examples", "--path", str(populated_repo), "--unstaged"])
    assert result.exit_code == 0
    assert "Relevant Historical Examples for Unstaged Changes" in result.output
    assert "docs: initial readme" in result.output


def test_cli_examples_no_changes(repo_with_initial_commit: Path) -> None:
    """Verify examples command output when working tree has no changes."""
    result = runner.invoke(app, ["examples", "--path", str(repo_with_initial_commit)])
    assert result.exit_code == 0
    assert "No staged changes found to match against." in result.output


def test_cli_examples_max_examples(populated_repo: Path) -> None:
    """Verify examples command respects -m/--max-examples limit."""
    result = runner.invoke(
        app,
        ["examples", "--path", str(populated_repo), "-m", "1"],
    )
    assert result.exit_code == 0
    assert "Relevant Historical Examples for Staged Changes (1)" in result.output


def test_cli_examples_non_git_dir(tmp_path: Path) -> None:
    """Verify examples command reports error on non-git directory."""
    non_git = tmp_path / "not_git"
    non_git.mkdir()
    result = runner.invoke(app, ["examples", "--path", str(non_git)])
    assert result.exit_code == 1
    assert "Error: Not a Git repository" in result.output
