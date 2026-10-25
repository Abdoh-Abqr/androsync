"""Guard against a run that would delete or copy far more than expected (task 36d, issue #9).

On 2026-10-15 a sync deleted 785 laptop files because the pair's phone folder had been changed (all 785 backed-up
files looked "deleted"). The history reset on a folder change prevents that case; this guard catches the rest: a
plan that deletes a large share of what the pair knows, or a first run that would copy a huge number of files, stops
before touching anything and waits for the user's confirmation.

Pure functions, no I/O. The limits come from the ``safety:`` block of the config (``defaults.safety``, then
``pair.safety``); a limit of 0 switches that check off.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from androsync.model import Action, ActionKind, FileEntry, Side, Snapshot

#: Defaults of the ``safety:`` config block.
SAFETY_DEFAULTS = {"max_delete_count": 50, "max_delete_percent": 20, "big_first_run_files": 5000, "big_first_run_gb": 5}

_EXAMPLES = 5
_SIDE_NAME = {Side.LOCAL: "laptop", Side.REMOTE: "phone"}


@dataclass(frozen=True)
class SafetyLimits:
    """How big a plan may be before it needs confirmation. 0 turns a check off."""

    max_delete_count: int = SAFETY_DEFAULTS["max_delete_count"]
    max_delete_percent: int = SAFETY_DEFAULTS["max_delete_percent"]
    big_first_run_files: int = SAFETY_DEFAULTS["big_first_run_files"]
    big_first_run_gb: int = SAFETY_DEFAULTS["big_first_run_gb"]


def _examples(rels: list[str]) -> str:
    shown = ", ".join(rels[:_EXAMPLES])
    more = len(rels) - _EXAMPLES
    return f"{shown} and {more} more" if more > 0 else shown


def _entry_size(action: Action, scan_local: Snapshot, scan_remote: Snapshot) -> int:
    source = scan_local if action.src is Side.LOCAL else scan_remote
    entry: FileEntry | None = action.entry if action.entry is not None else source.get(action.rel)
    return entry.size if entry is not None else 0


def check_plan(
    actions: list[Action],
    known_files: int,
    first_run: bool,
    scan_local: Snapshot,
    scan_remote: Snapshot,
    limits: SafetyLimits,
) -> list[str]:
    """Return one sentence per reason the plan needs confirmation (empty = fine to run).

    * **Deletions:** more than ``max_delete_count`` files deleted on one side AND more than
      ``max_delete_percent`` percent of the ``known_files`` the pair remembers (both must be exceeded).
    * **Big first run:** the pair has no history yet and the plan copies more than ``big_first_run_files`` files or
      ``big_first_run_gb`` GiB.
    """
    problems: list[str] = []

    if limits.max_delete_count > 0:
        for side in (Side.LOCAL, Side.REMOTE):
            doomed = sorted(a.rel for a in actions if a.kind is ActionKind.DELETE and a.dst is side)
            allowed = max(limits.max_delete_count, math.ceil(known_files * limits.max_delete_percent / 100))
            if len(doomed) > allowed:
                share = round(100 * len(doomed) / known_files) if known_files else 100
                problems.append(
                    f"It would delete {len(doomed)} files on the {_SIDE_NAME[side]} "
                    f"({share}% of the {known_files} files this pair knows). Examples: {_examples(doomed)}."
                )

    if first_run:
        copies = [a for a in actions if a.kind is ActionKind.COPY]
        total_bytes = sum(_entry_size(a, scan_local, scan_remote) for a in copies)
        too_many = limits.big_first_run_files > 0 and len(copies) > limits.big_first_run_files
        too_big = limits.big_first_run_gb > 0 and total_bytes > limits.big_first_run_gb * 1024**3
        if too_many or too_big:
            problems.append(
                f"This first sync would copy {len(copies)} files ({total_bytes / 1024**3:.1f} GB)."
            )

    return problems
