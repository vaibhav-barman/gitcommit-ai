"""Integration tests for SnapshotManager."""

from __future__ import annotations

import subprocess
from pathlib import Path

from git_commit_ai.git.repository import RepositoryInspector
from git_commit_ai.git.snapshots import SnapshotManager
from tests.fixtures.git_repo import commit_file, init_repo


def test_snapshot_capture_and_comparison_identical(repo_with_initial_commit: Path) -> None:
    """Verify snapshot equality when staged changes do not alter."""
    repo = repo_with_initial_commit
    inspector = RepositoryInspector(repo_path=str(repo))
    manager = SnapshotManager(inspector=inspector)

    snapshot1 = manager.capture()
    snapshot2 = manager.capture()

    assert manager.compare(snapshot1, snapshot2) is True
    assert manager.has_changed_since(snapshot1) is False


def test_snapshot_detects_new_staged_content(repo_with_initial_commit: Path) -> None:
    """Verify snapshot changes when files are staged."""
    repo = repo_with_initial_commit
    inspector = RepositoryInspector(repo_path=str(repo))
    manager = SnapshotManager(inspector=inspector)

    before_snapshot = manager.capture()

    # Stage a new file
    new_file = repo / "added.py"
    new_file.write_text("print('staged')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "added.py"], check=True)

    after_snapshot = manager.capture()

    assert before_snapshot.tree_hash != after_snapshot.tree_hash
    assert manager.compare(before_snapshot, after_snapshot) is False
    assert manager.has_changed_since(before_snapshot) is True
    assert "added.py" in after_snapshot.staged_files


def test_snapshot_detects_modified_staged_content(tmp_path: Path) -> None:
    """Verify snapshot detects changes when already-staged file is updated and re-staged."""
    repo = init_repo(tmp_path / "staged_mod_repo")
    commit_file(repo, "target.txt", "v1\n", "Initial commit")

    inspector = RepositoryInspector(repo_path=str(repo))
    manager = SnapshotManager(inspector=inspector)

    # First staged modification
    f = repo / "target.txt"
    f.write_text("v2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "target.txt"], check=True)
    snap1 = manager.capture()

    # Second staged modification
    f.write_text("v3\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "target.txt"], check=True)
    snap2 = manager.capture()

    assert manager.compare(snap1, snap2) is False
    assert manager.has_changed_since(snap1) is True


def test_empty_repo_snapshot(empty_repo: Path) -> None:
    """Verify snapshot in empty repository generates deterministic tree hash."""
    inspector = RepositoryInspector(repo_path=str(empty_repo))
    manager = SnapshotManager(inspector=inspector)

    snapshot = manager.capture()
    assert snapshot.tree_hash != ""
    assert snapshot.staged_files == []
