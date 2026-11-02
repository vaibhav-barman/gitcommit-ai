"""The "Which model?" menu: up to seven models from the provider's own order, then
"Show all N models" (a type-to-filter list) and "Type a model name"."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from git_commit_ai.llm.registry import FALLBACKS_AS_OF
from git_commit_ai.onboarding.prompts import Choice

if TYPE_CHECKING:
    from collections.abc import Sequence

    from git_commit_ai.llm.catalog import ModelInfo

SHORTLIST_SIZE = 7
FALLBACK_HEADING = f"These suggestions were current in {FALLBACKS_AS_OF} and may be out of date:"


class MenuAction(enum.Enum):
    """Menu rows that are not a model."""

    SHOW_ALL = "show-all"
    TYPE_NAME = "type"
    RETRY = "retry"


MenuValue = str | MenuAction


def model_menu(
    models: Sequence[ModelInfo], saved: str | None, limit: int = SHORTLIST_SIZE
) -> tuple[list[Choice[MenuValue]], str | None]:
    """Rows for the short menu and the default model.

    A list of ``limit`` models or fewer is shown whole. A longer one shows the first
    ``limit`` shortlisted models, then "Show all". A saved model that is still listed is
    always shown and is the default row; one that is no longer listed is not offered.
    """
    if len(models) <= limit:
        picked = list(models)
    else:
        picked = [m for m in models if m.shortlisted][:limit]
        kept = next((m for m in models if m.id == saved), None)
        if kept is not None and kept not in picked:
            picked = [kept, *picked[: limit - 1]]
    choices: list[Choice[MenuValue]] = [_row(m) for m in picked]
    if len(models) > len(picked):
        choices.append(Choice(MenuAction.SHOW_ALL, f"Show all {len(models)} models"))
    choices.append(Choice(MenuAction.TYPE_NAME, "Type a model name"))
    listed = {m.id for m in picked}
    default = saved if saved in listed else (picked[0].id if picked else None)
    return choices, default


def all_models_choices(models: Sequence[ModelInfo]) -> list[Choice[str]]:
    """Every model, for the type-to-filter list."""
    return [Choice(m.id, m.label, _hint(m)) for m in models]


def fallback_menu(
    fallbacks: Sequence[str], saved: str | None
) -> tuple[list[Choice[MenuValue]], str | None]:
    """Rows shown when listing failed: dated suggestions, retry, and typing a name."""
    names = list(fallbacks)
    if saved and saved not in names:
        names.insert(0, saved)
    choices: list[Choice[MenuValue]] = [Choice(name, name) for name in names]
    choices.append(Choice(MenuAction.RETRY, "Try fetching the list again"))
    choices.append(Choice(MenuAction.TYPE_NAME, "Type a model name"))
    return choices, saved or (names[0] if names else None)


def _row(model: ModelInfo) -> Choice[MenuValue]:
    return Choice(model.id, model.label, _hint(model))


def _hint(model: ModelInfo) -> str:
    """The id (when the label differs from it) and the provider's detail."""
    shown_id = model.id if model.label != model.id else ""
    return " · ".join(part for part in (shown_id, model.detail) if part)
