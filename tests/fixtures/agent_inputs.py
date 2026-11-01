"""Builders for fully prepared ``AgentState`` objects used in agent tests."""

from __future__ import annotations

from git_commit_ai.agents.context_retrieval import make_context_retrieval
from git_commit_ai.agents.diff_budget import build_diff_digest
from git_commit_ai.agents.state import AgentState, CommitType, DiffAnalysis
from git_commit_ai.core.schemas import HistoricalCommit
from tests.fixtures.diffs import make_diff

SCOPED_HISTORY = [
    "feat(api): add pagination",
    "fix(api): handle empty page",
    "docs(readme): update setup guide",
    "feat(cli): add verbose flag",
    "fix(cli): handle error exit",
    "test(api): add pagination tests",
]
GITMOJI_HISTORY = [
    "✨ add pagination",
    "🐛 fix empty page",
    "📝 describe setup",
    "✨ add verbose flag",
    "🐛 fix exit code",
    "✅ cover pagination",
]
BRACKET_HISTORY = [
    "[API] add pagination",
    "[API] fix empty page",
    "[DOCS] describe setup",
    "[CLI] add verbose flag",
    "[CLI] fix exit code",
    "[API] cover pagination",
]
PLAIN_HISTORY = [
    "Add pagination to the list endpoint.",
    "Fix crash on empty page.",
    "Describe setup in the readme.",
    "Add verbose flag to the command line.",
    "Fix exit code on error.",
    "Cover pagination with tests.",
]


def make_analysis(**overrides: object) -> DiffAnalysis:
    """Build a ``DiffAnalysis`` with sensible defaults."""
    values: dict[str, object] = {
        "change_type": CommitType.FEAT,
        "summary": "Add page size option to the API client",
        "intent": "Let callers bound response size",
        "scope_hint": "api",
    }
    values.update(overrides)
    return DiffAnalysis.model_validate(values)


def make_state(
    subjects: list[str] | None = None,
    files: dict[str, int] | None = None,
    analysis: DiffAnalysis | None = None,
    max_diff_chars: int = 10_000,
    with_bodies: bool = False,
) -> AgentState:
    """Build a state that has passed the analyst and context nodes.

    Args:
        subjects: Historical commit subjects, most recent first.
        files: Added files and line counts for the staged diff.
        analysis: Analyst output; a default is used if omitted.
        max_diff_chars: Budget for the diff digest.
        with_bodies: Give every historical commit a body.
    """
    diff = make_diff(files or {"src/api/client.py": 6})
    history = [
        HistoricalCommit(
            hash=f"{i:040d}",
            subject=subject,
            body="Explains why the change was needed." if with_bodies else "",
        )
        for i, subject in enumerate(subjects or [])
    ]
    state = AgentState(
        diff_result=diff,
        history=history,
        diff_digest=build_diff_digest(diff, max_diff_chars),
        analysis=analysis or make_analysis(),
    )
    context = make_context_retrieval()(state)["context"]
    return state.model_copy(update={"context": context})
