"""The model menu: a shortlist, then "Show all" and "Type a model name"."""

from __future__ import annotations

from git_commit_ai.llm.catalog import ModelInfo
from git_commit_ai.onboarding.model_menu import (
    FALLBACK_HEADING,
    MenuAction,
    all_models_choices,
    fallback_menu,
    model_menu,
)


def _models(count: int, shortlisted: bool = True) -> list[ModelInfo]:
    return [ModelInfo(f"m-{i}", f"Model {i}", shortlisted=shortlisted) for i in range(count)]


def test_a_long_list_is_cut_to_seven_with_show_all_and_type() -> None:
    choices, default = model_menu(_models(9), saved=None)
    values = [c.value for c in choices]
    assert values[:7] == [f"m-{i}" for i in range(7)]
    assert values[7:] == [MenuAction.SHOW_ALL, MenuAction.TYPE_NAME]
    assert choices[7].label == "Show all 9 models"
    assert default == "m-0"


def test_seven_or_fewer_are_all_listed_without_show_all() -> None:
    choices, _ = model_menu(_models(7), saved=None)
    assert MenuAction.SHOW_ALL not in [c.value for c in choices]
    assert [c.value for c in choices][-1] is MenuAction.TYPE_NAME


def test_models_outside_the_shortlist_only_appear_under_show_all() -> None:
    models = [
        *_models(3),
        *[ModelInfo(f"dated-{i}", f"dated-{i}", shortlisted=False) for i in range(6)],
    ]
    values = [c.value for c in model_menu(models, saved=None)[0]]
    assert values[:3] == ["m-0", "m-1", "m-2"]
    assert MenuAction.SHOW_ALL in values
    assert not any(isinstance(v, str) and v.startswith("dated") for v in values)


def test_a_short_list_shows_non_shortlisted_models_too() -> None:
    models = [ModelInfo("a", "a"), ModelInfo("b", "b", shortlisted=False)]
    assert [c.value for c in model_menu(models, saved=None)[0]][:2] == ["a", "b"]


def test_the_saved_model_is_the_default_row_even_outside_the_shortlist() -> None:
    choices, default = model_menu(_models(12), saved="m-10")
    assert default == "m-10"
    assert choices[0].value == "m-10"
    assert len([c for c in choices if isinstance(c.value, str)]) == 7


def test_a_saved_model_no_longer_listed_is_not_offered() -> None:
    choices, default = model_menu(_models(3), saved="retired-model")
    assert "retired-model" not in [c.value for c in choices]
    assert default == "m-0"


def test_labels_and_hints() -> None:
    models = [ModelInfo("claude-x", "Claude X", "deprecated"), ModelInfo("gpt-5", "gpt-5", "free")]
    choices, _ = model_menu(models, saved=None)
    assert (choices[0].label, choices[0].hint) == ("Claude X", "claude-x · deprecated")
    assert (choices[1].label, choices[1].hint) == ("gpt-5", "free")


def test_a_larger_limit_lists_more() -> None:
    choices, _ = model_menu(_models(9), saved=None, limit=10)
    assert MenuAction.SHOW_ALL not in [c.value for c in choices]


def test_show_all_lists_every_model() -> None:
    assert [c.value for c in all_models_choices(_models(9))] == [f"m-{i}" for i in range(9)]


def test_fallback_menu_offers_retry_and_typing() -> None:
    choices, default = fallback_menu(("gemini-2.5-flash", "gemini-2.5-pro"), saved=None)
    assert [c.value for c in choices] == [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        MenuAction.RETRY,
        MenuAction.TYPE_NAME,
    ]
    assert default == "gemini-2.5-flash"
    assert FALLBACK_HEADING == (
        "These suggestions were current in November 2026 and may be out of date:"
    )


def test_fallback_menu_keeps_a_saved_model_first() -> None:
    choices, default = fallback_menu(("a", "b"), saved="custom")
    assert [c.value for c in choices][:3] == ["custom", "a", "b"]
    assert default == "custom"


def test_exactly_seven_are_all_listed_even_outside_the_shortlist() -> None:
    models = [*_models(6), ModelInfo("dated", "dated", shortlisted=False)]
    values = [c.value for c in model_menu(models, saved=None)[0]]
    assert "dated" in values
    assert MenuAction.SHOW_ALL not in values
