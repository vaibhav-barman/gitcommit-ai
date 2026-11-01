"""The ``generate`` command: propose (and optionally create) a commit message."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, NoReturn

import typer

from git_commit_ai.agents.graph import CommitMessageGraph
from git_commit_ai.core.config import GitCommitAIConfig, ProviderName
from git_commit_ai.core.exceptions import GitCommitAIError
from git_commit_ai.core.schemas import DiffMode
from git_commit_ai.git.committer import GitCommitter
from git_commit_ai.git.diff import DiffExtractor
from git_commit_ai.git.history import CommitHistoryExtractor
from git_commit_ai.git.repository import RepositoryInspector
from git_commit_ai.git.snapshots import SnapshotManager
from git_commit_ai.llm import create_provider

if TYPE_CHECKING:
    from git_commit_ai.agents.graph import GenerationResult
    from git_commit_ai.core.schemas import DiffResult, RepositoryInfo


def generate(
    path: str = typer.Option(".", "--path", "-p", help="Path to the Git repository."),
    provider: ProviderName | None = typer.Option(
        None, "--provider", help="LLM provider (default: GITCOMMIT_AI_PROVIDER or openai)."
    ),
    model: str | None = typer.Option(
        None, "--model", "-m", help="Model name (default: the provider's default)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the message without committing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Commit without asking for confirmation."),
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
    """
    try:
        config = _build_config(provider, model, max_iterations)
        inspector = RepositoryInspector(repo_path=path, config=config)
        info = inspector.inspect()
        diff = DiffExtractor(inspector=inspector, config=config).extract(DiffMode.STAGED)
        if not diff.file_changes:
            _fail(_nothing_staged_message(info))

        history = CommitHistoryExtractor(inspector=inspector, config=config).get_history()
        snapshots = SnapshotManager(inspector, config)
        snapshot = snapshots.capture()

        llm = create_provider(config)
        _note(f"Generating a commit message with {llm.name}/{llm.model}...")
        result = CommitMessageGraph(llm, config).run(diff, history)

        _report_input_limits(diff, result)
        if verbose:
            _report_verbose(result)
        _report_review(result)
        typer.echo(result.message)

        if dry_run:
            return
        if not yes:
            typer.confirm("Commit with this message?", abort=True, err=True)
        short_hash = GitCommitter(inspector, snapshots).commit(result.message, snapshot)
        _note(f"Committed {short_hash}")
    except GitCommitAIError as exc:
        _fail(str(exc))


_git_subcommand = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
_git_subcommand.command()(generate)


def git_ai_commit() -> None:
    """Entry point for ``git ai-commit``: runs ``generate`` directly.

    Git intercepts ``--help`` for external subcommands and looks for a man
    page, so ``-h`` is accepted as well.
    """
    _git_subcommand()


def _build_config(
    provider: ProviderName | None,
    model: str | None,
    max_iterations: int | None,
) -> GitCommitAIConfig:
    config = GitCommitAIConfig.from_env(os.environ)
    overrides: dict[str, object] = {}
    if provider is not None:
        overrides["llm_provider"] = provider
    if model is not None:
        overrides["llm_model"] = model
    if max_iterations is not None:
        overrides["max_iterations"] = max_iterations
    return config.model_copy(update=overrides)


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
    if result.approved:
        return
    _note(
        f"No draft passed review after {result.iterations} round(s); showing the best one.",
        color=typer.colors.YELLOW,
    )
    best = next((d for d in result.state.drafts if d.message == result.message), None)
    for violation in best.critique.violations if best and best.critique else []:
        _note(f"  - {violation.rule}: {violation.message}", color=typer.colors.YELLOW)


def _note(text: str, color: str | None = None) -> None:
    typer.secho(text, fg=color, err=True)


def _fail(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
