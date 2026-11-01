"""The Ollama part of setup: this computer, Ollama Cloud, or another server (§2.6-2.10)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from git_commit_ai.core.config import ProviderName
from git_commit_ai.core.user_config import CommitMode
from git_commit_ai.onboarding.model_menu import MenuAction
from git_commit_ai.onboarding.ollama_flow import (
    OllamaLocation,
    ServerAction,
    normalize_url,
)
from git_commit_ai.onboarding.wizard import ProbeAction, SetupResult
from tests.fixtures.http import FakeHttp, json_response
from tests.fixtures.provider_payloads import OLLAMA_TAGS
from tests.unit.onboarding.test_wizard import CLOUD, KEY, LOCAL, OK, OTHER_KEY, Harness, _routes

if TYPE_CHECKING:
    from pathlib import Path

SERVER = "https://ollama.internal.example:11434"
PLAIN_SERVER = "http://192.168.1.20:11434"


def _run(
    tmp_path: Path, answers: list[object], http: FakeHttp, env: dict[str, str] | None = None
) -> tuple[SetupResult, Harness]:
    harness = Harness(
        tmp_path, [ProviderName.OLLAMA, *answers, CommitMode.CONFIRM], http=http, env=env
    )
    return harness.done(harness.wizard.run_full()), harness


def test_where_does_it_run_menu(tmp_path: Path) -> None:
    _result, harness = _run(tmp_path, [OllamaLocation.LOCAL, "gemma3:4b"], _routes())
    menu = harness.prompter.asked[1]
    assert menu.message == "Where does Ollama run?"
    assert menu.labels == ["On this computer", "Ollama Cloud", "Another server"]
    assert [c.hint for c in menu.choices] == [
        LOCAL,
        "https://ollama.com · API key",
        "enter its URL",
    ]


def test_no_models_pulled_then_check_again_section_2_7(tmp_path: Path) -> None:
    http = _routes()
    http.on(
        "GET",
        f"{LOCAL}/api/tags",
        json_response(200, {"models": []}),
        json_response(200, OLLAMA_TAGS),
    )
    result, harness = _run(
        tmp_path, [OllamaLocation.LOCAL, ServerAction.CHECK_AGAIN, "qwen3:8b"], http
    )
    out = harness.prompter.output
    assert "No chat models are pulled yet. Pull one in another terminal, for example:" in out
    assert "ollama pull <model>" in out
    assert harness.prompter.asked[2].labels == [
        "I've pulled a model; check again",
        "Type a model name",
        "Choose a different provider",
        "Cancel setup",
    ]
    assert result.model == "qwen3:8b"


def test_not_running_then_another_url_section_2_8(tmp_path: Path) -> None:
    http = _routes()
    http.on("GET", f"{LOCAL}/api/version", httpx.ConnectError("refused"))
    http.on("GET", f"{SERVER}/api/version", json_response(200, {"version": "0.39.2"}))
    http.on("GET", f"{SERVER}/api/tags", json_response(200, OLLAMA_TAGS))
    http.on("POST", f"{SERVER}/api/chat", OK)
    result, harness = _run(
        tmp_path,
        [OllamaLocation.LOCAL, ServerAction.OTHER_URL, SERVER + "/", "gemma3:4b"],
        http,
    )
    out = harness.prompter.output
    assert f"Looking for Ollama at {LOCAL}... not reachable (connection refused)." in out
    assert "https://ollama.com/download" in out
    assert harness.prompter.asked[2].labels == [
        "Check again",
        "Use a different URL",
        "Choose a different provider",
        "Cancel setup",
    ]
    assert f"Looking for Ollama at {SERVER}... found (version 0.39.2)" in out
    assert result.base_url == SERVER


def test_cloud_with_a_rejected_key_then_a_good_one_section_2_9(tmp_path: Path) -> None:
    http = _routes()
    http.on("GET", f"{CLOUD}/api/tags", json_response(200, OLLAMA_TAGS))
    http.on("POST", f"{CLOUD}/api/chat", json_response(401), OK)
    result, harness = _run(
        tmp_path,
        [OllamaLocation.CLOUD, OTHER_KEY, "gemma3:4b", ProbeAction.NEW_KEY, KEY, "gemma3:4b"],
        http,
    )
    assert (result.base_url, result.key) == (CLOUD, KEY)
    out = harness.prompter.output
    assert "Create a key at https://ollama.com/settings/keys" in out
    assert f"Fetching models from {CLOUD}... 2 models" in out
    assert harness.prompter.messages("secret") == ["Ollama API key:", "Ollama API key:"]
    assert http.calls[-1].headers["authorization"] == f"Bearer {KEY}"


def test_cloud_offers_the_environment_key(tmp_path: Path) -> None:
    http = _routes()
    http.on("GET", f"{CLOUD}/api/tags", json_response(200, OLLAMA_TAGS))
    http.on("POST", f"{CLOUD}/api/chat", OK)
    result, harness = _run(
        tmp_path, [OllamaLocation.CLOUD, True, "gemma3:4b"], http, env={"OLLAMA_API_KEY": KEY}
    )
    assert result.key is None
    assert "Found OLLAMA_API_KEY in your environment." in harness.prompter.output


def test_a_server_that_needs_a_key_section_2_10(tmp_path: Path) -> None:
    http = _routes()
    http.on(
        "GET",
        f"{SERVER}/api/version",
        json_response(401),
        json_response(200, {"version": "0.39.2"}),
    )
    http.on("GET", f"{SERVER}/api/tags", json_response(200, OLLAMA_TAGS))
    http.on("POST", f"{SERVER}/api/chat", OK)
    result, harness = _run(tmp_path, [OllamaLocation.OTHER, SERVER, KEY, "gemma3:4b"], http)
    assert harness.prompter.messages("secret") == ["This server needs a key. Key:"]
    assert result.key == KEY
    assert http.calls[1].headers["authorization"] == f"Bearer {KEY}"


def test_plain_http_to_another_machine_is_warned_about(tmp_path: Path) -> None:
    http = _routes()
    http.on("GET", f"{PLAIN_SERVER}/api/version", json_response(200, {"version": "1"}))
    http.on("GET", f"{PLAIN_SERVER}/api/tags", json_response(200, OLLAMA_TAGS))
    http.on("POST", f"{PLAIN_SERVER}/api/chat", OK)
    _result, harness = _run(tmp_path, [OllamaLocation.OTHER, PLAIN_SERVER, "gemma3:4b"], http)
    assert "not encrypted" in harness.prompter.output


def test_a_url_without_a_scheme_is_asked_again(tmp_path: Path) -> None:
    http = _routes()
    http.on("GET", f"{SERVER}/api/version", json_response(200, {"version": "1"}))
    http.on("GET", f"{SERVER}/api/tags", json_response(200, OLLAMA_TAGS))
    http.on("POST", f"{SERVER}/api/chat", OK)
    _result, harness = _run(
        tmp_path,
        [OllamaLocation.OTHER, "ollama.internal.example:11434", SERVER, "gemma3:4b"],
        http,
    )
    assert "Include http:// or https://" in harness.prompter.output


def test_type_a_model_name(tmp_path: Path) -> None:
    http = _routes()
    http.on("POST", f"{LOCAL}/api/chat", OK)
    result, _harness = _run(
        tmp_path, [OllamaLocation.LOCAL, MenuAction.TYPE_NAME, "llama3.3:70b"], http
    )
    assert result.model == "llama3.3:70b"


@pytest.mark.parametrize(
    ("text", "url", "problem"),
    [
        ("http://localhost:11434/", "http://localhost:11434", None),
        ("  https://box:1  ", "https://box:1", None),
        ("box:11434", None, "Include http:// or https://"),
        ("ftp://box", None, "Include http:// or https://"),
        ("", None, "Enter a URL"),
    ],
)
def test_normalize_url(text: str, url: str | None, problem: str | None) -> None:
    normalized, error = normalize_url(text)
    assert normalized == url
    assert (error or "").startswith(problem or "")
    assert (error is None) is (problem is None)
