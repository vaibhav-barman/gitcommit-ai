"""Installing and removing the ``prepare-commit-msg`` hook.

The hook proposes a message when the user runs ``git commit`` without ``-m``.
An existing hook that this tool did not write is never overwritten: it is
moved aside to a backup name and the new hook runs it first, so its behavior
is preserved. Uninstalling restores it.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from git_commit_ai.core.exceptions import HookError

if TYPE_CHECKING:
    from git_commit_ai.git.repository import RepositoryInspector

HOOK_NAME = "prepare-commit-msg"
MANAGED_MARKER = "# git-ai-commit managed hook"
PREVIOUS_MARKER = "# git-ai-commit previous-hook: "
SKIP_VARIABLE = "GITCOMMIT_AI_HOOK"
_BACKUP_SUFFIX = ".pre-ai-commit"
_EXECUTABLE = stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH

_TEMPLATE = """#!/bin/sh
{marker}
{previous_marker}
# Proposes a commit message when you run `git commit` without -m.
#   Skip it for one commit:  {skip}=0 git commit
#   Remove it:               git-ai-commit hook uninstall
{chain}
[ "${{{skip}:-1}}" = "0" ] && exit 0
# $2 is the message source: message (-m/-F), merge, squash and commit
# (--amend, -c, -C) already have a message, so leave them alone.
case "${{2:-}}" in message|merge|squash|commit) exit 0 ;; esac
command -v git-ai-commit >/dev/null 2>&1 || exit 0
git-ai-commit hook run "$1" "${{2:-}}" || true
exit 0
"""

_CHAIN = """hook_dir=$(dirname "$0")
if [ -x "$hook_dir/{previous}" ]; then
    "$hook_dir/{previous}" "$@" || exit $?
fi
"""


@dataclass(frozen=True)
class InstallResult:
    """Outcome of ``HookInstaller.install``."""

    path: Path
    already_installed: bool
    backup: Path | None


@dataclass(frozen=True)
class UninstallResult:
    """Outcome of ``HookInstaller.uninstall``."""

    path: Path
    was_installed: bool
    restored: Path | None


class HookInstaller:
    """Manages the ``prepare-commit-msg`` hook of one repository.

    The hooks directory comes from Git itself (``--git-path hooks``), so
    ``core.hooksPath`` and linked worktrees are respected.

    Args:
        inspector: A ``RepositoryInspector`` bound to the target repository.
    """

    def __init__(self, inspector: RepositoryInspector) -> None:
        self._inspector = inspector

    def hooks_directory(self) -> Path:
        """Return the directory Git reads hooks from.

        Raises:
            NotAGitRepositoryError: If the path is not inside a Git repository.
        """
        self._inspector.get_repository_root()
        output = self._inspector._run_git(["rev-parse", "--git-path", "hooks"]).stdout.strip()
        directory = Path(output)
        if not directory.is_absolute():
            directory = Path(self._inspector._repo_path) / directory
        return directory.resolve()

    def install(self) -> InstallResult:
        """Install the hook, backing up and chaining to any foreign hook.

        Installing again refreshes the script and keeps an existing chain, so it
        is safe to repeat.

        Raises:
            HookError: If the hook cannot be written.
        """
        directory = self.hooks_directory()
        path = directory / HOOK_NAME
        backup: Path | None = None
        previous: str | None = None
        already_installed = False

        if path.exists() or path.is_symlink():
            if self._is_managed(path):
                already_installed = True
                previous = self._previous_hook(path)
            else:
                backup = self._free_backup_path(directory)
                path.rename(backup)
                previous = backup.name

        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(_render(previous), encoding="utf-8")
            path.chmod(_EXECUTABLE)
        except OSError as exc:
            raise HookError(f"Could not write {path}: {exc}") from exc
        return InstallResult(path=path, already_installed=already_installed, backup=backup)

    def uninstall(self) -> UninstallResult:
        """Remove the hook and restore the one it replaced, if any.

        Raises:
            HookError: If the installed hook was not written by this tool.
        """
        directory = self.hooks_directory()
        path = directory / HOOK_NAME
        if not (path.exists() or path.is_symlink()):
            return UninstallResult(path=path, was_installed=False, restored=None)
        if not self._is_managed(path):
            raise HookError(
                f"{path} was not installed by git-ai-commit, so it was left alone. "
                "Remove it yourself if you want it gone."
            )

        previous = self._previous_hook(path)
        path.unlink()
        restored: Path | None = None
        if previous is not None and (directory / previous).exists():
            (directory / previous).rename(path)
            restored = path
        return UninstallResult(path=path, was_installed=True, restored=restored)

    @staticmethod
    def _is_managed(path: Path) -> bool:
        if path.is_symlink() or not path.is_file():
            return False
        return MANAGED_MARKER in path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _previous_hook(path: Path) -> str | None:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(PREVIOUS_MARKER):
                name = line[len(PREVIOUS_MARKER) :].strip()
                return name or None
        return None

    @staticmethod
    def _free_backup_path(directory: Path) -> Path:
        """First unused backup name, so an earlier backup is never overwritten."""
        candidate = directory / f"{HOOK_NAME}{_BACKUP_SUFFIX}"
        number = 0
        while candidate.exists() or candidate.is_symlink():
            number += 1
            candidate = directory / f"{HOOK_NAME}{_BACKUP_SUFFIX}.{number}"
        return candidate


def _render(previous: str | None) -> str:
    return _TEMPLATE.format(
        marker=MANAGED_MARKER,
        previous_marker=f"{PREVIOUS_MARKER}{previous}" if previous else "#",
        skip=SKIP_VARIABLE,
        chain=_CHAIN.format(previous=previous) if previous else "",
    )
