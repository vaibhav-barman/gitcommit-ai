"""Command line for the evaluation harness."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import typer

from git_commit_ai.cli.settings_loader import load_saved_layers
from git_commit_ai.core.config import ProviderName  # noqa: TC001  (Typer reads it at runtime)
from git_commit_ai.core.exceptions import GitCommitAIError
from git_commit_ai.core.settings import ENV_FILE_VARIABLE, load_settings
from git_commit_ai.llm import create_provider
from scripts.eval.offline import offline_llm
from scripts.eval.report import write_reports
from scripts.eval.runner import evaluate

if TYPE_CHECKING:
    from git_commit_ai.llm import LLMProvider
    from git_commit_ai.llm.retry import RetryEvent
    from scripts.eval.replay import Replay
    from scripts.eval.report import CommitRecord
    from scripts.eval.runner import LLMFactory

DEFAULT_COUNT = 30
DEFAULT_OUTPUT_DIR = Path("eval-output")

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def run(
    repo: Path = typer.Argument(..., help="A local Git repository to replay."),
    count: int = typer.Option(
        DEFAULT_COUNT, "--count", "-n", min=1, help="How many of the newest non-merge commits."
    ),
    provider: ProviderName | None = typer.Option(None, "--provider", help="LLM provider."),
    model: str | None = typer.Option(None, "--model", "-m", help="Model name."),
    offline: bool = typer.Option(
        False, "--offline", help="Use a deterministic stand-in for the LLM (no network, no key)."
    ),
    max_iterations: int | None = typer.Option(
        None, "--max-iterations", min=1, max=10, help="Maximum generate/critique rounds."
    ),
    output_dir: Path = typer.Option(
        DEFAULT_OUTPUT_DIR, "--output-dir", "-o", help="Where report.json and report.md go."
    ),
) -> None:
    """Replay a repository's recent commits and score the messages the agent graph writes."""
    if offline and (provider is not None or model is not None):
        _fail("--offline replaces the LLM, so it cannot be combined with --provider or --model.")
    try:
        # Offline runs ignore the real environment, any .env and the saved config, so they
        # are reproducible. Live runs use the same layers as the CLI.
        if offline:
            settings = load_settings({ENV_FILE_VARIABLE: ""})
        else:
            user_config, credentials = load_saved_layers()
            settings = load_settings(os.environ, user_config=user_config, credentials=credentials)
        settings = settings.with_flags(
            llm_provider=provider, llm_model=model, max_iterations=max_iterations
        )
        config = settings.config
        if offline:
            make_llm: LLMFactory = _offline_for
        else:
            key = settings.api_key_for(config.llm_provider)
            llm = create_provider(config, key.value if key else None, on_retry=_announce_retry)
            typer.secho(
                f"Live run with {llm.name}/{llm.model}: up to {1 + 2 * config.max_iterations} "
                f"model calls per commit, for up to {count} commits.",
                err=True,
            )
            make_llm = _always(llm)
        report = evaluate(
            str(repo), count, make_llm, config, "offline" if offline else "live", _progress
        )
    except GitCommitAIError as exc:
        _fail(str(exc))

    json_path, markdown_path = write_reports(report, output_dir)
    typer.echo(markdown_path.read_text(encoding="utf-8"))
    typer.secho(f"Wrote {json_path} and {markdown_path}", err=True)
    if report.aggregate.evaluated == 0:
        _fail("No commit could be evaluated.")


def _offline_for(replay: Replay) -> LLMProvider:
    return offline_llm(replay.diff)


def _always(llm: LLMProvider) -> LLMFactory:
    def make(_: Replay) -> LLMProvider:
        return llm

    return make


def _progress(done: int, total: int, record: CommitRecord) -> None:
    status = f"failed: {record.error}" if record.error else "ok"
    typer.secho(f"[{done}/{total}] {record.hash[:7]} {status}", err=True)


def _announce_retry(event: RetryEvent) -> None:
    typer.secho(
        f"{event.error} Retrying in {event.delay:.0f}s ({event.retry}/{event.max_retries})...",
        fg=typer.colors.YELLOW,
        err=True,
    )


def _fail(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
