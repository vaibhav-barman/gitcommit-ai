"""Integration tests for .env loading, precedence, and the config command."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from git_commit_ai.cli.git_subcommand import git_app
from git_commit_ai.cli.main import app
from git_commit_ai.core.settings import ENV_FILE_VARIABLE
from tests.fixtures.cli import use_llm
from tests.fixtures.git_repo import scoped_history_repo, stage_file
from tests.fixtures.scripted_llm import scripted_llm

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()
SECRET = "sk-test-0123456789abcd"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "GITCOMMIT_AI_PROVIDER",
        "GITCOMMIT_AI_MODEL",
        "GITCOMMIT_AI_MAX_ITERATIONS",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(ENV_FILE_VARIABLE, "")


@pytest.fixture
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "settings.env"
    monkeypatch.setenv(ENV_FILE_VARIABLE, str(path))
    return path


class TestConfigCommand:
    def test_shows_defaults(self) -> None:
        result = runner.invoke(app, ["config"])
        assert result.exit_code == 0
        assert "Effective settings" in result.stdout
        assert "flags > environment > .env > defaults" in result.stdout
        assert ".env file:  none found" in result.stdout
        assert "GITCOMMIT_AI_PROVIDER" in result.stdout
        assert "[default]" in result.stdout
        assert "(provider default: gpt-4o-mini)" in result.stdout
        assert "OPENAI_API_KEY = not set" in result.stdout
        assert "ollama" in result.stdout

    def test_secrets_are_masked_and_sources_shown(
        self, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file.write_text(f"ANTHROPIC_API_KEY={SECRET}-dotenv\nGITCOMMIT_AI_MODEL=m1\n")
        monkeypatch.setenv("OPENAI_API_KEY", SECRET)

        result = runner.invoke(app, ["config"])

        assert result.exit_code == 0
        assert SECRET not in result.stdout
        assert "dotenv" not in result.stdout
        assert "OPENAI_API_KEY = ********abcd  [environment]" in result.stdout
        assert "ANTHROPIC_API_KEY = ********tenv  [.env]" in result.stdout
        assert str(env_file) in result.stdout
        assert "m1" in result.stdout

    def test_environment_overrides_dotenv_in_the_report(
        self, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file.write_text("GITCOMMIT_AI_PROVIDER=ollama\n")
        monkeypatch.setenv("GITCOMMIT_AI_PROVIDER", "gemini")

        result = runner.invoke(app, ["config"])

        line = next(ln for ln in result.stdout.splitlines() if "GITCOMMIT_AI_PROVIDER" in ln)
        assert "gemini" in line
        assert "[environment]" in line

    def test_invalid_value_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITCOMMIT_AI_PROVIDER", "skynet")
        result = runner.invoke(app, ["config"])
        assert result.exit_code == 1
        assert "GITCOMMIT_AI_PROVIDER" in result.stderr

    def test_available_through_the_git_entry_point(self) -> None:
        result = runner.invoke(git_app, ["config"])
        assert result.exit_code == 0
        assert "Effective settings" in result.stdout


class TestGeneratePrecedence:
    @pytest.fixture
    def staged_repo(self, tmp_path: Path) -> Path:
        repo = scoped_history_repo(tmp_path / "repo")
        stage_file(repo, "src/api/orders.py", "def orders(): ...\n")
        return repo

    def _dry_run(self, repo: Path, *flags: str) -> None:
        result = runner.invoke(app, ["generate", "--path", str(repo), "--dry-run", *flags])
        assert result.exit_code == 0, result.stderr

    def test_dotenv_values_reach_the_provider(
        self, staged_repo: Path, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file.write_text(f"GITCOMMIT_AI_PROVIDER=anthropic\nANTHROPIC_API_KEY={SECRET}\n")
        created = use_llm(monkeypatch, scripted_llm(["feat(api): add orders endpoint"]))

        self._dry_run(staged_repo)

        assert created.configs[0].llm_provider.value == "anthropic"
        assert created.envs[0]["ANTHROPIC_API_KEY"] == SECRET

    def test_environment_overrides_dotenv(
        self, staged_repo: Path, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file.write_text("GITCOMMIT_AI_PROVIDER=anthropic\nGITCOMMIT_AI_MAX_ITERATIONS=5\n")
        monkeypatch.setenv("GITCOMMIT_AI_PROVIDER", "gemini")
        created = use_llm(monkeypatch, scripted_llm(["feat(api): add orders endpoint"]))

        self._dry_run(staged_repo)

        assert created.configs[0].llm_provider.value == "gemini"
        assert created.configs[0].max_iterations == 5

    def test_flags_override_environment_and_dotenv(
        self, staged_repo: Path, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file.write_text("GITCOMMIT_AI_PROVIDER=anthropic\nGITCOMMIT_AI_MODEL=file-model\n")
        monkeypatch.setenv("GITCOMMIT_AI_PROVIDER", "gemini")
        created = use_llm(monkeypatch, scripted_llm(["feat(api): add orders endpoint"]))

        self._dry_run(staged_repo, "--provider", "ollama", "--model", "flag-model")

        assert created.configs[0].llm_provider.value == "ollama"
        assert created.configs[0].llm_model == "flag-model"

    def test_environment_does_not_leak_from_dotenv_into_the_process(
        self, staged_repo: Path, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file.write_text(f"OPENAI_API_KEY={SECRET}\n")
        use_llm(monkeypatch, scripted_llm(["feat(api): add orders endpoint"]))

        self._dry_run(staged_repo)

        assert "OPENAI_API_KEY" not in os.environ

    def test_invalid_dotenv_is_a_clean_error(
        self, staged_repo: Path, env_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file.write_text("GITCOMMIT_AI_MAX_ITERATIONS=lots\n")
        use_llm(monkeypatch, scripted_llm(["x"]))
        result = runner.invoke(app, ["generate", "--path", str(staged_repo), "--dry-run"])
        assert result.exit_code == 1
        assert "GITCOMMIT_AI_MAX_ITERATIONS" in result.stderr


class TestDotenvDiscovery:
    """``.env`` is read from the working directory or the repository root, nowhere else."""

    @pytest.fixture(autouse=True)
    def discover(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_FILE_VARIABLE)

    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        repo = scoped_history_repo(tmp_path / "outer" / "repo")
        stage_file(repo, "src/api/orders.py", "def orders(): ...\n")
        return repo

    def test_repository_root_is_used_from_a_subdirectory(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (repo / ".env").write_text("GITCOMMIT_AI_MODEL=from-root\n")
        monkeypatch.chdir(repo / "src" / "api")

        result = runner.invoke(app, ["config"])

        assert result.exit_code == 0
        assert f".env file:  {(repo / '.env').resolve()}" in result.stdout
        assert "from-root" in result.stdout

    def test_working_directory_beats_repository_root(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (repo / ".env").write_text("GITCOMMIT_AI_MODEL=from-root\n")
        (repo / "src").mkdir(exist_ok=True)
        (repo / "src" / ".env").write_text("GITCOMMIT_AI_MODEL=from-cwd\n")
        monkeypatch.chdir(repo / "src")

        result = runner.invoke(app, ["config"])

        assert "from-cwd" in result.stdout
        assert "from-root" not in result.stdout

    def test_a_dotenv_above_the_repository_is_ignored(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (repo.parent / ".env").write_text(f"OPENAI_API_KEY={SECRET}\n")
        monkeypatch.chdir(repo)

        result = runner.invoke(app, ["config"])

        assert ".env file:  none found" in result.stdout
        assert "OPENAI_API_KEY = not set" in result.stdout

    def test_generate_ignores_a_dotenv_above_the_repository(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (repo.parent / ".env").write_text("GITCOMMIT_AI_PROVIDER=anthropic\n")
        monkeypatch.chdir(repo)
        created = use_llm(monkeypatch, scripted_llm(["feat(api): add orders endpoint"]))

        result = runner.invoke(app, ["generate", "--dry-run"])

        assert result.exit_code == 0, result.stderr
        assert created.configs[0].llm_provider.value == "openai"

    def test_generate_reads_the_root_of_the_repository_given_by_path(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (repo / ".env").write_text("GITCOMMIT_AI_PROVIDER=anthropic\n")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        created = use_llm(monkeypatch, scripted_llm(["feat(api): add orders endpoint"]))

        result = runner.invoke(app, ["generate", "--path", str(repo / "src"), "--dry-run"])

        assert result.exit_code == 0, result.stderr
        assert created.configs[0].llm_provider.value == "anthropic"

    def test_config_works_outside_a_repository(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".env").write_text("GITCOMMIT_AI_MODEL=plain-dir\n")
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["config"])

        assert result.exit_code == 0
        assert "plain-dir" in result.stdout

    def test_config_works_without_git_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".env").write_text("GITCOMMIT_AI_MODEL=no-git\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

        result = runner.invoke(app, ["config"])

        assert result.exit_code == 0
        assert "no-git" in result.stdout


class TestGitEntryPoint:
    def test_bare_invocation_runs_generate_with_its_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = scoped_history_repo(tmp_path / "repo")
        stage_file(repo, "src/api/orders.py", "def orders(): ...\n")
        use_llm(monkeypatch, scripted_llm(["feat(api): add orders endpoint"]))

        result = runner.invoke(git_app, ["--path", str(repo), "--dry-run"])

        assert result.exit_code == 0
        assert result.stdout.strip() == "feat(api): add orders endpoint"

    def test_subcommand_does_not_trigger_generation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        llm = scripted_llm([])
        use_llm(monkeypatch, llm)

        result = runner.invoke(git_app, ["config"])

        assert result.exit_code == 0
        assert llm.calls == []
