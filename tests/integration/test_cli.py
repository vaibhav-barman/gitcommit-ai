"""Integration tests for the Typer-based CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from git_commit_ai.cli.main import app

runner = CliRunner()


def test_cli_help() -> None:
    """Verify CLI help display."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "gitcommit-ai" in result.output
    assert "inspect" in result.output
    assert "diff" in result.output
    assert "history" in result.output
    assert "context" in result.output
    assert "examples" in result.output


def test_cli_inspect_command(populated_repo: Path) -> None:
    """Verify inspect command displays repository metadata."""
    result = runner.invoke(app, ["inspect", "--path", str(populated_repo)])
    assert result.exit_code == 0
    assert "Repository Information" in result.output
    assert "Branch:         main" in result.output
    assert "Staged files" in result.output
    assert "src/app.py" in result.output
    assert "src/staged_new.py" in result.output
    assert "Unstaged files" in result.output
    assert "README.md" in result.output
    assert "Untracked files" in result.output
    assert "scratch.txt" in result.output


def test_cli_inspect_non_git_dir(tmp_path: Path) -> None:
    """Verify inspect command error handling on non-git directory."""
    non_git = tmp_path / "not_a_repo"
    non_git.mkdir()
    result = runner.invoke(app, ["inspect", "--path", str(non_git)])
    assert result.exit_code == 1
    assert "Error: Not a Git repository" in result.output


def test_cli_diff_staged_command(populated_repo: Path) -> None:
    """Verify diff command with default staged changes."""
    result = runner.invoke(app, ["diff", "--path", str(populated_repo)])
    assert result.exit_code == 0
    assert "Staged Diff Statistics" in result.output
    assert "Files changed:  2" in result.output
    assert "src/app.py" in result.output
    assert "src/staged_new.py" in result.output


def test_cli_diff_unstaged_command(populated_repo: Path) -> None:
    """Verify diff command with --unstaged flag."""
    result = runner.invoke(app, ["diff", "--path", str(populated_repo), "--unstaged"])
    assert result.exit_code == 0
    assert "Unstaged Diff Statistics" in result.output
    assert "README.md" in result.output


def test_cli_history_command(populated_repo: Path) -> None:
    """Verify history command displays recent commits."""
    result = runner.invoke(app, ["history", "--path", str(populated_repo)])
    assert result.exit_code == 0
    assert "Recent Commits" in result.output
    assert "feat: initial app" in result.output
    assert "docs: initial readme" in result.output


def test_cli_history_with_count(populated_repo: Path) -> None:
    """Verify history command respects --count option."""
    result = runner.invoke(app, ["history", "--path", str(populated_repo), "--count", "1"])
    assert result.exit_code == 0
    assert "Recent Commits (1)" in result.output
    assert "feat: initial app" in result.output


def test_cli_history_empty_repo(empty_repo: Path) -> None:
    """Verify history command on empty repository."""
    result = runner.invoke(app, ["history", "--path", str(empty_repo)])
    assert result.exit_code == 0
    assert "No commits found" in result.output
