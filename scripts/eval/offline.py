"""A deterministic stand-in for an LLM, so the harness runs without a network.

Offline numbers say nothing about any model. They exercise the whole pipeline
(replay, graph, critic, scoring, reports) and show the reflection loop at work:
the first draft is deliberately naive, and later drafts follow the rules that the
generator's prompt states.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from git_commit_ai.core.schemas import FileStatus
from git_commit_ai.llm import FakeLLM

if TYPE_CHECKING:
    from collections.abc import Sequence

    from git_commit_ai.core.schemas import DiffResult
    from git_commit_ai.llm import LLMMessage

OFFLINE_MODEL = "offline-heuristic"

_ANALYST_MARKER = "analyze staged git diffs"
_CRITIC_MARKER = "verify a commit message"
_RULES_HEADING = "Format and style rules:"
_FACTS_HEADING = "What the change does:\n"
_VERBS = {
    "feat": "add",
    "fix": "fix",
    "test": "add",
    "docs": "update",
    "build": "update",
    "ci": "update",
}
_BUILD_FILES = {"pyproject.toml", "uv.lock", "makefile", "dockerfile", "package.json"}
_DEFAULT_SCOPE = "core"
_TYPE_LINE = re.compile(r"^- type: (\w+)$", re.MULTILINE)
_SUMMARY_LINE = re.compile(r"^- summary: (.+)$", re.MULTILINE)
_SUGGESTED_SCOPE = re.compile(r"Suggested scope: ([^.\s]+)\.")
_KNOWN_SCOPES = re.compile(r"Scopes used here: ([^.]+)\.")


def offline_llm(diff: DiffResult) -> FakeLLM:
    """Build a FakeLLM that answers the analyst, generator and critic for ``diff``."""
    main = max(diff.file_changes, key=lambda fc: fc.additions + fc.deletions)
    stem = PurePosixPath(main.path).stem or main.path
    parent = PurePosixPath(main.path).parent.name
    change_type = _change_type(diff)
    verb = _VERBS.get(change_type, "update")
    analysis = json.dumps(
        {
            "change_type": change_type,
            "summary": f"{verb} {stem}",
            "intent": "",
            "is_breaking": False,
            "scope_hint": parent or None,
        }
    )
    files = len(diff.file_changes)
    drafts = 0

    def respond(messages: Sequence[LLMMessage]) -> str:
        nonlocal drafts
        system, prompt = messages[0].content, messages[-1].content
        if _ANALYST_MARKER in system:
            return analysis
        if _CRITIC_MARKER in system:
            return json.dumps({"faithful": True, "problems": []})
        drafts += 1
        if drafts == 1:
            return f"Updated {stem}"
        return _follow_the_rules(prompt, files, stem, parent)

    return FakeLLM(respond, model=OFFLINE_MODEL)


def _change_type(diff: DiffResult) -> str:
    paths = [fc.path for fc in diff.file_changes]
    if all(path.startswith(("tests/", "test/")) for path in paths):
        return "test"
    if all(path.endswith((".md", ".rst", ".txt")) or path.startswith("docs/") for path in paths):
        return "docs"
    if all(path.startswith(".github/") for path in paths):
        return "ci"
    if all(PurePosixPath(path).name.lower() in _BUILD_FILES for path in paths):
        return "build"
    return "feat" if any(fc.status is FileStatus.ADDED for fc in diff.file_changes) else "fix"


def _follow_the_rules(prompt: str, files: int, stem: str, parent: str) -> str:
    """Write the message the generator's own rules ask for.

    Only the rules and the facts about the change are read. The rest of the prompt
    holds the diff, which can contain any text, including these very rule phrases.
    """
    rules = _section(prompt, _RULES_HEADING)
    facts = _section(prompt, _FACTS_HEADING)
    commit_type = _first(_TYPE_LINE, facts, "chore")
    description = _first(_SUMMARY_LINE, facts, f"update {stem}")
    suggested = _first(_SUGGESTED_SCOPE, rules, "")
    known = _first(_KNOWN_SCOPES, rules, "").split(",")[0].strip()
    scope = suggested or known or parent or _DEFAULT_SCOPE

    if "Start the description with a lowercase letter." in rules:
        description = description[:1].lower() + description[1:]
    elif "Start the description with a capital letter." in rules:
        description = description[:1].upper() + description[1:]
    elif "Write the description in uppercase." in rules:
        description = description.upper()
    if "End the subject with a period." in rules:
        description += "."

    if "A scope is required." in rules:
        subject = f"{commit_type}({scope}): {description}"
    elif "Format: Conventional Commits" in rules:
        subject = f"{commit_type}: {description}"
    elif "Format: a gitmoji" in rules:
        subject = f"✨ {description}"
    elif "Format: '[PREFIX] description'" in rules:
        subject = f"[{scope.upper()}] {description}"
    else:
        subject = description

    if "Add a short body" not in rules:
        return subject
    return f"{subject}\n\nTouches {files} file(s), mainly {stem}."


def _section(prompt: str, heading: str) -> str:
    """The paragraph that starts at ``heading``, or an empty string if it is absent."""
    start = prompt.find(heading)
    return prompt[start:].split("\n\n", 1)[0] if start >= 0 else ""


def _first(pattern: re.Pattern[str], text: str, default: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else default
