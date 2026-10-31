"""Integration tests for DiffExtractor with staged, unstaged, binary, and rename diffs."""

from __future__ import annotations

import subprocess
from pathlib import Path

from git_commit_ai.core.schemas import DiffMode, FileStatus
from git_commit_ai.git.diff import DiffExtractor
from git_commit_ai.git.repository import RepositoryInspector
from tests.fixtures.git_repo import commit_file, init_repo


def test_staged_diff_extraction(populated_repo: Path) -> None:
    """Verify extracting staged diffs with correct statistics and file statuses."""
    inspector = RepositoryInspector(repo_path=str(populated_repo))
    extractor = DiffExtractor(inspector=inspector)

    result = extractor.get_staged_diff()

    assert result.mode == DiffMode.STAGED
    assert result.statistics.files_changed == 2
    assert result.statistics.total_additions > 0
    assert "diff --git" in result.raw_diff

    # Verify per-file changes
    change_map = {fc.path: fc for fc in result.file_changes}
    assert "src/app.py" in change_map
    assert change_map["src/app.py"].status == FileStatus.MODIFIED
    assert "src/staged_new.py" in change_map
    assert change_map["src/staged_new.py"].status == FileStatus.ADDED


def test_unstaged_diff_extraction(populated_repo: Path) -> None:
    """Verify extracting unstaged diffs."""
    inspector = RepositoryInspector(repo_path=str(populated_repo))
    extractor = DiffExtractor(inspector=inspector)

    result = extractor.get_unstaged_diff()

    assert result.mode == DiffMode.UNSTAGED
    assert result.statistics.files_changed == 1
    change = result.file_changes[0]
    assert change.path == "README.md"
    assert change.status == FileStatus.MODIFIED
    assert change.additions >= 1


def test_binary_file_diff_handling(tmp_path: Path) -> None:
    """Verify binary files are detected and marked with is_binary=True."""
    repo = init_repo(tmp_path / "binary_repo")
    commit_file(repo, "README.md", "init\n", "Initial commit")

    # Write a binary file (e.g. non-UTF8 bytes with null byte)
    bin_file = repo / "image.png"
    bin_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01")
    subprocess.run(["git", "-C", str(repo), "add", "image.png"], check=True)

    inspector = RepositoryInspector(repo_path=str(repo))
    extractor = DiffExtractor(inspector=inspector)
    result = extractor.get_staged_diff()

    assert result.statistics.files_changed == 1
    bin_change = result.file_changes[0]
    assert bin_change.path == "image.png"
    assert bin_change.is_binary is True
    assert bin_change.additions == 0
    assert bin_change.deletions == 0


def test_rename_detection(tmp_path: Path) -> None:
    """Verify rename detection sets status=RENAMED and captures old_path."""
    repo = init_repo(tmp_path / "rename_repo")
    commit_file(repo, "original.py", "def compute():\n    return 42\n", "Initial compute")

    # Rename file via git mv
    subprocess.run(
        ["git", "-C", str(repo), "mv", "original.py", "renamed.py"],
        check=True,
    )

    inspector = RepositoryInspector(repo_path=str(repo))
    extractor = DiffExtractor(inspector=inspector)
    result = extractor.get_staged_diff()

    assert result.statistics.files_changed == 1
    change = result.file_changes[0]
    assert change.status == FileStatus.RENAMED
    assert change.path == "renamed.py"
    assert change.old_path == "original.py"


def test_empty_repository_staged_diff(empty_repo: Path) -> None:
    """Verify staged diff in a repo with zero commits diffs against empty tree."""
    new_file = empty_repo / "initial.py"
    new_file.write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(empty_repo), "add", "initial.py"], check=True)

    inspector = RepositoryInspector(repo_path=str(empty_repo))
    extractor = DiffExtractor(inspector=inspector)
    result = extractor.get_staged_diff()

    assert result.statistics.files_changed == 1
    assert result.file_changes[0].path == "initial.py"
    assert result.file_changes[0].status == FileStatus.ADDED
    assert result.file_changes[0].additions == 1
    assert "initial.py" in result.raw_diff
