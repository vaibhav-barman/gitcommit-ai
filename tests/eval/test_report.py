"""Aggregation arithmetic and the JSON and Markdown renderings."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from scripts.eval.report import (
    METRIC_LABELS,
    SCHEMA_VERSION,
    Checks,
    CommitRecord,
    Outcome,
    Report,
    ViolationCounts,
    aggregate,
    render_markdown,
    write_reports,
)

if TYPE_CHECKING:
    from pathlib import Path

PASSING = Checks(
    convention_match=True,
    subject_length_ok=True,
    imperative_mood=True,
    capitalization_agrees=True,
    punctuation_agrees=True,
    scope_agrees=True,
    body_agrees=True,
)


def _record(
    name: str = "a",
    *,
    checks: Checks = PASSING,
    iterations: int = 1,
    errors: tuple[int, int] = (0, 0),
    warnings: tuple[int, int] = (0, 0),
    interrupted: bool = False,
    generated: str = "feat: add x",
) -> CommitRecord:
    return CommitRecord(
        hash=name * 40,
        real_subject=f"real {name}",
        outcome=Outcome(
            generated_message=generated,
            real_convention="conventional",
            generated_convention="conventional",
            iterations=iterations,
            approved=errors[1] == 0,
            interrupted=interrupted,
            checks=checks,
            before=ViolationCounts(errors=errors[0], warnings=warnings[0], rules=[]),
            after=ViolationCounts(errors=errors[1], warnings=warnings[1], rules=[]),
        ),
    )


def _failed(name: str = "f") -> CommitRecord:
    return CommitRecord(hash=name * 40, real_subject="real f", error="rate limit")


def _report(records: list[CommitRecord], mode: str = "offline") -> Report:
    return Report(
        generated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        repository="/repo",
        mode=mode,
        provider="fake",
        model="offline-heuristic",
        requested=len(records),
        max_iterations=3,
        commits=records,
        aggregate=aggregate(records),
    )


class TestAggregate:
    def test_rates_are_passed_over_applicable(self) -> None:
        records = [
            _record("a"),
            _record("b", checks=PASSING.model_copy(update={"imperative_mood": False})),
            _record("c", checks=PASSING.model_copy(update={"imperative_mood": False})),
            _record("d"),
        ]
        metric = aggregate(records).metrics["imperative_mood"]
        assert (metric.passed, metric.applicable, metric.rate) == (2, 4, 0.5)

    def test_inapplicable_checks_are_left_out_of_the_denominator(self) -> None:
        records = [
            _record("a"),
            _record("b", checks=PASSING.model_copy(update={"scope_agrees": None})),
            _record("c", checks=PASSING.model_copy(update={"scope_agrees": False})),
        ]
        metric = aggregate(records).metrics["scope_agrees"]
        assert (metric.passed, metric.applicable) == (1, 2)
        assert metric.rate == 0.5

    def test_a_check_that_never_applied_has_no_rate(self) -> None:
        records = [_record(checks=PASSING.model_copy(update={"capitalization_agrees": None}))]
        metric = aggregate(records).metrics["capitalization_agrees"]
        assert (metric.passed, metric.applicable, metric.rate) == (0, 0, None)

    def test_critic_cleanliness_before_and_after_reflection(self) -> None:
        records = [
            _record("a", errors=(0, 0)),
            _record("b", errors=(3, 0)),
            _record("c", errors=(2, 1)),
            _record("d", errors=(4, 0)),
        ]
        agg = aggregate(records)
        assert (
            agg.metrics["critic_clean_before"].passed,
            agg.metrics["critic_clean_after"].passed,
        ) == (1, 3)
        assert agg.mean_errors_before == pytest.approx(9 / 4)
        assert agg.mean_errors_after == pytest.approx(1 / 4)

    def test_means_of_iterations_and_warnings(self) -> None:
        records = [
            _record("a", iterations=1, warnings=(0, 1)),
            _record("b", iterations=3, warnings=(2, 1)),
        ]
        agg = aggregate(records)
        assert agg.mean_iterations == 2.0
        assert agg.mean_warnings_before == 1.0
        assert agg.mean_warnings_after == 1.0

    def test_failed_commits_are_counted_but_never_scored(self) -> None:
        agg = aggregate([_record("a"), _failed(), _failed("g")])
        assert (agg.evaluated, agg.failed) == (1, 2)
        assert agg.metrics["convention_match"].applicable == 1

    def test_interrupted_runs_are_counted(self) -> None:
        agg = aggregate(
            [_record("a", interrupted=True), _record("b"), _record("c", interrupted=True)]
        )
        assert agg.interrupted == 2

    def test_no_commits_gives_empty_but_valid_totals(self) -> None:
        agg = aggregate([_failed()])
        assert agg.evaluated == 0
        assert agg.mean_iterations is None
        assert agg.mean_errors_before is None
        assert all(m.rate is None for m in agg.metrics.values())

    def test_every_labelled_metric_is_aggregated(self) -> None:
        assert set(aggregate([_record()]).metrics) == set(METRIC_LABELS)


class TestMarkdown:
    def test_summary_table_shows_counts_and_percentages(self) -> None:
        records = [
            _record("a"),
            _record("b", checks=PASSING.model_copy(update={"punctuation_agrees": False})),
            _record("c"),
        ]
        markdown = render_markdown(_report(records))
        assert "| Trailing punctuation agrees with the real commit | 2 | 3 | 66.7% |" in markdown
        assert "| Imperative mood | 3 | 3 | 100.0% |" in markdown
        assert "3 requested, 3 evaluated, 0 failed, 0 interrupted" in markdown

    def test_inapplicable_metrics_read_na(self) -> None:
        records = [_record(checks=PASSING.model_copy(update={"scope_agrees": None}))]
        assert "| Scope presence agrees with the real commit | 0 | 0 | n/a |" in render_markdown(
            _report(records)
        )

    def test_offline_runs_are_labelled_as_saying_nothing_about_a_model(self) -> None:
        assert "say nothing about any model" in render_markdown(_report([_record()]))

    def test_live_runs_name_the_model(self) -> None:
        report = _report([_record()], mode="live").model_copy(
            update={"provider": "openai", "model": "gpt-x"}
        )
        markdown = render_markdown(report)
        assert "live: openai/gpt-x" in markdown
        assert "say nothing" not in markdown

    def test_per_commit_rows_show_rounds_error_change_and_failed_checks(self) -> None:
        bad = PASSING.model_copy(update={"imperative_mood": False, "body_agrees": False})
        markdown = render_markdown(
            _report([_record("a", iterations=2, errors=(4, 1), checks=bad, generated="Added x")])
        )
        assert (
            "| `aaaaaaa` | real a | Added x | 2 | 4 to 1 | imperative_mood, body_agrees |"
            in markdown
        )

    def test_failed_commits_show_their_reason(self) -> None:
        markdown = render_markdown(_report([_failed()]))
        assert "_failed: rate limit_" in markdown

    def test_table_cells_are_escaped_and_shortened(self) -> None:
        record = _record(generated="feat: a | b " + "x" * 100)
        row = next(
            line for line in render_markdown(_report([record])).splitlines() if "`aaaaaaa`" in line
        )
        assert "a \\| b" in row
        assert "…" in row
        assert row.count("|") - row.count("\\|") == 7


class TestJson:
    def test_files_are_written_and_the_json_round_trips(self, tmp_path: Path) -> None:
        report = _report([_record("a"), _failed()])
        json_path, markdown_path = write_reports(report, tmp_path / "nested" / "out")

        assert json_path.name == "report.json"
        assert markdown_path.name == "report.md"
        assert Report.model_validate_json(json_path.read_text(encoding="utf-8")) == report

    def test_schema(self, tmp_path: Path) -> None:
        json_path, _ = write_reports(_report([_record("a"), _failed()]), tmp_path)
        data = json.loads(json_path.read_text(encoding="utf-8"))

        assert list(data) == [
            "schema_version",
            "generated_at",
            "repository",
            "mode",
            "provider",
            "model",
            "requested",
            "max_iterations",
            "commits",
            "aggregate",
        ]
        assert data["schema_version"] == SCHEMA_VERSION
        assert data["generated_at"].startswith("2026-01-02T03:04:05")
        assert set(data["commits"][0]) == {"hash", "real_subject", "error", "outcome"}
        assert set(data["commits"][0]["outcome"]) == {
            "generated_message",
            "real_convention",
            "generated_convention",
            "iterations",
            "approved",
            "interrupted",
            "checks",
            "before",
            "after",
        }
        assert set(data["commits"][0]["outcome"]["checks"]) == set(Checks.model_fields)
        assert set(data["commits"][0]["outcome"]["before"]) == {"errors", "warnings", "rules"}
        assert data["commits"][1]["outcome"] is None
        assert data["commits"][1]["error"] == "rate limit"
        assert set(data["aggregate"]) == {
            "evaluated",
            "failed",
            "interrupted",
            "metrics",
            "mean_iterations",
            "mean_errors_before",
            "mean_errors_after",
            "mean_warnings_before",
            "mean_warnings_after",
        }
        assert set(data["aggregate"]["metrics"]) == set(METRIC_LABELS)
        assert set(data["aggregate"]["metrics"]["imperative_mood"]) == {
            "passed",
            "applicable",
            "rate",
        }
