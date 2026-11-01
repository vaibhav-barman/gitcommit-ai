"""Hook installation and the generated script, tested against real repositories."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from git_commit_ai.core.config import GitCommitAIConfig
from git_commit_ai.core.exceptions import HookError
from git_commit_ai.git.hooks import HOOK_NAME, MANAGED_MARKER, HookInstaller
from git_commit_ai.git.repository import RepositoryInspector
from tests.fixtures.git_repo import (
    commit_count,
    head_message,
    install_hook,
    run_git,
    stage_file,
)

FOREIGN = '#!/bin/sh\necho foreign >> "$STUB_LOG"\n'


def _installer(repo: Path) -> HookInstaller:
    return HookInstaller(RepositoryInspector(repo_path=str(repo), config=GitCommitAIConfig()))


def _hook(repo: Path) -> Path:
    return repo / ".git" / "hooks" / HOOK_NAME


class TestInstall:
    def test_installs_an_executable_managed_hook(self, repo_with_initial_commit: Path) -> None:
        result = _installer(repo_with_initial_commit).install()

        hook = _hook(repo_with_initial_commit)
        assert result.path == hook.resolve()
        assert not result.already_installed
        assert result.backup is None
        assert MANAGED_MARKER in hook.read_text()
        assert hook.stat().st_mode & stat.S_IXUSR

    def test_reinstalling_refreshes_without_creating_a_backup(
        self, repo_with_initial_commit: Path
    ) -> None:
        installer = _installer(repo_with_initial_commit)
        installer.install()
        second = installer.install()

        assert second.already_installed
        assert second.backup is None
        names = [p.name for p in _hook(repo_with_initial_commit).parent.iterdir()]
        assert not [n for n in names if "pre-ai-commit" in n]

    def test_existing_hook_is_backed_up_untouched_and_chained(
        self, repo_with_initial_commit: Path
    ) -> None:
        repo = repo_with_initial_commit
        foreign = install_hook(repo, HOOK_NAME, FOREIGN)
        original_mode = foreign.stat().st_mode

        result = _installer(repo).install()

        assert result.backup is not None
        assert result.backup.read_text() == FOREIGN
        assert result.backup.stat().st_mode == original_mode
        assert MANAGED_MARKER in _hook(repo).read_text()
        assert result.backup.name in _hook(repo).read_text()

    def test_reinstalling_over_a_chained_hook_keeps_the_chain_and_adds_no_backup(
        self, repo_with_initial_commit: Path
    ) -> None:
        repo = repo_with_initial_commit
        install_hook(repo, HOOK_NAME, FOREIGN)
        installer = _installer(repo)
        first = installer.install()
        second = installer.install()

        assert second.already_installed
        assert second.backup is None
        assert first.backup is not None
        assert first.backup.name in _hook(repo).read_text()

    def test_an_earlier_backup_is_never_overwritten(self, repo_with_initial_commit: Path) -> None:
        repo = repo_with_initial_commit
        install_hook(repo, HOOK_NAME, "#!/bin/sh\n# first\n")
        first = _installer(repo).install().backup
        install_hook(repo, HOOK_NAME, "#!/bin/sh\n# second\n")
        second = _installer(repo).install().backup

        assert first is not None
        assert second is not None
        assert first != second
        assert "# first" in first.read_text()
        assert "# second" in second.read_text()

    def test_core_hooks_path_is_respected(self, repo_with_initial_commit: Path) -> None:
        repo = repo_with_initial_commit
        shared = repo / "shared-hooks"
        run_git(repo, "config", "core.hooksPath", str(shared))

        result = _installer(repo).install()

        assert result.path == (shared / HOOK_NAME).resolve()
        assert not _hook(repo).exists()


class TestUninstall:
    def test_removes_the_hook(self, repo_with_initial_commit: Path) -> None:
        installer = _installer(repo_with_initial_commit)
        installer.install()
        result = installer.uninstall()
        assert result.was_installed
        assert result.restored is None
        assert not _hook(repo_with_initial_commit).exists()

    def test_restores_the_hook_it_replaced(self, repo_with_initial_commit: Path) -> None:
        repo = repo_with_initial_commit
        foreign = install_hook(repo, HOOK_NAME, FOREIGN)
        mode = foreign.stat().st_mode
        installer = _installer(repo)
        installer.install()

        result = installer.uninstall()

        assert result.restored is not None
        assert _hook(repo).read_text() == FOREIGN
        assert _hook(repo).stat().st_mode == mode
        assert [p.name for p in _hook(repo).parent.iterdir() if "pre-ai-commit" in p.name] == []

    def test_nothing_installed_is_not_an_error(self, repo_with_initial_commit: Path) -> None:
        result = _installer(repo_with_initial_commit).uninstall()
        assert not result.was_installed

    def test_refuses_to_remove_a_hook_it_did_not_write(
        self, repo_with_initial_commit: Path
    ) -> None:
        install_hook(repo_with_initial_commit, HOOK_NAME, FOREIGN)
        with pytest.raises(HookError, match="not installed by git-ai-commit"):
            _installer(repo_with_initial_commit).uninstall()
        assert _hook(repo_with_initial_commit).read_text() == FOREIGN


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    """A fake `git-ai-commit` that logs its arguments and writes a fixed proposal."""
    directory = tmp_path / "stub-bin"
    directory.mkdir()
    stub = directory / "git-ai-commit"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "$@" >> "$STUB_LOG"\n'
        'if [ "$1 $2" = "hook run" ]; then\n'
        '  { echo "feat: stub proposal"; echo; cat "$3"; } > "$3.new" && mv "$3.new" "$3"\n'
        "fi\n"
    )
    stub.chmod(0o755)
    return directory


class TestGeneratedScript:
    """Run the installed script through real `git commit`."""

    @pytest.fixture
    def repo(self, repo_with_initial_commit: Path) -> Path:
        stage_file(repo_with_initial_commit, "app.py", "x = 1\n")
        _installer(repo_with_initial_commit).install()
        return repo_with_initial_commit

    def _env(self, stub_bin: Path | None, log: Path, **extra: str) -> dict[str, str]:
        git_dir = str(Path(shutil.which("git") or "/usr/bin/git").parent)
        path = os.pathsep.join(
            ([str(stub_bin)] if stub_bin else []) + [git_dir, "/usr/bin", "/bin"]
        )
        return {
            "PATH": path,
            "HOME": str(log.parent),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_EDITOR": "true",
            "STUB_LOG": str(log),
            **extra,
        }

    def test_plain_git_commit_gets_the_proposal(
        self, repo: Path, stub_bin: Path, tmp_path: Path
    ) -> None:
        log = tmp_path / "log.txt"
        run_git(repo, "commit", env=self._env(stub_bin, log))
        assert head_message(repo) == "feat: stub proposal"
        assert log.read_text().startswith("hook run ")

    def test_commit_with_a_message_is_left_alone(
        self, repo: Path, stub_bin: Path, tmp_path: Path
    ) -> None:
        log = tmp_path / "log.txt"
        run_git(repo, "commit", "-m", "chore: manual", env=self._env(stub_bin, log))
        assert head_message(repo) == "chore: manual"
        assert not log.exists()

    def test_amend_is_left_alone(self, repo: Path, stub_bin: Path, tmp_path: Path) -> None:
        log = tmp_path / "log.txt"
        run_git(repo, "commit", "--amend", "--no-edit", env=self._env(stub_bin, log))
        assert not log.exists()

    def test_environment_switch_skips_the_hook(
        self, repo: Path, stub_bin: Path, tmp_path: Path
    ) -> None:
        log = tmp_path / "log.txt"
        env = self._env(stub_bin, log, GITCOMMIT_AI_HOOK="0")
        run_git(repo, "commit", "--allow-empty-message", env=env)
        assert not log.exists()
        assert commit_count(repo) == 2

    def test_missing_executable_never_blocks_the_commit(self, repo: Path, tmp_path: Path) -> None:
        log = tmp_path / "log.txt"
        run_git(repo, "commit", "--allow-empty-message", env=self._env(None, log))
        assert commit_count(repo) == 2

    def test_a_failing_proposal_never_blocks_the_commit(self, repo: Path, tmp_path: Path) -> None:
        failing = tmp_path / "failing-bin"
        failing.mkdir()
        (failing / "git-ai-commit").write_text("#!/bin/sh\nexit 3\n")
        (failing / "git-ai-commit").chmod(0o755)
        run_git(repo, "commit", "--allow-empty-message", env=self._env(failing, tmp_path / "l"))
        assert commit_count(repo) == 2

    def test_a_chained_hook_runs_first_and_can_veto_the_commit(
        self, repo_with_initial_commit: Path, stub_bin: Path, tmp_path: Path
    ) -> None:
        repo = repo_with_initial_commit
        stage_file(repo, "app.py", "x = 1\n")
        install_hook(repo, HOOK_NAME, FOREIGN)
        _installer(repo).install()
        log = tmp_path / "log.txt"

        run_git(repo, "commit", env=self._env(stub_bin, log))

        lines = log.read_text().splitlines()
        assert lines[0] == "foreign"
        assert lines[1].startswith("hook run")

        stage_file(repo, "more.py", "y = 1\n")
        vetoing = repo / ".git" / "hooks" / "prepare-commit-msg.pre-ai-commit"
        vetoing.write_text("#!/bin/sh\nexit 1\n")
        with pytest.raises(subprocess.CalledProcessError):
            run_git(repo, "commit", env=self._env(stub_bin, log))
