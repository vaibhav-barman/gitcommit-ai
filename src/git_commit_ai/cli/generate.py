"""The ``generate`` command: propose (and optionally create) a commit message."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, NoReturn

import typer

from git_commit_ai.agents.graph import CommitMessageGraph
from git_commit_ai.core.config import ProviderName  # noqa: TC001  (Typer reads it at runtime)
from git_commit_ai.core.exceptions import GitCommitAIError
from git_commit_ai.core.schemas import DiffMode, RepositoryOperation
from git_commit_ai.core.settings import load_settings
from git_commit_ai.git.committer import GitCommitter
from git_commit_ai.git.diff import DiffExtractor
from git_commit_ai.git.history import CommitHistoryExtractor
from git_commit_ai.git.repository import RepositoryInspector
from git_commit_ai.git.snapshots import SnapshotManager
from git_commit_ai.llm import create_provider

if TYPE_CHECKING:
    from git_commit_ai.agents.graph import GenerationResult
    from git_commit_ai.core.schemas import DiffResult, RepositoryInfo
    from git_commit_ai.llm.retry import RetryEvent

_MAX_HINT_CHARS = 1_000
_GIT_OWNS_THE_MESSAGE = frozenset(
    {RepositoryOperation.MERGE, RepositoryOperation.CHERRY_PICK, RepositoryOperation.REVERT}
)


def generate(
    ctx: typer.Context,
    path: str = typer.Option(".", "--path", "-p", help="Path to the Git repository."),
    provider: ProviderName | None = typer.Option(
        None, "--provider", help="LLM provider (default: GITCOMMIT_AI_PROVIDER or openai)."
    ),
    model: str | None = typer.Option(
        None, "--model", "-m", help="Model name (default: the provider's default)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the message without committing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Commit without asking for confirmation."),
    amend: bool = typer.Option(
        False,
        "--amend",
        help="Replace the last commit; the message describes it plus any staged changes.",
    ),
    no_verify: bool = typer.Option(
        False, "--no-verify", help="Skip the pre-commit and commit-msg hooks when committing."
    ),
    hint: str | None = typer.Option(
        None,
        "--hint",
        help="What the change is for, in your own words; steers the type and wording.",
    ),
    max_iterations: int | None = typer.Option(
        None,
        "--max-iterations",
        min=1,
        max=10,
        help="Maximum generate/critique rounds before using the best draft.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show the detected convention, examples and critic rounds."
    ),
) -> None:
    """Generate a commit message for the staged changes.

    The message is printed to stdout; progress and diagnostics go to stderr.
    Unless --dry-run is given, you are asked to confirm before committing.
    With --amend the last commit is replaced. Its own message is not used as
    an example, and the message is written for the combined change.
    """
    if ctx.invoked_subcommand is not None:
        return
    if hint is not None and len(hint) > _MAX_HINT_CHARS:
        _fail(f"--hint is {len(hint)} characters; keep it under {_MAX_HINT_CHARS}.")
    try:
        settings = load_settings(os.environ).with_flags(
            llm_provider=provider, llm_model=model, max_iterations=max_iterations
        )
        config = settings.config
        inspector = RepositoryInspector(repo_path=path, config=config)
        info = inspector.inspect()
        _check_repository_state(info.operation)
        if amend and info.is_empty:
            _fail("There is no commit to amend.")
        mode = DiffMode.AMEND if amend else DiffMode.STAGED
        diff = DiffExtractor(inspector=inspector, config=config).extract(mode)
        if not diff.file_changes:
            _fail(
                "The commit to amend has no changes to describe."
                if amend
                else _nothing_staged_message(info)
            )

        history = CommitHistoryExtractor(inspector=inspector, config=config).get_history(
            exclude_head=amend
        )
        snapshots = SnapshotManager(inspector, config)
        snapshot = snapshots.capture()

        llm = create_provider(config, env=settings.environment, on_retry=_announce_retry)
        if amend:
            _note("Amending the last commit; the message covers it together with staged changes.")
        _note(f"Generating a commit message with {llm.name}/{llm.model}...")
        result = CommitMessageGraph(llm, config).run(diff, history, hint=hint)

        _report_input_limits(diff, result)
        if verbose:
            _report_verbose(result)
        _report_review(result)
        typer.echo(result.message)

        if dry_run:
            return
        if info.is_detached_head:
            _note(
                "HEAD is detached: the new commit will not belong to any branch.",
                color=typer.colors.YELLOW,
            )
        if not yes:
            typer.confirm("Commit with this message?", abort=True, err=True)
        short_hash = GitCommitter(inspector, snapshots).commit(
            result.message, snapshot, amend=amend, no_verify=no_verify
        )
        _note(f"Committed {short_hash}")
    except GitCommitAIError as exc:
        _fail(str(exc))


def _check_repository_state(operation: RepositoryOperation) -> None:
    """Refuse mid-merge/cherry-pick/revert; note a rebase in progress."""
    if operation in _GIT_OWNS_THE_MESSAGE:
        _fail(
            f"A {operation.value} is in progress. Git has already prepared the commit "
            "message for it; finish with `git commit` instead."
        )
    if operation is RepositoryOperation.REBASE:
        _note("A rebase is in progress; the message is written for the commit being created.")


def _nothing_staged_message(info: RepositoryInfo) -> str:
    if info.unstaged_files or info.untracked_files:
        return "Nothing is staged. Stage your changes first, for example: git add -p"
    return "Nothing is staged and there are no changes to commit."


def _report_input_limits(diff: DiffResult, result: GenerationResult) -> None:
    if all(fc.is_binary for fc in diff.file_changes):
        _note("Only binary files are staged, so the message is based on file names alone.")
    digest = result.state.diff_digest
    if digest is not None and digest.was_truncated:
        omitted = len(digest.omitted_files)
        detail = f" ({omitted} files listed by name only)" if omitted else ""
        _note(f"The diff was too large to send in full; long patches were truncated{detail}.")


def _report_verbose(result: GenerationResult) -> None:
    context = result.state.context
    if context is not None:
        convention = context.convention_result
        _note(
            f"Convention: {convention.detected_convention.value} "
            f"(confidence {convention.confidence * 100:.0f}%, "
            f"{convention.analyzed_commits} commits analyzed)"
        )
        _note(f"Examples used ({len(context.historical_examples)}):")
        for example in context.historical_examples:
            _note(f"  {example.hash[:7]} {example.subject} [score {example.relevance_score:.1f}]")

    _note(f"Critic rounds ({result.iterations}):")
    for draft in result.state.drafts:
        subject = draft.message.splitlines()[0] if draft.message else ""
        critique = draft.critique
        verdict = "approved" if critique is not None and critique.approved else "rejected"
        _note(f"  {draft.iteration}. {subject} -> {verdict}")
        for violation in critique.violations if critique else []:
            _note(f"       [{violation.severity.value}] {violation.rule}: {violation.message}")


def _report_review(result: GenerationResult) -> None:
    if result.interruption:
        _note(
            f"Stopped early: {result.interruption}. Showing the best draft so far.",
            color=typer.colors.YELLOW,
        )
    elif not result.approved:
        _note(
            f"No draft passed review after {result.iterations} round(s); showing the best one.",
            color=typer.colors.YELLOW,
        )
    else:
        return
    best = next((d for d in result.state.drafts if d.message == result.message), None)
    for violation in best.critique.violations if best and best.critique else []:
        _note(f"  - {violation.rule}: {violation.message}", color=typer.colors.YELLOW)


def _announce_retry(event: RetryEvent) -> None:
    _note(
        f"{event.error} Retrying in {event.delay:.0f}s ({event.retry}/{event.max_retries})...",
        color=typer.colors.YELLOW,
    )


def _note(text: str, color: str | None = None) -> None:
    typer.secho(text, fg=color, err=True)


def _fail(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
