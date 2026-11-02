"""Typer-based CLI for GitCommit AI.

Provides commands for Git intelligence and repository context:
    - ``inspect``: Display repository metadata.
    - ``diff``: Display staged diff statistics.
    - ``history``: Display recent commit subjects.
    - ``context``: Display repository commit conventions and writing style.
    - ``examples``: Retrieve relevant historical commit examples.
    - ``generate``: Generate a commit message with the agent graph.
    - ``config``: Show the effective settings and their sources.
    - ``hook``: Install or remove the prepare-commit-msg hook.
    - ``setup``, ``provider``, ``model``, ``key``: Change the saved setup.
"""

from __future__ import annotations

import typer

from git_commit_ai.cli.config_cmd import config
from git_commit_ai.cli.generate import generate
from git_commit_ai.cli.hook import hook_app
from git_commit_ai.cli.setup_cmds import key, model, provider, setup
from git_commit_ai.context.engine import RepositoryContextEngine
from git_commit_ai.context.example_selector import HistoricalExampleSelector
from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import GitCommitAIError
from git_commit_ai.core.schemas import DiffMode, DiffResult
from git_commit_ai.git.diff import DiffExtractor
from git_commit_ai.git.history import CommitHistoryExtractor
from git_commit_ai.git.repository import RepositoryInspector

app = typer.Typer(
    name="gitcommit-ai",
    help="An agentic Git commit message generator.",
    no_args_is_help=True,
)


app.command()(generate)
app.command()(config)
app.add_typer(hook_app, name="hook")
for _command in (setup, provider, model, key):
    app.command()(_command)


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


@app.command()
def context(
    path: str = typer.Option(".", "--path", "-p", help="Path to the Git repository."),
    count: int | None = typer.Option(
        None, "--count", "-n", help="Number of historical commits to analyze."
    ),
) -> None:
    """Analyze and display repository commit conventions and writing style."""
    config = GitCommitAIConfig()
    inspector = RepositoryInspector(repo_path=path, config=config)
    history_extractor = CommitHistoryExtractor(inspector=inspector, config=config)
    engine = RepositoryContextEngine()

    try:
        inspector.inspect()
        commits = history_extractor.get_history(count=count)
        diff_result = DiffResult()
        ctx = engine.build_context(diff_result, commits)
    except GitCommitAIError as exc:
        _handle_error(exc)
        return

    conv = ctx.convention_result
    profile = ctx.style_profile

    typer.secho("Repository Commit Context", bold=True)
    conv_badge = typer.style(
        conv.detected_convention.value.upper(),
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(f"  Convention:     {conv_badge} (confidence: {conv.confidence * 100:.1f}%)")
    typer.echo(f"  Analyzed:       {conv.analyzed_commits} commits")

    typer.echo()
    typer.secho("Style Characteristics", bold=True)
    typer.echo(
        f"  Subject length: {profile.average_subject_length} chars avg / "
        f"{profile.median_subject_length} chars median"
    )
    typer.echo(f"  Capitalization: {profile.typical_capitalization}")
    punct_str = ", ".join(profile.punctuation_patterns) or "none"
    typer.echo(f"  Punctuation:    {punct_str}")
    typer.echo(f"  Body frequency: {profile.body_usage_frequency * 100:.1f}%")
    typer.echo(f"  Scope usage:    {profile.scope_usage_frequency * 100:.1f}%")
    if profile.imperative_mood_frequency is not None:
        typer.echo(f"  Imperative:     {profile.imperative_mood_frequency * 100:.1f}%")

    if profile.type_frequencies:
        typer.echo()
        typer.secho("Common Types:", bold=True)
        for t, cnt in list(profile.type_frequencies.items())[:6]:
            typer.echo(f"  {typer.style(t, fg=typer.colors.GREEN)}: {cnt}")

    if profile.common_scopes:
        typer.echo()
        typer.secho("Common Scopes:", bold=True)
        typer.echo("  " + ", ".join(profile.common_scopes[:8]))

    if profile.recent_convention_shifts:
        typer.echo()
        typer.secho("Recent Patterns & Shifts:", bold=True)
        for shift in profile.recent_convention_shifts:
            typer.echo(f"  {typer.style('•', fg=typer.colors.YELLOW)} {shift}")

    if conv.supporting_evidence:
        typer.echo()
        typer.secho("Evidence:", bold=True)
        for ev in conv.supporting_evidence:
            typer.echo(f"  - {ev}")


@app.command()
def examples(
    path: str = typer.Option(".", "--path", "-p", help="Path to the Git repository."),
    max_examples: int = typer.Option(
        5, "--max-examples", "-m", help="Maximum number of examples to show."
    ),
    unstaged: bool = typer.Option(
        False, "--unstaged", "-u", help="Match against unstaged changes."
    ),
) -> None:
    """Retrieve and display historical examples relevant to current changes."""
    config = GitCommitAIConfig()
    inspector = RepositoryInspector(repo_path=path, config=config)
    diff_extractor = DiffExtractor(inspector=inspector, config=config)
    history_extractor = CommitHistoryExtractor(inspector=inspector, config=config)
    selector = HistoricalExampleSelector(max_examples=max_examples)
    engine = RepositoryContextEngine(selector=selector)

    mode = DiffMode.UNSTAGED if unstaged else DiffMode.STAGED

    try:
        inspector.inspect()
        diff_result = diff_extractor.extract(mode=mode)
        commits = history_extractor.get_history()
        ctx = engine.build_context(diff_result, commits)
    except GitCommitAIError as exc:
        _handle_error(exc)
        return

    if not diff_result.file_changes:
        label = "unstaged" if unstaged else "staged"
        typer.secho(f"No {label} changes found to match against.", fg=typer.colors.YELLOW)
        return

    if not ctx.historical_examples:
        typer.secho("No relevant historical examples found.", fg=typer.colors.YELLOW)
        return

    label = "Unstaged" if unstaged else "Staged"
    typer.secho(
        f"Relevant Historical Examples for {label} Changes ({len(ctx.historical_examples)})",
        bold=True,
    )

    for i, ex in enumerate(ctx.historical_examples, 1):
        typer.echo()
        score_badge = typer.style(
            f"Score: {ex.relevance_score:.2f}",
            fg=typer.colors.GREEN,
            bold=True,
        )
        short_hash = typer.style(ex.hash[:7], fg=typer.colors.YELLOW)
        typer.echo(f"  {i}. [{short_hash}] {ex.subject} ({score_badge})")
        if ex.body:
            first_line = ex.body.splitlines()[0]
            if len(ex.body.splitlines()) > 1:
                first_line += "..."
            typer.echo(f"     {typer.style(first_line, fg=typer.colors.BRIGHT_BLACK)}")
        for r in ex.selection_reasons:
            typer.echo(f"     {typer.style('✓', fg=typer.colors.CYAN)} {r}")


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
