"""The ``generate`` command: propose (and optionally create) a commit message."""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import typer

from git_commit_ai.agents.graph import CommitMessageGraph
from git_commit_ai.cli.interaction import current, run_wizard, setup_required
from git_commit_ai.cli.settings_loader import load_cli_settings
from git_commit_ai.cli.setup_cmds import report_setup
from git_commit_ai.core.config import ProviderName
from git_commit_ai.core.exceptions import (
    AuthenticationError,
    GitCommitAIError,
    ModelNotAvailableError,
    SetupRequiredError,
)
from git_commit_ai.core.schemas import DiffMode, RepositoryOperation
from git_commit_ai.core.settings import Settings, SettingSource
from git_commit_ai.git.committer import GitCommitter
from git_commit_ai.git.diff import DiffExtractor
from git_commit_ai.git.history import CommitHistoryExtractor
from git_commit_ai.git.repository import RepositoryInspector
from git_commit_ai.git.snapshots import SnapshotManager
from git_commit_ai.llm import create_provider
from git_commit_ai.llm.ollama_provider import sends_key_unencrypted
from git_commit_ai.llm.registry import spec
from git_commit_ai.onboarding.wizard import SetupWizard

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
    accept_partial: bool = typer.Option(
        False,
        "--accept-partial",
        help="Let --yes commit a draft left unfinished by a provider error.",
    ),
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

    If a provider error cut the run short, --yes refuses to commit the unfinished
    draft (exit code 1) unless --accept-partial is also given.
    """
    if ctx.invoked_subcommand is not None:
        return
    if hint is not None and len(hint) > _MAX_HINT_CHARS:
        _fail(f"--hint is {len(hint)} characters; keep it under {_MAX_HINT_CHARS}.")
    settings: Settings | None = None
    try:
        settings = load_cli_settings(path).with_flags(
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

        settings = _ensure_provider(ctx, settings, path, provider, model, max_iterations)
        config = settings.config
        key = settings.api_key_for(config.llm_provider)
        if key and config.llm_provider is ProviderName.OLLAMA:
            _warn_if_unencrypted(config.ollama_base_url)
        llm = create_provider(config, key.value if key else None, on_retry=_announce_retry)
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
        if yes and result.interruption and not accept_partial:
            _fail(
                f"Not committing: the run stopped early ({result.interruption}). "
                "Rerun, review the draft above and commit it yourself, "
                "or pass --accept-partial to let --yes commit it."
            )
        if not yes:
            typer.confirm("Commit with this message?", abort=True, err=True)
        short_hash = GitCommitter(inspector, snapshots).commit(
            result.message, snapshot, amend=amend, no_verify=no_verify
        )
        _note(f"Committed {short_hash}")
    except AuthenticationError as exc:
        _fail(_rejected_key_message(exc, settings))
    except ModelNotAvailableError as exc:
        _fail(f"{exc} Run `git ai-commit model` to choose another.")
    except GitCommitAIError as exc:
        _fail(str(exc))


def _ensure_provider(
    ctx: typer.Context,
    settings: Settings,
    path: str,
    provider: ProviderName | None,
    model: str | None,
    max_iterations: int | None,
) -> Settings:
    """Make sure the provider has a key, setting one up if a terminal allows it.

    With nothing set up at all (plan D9) the full wizard runs, or, without a terminal,
    ``SetupRequiredError`` explains what to do. With a saved setup that only lacks the
    key, only the key is asked for. Otherwise the settings are returned unchanged and
    ``create_provider`` reports a missing key as before.
    """
    config = settings.config
    if not spec(config.llm_provider).requires_key or settings.api_key_for(config.llm_provider):
        return settings
    interaction = current(ctx)
    if setup_required(settings):
        if not interaction.interactive:
            raise SetupRequiredError
        result, saved = run_wizard(interaction, settings, SetupWizard.run_full)
        report_setup(result, saved)
        typer.echo(err=True)
    elif interaction.interactive:
        run_wizard(interaction, settings, lambda w: w.run_key(config.llm_provider))
    else:
        return settings
    return load_cli_settings(path).with_flags(
        llm_provider=provider, llm_model=model, max_iterations=max_iterations
    )


def _rejected_key_message(error: AuthenticationError, settings: Settings | None) -> str:
    provider = settings.config.llm_provider if settings else None
    if provider is None:
        return str(error)
    label = spec(provider).label
    key = settings.api_key_for(provider) if settings else None
    if key is None:
        return str(error)
    where = {
        SettingSource.ENVIRONMENT: "the environment",
        SettingSource.DOTENV: "the .env file",
    }.get(key.source, f"the {key.source.value}")
    fix = f"Update {key.variable}." if key.variable else "Run `git ai-commit key` to replace it."
    return f"{label} rejected the API key from {where}. {fix}"


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


def _warn_if_unencrypted(base_url: str) -> None:
    if sends_key_unencrypted(base_url):
        _note(
            f"Warning: your Ollama key is sent to {base_url} without encryption; "
            "use an https:// URL if the server supports it.",
            color=typer.colors.YELLOW,
        )


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
