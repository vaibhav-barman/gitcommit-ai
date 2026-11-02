"""Pytest configuration and shared fixtures."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from git_commit_ai.core.paths import CONFIG_DIR_VARIABLE
from git_commit_ai.core.settings import ENV_FILE_VARIABLE
from tests.fixtures.git_repo import commit_file, init_repo

# Variables that would let a developer's own setup leak into a test.
_ISOLATED_VARIABLES = (
    "GITCOMMIT_AI_PROVIDER",
    "GITCOMMIT_AI_MODEL",
    "GITCOMMIT_AI_COMMIT_MODE",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "HF_TOKEN",
    "OLLAMA_API_KEY",
    "CI",
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that would send a real HTTP request. Tests pass a fake transport."""

    def refuse(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"network access in a test: {request.method} {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the per-user config at a temporary directory, skip ``.env`` loading and clear
    provider variables, so neither the real ``~/.config/gitcommit-ai`` nor a developer's own
    ``.env`` in the checkout can change a test. Tests of ``.env`` handling set
    ``GITCOMMIT_AI_ENV_FILE`` themselves (or delete it to test discovery)."""
    directory = tmp_path / "user-config"
    monkeypatch.setenv(CONFIG_DIR_VARIABLE, str(directory))
    monkeypatch.setenv(ENV_FILE_VARIABLE, "")
    for variable in _ISOLATED_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    return directory


@pytest.fixture
def empty_repo(tmp_path: Path) -> Path:
    """Provide a newly initialized, empty Git repository (zero commits)."""
    return init_repo(tmp_path / "empty_repo")


@pytest.fixture
def repo_with_initial_commit(tmp_path: Path) -> Path:
    """Provide a Git repository with a single initial commit."""
    repo = init_repo(tmp_path / "repo_with_commit")
    commit_file(repo, "README.md", "# Hello World\n", "Initial commit")
    return repo


@pytest.fixture
def populated_repo(tmp_path: Path) -> Path:
    """Provide a repository with staged, unstaged, untracked, and committed files."""
    repo = init_repo(tmp_path / "populated_repo")
    # Initial commit
    commit_file(repo, "README.md", "# Main\n", "docs: initial readme")
    commit_file(repo, "src/app.py", "print('v1')\n", "feat: initial app")

    # Staged modification and new staged file
    app_py = repo / "src" / "app.py"
    app_py.write_text("print('v2 staged')\n", encoding="utf-8")
    staged_new = repo / "src" / "staged_new.py"
    staged_new.write_text("def staged(): pass\n", encoding="utf-8")

    import subprocess

    subprocess.run(
        ["git", "-C", str(repo), "add", "src/app.py", "src/staged_new.py"],
        check=True,
        capture_output=True,
    )

    # Unstaged modification
    readme = repo / "README.md"
    readme.write_text("# Main\nUnstaged edit line\n", encoding="utf-8")

    # Untracked file
    untracked = repo / "scratch.txt"
    untracked.write_text("untracked notes\n", encoding="utf-8")

    return repo
