"""Parse ``git diff --name-status`` output (§5.9).

The name-status format is one change per line, tab-separated:

    M\tpath/to/file            (modified)
    A\tpath/to/new             (added)
    D\tpath/to/gone            (deleted)
    R096\told/path\tnew/path   (renamed, with similarity score)
    C075\tsrc/path\tcopy/path  (copied)

Kept pure so the reindex logic is tested without invoking git; a thin
:func:`git_name_status` runs the real command for the webhook path.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ChangeStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass(frozen=True)
class FileChange:
    status: ChangeStatus
    path: str  # the current (post-change) path; the deleted path for deletions
    old_path: str | None = None  # populated for renames


_CODE_MAP = {"A": ChangeStatus.ADDED, "M": ChangeStatus.MODIFIED, "D": ChangeStatus.DELETED}


def parse_name_status(output: str) -> list[FileChange]:
    """Parse name-status text into changes, skipping blank lines.

    Renames and copies (``R``/``C`` with a similarity score) are modelled as a
    :class:`ChangeStatus.RENAMED` carrying both paths — the reindexer turns a
    rename into a delete-of-old + add-of-new that can carry the summary forward.
    """
    changes: list[FileChange] = []
    for raw in output.splitlines():
        line = raw.strip("\n")
        if not line.strip():
            continue
        fields = line.split("\t")
        code = fields[0].strip()
        letter = code[:1].upper()
        if letter in ("R", "C") and len(fields) >= 3:
            changes.append(
                FileChange(status=ChangeStatus.RENAMED, path=fields[2], old_path=fields[1])
            )
        elif letter in _CODE_MAP and len(fields) >= 2:
            changes.append(FileChange(status=_CODE_MAP[letter], path=fields[1]))
        # Unknown status letters (T, U, X, B) are skipped rather than guessed at.
    return changes


def git_name_status(root: Path, last_sha: str, head: str = "HEAD") -> list[FileChange]:
    """Run ``git diff --name-status last_sha..head`` and parse it (webhook path)."""
    out = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-status", f"{last_sha}..{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return parse_name_status(out.stdout)
