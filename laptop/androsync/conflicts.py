"""List, restore and clean up conflict-copy backups (Phase H, task 29b).

Two things live here:

- Folder-mode backups (the default since task 29b): every real conflict is
  saved under ``<local>/.androsync/conflicts/<timestamp>/<rel>`` by
  ``executor._do_conflict_copy``. :func:`list_conflicts`/:func:`restore`
  browse and restore those.
- Clean-up of the *old* (``conflict_copies: beside``) duplicates: before
  task 29b, a real conflict left a ``*.conflict-<side>-<timestamp>.*`` file
  next to the original on both sides. :func:`find_duplicates`/:func:`clean`
  implement Phase H decision 4: hash each one against its original and
  either drop it (content matches) or archive it into the folder-mode
  conflicts directory and delete it from the phone (content differs).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from androsync.config import PairConfig
from androsync.conflict import conflict_folder_path, conflict_folder_rel, is_conflict_copy, strip_conflict_marker
from androsync.ignore import IgnoreRules
from androsync.transport.base import Transport


class ConflictsError(Exception):
    """Raised for a ``conflicts`` operation that cannot proceed (unknown rel/timestamp, ...)."""


@dataclass(frozen=True)
class ConflictVersion:
    """One saved folder-mode conflict backup."""

    when: str  # "YYYYMMDD-HHMMSS"
    rel: str
    size: int
    mtime: float


def list_conflicts(pair: PairConfig) -> list[ConflictVersion]:
    """List every folder-mode conflict backup saved for ``pair``, newest first."""
    root = Path(pair.local) / ".androsync" / "conflicts"
    if not root.is_dir():
        return []

    versions: list[ConflictVersion] = []
    for stamp_dir in root.iterdir():
        if not stamp_dir.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(stamp_dir):
            for name in filenames:
                full = Path(dirpath) / name
                rel = full.relative_to(stamp_dir).as_posix()
                st = full.stat()
                versions.append(ConflictVersion(when=stamp_dir.name, rel=rel, size=st.st_size, mtime=st.st_mtime))

    versions.sort(key=lambda v: (v.when, v.rel), reverse=True)
    return versions


def _parse_stamp(stamp: str) -> datetime:
    try:
        return datetime.strptime(stamp, "%Y%m%d-%H%M%S")
    except ValueError as exc:
        raise ConflictsError(f"not a valid conflict timestamp: {stamp!r}") from exc


def restore(pair: PairConfig, local: Transport, rel: str, when: str | None = None) -> str:
    """Copy a saved conflict version of ``rel`` back over the live local file.

    Uses the most recent saved version of ``rel`` when ``when`` is omitted.
    Returns the ``when`` stamp that was restored. Only the local file is
    touched - the phone side is untouched, and the next sync run propagates
    the restored content there like any other local edit. Raises
    :class:`ConflictsError` if no matching saved version exists.
    """
    versions = [v for v in list_conflicts(pair) if v.rel == rel and (when is None or v.when == when)]
    if not versions:
        detail = f" at {when!r}" if when else ""
        raise ConflictsError(f"no saved conflict version of {rel!r}{detail}")
    chosen = versions[0]  # list_conflicts() is already newest-first

    src = conflict_folder_path(str(pair.local), _parse_stamp(chosen.when), rel)
    local.store(src, str(pair.local), rel, chosen.mtime)
    return chosen.when


# ---------------------------------------------------------------------------
# Clean-up of pre-task-29b "beside" duplicates (decision 4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DuplicateEntry:
    """One old-style (``conflict_copies: beside``) duplicate file found on either side."""

    rel: str
    original_rel: str
    on_local: bool
    on_remote: bool


def find_duplicates(pair: PairConfig, local: Transport, remote: Transport) -> list[DuplicateEntry]:
    """Find every ``*.conflict-(laptop|phone)-*`` file left over from ``conflict_copies: beside``."""
    ignore = IgnoreRules(pair.ignore)
    scan_local = local.scan(str(pair.local), ignore)
    scan_remote = remote.scan(pair.remote, ignore)
    rels = {rel for rel in set(scan_local) | set(scan_remote) if is_conflict_copy(rel)}
    return [
        DuplicateEntry(
            rel=rel,
            original_rel=strip_conflict_marker(rel),
            on_local=rel in scan_local,
            on_remote=rel in scan_remote,
        )
        for rel in sorted(rels)
    ]


CleanOutcome = Literal["deleted", "archived_and_deleted", "would_delete", "would_archive_and_delete"]


@dataclass(frozen=True)
class CleanAction:
    """What :func:`clean` did (or, under ``dry_run``, would do) for one duplicate."""

    rel: str
    original_rel: str
    identical_to_original: bool
    outcome: CleanOutcome


@dataclass(frozen=True)
class CleanResult:
    actions: list[CleanAction]


def _same_content(transport: Transport, root: str, rel_a: str, rel_b: str) -> bool:
    hashes = transport.hash_many(root, [rel_a, rel_b])
    hash_a = hashes.get(rel_a)
    hash_b = hashes.get(rel_b)
    return hash_a is not None and hash_a == hash_b


def _archive_and_delete(
    pair: PairConfig,
    local: Transport,
    remote: Transport,
    dup: DuplicateEntry,
    tmp_dir: Path,
    now: datetime,
) -> None:
    """Fetch ``dup``'s bytes (from whichever side has it) into the conflicts folder, then delete it everywhere."""
    backup_rel = conflict_folder_rel(now, dup.original_rel)
    if dup.on_local:
        entry = local.stat(str(pair.local), dup.rel)
        tmp_path = tmp_dir / f"{uuid4().hex}.tmp"
        local.fetch(str(pair.local), dup.rel, tmp_path)
        local.store(tmp_path, str(pair.local), backup_rel, entry.mtime if entry else now.timestamp())
        tmp_path.unlink(missing_ok=True)
        local.delete(str(pair.local), dup.rel)
    elif dup.on_remote:
        entry = remote.stat(pair.remote, dup.rel)
        tmp_path = tmp_dir / f"{uuid4().hex}.tmp"
        remote.fetch(pair.remote, dup.rel, tmp_path)
        local.store(tmp_path, str(pair.local), backup_rel, entry.mtime if entry else now.timestamp())
        tmp_path.unlink(missing_ok=True)

    if dup.on_remote:
        remote.delete(pair.remote, dup.rel)


def clean(
    pair: PairConfig, local: Transport, remote: Transport, *, dry_run: bool, now: datetime | None = None
) -> CleanResult:
    """Implements Phase H decision 4: clean up pre-task-29b ``*.conflict-*`` duplicates.

    For each duplicate found on either side: if its content matches the file
    it is a duplicate of, just delete the duplicate (both sides, wherever
    present); otherwise (content differs, or the original is gone) archive
    its bytes into the folder-mode conflicts directory on the laptop and
    delete the duplicate from both sides. ``dry_run`` (the default via the
    CLI) only reports what would happen.
    """
    now = now if now is not None else datetime.now()
    duplicates = find_duplicates(pair, local, remote)

    ignore = IgnoreRules(pair.ignore)
    scan_local = local.scan(str(pair.local), ignore)
    scan_remote = remote.scan(pair.remote, ignore)

    actions: list[CleanAction] = []
    with tempfile.TemporaryDirectory(prefix="androsync-clean-") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        for dup in duplicates:
            identical = False
            if dup.on_local and dup.original_rel in scan_local:
                identical = _same_content(local, str(pair.local), dup.rel, dup.original_rel)
            elif dup.on_remote and dup.original_rel in scan_remote:
                identical = _same_content(remote, pair.remote, dup.rel, dup.original_rel)

            if identical:
                outcome: CleanOutcome = "would_delete" if dry_run else "deleted"
                if not dry_run:
                    if dup.on_local:
                        local.delete(str(pair.local), dup.rel)
                    if dup.on_remote:
                        remote.delete(pair.remote, dup.rel)
            else:
                outcome = "would_archive_and_delete" if dry_run else "archived_and_deleted"
                if not dry_run:
                    _archive_and_delete(pair, local, remote, dup, tmp_dir, now)

            actions.append(
                CleanAction(
                    rel=dup.rel,
                    original_rel=dup.original_rel,
                    identical_to_original=identical,
                    outcome=outcome,
                )
            )

    return CleanResult(actions=actions)
