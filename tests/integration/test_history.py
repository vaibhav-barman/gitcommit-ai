"""Integration tests for CommitHistoryExtractor."""

from __future__ import annotations

import subprocess
from pathlib import Path

from git_commit_ai.git.history import CommitHistoryExtractor
from git_commit_ai.git.repository import RepositoryInspector
from tests.fixtures.git_repo import commit_file, init_repo


def test_empty_repo_history(empty_repo: Path) -> None:
    """Verify empty repository returns empty list of historical commits."""
    inspector = RepositoryInspector(repo_path=str(empty_repo))
    extractor = CommitHistoryExtractor(inspector=inspector)
    commits = extractor.get_history()

    assert commits == []


def test_fewer_than_requested_commits(repo_with_initial_commit: Path) -> None:
    """Verify history retrieval when repo has fewer commits than the requested count."""
    inspector = RepositoryInspector(repo_path=str(repo_with_initial_commit))
    extractor = CommitHistoryExtractor(inspector=inspector)
    commits = extractor.get_history(count=10)

    assert len(commits) == 1
    assert commits[0].subject == "Initial commit"
    assert len(commits[0].hash) == 40
    assert commits[0].body == ""


def test_multiline_commit_body_and_special_characters(tmp_path: Path) -> None:
    """Verify parsing multiline bodies, empty bodies, and special characters."""
    repo = init_repo(tmp_path / "history_repo")

    # Commit 1: with empty body
    commit_file(repo, "file1.txt", "1", "feat(core): simple subject without body")

    # Commit 2: with multiline body and special characters
    multiline_msg = (
        'fix(git): resolve edge-case with "quotes", [brackets], and $pecial #chars!\n\n'
        "This is paragraph 1 of the body with detailed explanations.\n"
        "- Bullet point 1: item 'one'\n"
        "- Bullet point 2: item `two`\n\n"
        "Closing paragraph with reference #123."
    )
    file2 = repo / "file2.txt"
    file2.write_text("2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "file2.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", multiline_msg],
        check=True,
    )

    inspector = RepositoryInspector(repo_path=str(repo))
    extractor = CommitHistoryExtractor(inspector=inspector)
    commits = extractor.get_history(count=5)

    assert len(commits) == 2

    # Most recent commit first
    latest = commits[0]
    expected_subject = 'fix(git): resolve edge-case with "quotes", [brackets], and $pecial #chars!'
    assert latest.subject == expected_subject
    assert "This is paragraph 1 of the body" in latest.body
    assert "- Bullet point 1: item 'one'" in latest.body
    assert "Closing paragraph with reference #123." in latest.body

    first = commits[1]
    assert first.subject == "feat(core): simple subject without body"
    assert first.body == ""


def test_skips_merge_commits(tmp_path: Path) -> None:
    """Verify merge commits are excluded from commit history."""
    repo = init_repo(tmp_path / "merge_repo")
    commit_file(repo, "base.txt", "base", "feat: base commit")

    # Create and switch to feature branch
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feature"], check=True)
    commit_file(repo, "feature.txt", "feature", "feat: feature branch commit")

    # Switch back to main and commit another change
    subprocess.run(["git", "-C", str(repo), "checkout", "main"], check=True)
    commit_file(repo, "main.txt", "main", "feat: main branch commit")

    # Merge feature branch with a merge commit
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "feature", "-m", "Merge branch 'feature'"],
        check=True,
    )

    inspector = RepositoryInspector(repo_path=str(repo))
    extractor = CommitHistoryExtractor(inspector=inspector)
    commits = extractor.get_history()

    # Should only contain the 3 non-merge commits, not the merge commit
    subjects = [c.subject for c in commits]
    assert "Merge branch 'feature'" not in subjects
    assert "feat: main branch commit" in subjects
    assert "feat: feature branch commit" in subjects
    assert "feat: base commit" in subjects
    assert len(commits) == 3


def test_history_can_start_from_another_revision(tmp_path: Path) -> None:
    """Starting from an older revision hides everything that came after it."""
    repo = init_repo(tmp_path / "revision_repo")
    for index in range(4):
        commit_file(repo, f"f{index}.txt", str(index), f"feat: add f{index}")
    extractor = CommitHistoryExtractor(inspector=RepositoryInspector(repo_path=str(repo)))

    assert [c.subject for c in extractor.get_history(revision="HEAD~1")] == [
        "feat: add f2",
        "feat: add f1",
        "feat: add f0",
    ]
    assert [c.subject for c in extractor.get_history(revision="HEAD~1", exclude_head=True)] == [
        "feat: add f1",
        "feat: add f0",
    ]
    assert [c.subject for c in extractor.get_history(count=1, revision="HEAD~2")] == [
        "feat: add f1"
    ]


def test_history_before_the_root_commit_is_empty(repo_with_initial_commit: Path) -> None:
    extractor = CommitHistoryExtractor(
        inspector=RepositoryInspector(repo_path=str(repo_with_initial_commit))
    )
    assert extractor.get_history(revision="HEAD^") == []
    assert extractor.get_history(revision="no-such-revision") == []
