"""Typed state shared by every node of the commit-generation graph."""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field

from git_commit_ai.core.exceptions import AgentStateError
from git_commit_ai.core.schemas import DiffResult, HistoricalCommit, RepositoryContext


class CommitType(enum.StrEnum):
    """Conventional change categories the analyst can assign."""

    FEAT = "feat"
    FIX = "fix"
    DOCS = "docs"
    STYLE = "style"
    REFACTOR = "refactor"
    PERF = "perf"
    TEST = "test"
    BUILD = "build"
    CI = "ci"
    CHORE = "chore"
    REVERT = "revert"


class Severity(enum.StrEnum):
    """How strongly a critic finding blocks approval."""

    ERROR = "error"
    WARNING = "warning"


class DiffDigest(BaseModel):
    """A prompt-sized rendering of the staged diff."""

    text: str = Field(description="File summary followed by (possibly truncated) patches")
    was_truncated: bool = Field(default=False, description="True if any patch was cut or omitted")
    omitted_files: list[str] = Field(
        default_factory=list,
        description="Files listed by name only because the budget ran out",
    )


class DiffAnalysis(BaseModel):
    """The analyst's reading of what a diff does and why."""

    change_type: CommitType = Field(description="Dominant kind of change")
    summary: str = Field(min_length=1, description="One-sentence description of the change")
    intent: str = Field(default="", description="Why the change was likely made")
    is_breaking: bool = Field(default=False, description="Whether it breaks public behaviour")
    scope_hint: str | None = Field(default=None, description="Suggested commit scope, if any")


class Violation(BaseModel):
    """A single finding raised by the critic."""

    rule: str = Field(description="Stable identifier of the rule that fired")
    message: str = Field(description="Actionable explanation for the generator")
    severity: Severity = Field(default=Severity.ERROR)


class Critique(BaseModel):
    """Structured critic feedback on one draft."""

    violations: list[Violation] = Field(default_factory=list)

    @property
    def approved(self) -> bool:
        """A draft is approved when it has no error-level violations."""
        return not any(v.severity is Severity.ERROR for v in self.violations)

    @property
    def penalty(self) -> int:
        """Weighted violation count used to rank drafts; lower is better."""
        return sum(10 if v.severity is Severity.ERROR else 1 for v in self.violations)


class Draft(BaseModel):
    """A proposed commit message and the critic's verdict on it."""

    message: str
    iteration: int = Field(ge=1, description="Generation round that produced this draft")
    critique: Critique | None = None


class AgentState(BaseModel):
    """Everything the nodes read and write while the graph runs."""

    diff_result: DiffResult
    history: list[HistoricalCommit] = Field(default_factory=list)
    diff_digest: DiffDigest | None = None
    analysis: DiffAnalysis | None = None
    context: RepositoryContext | None = None
    drafts: list[Draft] = Field(default_factory=list)
    iteration: int = Field(default=0, ge=0, description="Completed generation rounds")

    def drafting_inputs(self) -> tuple[DiffDigest, DiffAnalysis, RepositoryContext]:
        """Return the upstream results that generator and critic need.

        Raises:
            AgentStateError: If the analyst or context node has not run yet.
        """
        if self.diff_digest is None or self.analysis is None:
            raise AgentStateError("analysis")
        if self.context is None:
            raise AgentStateError("context")
        return self.diff_digest, self.analysis, self.context
