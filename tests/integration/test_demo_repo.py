"""The demo repository and tape: what docs/demo.tape records must actually be there."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from git_commit_ai.agents.conventions import body_expected
from git_commit_ai.context.engine import RepositoryContextEngine
from git_commit_ai.core.schemas import CommitConvention, DiffMode
from git_commit_ai.git.diff import DiffExtractor
from git_commit_ai.git.history import CommitHistoryExtractor
from git_commit_ai.git.repository import RepositoryInspector
from tests.fixtures.git_repo import run_git

ROOT = Path(__file__).resolve().parents[2]
PREPARE = ROOT / "scripts" / "demo" / "prepare.sh"
TAPE = ROOT / "docs" / "demo.tape"
DEFAULT_DEMO_DIR = "/tmp/gitcommit-ai-demo"


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("demo") / "demo"
    subprocess.run(
        ["bash", str(PREPARE)],
        check=True,
        capture_output=True,
        env={**os.environ, "DEMO_DIR": str(target)},
    )
    return target


def test_history_is_scoped_conventional_with_bodies(demo_dir: Path) -> None:
    inspector = RepositoryInspector(repo_path=str(demo_dir / "repo"))
    history = CommitHistoryExtractor(inspector).get_history()
    diff = DiffExtractor(inspector).extract(DiffMode.STAGED)
    context = RepositoryContextEngine().build_context(diff, history)

    assert len(history) == 8
    assert context.convention_result.detected_convention is CommitConvention.SCOPED_CONVENTIONAL
    assert context.convention_result.confidence == 1.0
    assert context.style_profile.typical_capitalization == "lowercase"
    assert context.style_profile.body_usage_frequency == 1.0
    assert body_expected(context, diff)
    assert len(context.historical_examples) > 0


def test_a_change_is_staged_and_nothing_else_is_pending(demo_dir: Path) -> None:
    repo = demo_dir / "repo"
    assert run_git(repo, "diff", "--cached", "--name-only").stdout.split() == [
        "src/orders/api.py",
        "tests/test_api.py",
    ]
    assert run_git(repo, "status", "--porcelain").stdout.splitlines() == [
        "M  src/orders/api.py",
        "M  tests/test_api.py",
    ]


def test_the_shim_is_executable_and_points_at_this_checkout(demo_dir: Path) -> None:
    shim = demo_dir / "bin" / "git-ai-commit"
    assert os.access(shim, os.X_OK)
    text = shim.read_text(encoding="utf-8")
    assert f'--project "{ROOT}"' in text
    assert "git-ai-commit" in text


def test_the_tape_records_the_prepared_repository_and_the_documented_output() -> None:
    tape = TAPE.read_text(encoding="utf-8")
    assert "Output docs/assets/demo.gif" in tape
    assert f"cd {DEFAULT_DEMO_DIR}/repo" in tape
    assert f"PATH={DEFAULT_DEMO_DIR}/bin:" in tape
    assert 'Type "git ai-commit --verbose"' in tape
    assert "Wait+Screen" in tape


def test_the_makefile_target_runs_prepare_then_vhs() -> None:
    lines = (ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    assert "demo:" in lines
    assert lines.index("\tbash scripts/demo/prepare.sh") < lines.index("\tvhs docs/demo.tape")
