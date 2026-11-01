"""The evaluation report: typed records, aggregation, and JSON/Markdown rendering."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA_VERSION = 1

# Metric key -> label, in the order they appear in the summary table.
METRIC_LABELS: dict[str, str] = {
    "convention_match": "Convention matches the real commit",
    "subject_length_ok": "Subject length within the repository's profile",
    "imperative_mood": "Imperative mood",
    "capitalization_agrees": "Capitalization agrees with the real commit",
    "punctuation_agrees": "Trailing punctuation agrees with the real commit",
    "scope_agrees": "Scope presence agrees with the real commit",
    "body_agrees": "Body presence agrees with the real commit",
    "critic_clean_before": "Critic finds no errors in the first draft",
    "critic_clean_after": "Critic finds no errors in the final draft",
}
_CELL_WIDTH = 48


class ViolationCounts(BaseModel):
    """Critic findings on one draft."""

    errors: int = Field(ge=0)
    warnings: int = Field(ge=0)
    rules: list[str] = Field(description="Distinct rule names that fired, sorted")


class Checks(BaseModel):
    """Deterministic checks on the generated message.

    ``None`` means the check does not apply to this commit, and it is left out of
    the aggregate rather than counted as a pass.
    """

    convention_match: bool
    subject_length_ok: bool
    imperative_mood: bool
    capitalization_agrees: bool | None
    punctuation_agrees: bool
    scope_agrees: bool | None
    body_agrees: bool


class Outcome(BaseModel):
    """What the agent graph produced for one commit, and how it scored."""

    generated_message: str
    real_convention: str
    generated_convention: str
    iterations: int = Field(ge=1)
    approved: bool
    interrupted: bool
    checks: Checks
    before: ViolationCounts = Field(description="Critic findings on the first draft")
    after: ViolationCounts = Field(description="Critic findings on the draft that was kept")


class CommitRecord(BaseModel):
    """One replayed commit: a score, or the reason there is none."""

    hash: str
    real_subject: str
    error: str | None = None
    outcome: Outcome | None = None


class MetricRate(BaseModel):
    """How often a check passed among the commits it applied to."""

    passed: int = Field(ge=0)
    applicable: int = Field(ge=0)
    rate: float | None = Field(description="passed / applicable, or null when nothing applied")


class Aggregate(BaseModel):
    """Totals over every successfully evaluated commit."""

    evaluated: int
    failed: int
    interrupted: int
    metrics: dict[str, MetricRate]
    mean_iterations: float | None
    mean_errors_before: float | None
    mean_errors_after: float | None
    mean_warnings_before: float | None
    mean_warnings_after: float | None


class Report(BaseModel):
    """The complete, serializable result of one evaluation run."""

    schema_version: int = SCHEMA_VERSION
    generated_at: datetime
    repository: str
    mode: str = Field(description="'offline' (deterministic stand-in) or 'live'")
    provider: str
    model: str
    requested: int
    max_iterations: int
    commits: list[CommitRecord]
    aggregate: Aggregate


def aggregate(records: list[CommitRecord]) -> Aggregate:
    """Summarize ``records``; failed commits are counted but never scored."""
    outcomes = [r.outcome for r in records if r.outcome is not None]
    columns: dict[str, list[bool | None]] = {
        key: [getattr(o.checks, key) for o in outcomes] for key in list(Checks.model_fields)
    }
    columns["critic_clean_before"] = [o.before.errors == 0 for o in outcomes]
    columns["critic_clean_after"] = [o.after.errors == 0 for o in outcomes]

    return Aggregate(
        evaluated=len(outcomes),
        failed=len(records) - len(outcomes),
        interrupted=sum(1 for o in outcomes if o.interrupted),
        metrics={key: _rate(columns[key]) for key in METRIC_LABELS},
        mean_iterations=_mean([o.iterations for o in outcomes]),
        mean_errors_before=_mean([o.before.errors for o in outcomes]),
        mean_errors_after=_mean([o.after.errors for o in outcomes]),
        mean_warnings_before=_mean([o.before.warnings for o in outcomes]),
        mean_warnings_after=_mean([o.after.warnings for o in outcomes]),
    )


def _rate(values: list[bool | None]) -> MetricRate:
    applicable = [v for v in values if v is not None]
    passed = sum(applicable)
    return MetricRate(
        passed=passed,
        applicable=len(applicable),
        rate=passed / len(applicable) if applicable else None,
    )


def _mean(values: list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def render_markdown(report: Report) -> str:
    """Render ``report`` as a Markdown summary: aggregate table, then one row per commit."""
    agg = report.aggregate
    source = (
        "offline: a deterministic stand-in, so these numbers say nothing about any model"
        if report.mode == "offline"
        else f"live: {report.provider}/{report.model}"
    )
    lines = [
        "# Evaluation report",
        "",
        f"- Repository: `{report.repository}`",
        f"- Source: {source}",
        f"- Commits: {report.requested} requested, {agg.evaluated} evaluated, "
        f"{agg.failed} failed, {agg.interrupted} interrupted by a provider error",
        f"- Critic rounds allowed: {report.max_iterations}",
        "",
        "## Summary",
        "",
        "| Metric | Passed | Applicable | Rate |",
        "|---|---:|---:|---:|",
    ]
    for key, label in METRIC_LABELS.items():
        metric = agg.metrics[key]
        lines.append(
            f"| {label} | {metric.passed} | {metric.applicable} | {_percent(metric.rate)} |"
        )
    lines += [
        "",
        f"- Mean rounds used: {_number(agg.mean_iterations)}",
        f"- Mean critic errors, first draft to final: "
        f"{_number(agg.mean_errors_before)} to {_number(agg.mean_errors_after)}",
        f"- Mean critic warnings, first draft to final: "
        f"{_number(agg.mean_warnings_before)} to {_number(agg.mean_warnings_after)}",
        "",
        "## Commits",
        "",
        "| Commit | Real subject | Generated subject | Rounds | Errors | Failed checks |",
        "|---|---|---|---:|---:|---|",
    ]
    for record in report.commits:
        lines.append(_commit_row(record))
    return "\n".join(lines) + "\n"


def _commit_row(record: CommitRecord) -> str:
    short = record.hash[:7]
    real = _cell(record.real_subject)
    outcome = record.outcome
    if outcome is None:
        return f"| `{short}` | {real} | _failed: {_cell(record.error or 'unknown')}_ | | | |"
    generated = _cell(outcome.generated_message.splitlines()[0])
    failed = [key for key in Checks.model_fields if getattr(outcome.checks, key) is False]
    return (
        f"| `{short}` | {real} | {generated} | {outcome.iterations} | "
        f"{outcome.before.errors} to {outcome.after.errors} | {', '.join(failed) or 'none'} |"
    )


def _cell(text: str) -> str:
    flat = " ".join(text.split()).replace("|", "\\|")
    return flat if len(flat) <= _CELL_WIDTH else flat[: _CELL_WIDTH - 1] + "…"


def _percent(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate * 100:.1f}%"


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def write_reports(report: Report, output_dir: Path) -> tuple[Path, Path]:
    """Write ``report.json`` and ``report.md`` into ``output_dir`` and return their paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
