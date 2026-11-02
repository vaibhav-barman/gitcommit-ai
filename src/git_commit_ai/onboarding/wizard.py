"""The setup wizard: provider, key, model, commit mode, and where the key is kept.

The wizard only asks and checks; it writes nothing. ``persist`` saves a finished
result, so Ctrl+C at any point leaves the previous files untouched.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from git_commit_ai.core import credentials as credential_stores
from git_commit_ai.core.config import CommitMode, ProviderName
from git_commit_ai.core.exceptions import (
    AuthenticationError,
    LLMError,
    ModelNotAvailableError,
    UnsupportedConfigVersionError,
)
from git_commit_ai.core.user_config import (
    CONFIG_FILE,
    SCHEMA_VERSION,
    CredentialStoreKind,
    UserConfig,
    save_user_config,
)
from git_commit_ai.llm.catalog import KeyCheck, ModelListing, failure_reason
from git_commit_ai.llm.registry import PROVIDERS, spec
from git_commit_ai.onboarding.common import (
    ChooseProvider,
    Credentials,
    cancel,
    offer_existing_key,
)
from git_commit_ai.onboarding.model_menu import (
    FALLBACK_HEADING,
    SHORTLIST_SIZE,
    MenuAction,
    MenuValue,
    all_models_choices,
    fallback_menu,
    model_menu,
)
from git_commit_ai.onboarding.ollama_flow import OllamaFlow, ServerAction
from git_commit_ai.onboarding.prompts import Choice

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from git_commit_ai.core.credentials import CredentialStore
    from git_commit_ai.core.settings import Settings
    from git_commit_ai.onboarding.common import CatalogFactory, OllamaDetector
    from git_commit_ai.onboarding.prompts import Prompter

OLLAMA_SHORTLIST_SIZE = 10
MODE_QUESTION = "When a message is ready, should GitCommit AI commit it?"
AUTO_WARNING = "Commit without asking? Messages will be committed as soon as they're generated."
MODE_CHOICES: list[Choice[CommitMode]] = [
    Choice(CommitMode.CONFIRM, "Ask me first (recommended)", "confirm"),
    Choice(CommitMode.PREVIEW, "Only print the message", "preview"),
    Choice(CommitMode.AUTO, "Commit without asking", "auto"),
]


class KeyAction(enum.Enum):
    """Answers to "What now?" after a rejected key."""

    REENTER = "reenter"
    OTHER_PROVIDER = "provider"
    CANCEL = "cancel"


class ProbeAction(enum.Enum):
    """Answers to "What now?" after a failed test request."""

    RETRY = "retry"
    NEW_KEY = "key"
    OTHER_MODEL = "model"
    KEEP = "keep"
    OTHER_PROVIDER = "provider"
    CANCEL = "cancel"


@dataclass(frozen=True)
class SetupResult:
    """What the user chose. ``None`` fields leave the saved value unchanged.

    Attributes:
        provider: The provider whose settings changed.
        key: A newly entered key to save; never shown in ``repr``.
        select_provider: Whether ``provider`` becomes the active provider.
    """

    provider: ProviderName
    model: str | None
    base_url: str | None
    key: str | None = field(repr=False)
    commit_mode: CommitMode | None
    credential_store: CredentialStoreKind
    select_provider: bool


class SetupWizard:
    """Asks the questions in plan §2 through ``prompter`` and checks answers live.

    Args:
        catalogs: Builds the model catalogue for a provider, key and base URL.
        ollama_detector: Looks for an Ollama server at a URL, with an optional key.
        keyring_ok: Whether to offer the system keychain for a new key.
        directory: The per-user config directory, named in messages.
    """

    def __init__(
        self,
        prompter: Prompter,
        settings: Settings,
        catalogs: CatalogFactory,
        ollama_detector: OllamaDetector,
        keyring_ok: bool,
        directory: Path,
    ) -> None:
        self._prompter = prompter
        self._settings = settings
        self._catalogs = catalogs
        self._ollama = OllamaFlow(prompter, settings, catalogs, ollama_detector)
        self._keyring_ok = keyring_ok
        self._directory = directory
        saved = settings.user_config
        self._store_kind = saved.credential_store if saved else CredentialStoreKind.FILE
        self._mode = (saved.commit_mode if saved else None) or CommitMode.CONFIRM

    def run_full(self) -> SetupResult:
        """First-run setup: provider, key, model, commit mode, key storage."""
        self._prompter.note(
            "Welcome to GitCommit AI. Let's set up a model provider (about a minute).\n"
            f"Your answers are saved to {self._directory / CONFIG_FILE}; change them later\n"
            "with `git ai-commit setup`.\n"
        )
        creds, model = self._provider_key_model()
        mode = self._choose_mode()
        return self._result(creds, model, mode, select=True)

    def run_provider(self) -> SetupResult:
        """Switch provider: provider, key, model. The commit mode is left as it is."""
        self._show_current()
        creds, model = self._provider_key_model()
        return self._result(creds, model, None, select=True)

    def run_model(self) -> SetupResult:
        """Choose another model for the current provider."""
        self._show_current()
        provider = self._settings.config.llm_provider
        try:
            creds = self._current_credentials(provider)
            creds, model = self._choose_model(creds)
        except ChooseProvider:
            return self.run_provider()
        return self._result(creds, model, None, select=False)

    def run_key(self, provider: ProviderName) -> SetupResult:
        """Replace ``provider``'s key. Hosted keys are checked; Ollama keys are not,
        because Ollama has no endpoint that checks a key on its own."""
        if provider is ProviderName.OLLAMA:
            key = self._ollama.ask_key()
            return SetupResult(
                provider, None, None, key, None, self._choose_store(key), select_provider=False
            )
        try:
            creds = self._hosted_key(provider, offer_existing=False)
        except ChooseProvider:
            return self.run_provider()
        return self._result(creds, None, None, select=False)

    def run_mode(self) -> SetupResult:
        """Choose the commit mode."""
        self._prompter.note(f"Current: {self._mode.value}")
        return SetupResult(
            provider=self._settings.config.llm_provider,
            model=None,
            base_url=None,
            key=None,
            commit_mode=self._choose_mode(),
            credential_store=self._store_kind,
            select_provider=False,
        )

    def _show_current(self) -> None:
        config = self._settings.config
        model = config.llm_model or spec(config.llm_provider).default_model
        self._prompter.note(f"Current: {config.llm_provider.value} / {model}")

    def _result(
        self, creds: Credentials, model: str | None, mode: CommitMode | None, select: bool
    ) -> SetupResult:
        return SetupResult(
            provider=creds.provider,
            model=model,
            base_url=creds.base_url if creds.provider is ProviderName.OLLAMA else None,
            key=creds.new_key,
            commit_mode=mode,
            credential_store=self._choose_store(creds.new_key),
            select_provider=select,
        )

    def _provider_key_model(self) -> tuple[Credentials, str]:
        while True:
            provider = self._choose_provider()
            try:
                if provider is ProviderName.OLLAMA:
                    creds = self._ollama.choose_server()
                else:
                    creds = self._hosted_key(provider, offer_existing=True)
                return self._choose_model(creds)
            except ChooseProvider:
                continue

    def _choose_provider(self) -> ProviderName:
        return self._prompter.select(
            "Which provider do you want to use?",
            [Choice(entry.name, entry.label, entry.blurb) for entry in PROVIDERS.values()],
            default=self._settings.config.llm_provider,
        )

    def _current_credentials(self, provider: ProviderName) -> Credentials:
        if provider is ProviderName.OLLAMA:
            url = self._settings.config.ollama_base_url
            found = self._settings.api_key_for(provider)
            key = found.value if found else None
            return Credentials(provider, url, key, None, self._catalogs(provider, key, url))
        found = self._settings.api_key_for(provider)
        if found is None:
            return self._hosted_key(provider, offer_existing=False)
        base = spec(provider).base_url
        return Credentials(
            provider, base, found.value, None, self._catalogs(provider, found.value, base)
        )

    def _hosted_key(self, provider: ProviderName, offer_existing: bool) -> Credentials:
        entry = spec(provider)
        base = entry.base_url
        if offer_existing:
            existing = offer_existing_key(self._prompter, self._settings, provider, entry.label)
            if existing:
                creds = Credentials(
                    provider, base, existing, None, self._catalogs(provider, existing, base)
                )
                if self._key_accepted(creds):
                    return creds
        noun = _key_noun(provider)
        while True:
            if provider is ProviderName.HUGGINGFACE:
                self._prompter.note(
                    '  Create a fine-grained token with "Make calls to Inference Providers" at\n'
                    f"  {entry.key_url}"
                )
            else:
                self._prompter.note(f"Create a key at {entry.key_url}")
            key = self._prompter.secret(f"{entry.label} {noun}:")
            if not key:
                self._prompter.note("Nothing was entered.")
                continue
            creds = Credentials(provider, base, key, key, self._catalogs(provider, key, base))
            if self._key_accepted(creds):
                return creds

    def _key_accepted(self, creds: Credentials) -> bool:
        """Check the key. ``False`` means: ask for another one."""
        entry = spec(creds.provider)
        word = "token" if creds.provider is ProviderName.HUGGINGFACE else "key"
        with self._prompter.status(f"Checking the {word}") as done:
            check = creds.catalog.check_key()
            if check is KeyCheck.VALID:
                done("ok")
            elif check is KeyCheck.UNKNOWN:
                done(f"couldn't check the {word} ({creds.catalog.check_failure}); continuing")
            else:
                done(f"{entry.label} rejected this {word}.")
        if check is not KeyCheck.INVALID:
            return True
        action = self._prompter.select(
            "What now?",
            [
                Choice(KeyAction.REENTER, f"Enter the {word} again"),
                Choice(KeyAction.OTHER_PROVIDER, "Choose a different provider"),
                Choice(KeyAction.CANCEL, "Cancel setup"),
            ],
        )
        if action is KeyAction.OTHER_PROVIDER:
            raise ChooseProvider
        if action is KeyAction.CANCEL:
            raise cancel()
        return False

    def _choose_model(self, creds: Credentials) -> tuple[Credentials, str]:
        saved = self._saved_model(creds.provider)
        while True:
            model = self._pick_model(creds, saved)
            action = self._probe(creds, model)
            if action is None or action is ProbeAction.KEEP:
                return creds, model
            if action is ProbeAction.NEW_KEY:
                creds = self._new_key(creds)

    def _pick_model(self, creds: Credentials, saved: str | None) -> str:
        entry = spec(creds.provider)
        is_ollama = creds.provider is ProviderName.OLLAMA
        while True:
            listing = self._list(creds)
            if listing is not None and listing.models:
                choice = self._menu(listing, saved, entry.label, is_ollama)
            elif is_ollama:
                action = self._ollama.no_models() if listing is not None else self._server_failed()
                if action is ServerAction.CHECK_AGAIN:
                    continue
                if action is ServerAction.OTHER_PROVIDER:
                    raise ChooseProvider
                if action is ServerAction.CANCEL:
                    raise cancel()
                choice = MenuAction.TYPE_NAME
            else:
                self._prompter.note(FALLBACK_HEADING)
                choices, default = fallback_menu(entry.fallback_models, saved)
                choice = self._prompter.select("Which model?", choices, default=default)
            if choice is MenuAction.RETRY:
                continue
            if choice is MenuAction.SHOW_ALL and listing is not None:
                return self._prompter.select(
                    "Which model?", all_models_choices(listing.models), searchable=True
                )
            if isinstance(choice, MenuAction):
                return self._prompter.text(
                    "Model name:", validate=lambda text: None if text else "Enter a model name."
                )
            return choice

    def _list(self, creds: Credentials) -> ModelListing | None:
        if creds.provider is ProviderName.OLLAMA:
            message = f"Fetching models from {creds.base_url}"
        elif creds.provider is ProviderName.HUGGINGFACE:
            message = "Fetching models served by Inference Providers"
        else:
            message = "Fetching the models available to this key"
        with self._prompter.status(message) as done:
            try:
                listing = creds.catalog.list_models()
            except LLMError as exc:
                done(f"failed ({failure_reason(exc)})")
                return None
            count = len(listing.models)
            done(f"{count} model{'s' if count != 1 else ''}")
        return listing

    def _menu(
        self, listing: ModelListing, saved: str | None, label: str, is_ollama: bool
    ) -> MenuValue:
        if listing.hidden:
            count = len(listing.hidden)
            names = ", ".join(listing.hidden)
            self._prompter.note(
                f"({count} embedding-only model{'s' if count != 1 else ''} hidden: {names})"
            )
        if saved and saved not in {m.id for m in listing.models}:
            self._prompter.note(f"{saved}, your saved model, is no longer offered by {label}.")
        limit = OLLAMA_SHORTLIST_SIZE if is_ollama else SHORTLIST_SIZE
        choices, default = model_menu(listing.models, saved, limit)
        return self._prompter.select("Which model?", choices, default=default)

    def _server_failed(self) -> ServerAction:
        return self._prompter.select(
            "What now?",
            [
                Choice(ServerAction.CHECK_AGAIN, "Check again"),
                Choice(ServerAction.TYPE_NAME, "Type a model name"),
                Choice(ServerAction.OTHER_PROVIDER, "Choose a different provider"),
                Choice(ServerAction.CANCEL, "Cancel setup"),
            ],
        )

    def _probe(self, creds: Credentials, model: str) -> ProbeAction | None:
        """Send the test request; ``None`` when it passed, else what the user chose."""
        entry = spec(creds.provider)
        options: list[Choice[ProbeAction]]
        with self._prompter.status(f"Sending a test request to {model}") as done:
            try:
                creds.catalog.probe(model)
            except AuthenticationError as exc:
                done(_auth_failure(creds.provider, exc))
                options = [Choice(ProbeAction.NEW_KEY, f"Enter a different {_key_word(creds)}")]
            except ModelNotAvailableError:
                done(f"failed: {model} isn't available from {entry.label}.")
                options = []
            except LLMError as exc:
                done(f"failed ({failure_reason(exc)})")
                options = [
                    Choice(ProbeAction.RETRY, "Try again"),
                    Choice(ProbeAction.KEEP, "Keep this model anyway"),
                ]
            else:
                done("ok")
                return None
        options += [
            Choice(ProbeAction.OTHER_MODEL, "Choose a different model"),
            Choice(ProbeAction.OTHER_PROVIDER, "Choose a different provider"),
            Choice(ProbeAction.CANCEL, "Cancel setup"),
        ]
        action = self._prompter.select("What now?", _ordered(options))
        if action is ProbeAction.OTHER_PROVIDER:
            raise ChooseProvider
        if action is ProbeAction.CANCEL:
            raise cancel()
        if action is ProbeAction.RETRY:
            return self._probe(creds, model)
        return action

    def _new_key(self, creds: Credentials) -> Credentials:
        if creds.provider is ProviderName.OLLAMA:
            key = self._ollama.ask_key()
            catalog = self._catalogs(creds.provider, key, creds.base_url)
            return Credentials(creds.provider, creds.base_url, key, key, catalog)
        return self._hosted_key(creds.provider, offer_existing=False)

    def _choose_mode(self) -> CommitMode:
        while True:
            mode = self._prompter.select(MODE_QUESTION, MODE_CHOICES, default=self._mode)
            if mode is not CommitMode.AUTO or self._prompter.confirm(AUTO_WARNING, default=False):
                return mode

    def _choose_store(self, new_key: str | None) -> CredentialStoreKind:
        if not (self._keyring_ok and new_key):
            return self._store_kind
        return self._prompter.select(
            "Where should the key be stored?",
            [
                Choice(
                    CredentialStoreKind.KEYRING,
                    "This computer's keychain",
                    "may show a permission dialog, which can pause the commit hook",
                ),
                Choice(CredentialStoreKind.FILE, f"A private file in {self._directory}"),
            ],
            default=self._store_kind,
        )

    def _saved_model(self, provider: ProviderName) -> str | None:
        saved = self._settings.user_config
        return saved.preferences(provider).model if saved else None


def persist(
    result: SetupResult,
    directory: Path,
    previous: UserConfig | None,
    open_store: Callable[[Path, CredentialStoreKind], CredentialStore] | None = None,
) -> UserConfig:
    """Save ``result``: keys first, then ``config.toml``, each written atomically, so a
    complete ``config.toml`` means setup finished. Other providers' models and keys are
    kept. Switching between the file and the keychain moves every saved key.

    Raises:
        UnsupportedConfigVersionError: If ``previous`` came from a newer release.
    """
    if previous is not None and previous.written_by_newer_version:
        raise UnsupportedConfigVersionError(str(directory / CONFIG_FILE))
    opener = open_store or credential_stores.open_credential_store
    old_kind = previous.credential_store if previous else CredentialStoreKind.FILE
    if result.credential_store is not old_kind:
        credential_stores.migrate_keys(
            opener(directory, old_kind), opener(directory, result.credential_store)
        )
    if result.key:
        opener(directory, result.credential_store).set(result.provider, result.key)
    base = previous or UserConfig()
    changes = {"model": result.model, "base_url": result.base_url}
    preferences = base.preferences(result.provider).model_copy(
        update={name: value for name, value in changes.items() if value is not None}
    )
    updated = base.model_copy(
        update={
            "schema_version": SCHEMA_VERSION,
            "provider": result.provider if result.select_provider else base.provider,
            "commit_mode": result.commit_mode or base.commit_mode,
            "credential_store": result.credential_store,
            "providers": {**base.providers, result.provider: preferences},
        }
    )
    save_user_config(directory, updated)
    return updated


def _key_noun(provider: ProviderName) -> str:
    return "access token" if provider is ProviderName.HUGGINGFACE else "API key"


def _key_word(creds: Credentials) -> str:
    return "token" if creds.provider is ProviderName.HUGGINGFACE else "key"


def _auth_failure(provider: ProviderName, error: AuthenticationError) -> str:
    status = f" (HTTP {error.status})" if error.status else ""
    if provider is ProviderName.HUGGINGFACE:
        return f"failed: this token may not call Inference Providers{status}."
    return f"failed: {spec(provider).label} rejected the key for this request{status}."


def _ordered(options: list[Choice[ProbeAction]]) -> list[Choice[ProbeAction]]:
    """Retry first, then the key, model and provider options, then keep, then cancel."""
    order = [
        ProbeAction.RETRY,
        ProbeAction.NEW_KEY,
        ProbeAction.OTHER_MODEL,
        ProbeAction.OTHER_PROVIDER,
        ProbeAction.KEEP,
        ProbeAction.CANCEL,
    ]
    return sorted(options, key=lambda choice: order.index(choice.value))
