"""The setup wizard, driven by scripted answers over a fake HTTP layer (no TTY, no network)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import httpx
import pytest

from git_commit_ai.core.config import ProviderName
from git_commit_ai.core.credentials import (
    CredentialStore,
    FileCredentialStore,
    KeyringCredentialStore,
)
from git_commit_ai.core.exceptions import SetupCancelledError, UnsupportedConfigVersionError
from git_commit_ai.core.settings import ENV_FILE_VARIABLE, load_settings
from git_commit_ai.core.user_config import (
    SCHEMA_VERSION,
    CommitMode,
    CredentialStoreKind,
    ProviderPreferences,
    UserConfig,
    load_user_config,
    save_user_config,
)
from git_commit_ai.llm.catalog import catalog_for, detect_ollama
from git_commit_ai.onboarding.model_menu import MenuAction
from git_commit_ai.onboarding.ollama_flow import OllamaLocation
from git_commit_ai.onboarding.wizard import (
    KeyAction,
    ProbeAction,
    SetupResult,
    SetupWizard,
    persist,
)
from tests.fixtures.http import FakeHttp, json_response
from tests.fixtures.prompter import CTRL_C, ScriptedPrompter
from tests.fixtures.provider_payloads import (
    ANTHROPIC_PAGES,
    GEMINI_PAGES,
    HUGGINGFACE_MODELS,
    OLLAMA_TAGS,
    OPENAI_MODELS,
    OPENROUTER_MODELS,
)

if TYPE_CHECKING:
    from pathlib import Path

KEY = "FAKE-" + "w" * 24 + "3f2a"
OTHER_KEY = "FAKE-" + "v" * 24 + "a91c"
ANTHROPIC = "https://api.anthropic.com/v1"
OPENAI = "https://api.openai.com/v1"
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
OPENROUTER = "https://openrouter.ai/api/v1"
HF = "https://router.huggingface.co/v1"
LOCAL = "http://localhost:11434"
CLOUD = "https://ollama.com"
OK = json_response(200, {})


def _anthropic_models() -> httpx.Response:
    pages = [*ANTHROPIC_PAGES[0]["data"], *ANTHROPIC_PAGES[1]["data"]]
    return json_response(200, {"data": pages, "has_more": False})


def _routes() -> FakeHttp:
    http = FakeHttp()
    http.on("GET", f"{ANTHROPIC}/models", _anthropic_models())
    http.on("POST", f"{ANTHROPIC}/messages", OK)
    http.on("GET", f"{OPENAI}/models", json_response(200, OPENAI_MODELS))
    http.on("POST", f"{OPENAI}/chat/completions", OK)
    http.on("GET", f"{GEMINI}/models", json_response(200, {"models": GEMINI_PAGES[1]["models"]}))
    http.on("POST", f"{GEMINI}/models/gemini-3.0-flash:generateContent", OK)
    http.on("GET", f"{OPENROUTER}/key", OK)
    http.on("GET", f"{OPENROUTER}/models", json_response(200, OPENROUTER_MODELS))
    http.on("POST", f"{OPENROUTER}/chat/completions", OK)
    http.on("GET", "https://huggingface.co/api/whoami-v2", json_response(200, {"name": "alice"}))
    http.on("GET", f"{HF}/models", json_response(200, HUGGINGFACE_MODELS))
    http.on("POST", f"{HF}/chat/completions", OK)
    http.on("GET", f"{LOCAL}/api/version", json_response(200, {"version": "0.40.12"}))
    http.on("GET", f"{LOCAL}/api/tags", json_response(200, OLLAMA_TAGS))
    http.on("POST", f"{LOCAL}/api/chat", OK)
    return http


class Harness:
    def __init__(
        self,
        tmp_path: Path,
        answers: list[object],
        http: FakeHttp | None = None,
        env: dict[str, str] | None = None,
        user_config: UserConfig | None = None,
        keyring_ok: bool = False,
    ) -> None:
        self.directory = tmp_path / "cfg"
        self.http = http or _routes()
        self.prompter = ScriptedPrompter(answers)
        self.store = FileCredentialStore(self.directory)
        settings = load_settings(
            {ENV_FILE_VARIABLE: "", **(env or {})},
            tmp_path,
            user_config=user_config,
            credentials=self.store,
        )
        transport = self.http.transport
        self.wizard = SetupWizard(
            self.prompter,
            settings,
            catalogs=lambda p, k, u: catalog_for(p, k, u, transport, sleep=lambda _s: None),
            ollama_detector=lambda u, k: detect_ollama(u, k, transport),
            keyring_ok=keyring_ok,
            directory=self.directory,
        )

    def done(self, result: SetupResult) -> SetupResult:
        self.prompter.assert_done()
        return result


def _full(
    tmp_path: Path,
    answers: list[object],
    http: FakeHttp | None = None,
    env: dict[str, str] | None = None,
    keyring_ok: bool = False,
) -> tuple[SetupResult, Harness]:
    harness = Harness(tmp_path, answers, http=http, env=env, keyring_ok=keyring_ok)
    return harness.done(harness.wizard.run_full()), harness


class TestHappyPaths:
    @pytest.mark.parametrize(
        ("provider", "model"),
        [
            (ProviderName.ANTHROPIC, "claude-sonnet-5-5"),
            (ProviderName.OPENAI, "gpt-5-mini"),
            (ProviderName.GEMINI, "gemini-3.0-flash"),
            (ProviderName.OPENROUTER, "anthropic/claude-sonnet-5.5"),
            (ProviderName.HUGGINGFACE, "Qwen/Qwen3-Coder"),
        ],
    )
    def test_hosted_provider(self, tmp_path: Path, provider: ProviderName, model: str) -> None:
        result, harness = _full(tmp_path, [provider, KEY, model, CommitMode.CONFIRM])
        assert result == SetupResult(
            provider=provider,
            model=model,
            base_url=None,
            key=KEY,
            commit_mode=CommitMode.CONFIRM,
            credential_store=CredentialStoreKind.FILE,
            select_provider=True,
        )
        out = harness.prompter.output
        assert "Checking the" in out
        assert f"Sending a test request to {model}... ok" in out
        assert KEY not in out
        assert not harness.directory.exists()

    def test_ollama_local(self, tmp_path: Path) -> None:
        result, harness = _full(
            tmp_path,
            [ProviderName.OLLAMA, OllamaLocation.LOCAL, "gemma3:4b", CommitMode.PREVIEW],
        )
        assert (result.provider, result.model, result.base_url, result.key) == (
            ProviderName.OLLAMA,
            "gemma3:4b",
            LOCAL,
            None,
        )
        out = harness.prompter.output
        assert f"Looking for Ollama at {LOCAL}... found (version 0.40.12)" in out
        assert "1 embedding-only model hidden: nomic-embed-text:latest" in out


class TestTranscripts:
    def test_first_run_anthropic_matches_section_2_1(self, tmp_path: Path) -> None:
        _, harness = _full(
            tmp_path,
            [ProviderName.ANTHROPIC, KEY, "claude-sonnet-5-5", CommitMode.CONFIRM],
        )
        prompter = harness.prompter
        assert prompter.output.startswith(
            "Welcome to GitCommit AI. Let's set up a model provider (about a minute)."
        )
        provider_menu = prompter.asked[0]
        assert provider_menu.message == "Which provider do you want to use?"
        assert provider_menu.labels == [
            "Anthropic",
            "Google Gemini",
            "Hugging Face",
            "OpenAI",
            "OpenRouter",
            "Ollama",
        ]
        assert provider_menu.default is ProviderName.OPENAI
        assert "Create a key at https://console.anthropic.com/settings/keys" in prompter.output
        assert prompter.asked[1].message == "Anthropic API key:"
        assert "Checking the key... ok" in prompter.output
        assert "Fetching the models available to this key... 4 models" in prompter.output
        model_menu = prompter.asked[2]
        assert model_menu.values[0] == "claude-sonnet-5-5"
        assert model_menu.choices[0].hint == "claude-sonnet-5-5"
        mode_menu = prompter.asked[3]
        assert mode_menu.labels == [
            "Ask me first (recommended)",
            "Only print the message",
            "Commit without asking",
        ]
        assert mode_menu.default is CommitMode.CONFIRM

    def test_bad_key_then_retry_matches_section_2_2(self, tmp_path: Path) -> None:
        http = _routes()
        body_key_echo = {"error": {"message": f"Incorrect API key provided: {OTHER_KEY}"}}
        http.on(
            "GET",
            f"{OPENAI}/models",
            json_response(401, body_key_echo),
            json_response(200, OPENAI_MODELS),
        )
        result, harness = _full(
            tmp_path,
            [
                ProviderName.OPENAI,
                OTHER_KEY,
                KeyAction.REENTER,
                KEY,
                "gpt-5-mini",
                CommitMode.CONFIRM,
            ],
            http=http,
        )
        assert result.key == KEY
        out = harness.prompter.output
        assert "Checking the key... OpenAI rejected this key." in out
        assert OTHER_KEY not in out
        what_now = harness.prompter.asked[2]
        assert what_now.message == "What now?"
        assert what_now.labels == [
            "Enter the key again",
            "Choose a different provider",
            "Cancel setup",
        ]

    def test_bad_key_then_another_provider(self, tmp_path: Path) -> None:
        http = _routes()
        http.on("GET", f"{OPENAI}/models", json_response(401))
        result, _ = _full(
            tmp_path,
            [
                ProviderName.OPENAI,
                OTHER_KEY,
                KeyAction.OTHER_PROVIDER,
                ProviderName.ANTHROPIC,
                KEY,
                "claude-sonnet-5-5",
                CommitMode.CONFIRM,
            ],
            http=http,
        )
        assert result.provider is ProviderName.ANTHROPIC

    def test_bad_key_then_cancel(self, tmp_path: Path) -> None:
        http = _routes()
        http.on("GET", f"{OPENAI}/models", json_response(401))
        harness = Harness(tmp_path, [ProviderName.OPENAI, OTHER_KEY, KeyAction.CANCEL], http=http)
        with pytest.raises(SetupCancelledError):
            harness.wizard.run_full()

    def test_listing_failure_falls_back_to_dated_suggestions_section_2_3(
        self, tmp_path: Path
    ) -> None:
        http = _routes()
        http.on("GET", f"{GEMINI}/models", httpx.ReadTimeout("slow"))
        http.on("POST", f"{GEMINI}/models/my-gemini:generateContent", OK)
        result, harness = _full(
            tmp_path,
            [ProviderName.GEMINI, KEY, MenuAction.TYPE_NAME, "my-gemini", CommitMode.CONFIRM],
            http=http,
        )
        assert result.model == "my-gemini"
        out = harness.prompter.output
        assert "couldn't check the key (timed out after 10s); continuing" in out
        assert "Fetching the models available to this key... failed (timed out after 10s)" in out
        assert "These suggestions were current in November 2026 and may be out of date:" in out
        menu = harness.prompter.asked[2]
        assert menu.values == [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            MenuAction.RETRY,
            MenuAction.TYPE_NAME,
        ]

    def test_retrying_the_listing_after_a_failure(self, tmp_path: Path) -> None:
        http = _routes()
        http.on("GET", f"{OPENAI}/models", json_response(503), json_response(200, OPENAI_MODELS))
        harness = Harness(
            tmp_path,
            [MenuAction.RETRY, "gpt-5-mini"],
            http=http,
            env={"OPENAI_API_KEY": KEY},
        )
        result = harness.done(harness.wizard.run_model())
        assert result.model == "gpt-5-mini"
        assert "failed (HTTP 503)" in harness.prompter.output
        assert "Fetching the models available to this key... 5 models" in harness.prompter.output

    def test_openrouter_show_all_is_searchable_section_2_4(self, tmp_path: Path) -> None:
        many = {
            "data": [
                {
                    "id": f"vendor/model-{i}",
                    "name": f"Vendor: Model {i}",
                    "created": 1000 - i,
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                    "pricing": {"prompt": "1", "completion": "1"},
                }
                for i in range(12)
            ]
        }
        http = _routes()
        http.on("GET", f"{OPENROUTER}/models", json_response(200, many))
        result, harness = _full(
            tmp_path,
            [
                ProviderName.OPENROUTER,
                KEY,
                MenuAction.SHOW_ALL,
                "vendor/model-11",
                CommitMode.CONFIRM,
            ],
            http=http,
        )
        assert result.model == "vendor/model-11"
        menu = harness.prompter.asked[2]
        assert "Show all 12 models" in menu.labels
        assert len(harness.prompter.asked[3].choices) == 12

    def test_hugging_face_probe_403_section_2_5(self, tmp_path: Path) -> None:
        http = _routes()
        http.on("POST", f"{HF}/chat/completions", json_response(403), OK)
        result, harness = _full(
            tmp_path,
            [
                ProviderName.HUGGINGFACE,
                OTHER_KEY,
                "Qwen/Qwen3-Coder",
                ProbeAction.NEW_KEY,
                KEY,
                "Qwen/Qwen3-Coder",
                CommitMode.CONFIRM,
            ],
            http=http,
        )
        assert result.key == KEY
        out = harness.prompter.output
        assert "huggingface.co/settings/tokens/new" in out
        assert harness.prompter.asked[1].message == "Hugging Face access token:"
        assert (
            "Sending a test request to Qwen/Qwen3-Coder... failed: this token may not call "
            "Inference Providers (HTTP 403)." in out
        )
        assert harness.prompter.asked[3].labels == [
            "Enter a different token",
            "Choose a different model",
            "Choose a different provider",
            "Cancel setup",
        ]

    def test_a_retired_model_offers_another(self, tmp_path: Path) -> None:
        http = _routes()
        gone = json_response(404, {"error": {"message": "model not found"}})
        http.on("POST", f"{OPENAI}/chat/completions", gone, OK)
        result, harness = _full(
            tmp_path,
            [
                ProviderName.OPENAI,
                KEY,
                "gpt-5",
                ProbeAction.OTHER_MODEL,
                "gpt-5-mini",
                CommitMode.CONFIRM,
            ],
            http=http,
        )
        assert result.model == "gpt-5-mini"
        assert "failed: gpt-5 isn't available from OpenAI." in harness.prompter.output

    def test_a_transient_probe_failure_can_keep_the_model(self, tmp_path: Path) -> None:
        http = _routes()
        http.on("POST", f"{OPENAI}/chat/completions", json_response(503))
        result, harness = _full(
            tmp_path,
            [ProviderName.OPENAI, KEY, "gpt-5", ProbeAction.KEEP, CommitMode.CONFIRM],
            http=http,
        )
        assert result.model == "gpt-5"
        assert "Sending a test request to gpt-5... failed (HTTP 503)" in harness.prompter.output

    def test_auto_mode_needs_an_explicit_yes(self, tmp_path: Path) -> None:
        result, harness = _full(
            tmp_path,
            [
                ProviderName.OPENAI,
                KEY,
                "gpt-5-mini",
                CommitMode.AUTO,
                False,
                CommitMode.AUTO,
                True,
            ],
        )
        assert result.commit_mode is CommitMode.AUTO
        assert (
            harness.prompter.messages("confirm")
            == ["Commit without asking? Messages will be committed as soon as they're generated."]
            * 2
        )


class TestKeysAlreadyThere:
    def test_an_environment_key_is_offered_and_not_saved(self, tmp_path: Path) -> None:
        result, harness = _full(
            tmp_path,
            [ProviderName.ANTHROPIC, True, "claude-sonnet-5-5", CommitMode.CONFIRM],
            env={"ANTHROPIC_API_KEY": KEY},
        )
        assert result.key is None
        assert "Found ANTHROPIC_API_KEY in your environment." in harness.prompter.output
        assert harness.prompter.messages("confirm") == [
            "Use it? It will not be saved, so it must stay set."
        ]
        assert harness.http.calls[0].headers["x-api-key"] == KEY

    def test_declining_the_environment_key_asks_for_one(self, tmp_path: Path) -> None:
        result, _ = _full(
            tmp_path,
            [ProviderName.ANTHROPIC, False, OTHER_KEY, "claude-sonnet-5-5", CommitMode.CONFIRM],
            env={"ANTHROPIC_API_KEY": KEY},
        )
        assert result.key == OTHER_KEY

    def test_a_saved_key_is_reused_on_provider_switch_section_2_12(self, tmp_path: Path) -> None:
        harness = Harness(
            tmp_path,
            [ProviderName.OPENROUTER, True, "anthropic/claude-sonnet-5.5"],
            user_config=UserConfig(
                provider=ProviderName.ANTHROPIC,
                providers={ProviderName.ANTHROPIC: ProviderPreferences(model="claude-sonnet-5-5")},
            ),
        )
        harness.store.set(ProviderName.OPENROUTER, KEY)
        result = harness.done(harness.wizard.run_provider())
        assert (result.provider, result.key, result.commit_mode) == (
            ProviderName.OPENROUTER,
            None,
            None,
        )
        out = harness.prompter.output
        assert out.startswith("Current: anthropic / claude-sonnet-5-5")
        assert "A key for OpenRouter is already saved (********3f2a)." in out

    def test_the_saved_model_is_the_default_when_switching_back(self, tmp_path: Path) -> None:
        harness = Harness(
            tmp_path,
            [ProviderName.OPENAI, True, "gpt-5"],
            user_config=UserConfig(
                provider=ProviderName.ANTHROPIC,
                providers={ProviderName.OPENAI: ProviderPreferences(model="gpt-5")},
            ),
        )
        harness.store.set(ProviderName.OPENAI, KEY)
        harness.done(harness.wizard.run_provider())
        assert harness.prompter.asked[2].default == "gpt-5"


class TestOtherRuns:
    def test_run_model_lists_with_the_current_key(self, tmp_path: Path) -> None:
        harness = Harness(
            tmp_path,
            ["claude-opus-5-5"],
            env={"GITCOMMIT_AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": KEY},
        )
        result = harness.done(harness.wizard.run_model())
        assert (result.provider, result.model, result.key, result.select_provider) == (
            ProviderName.ANTHROPIC,
            "claude-opus-5-5",
            None,
            False,
        )
        assert harness.prompter.output.startswith("Current: anthropic / claude-sonnet-5-5")

    def test_run_model_notes_a_saved_model_that_is_gone(self, tmp_path: Path) -> None:
        harness = Harness(
            tmp_path,
            ["claude-opus-5-5"],
            env={"ANTHROPIC_API_KEY": KEY},
            user_config=UserConfig(
                provider=ProviderName.ANTHROPIC,
                providers={ProviderName.ANTHROPIC: ProviderPreferences(model="claude-2")},
            ),
        )
        harness.done(harness.wizard.run_model())
        assert "claude-2, your saved model, is no longer offered by Anthropic" in (
            harness.prompter.output
        )

    def test_run_model_without_a_key_asks_for_one(self, tmp_path: Path) -> None:
        harness = Harness(
            tmp_path, [KEY, "gpt-5-mini"], user_config=UserConfig(provider=ProviderName.OPENAI)
        )
        result = harness.done(harness.wizard.run_model())
        assert result.key == KEY

    def test_run_key_checks_and_returns_the_new_key(self, tmp_path: Path) -> None:
        harness = Harness(tmp_path, [KEY])
        result = harness.done(harness.wizard.run_key(ProviderName.ANTHROPIC))
        assert (result.provider, result.key, result.model, result.select_provider) == (
            ProviderName.ANTHROPIC,
            KEY,
            None,
            False,
        )
        assert "Checking the key... ok" in harness.prompter.output

    def test_run_key_for_ollama_is_not_checked(self, tmp_path: Path) -> None:
        harness = Harness(tmp_path, [KEY])
        result = harness.done(harness.wizard.run_key(ProviderName.OLLAMA))
        assert result.key == KEY
        assert harness.http.calls == []

    def test_run_mode(self, tmp_path: Path) -> None:
        harness = Harness(
            tmp_path, [CommitMode.PREVIEW], user_config=UserConfig(commit_mode=CommitMode.AUTO)
        )
        result = harness.done(harness.wizard.run_mode())
        assert result.commit_mode is CommitMode.PREVIEW
        assert harness.prompter.asked[0].default is CommitMode.AUTO
        assert harness.prompter.output.startswith("Current: auto")

    def test_keychain_question_appears_only_when_usable(self, tmp_path: Path) -> None:
        result, harness = _full(
            tmp_path,
            [
                ProviderName.OPENAI,
                KEY,
                "gpt-5-mini",
                CommitMode.CONFIRM,
                CredentialStoreKind.KEYRING,
            ],
            keyring_ok=True,
        )
        assert result.credential_store is CredentialStoreKind.KEYRING
        assert harness.prompter.asked[-1].message == "Where should the key be stored?"

    def test_no_keychain_question_without_a_key_to_store(self, tmp_path: Path) -> None:
        result, _ = _full(
            tmp_path,
            [ProviderName.OLLAMA, OllamaLocation.LOCAL, "gemma3:4b", CommitMode.CONFIRM],
            keyring_ok=True,
        )
        assert result.credential_store is CredentialStoreKind.FILE


class TestCancel:
    FULL: ClassVar[list[object]] = [
        ProviderName.ANTHROPIC,
        KEY,
        "claude-sonnet-5-5",
        CommitMode.AUTO,
        True,
        CredentialStoreKind.FILE,
    ]

    @pytest.mark.parametrize("step", range(len(FULL)))
    def test_ctrl_c_at_every_step_writes_nothing(self, tmp_path: Path, step: int) -> None:
        answers = [*self.FULL[:step], CTRL_C]
        harness = Harness(tmp_path, answers, keyring_ok=True)
        with pytest.raises(SetupCancelledError):
            harness.wizard.run_full()
        assert not harness.directory.exists()

    @pytest.mark.parametrize(
        "answers",
        [
            [ProviderName.OLLAMA, CTRL_C],
            [ProviderName.OLLAMA, OllamaLocation.LOCAL, CTRL_C],
            [ProviderName.OLLAMA, OllamaLocation.CLOUD, CTRL_C],
            [ProviderName.OLLAMA, OllamaLocation.OTHER, CTRL_C],
        ],
    )
    def test_ctrl_c_in_the_ollama_steps_writes_nothing(
        self, tmp_path: Path, answers: list[object]
    ) -> None:
        harness = Harness(tmp_path, answers)
        with pytest.raises(SetupCancelledError):
            harness.wizard.run_full()
        assert not harness.directory.exists()


def _result(**changes: Any) -> SetupResult:
    fields: dict[str, Any] = {
        "provider": ProviderName.ANTHROPIC,
        "model": "claude-sonnet-5-5",
        "base_url": None,
        "key": KEY,
        "commit_mode": CommitMode.CONFIRM,
        "credential_store": CredentialStoreKind.FILE,
        "select_provider": True,
    }
    return SetupResult(**{**fields, **changes})


class TestPersist:
    def test_writes_the_key_then_the_config(self, tmp_path: Path) -> None:
        saved = persist(_result(), tmp_path, None)
        assert load_user_config(tmp_path) == saved
        assert saved.provider is ProviderName.ANTHROPIC
        assert saved.commit_mode is CommitMode.CONFIRM
        assert saved.preferences(ProviderName.ANTHROPIC).model == "claude-sonnet-5-5"
        assert FileCredentialStore(tmp_path).get(ProviderName.ANTHROPIC) == KEY
        assert KEY not in (tmp_path / "config.toml").read_text(encoding="utf-8")

    def test_keeps_other_providers_models_and_keys(self, tmp_path: Path) -> None:
        first = persist(
            _result(provider=ProviderName.OPENAI, model="gpt-5", key=OTHER_KEY), tmp_path, None
        )
        second = persist(_result(commit_mode=None), tmp_path, first)
        assert second.provider is ProviderName.ANTHROPIC
        assert second.commit_mode is CommitMode.CONFIRM
        assert second.preferences(ProviderName.OPENAI).model == "gpt-5"
        store = FileCredentialStore(tmp_path)
        assert store.get(ProviderName.OPENAI) == OTHER_KEY
        assert store.get(ProviderName.ANTHROPIC) == KEY

    def test_a_result_that_does_not_select_keeps_the_active_provider(self, tmp_path: Path) -> None:
        first = persist(_result(), tmp_path, None)
        second = persist(
            _result(provider=ProviderName.OPENAI, model="gpt-5", key=None, select_provider=False),
            tmp_path,
            first,
        )
        assert second.provider is ProviderName.ANTHROPIC
        assert second.preferences(ProviderName.OPENAI).model == "gpt-5"

    def test_ollama_base_url_is_saved(self, tmp_path: Path) -> None:
        saved = persist(
            _result(provider=ProviderName.OLLAMA, model="m", base_url=CLOUD, key=KEY),
            tmp_path,
            None,
        )
        assert saved.preferences(ProviderName.OLLAMA).base_url == CLOUD

    def test_refuses_a_newer_config_and_writes_nothing(self, tmp_path: Path) -> None:
        newer = UserConfig(schema_version=SCHEMA_VERSION + 1)
        with pytest.raises(UnsupportedConfigVersionError):
            persist(_result(), tmp_path, newer)
        assert not tmp_path.joinpath("credentials.toml").exists()

    def test_switching_to_the_keychain_moves_every_key(self, tmp_path: Path) -> None:
        from tests.unit.core.test_credentials import FakeKeyring

        fake = FakeKeyring()

        def open_store(directory: Path, kind: CredentialStoreKind) -> CredentialStore:
            if kind is CredentialStoreKind.KEYRING:
                return KeyringCredentialStore(backend=fake)
            return FileCredentialStore(directory)

        first = persist(_result(provider=ProviderName.OPENAI, key=OTHER_KEY), tmp_path, None)
        persist(
            _result(credential_store=CredentialStoreKind.KEYRING),
            tmp_path,
            first,
            open_store=open_store,
        )
        assert fake.passwords == {
            ("gitcommit-ai", "openai"): OTHER_KEY,
            ("gitcommit-ai", "anthropic"): KEY,
        }
        assert not tmp_path.joinpath("credentials.toml").exists()

    def test_results_never_show_the_key(self) -> None:
        assert KEY not in repr(_result())
        assert KEY not in str(_result())


def test_saved_config_round_trips_through_load(tmp_path: Path) -> None:
    save_user_config(tmp_path, UserConfig(provider=ProviderName.OPENAI))
    assert load_user_config(tmp_path) == UserConfig(provider=ProviderName.OPENAI)
