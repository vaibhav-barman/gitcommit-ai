"""Fit a staged diff into a prompt-sized character budget.

Strategy: the file summary is always included in full. The remaining budget
is shared between patches with a water-filling split: small patches are kept
whole and the space they do not need is redistributed to larger ones, which
are truncated at a line boundary. A patch that would get fewer than
``_MIN_PATCH_CHARS`` is dropped and its file is reported by name only.
Binary files never contribute patch text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from git_commit_ai.agents.state import DiffDigest

if TYPE_CHECKING:
    from git_commit_ai.core.schemas import DiffResult, FileChange

_MIN_PATCH_CHARS = 200
_FILE_HEADER = "diff --git "


def build_diff_digest(diff_result: DiffResult, max_chars: int) -> DiffDigest:
    """Render ``diff_result`` as text no longer than roughly ``max_chars``.

    Args:
        diff_result: The staged diff to render.
        max_chars: Budget for the whole digest; the file summary may exceed
            it for huge file lists, since it is never dropped.

    Returns:
        A ``DiffDigest`` flagging whether any content was truncated or omitted.
    """
    changes = diff_result.file_changes
    summary = "\n".join(_summary_line(fc) for fc in changes)
    patches = _patches_by_path(diff_result.raw_diff, changes)

    text_files = [fc for fc in changes if not fc.is_binary and fc.path in patches]
    budget = max(0, max_chars - len(summary))
    allowances = _share_budget({fc.path: len(patches[fc.path]) for fc in text_files}, budget)

    sections: list[str] = []
    omitted: list[str] = []
    truncated = False
    for fc in text_files:
        patch = patches[fc.path]
        allowance = allowances[fc.path]
        if allowance < min(len(patch), _MIN_PATCH_CHARS):
            omitted.append(fc.path)
            continue
        if allowance < len(patch):
            patch = _truncate_at_line(patch, allowance)
            truncated = True
        sections.append(patch)

    parts = [f"Changed files:\n{summary}"]
    if sections:
        parts.append("\n".join(sections))
    if omitted:
        parts.append("Patches omitted for size (see file list above): " + ", ".join(omitted))
    return DiffDigest(
        text="\n\n".join(parts),
        was_truncated=truncated or bool(omitted),
        omitted_files=omitted,
    )


def _summary_line(fc: FileChange) -> str:
    origin = f" (from {fc.old_path})" if fc.old_path else ""
    binary = " [binary]" if fc.is_binary else ""
    return f"- {fc.status.value} {fc.path}{origin}{binary} (+{fc.additions}/-{fc.deletions})"


def _patches_by_path(raw_diff: str, changes: list[FileChange]) -> dict[str, str]:
    """Split a unified diff into per-file patches keyed by destination path."""
    chunks: list[str] = []
    for line in raw_diff.splitlines(keepends=True):
        if line.startswith(_FILE_HEADER) or not chunks:
            chunks.append("")
        chunks[-1] += line

    by_path: dict[str, str] = {}
    for chunk in chunks:
        header = chunk.splitlines()[0] if chunk else ""
        for fc in changes:
            if header.endswith(f" b/{fc.path}") and fc.path not in by_path:
                by_path[fc.path] = chunk.rstrip("\n")
                break
    return by_path


def _share_budget(sizes: dict[str, int], budget: int) -> dict[str, int]:
    """Water-fill ``budget`` across items: small items keep their full size."""
    allowances: dict[str, int] = {}
    remaining = budget
    pending = sorted(sizes.items(), key=lambda item: item[1])
    for position, (key, size) in enumerate(pending):
        fair_share = remaining // (len(pending) - position)
        allowances[key] = min(size, fair_share)
        remaining -= allowances[key]
    return allowances


def _truncate_at_line(patch: str, limit: int) -> str:
    kept = patch[:limit].rsplit("\n", 1)[0]
    dropped = patch[len(kept) :].count("\n")
    return f"{kept}\n... [{dropped} more lines truncated]"
