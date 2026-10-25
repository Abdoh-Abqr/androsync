"""Conflict-copy naming for the planner and executor.

When a file changed on both sides between syncs and the conflict policy
keeps a copy of the losing side, that copy is renamed in place with a
``.conflict-<side>-<timestamp>`` marker inserted before the extension
(or appended, for extension-less files and dotfiles).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from androsync.model import Side

_SIDE_NAMES = {Side.LOCAL: "laptop", Side.REMOTE: "phone"}

_CONFLICT_RE = re.compile(r"\.conflict-(?:laptop|phone)-\d{8}-\d{6}")

#: Where folder-mode conflict backups live, relative to a pair's local root
#: (task 29b). Always under ``.androsync/`` - see ``ignore._ALWAYS_IGNORE``.
CONFLICTS_DIR = ".androsync/conflicts"


def conflict_name(rel: str, side: Side, when: datetime) -> str:
    """Return ``rel`` with a conflict marker for ``side``/``when`` inserted before the extension.

    Examples:
        "notes/a.md" + LOCAL/REMOTE + 2026-09-05 15:30:00
            -> "notes/a.conflict-laptop-20260905-153000.md"
            -> "notes/a.conflict-phone-20260905-153000.md"
        "notes/README" (no extension) -> "notes/README.conflict-phone-...."
        ".env" (dotfile, no extension) -> ".env.conflict-phone-...."
    """
    side_name = _SIDE_NAMES[side]
    stamp = when.strftime("%Y%m%d-%H%M%S")
    marker = f".conflict-{side_name}-{stamp}"

    dir_part, _, base = rel.rpartition("/")
    prefix = f"{dir_part}/" if dir_part else ""

    # Find the extension, treating a name that is *only* a leading dot
    # (dotfile with no further "." ) as having no extension.
    dot_index = base.rfind(".")
    if dot_index <= 0:
        new_base = base + marker
    else:
        new_base = base[:dot_index] + marker + base[dot_index:]

    return prefix + new_base


def is_conflict_copy(rel: str) -> bool:
    """Return True if ``rel`` already contains a conflict-copy marker."""
    return _CONFLICT_RE.search(rel) is not None


def strip_conflict_marker(rel: str) -> str:
    """Return ``rel`` with its conflict-copy marker removed (the "original" it is a copy of).

    ``rel`` unchanged if it has no marker at all.
    """
    return _CONFLICT_RE.sub("", rel)


def conflict_stamp(when: datetime) -> str:
    """Return the ``YYYYMMDD-HHMMSS`` stamp used to name a conflict-backup run."""
    return when.strftime("%Y%m%d-%H%M%S")


def conflict_folder_rel(when: datetime, rel: str) -> str:
    """Return the rel path (relative to a pair's local root) of a folder-mode conflict backup."""
    return f"{CONFLICTS_DIR}/{conflict_stamp(when)}/{rel}"


def conflict_folder_path(local_root: str, when: datetime, rel: str) -> Path:
    """Return the absolute filesystem path of a folder-mode conflict backup."""
    return Path(local_root) / conflict_folder_rel(when, rel)
