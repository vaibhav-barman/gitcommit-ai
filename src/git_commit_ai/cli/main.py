"""Typer-based CLI for GitCommit AI.

Provides three commands for Phase 1:
    - ``inspect``: Display repository metadata.
    - ``diff``: Display staged diff statistics.
    - ``history``: Display recent commit subjects.
"""

from __future__ import annotations

import typer

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import GitCommitAIError
from git_commit_ai.core.schemas import DiffMode
from git_commit_ai.git.diff import DiffExtractor
from git_commit_ai.git.history import CommitHistoryExtractor
from git_commit_ai.git.repository import RepositoryInspector

app = typer.Typer(
    name="gitcommit-ai",
    help="An agentic Git commit message generator.",
    no_args_is_help=True,
)


def _handle_error(err: GitCommitAIError) -> None:
    """Print an error message and exit with code 1."""
    typer.secho(f"Error: {err}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


@app.command()
def inspect(
    path: str = typer.Option(".", "--path", "-p", help="Path to the Git repository."),
) -> None:
    """Display repository metadata: branch, staged files, and more."""
    config = GitCommitAIConfig()
    inspector = RepositoryInspector(repo_path=path, config=config)

    try:
        info = inspector.inspect()
    except GitCommitAIError as exc:
        _handle_error(exc)
        return  # unreachable, but keeps mypy happy

    typer.secho("Repository Information", bold=True)
    typer.echo(f"  Root:           {info.root_path}")
    typer.echo(f"  Branch:         {info.branch or '(detached HEAD)'}")
    typer.echo(f"  Detached HEAD:  {info.is_detached_head}")
    typer.echo(f"  Empty repo:     {info.is_empty}")

    typer.echo()
    _print_file_list("Staged files", info.staged_files, typer.colors.GREEN)
    _print_file_list("Unstaged files", info.unstaged_files, typer.colors.YELLOW)
    _print_file_list("Untracked files", info.untracked_files, typer.colors.CYAN)


@app.command()
def diff(
    path: str = typer.Option(".", "--path", "-p", help="Path to the Git repository."),
    unstaged: bool = typer.Option(False, "--unstaged", "-u", help="Show unstaged changes."),
) -> None:
    """Display staged diff statistics and changed files."""
    config = GitCommitAIConfig()
    inspector = RepositoryInspector(repo_path=path, config=config)
    extractor = DiffExtractor(inspector=inspector, config=config)

    mode = DiffMode.UNSTAGED if unstaged else DiffMode.STAGED

    try:
        result = extractor.extract(mode=mode)
    except GitCommitAIError as exc:
        _handle_error(exc)
        return

    label = "Unstaged" if unstaged else "Staged"
    typer.secho(f"{label} Diff Statistics", bold=True)
    typer.echo(f"  Files changed:  {result.statistics.files_changed}")
    typer.echo(f"  Additions:      +{result.statistics.total_additions}")
    typer.echo(f"  Deletions:      -{result.statistics.total_deletions}")

    if result.statistics.files_changed > 0:
        typer.echo()
        typer.secho("Changed Files", bold=True)
        for fc in result.file_changes:
            status_color = _status_color(fc.status.value)
            binary_tag = " [binary]" if fc.is_binary else ""
            rename_tag = f" (from {fc.old_path})" if fc.old_path else ""
            status_label = fc.status.value.upper().rjust(12)
            typer.echo(
                f"  {typer.style(status_label, fg=status_color)} "
                f"{fc.path}{binary_tag}{rename_tag}  "
                f"(+{fc.additions} / -{fc.deletions})"
            )

    # Warn on large diffs
    raw_size = len(result.raw_diff.encode())
    if raw_size > config.max_diff_size_bytes:
        typer.echo()
        typer.secho(
            f"⚠  Diff is {raw_size:,} bytes (threshold: {config.max_diff_size_bytes:,} bytes)",
            fg=typer.colors.YELLOW,
        )


@app.command()
def history(
    path: str = typer.Option(".", "--path", "-p", help="Path to the Git repository."),
    count: int | None = typer.Option(None, "--count", "-n", help="Number of commits to show."),
) -> None:
    """Display recent commit subjects."""
    config = GitCommitAIConfig()
    inspector = RepositoryInspector(repo_path=path, config=config)
    extractor = CommitHistoryExtractor(inspector=inspector, config=config)

    try:
        commits = extractor.get_history(count=count)
    except GitCommitAIError as exc:
        _handle_error(exc)
        return

    if not commits:
        typer.secho("No commits found.", fg=typer.colors.YELLOW)
        return

    typer.secho(f"Recent Commits ({len(commits)})", bold=True)
    for commit in commits:
        short_hash = commit.hash[:7]
        typer.echo(f"  {typer.style(short_hash, fg=typer.colors.YELLOW)} {commit.subject}")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _print_file_list(label: str, files: list[str], color: str) -> None:
    """Print a labelled list of file paths."""
    typer.secho(f"{label} ({len(files)}):", bold=True)
    if not files:
        typer.echo("  (none)")
    else:
        for f in files:
            typer.secho(f"  {f}", fg=color)


def _status_color(status: str) -> str:
    """Map a file-change status string to a terminal color."""
    colors: dict[str, str] = {
        "added": typer.colors.GREEN,
        "deleted": typer.colors.RED,
        "modified": typer.colors.YELLOW,
        "renamed": typer.colors.CYAN,
        "copied": typer.colors.CYAN,
    }
    return colors.get(status, typer.colors.WHITE)
