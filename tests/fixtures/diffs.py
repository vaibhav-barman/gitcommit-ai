"""Builders for synthetic ``DiffResult`` objects used in agent tests."""

from __future__ import annotations

from git_commit_ai.core.schemas import DiffResult, DiffStatistics, FileChange, FileStatus


def make_diff(files: dict[str, int], binary: tuple[str, ...] = ()) -> DiffResult:
    """Build a diff of newly added files.

    Args:
        files: Mapping of path to the number of added lines in that file.
        binary: Paths to include as binary additions (no patch text).
    """
    changes: list[FileChange] = []
    patches: list[str] = []
    for path, line_count in files.items():
        body = "".join(f"+line {n} of {path}\n" for n in range(line_count))
        patches.append(
            f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
            f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{line_count} @@\n{body}"
        )
        changes.append(FileChange(path=path, status=FileStatus.ADDED, additions=line_count))
    for path in binary:
        patches.append(
            f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
            f"Binary files /dev/null and b/{path} differ\n"
        )
        changes.append(FileChange(path=path, status=FileStatus.ADDED, is_binary=True))
    return DiffResult(
        raw_diff="".join(patches),
        file_changes=changes,
        statistics=DiffStatistics(
            files_changed=len(changes),
            total_additions=sum(fc.additions for fc in changes),
        ),
    )
