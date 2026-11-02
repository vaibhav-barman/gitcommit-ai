"""``doctor``: check the effective setup against the provider, without asking anything."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import typer

from git_commit_ai.cli.interaction import current
from git_commit_ai.cli.settings_loader import load_cli_settings
from git_commit_ai.core.config import ProviderName
from git_commit_ai.core.exceptions import (
    AuthenticationError,
    GitCommitAIError,
    LLMError,
    ModelNotAvailableError,
)
from git_commit_ai.core.paths import config_dir
from git_commit_ai.core.settings import mask_secret
from git_commit_ai.core.user_config import CONFIG_FILE, unknown_keys
from git_commit_ai.llm.catalog import KeyCheck, catalog_for, detect_ollama, failure_reason
from git_commit_ai.llm.registry import spec

if TYPE_CHECKING:
    from git_commit_ai.cli.interaction import Interaction
    from git_commit_ai.core.settings import Settings
    from git_commit_ai.llm.catalog import ModelCatalog

KEY_FIX = "run `git ai-commit key`"
MODEL_FIX = "run `git ai-commit model`"


def doctor(
    ctx: typer.Context,
    no_request: bool = typer.Option(
        False, "--no-request", help="Skip the test request, so no tokens are spent."
    ),
) -> None:
    """Check the provider, key and model in effect. Exits 1 if any check fails."""
    try:
        settings = load_cli_settings()
    except GitCommitAIError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    checker = _Checks(current(ctx), settings)
    checker.describe()
    checker.run(probe=not no_request)
    if checker.failed:
        raise typer.Exit(code=1)


class _Checks:
    def __init__(self, interaction: Interaction, settings: Settings) -> None:
        self._interaction = interaction
        self._settings = settings
        config = settings.config
        self._provider = config.llm_provider
        self._entry = spec(self._provider)
        self._model = config.llm_model or self._entry.default_model
        self._key = settings.api_key_for(self._provider)
        self.failed = False

    def describe(self) -> None:
        settings = self._settings
        directory = config_dir(os.environ)
        if self._key:
            key_text, key_source = mask_secret(self._key.value), self._key.source.value
        elif self._entry.requires_key:
            key_text, key_source = "not set", ""
        else:
            key_text, key_source = "not needed (local server)", ""
        rows = [
            ("Provider", self._provider.value, settings.source_of_field("llm_provider").value),
            ("Model", self._model, settings.source_of_field("llm_model").value),
            ("API key", key_text, key_source),
        ]
        for label, value, source in rows:
            suffix = f" [{source}]" if source else ""
            typer.echo(f"{label:<10} {value:<28}{suffix}".rstrip())
        config_file = directory / CONFIG_FILE
        typer.echo(f"{'Config':<10} {config_file if config_file.exists() else 'none'}")
        ignored = unknown_keys(directory)
        if ignored:
            typer.echo(
                f"Warning: config.toml has settings this version ignores: {', '.join(ignored)}"
            )

    def run(self, probe: bool) -> None:
        transport = self._interaction.transport
        if self._provider is ProviderName.OLLAMA:
            url = self._settings.config.ollama_base_url
            server = detect_ollama(url, self._key.value if self._key else None, transport)
            if not server.reachable:
                self._fail("Ollama server", f"not reachable ({server.error})")
                return
            if server.needs_key:
                self._fail("Ollama server", f"needs a key; {KEY_FIX}")
                return
            self._line("Ollama server", f"ok (version {server.version})")
        elif self._key is None:
            variable = self._entry.key_variables[0]
            self._fail("Key check", f"no key; {KEY_FIX}, or set {variable}")
            return
        key = self._key.value if self._key else None
        base = (
            self._settings.config.ollama_base_url
            if self._provider is ProviderName.OLLAMA
            else self._entry.base_url
        )
        catalog = catalog_for(self._provider, key, base, transport)
        if self._provider is not ProviderName.OLLAMA and not self._check_key(catalog):
            return
        self._check_listing(catalog)
        if probe:
            self._check_probe(catalog)
        else:
            self._line("Test request", "skipped (--no-request)")

    def _check_key(self, catalog: ModelCatalog) -> bool:
        started = time.monotonic()
        result = catalog.check_key()
        elapsed = time.monotonic() - started
        if result is KeyCheck.INVALID:
            fix = (
                f"update {self._key.variable}"
                if self._key and self._key.variable
                else f"{KEY_FIX} to replace it"
            )
            self._fail("Key check", f"failed: {self._entry.label} rejected the key. {fix}.")
            return False
        if result is KeyCheck.UNKNOWN:
            self._line("Key check", f"couldn't check ({catalog.check_failure})")
        else:
            self._line("Key check", f"ok ({elapsed:.1f}s)")
        return True

    def _check_listing(self, catalog: ModelCatalog) -> None:
        try:
            listing = catalog.list_models()
        except LLMError as exc:
            self._line("Model listed", f"couldn't list models ({failure_reason(exc)})")
            return
        if self._model in {m.id for m in listing.models}:
            self._line("Model listed", "ok")
        else:
            self._fail(
                "Model listed", f"not listed by {self._entry.label}; {MODEL_FIX} to pick one"
            )

    def _check_probe(self, catalog: ModelCatalog) -> None:
        started = time.monotonic()
        try:
            catalog.probe(self._model)
        except AuthenticationError as exc:
            self._fail("Test request", f"failed: {exc} {KEY_FIX}")
        except ModelNotAvailableError as exc:
            self._fail("Test request", f"failed: {exc} {MODEL_FIX} to choose another")
        except LLMError as exc:
            self._fail("Test request", f"failed ({failure_reason(exc)})")
        else:
            self._line("Test request", f"ok ({time.monotonic() - started:.1f}s)")

    def _line(self, name: str, result: str) -> None:
        typer.echo(f"{name:<20} {result}")

    def _fail(self, name: str, result: str) -> None:
        self.failed = True
        self._line(name, result)
