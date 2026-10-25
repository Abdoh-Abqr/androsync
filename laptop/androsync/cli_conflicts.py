"""``conflicts list|restore|clean`` subcommands (Phase H, task 29b).

``list``/``restore`` browse and restore the folder-mode conflict backups
``executor.run_pair`` saves under ``<local>/.androsync/conflicts/``.
``clean`` implements decision 4: cleaning up the pre-task-29b
``*.conflict-<side>-<timestamp>.*`` duplicates left beside the original by
``conflict_copies: beside`` (still the default before this task shipped).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Callable

from androsync.config import PairConfig
from androsync.conflicts import ConflictsError, clean, list_conflicts, restore
from androsync.model import Side
from androsync.transport.local import LocalDirTransport

LogFn = Callable[[str], None]


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "K", "M", "G", "T"):
        if value < 1024 or unit == "T":
            return f"{int(value)}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}T"


def _one_pair(args: argparse.Namespace) -> PairConfig | int:
    """Resolve ``args.pair`` to exactly one ``PairConfig``, or return an exit code on failure."""
    from androsync.cli import _load_config  # local import: avoid a cycle

    config = _load_config(args)
    pair = next((p for p in config.pairs if p.name == args.pair), None)
    if pair is None:
        known = ", ".join(sorted(p.name for p in config.pairs)) or "(none configured)"
        print(f"androsync: unknown pair {args.pair!r}; known pairs: {known}", file=sys.stderr)
        return 2
    return pair


# ---------------------------------------------------------------------------
# conflicts list
# ---------------------------------------------------------------------------


def cmd_conflicts_list(args: argparse.Namespace) -> int:
    pair = _one_pair(args)
    if isinstance(pair, int):
        return pair

    versions = list_conflicts(pair)
    if not versions:
        print("no saved conflict versions")
        return 0

    for v in versions:
        print(f"{v.when}  {_human_size(v.size):>8}  {v.rel}")
    return 0


# ---------------------------------------------------------------------------
# conflicts restore
# ---------------------------------------------------------------------------


def cmd_conflicts_restore(args: argparse.Namespace) -> int:
    pair = _one_pair(args)
    if isinstance(pair, int):
        return pair

    try:
        when = restore(pair, LocalDirTransport(Side.LOCAL), args.rel, args.when)
    except ConflictsError as exc:
        print(f"androsync: {exc}", file=sys.stderr)
        return 2

    print(f"restored {args.rel!r} from {when} - run a sync to propagate it to the phone")
    return 0


# ---------------------------------------------------------------------------
# conflicts clean
# ---------------------------------------------------------------------------


def cmd_conflicts_clean(args: argparse.Namespace) -> int:
    from androsync.cli import _load_config, open_pair_transports  # local import: avoid a cycle

    config = _load_config(args)
    pair = next((p for p in config.pairs if p.name == args.pair), None)
    if pair is None:
        known = ", ".join(sorted(p.name for p in config.pairs)) or "(none configured)"
        print(f"androsync: unknown pair {args.pair!r}; known pairs: {known}", file=sys.stderr)
        return 2

    local, remote = open_pair_transports(config)

    dry_run = not args.yes
    result = clean(pair, local, remote, dry_run=dry_run, now=datetime.now())

    if not result.actions:
        print("no old-style conflict-copy duplicates found")
        return 0

    verb = {
        "would_delete": "would delete (identical to original)",
        "deleted": "deleted (was identical to original)",
        "would_archive_and_delete": "would archive and delete (differs from original)",
        "archived_and_deleted": "archived and deleted (differed from original)",
    }
    for action in result.actions:
        print(f"{action.rel}: {verb[action.outcome]}")

    if dry_run:
        print(f"\n{len(result.actions)} duplicate(s) found - re-run with --yes to apply")
    else:
        print(f"\n{len(result.actions)} duplicate(s) cleaned up")
    return 0


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def cmd_conflicts_default(args: argparse.Namespace) -> int:
    print("usage: androsync conflicts {list,restore,clean} ...", file=sys.stderr)
    return 2


def add_conflicts_commands(subparsers, common: argparse.ArgumentParser) -> None:
    """Attach ``conflicts`` (with its ``list``/``restore``/``clean`` subcommands) to ``subparsers``."""
    conflicts_p = subparsers.add_parser("conflicts", help="Manage saved conflict-copy backups")
    conflicts_p.set_defaults(func=cmd_conflicts_default)
    conflicts_sub = conflicts_p.add_subparsers(dest="conflicts_command")

    list_p = conflicts_sub.add_parser(
        "list", parents=[common], help="List saved conflict versions for a pair"
    )
    list_p.add_argument("pair", metavar="PAIR", help="Pair name")
    list_p.set_defaults(func=cmd_conflicts_list)

    restore_p = conflicts_sub.add_parser(
        "restore", parents=[common], help="Copy a saved conflict version back over the live file"
    )
    restore_p.add_argument("pair", metavar="PAIR", help="Pair name")
    restore_p.add_argument("rel", metavar="REL", help="Relative path to restore")
    restore_p.add_argument(
        "--when", metavar="YYYYMMDD-HHMMSS", help="Which saved version (default: the most recent)"
    )
    restore_p.set_defaults(func=cmd_conflicts_restore)

    clean_p = conflicts_sub.add_parser(
        "clean",
        parents=[common],
        help="Clean up pre-task-29b '*.conflict-*' duplicate files (dry run unless --yes)",
    )
    clean_p.add_argument("pair", metavar="PAIR", help="Pair name")
    clean_p.add_argument("--yes", action="store_true", help="Actually delete/archive (default: dry run)")
    clean_p.set_defaults(func=cmd_conflicts_clean)
