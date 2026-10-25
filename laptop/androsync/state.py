"""Per-pair persisted state: file snapshots and run metadata.

Snapshots are stored as JSON at ``state_dir()/<pair>.json`` and pair
metadata (last_run/last_result, filled in by later tasks) at
``state_dir()/<pair>.meta.json``. Writes are atomic: a ``.tmp`` file is
written first and then renamed into place with ``os.replace``.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from androsync.config import state_dir
from androsync.model import PersistedSnapshot, SnapshotEntry


def _snapshot_path(pair_name: str) -> Path:
    return state_dir() / f"{pair_name}.json"


def _meta_path(pair_name: str) -> Path:
    return state_dir() / f"{pair_name}.meta.json"


def _pending_path(pair_name: str) -> Path:
    return state_dir() / f"{pair_name}.pending.json"


def undo_dir(pair_name: str) -> Path:
    """Return ``state_dir()/undo/<pair_name>``: run journals live under here (task 30a)."""
    return state_dir() / "undo" / pair_name


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def raw_to_snapshot(raw: dict[str, Any]) -> PersistedSnapshot:
    """Parse a snapshot's JSON shape into a ``PersistedSnapshot``.

    Migrates the pre-task-29a shape (one shared ``mtime`` per entry) by
    copying it into both ``mtime_local`` and ``mtime_remote``. Shared by
    :func:`load_snapshot` and ``undo.py`` (a run journal's own
    ``snapshot_before.json`` uses the same shape).
    """
    snapshot: PersistedSnapshot = {}
    for rel, entry in raw.items():
        if "mtime_local" in entry:
            snapshot[rel] = SnapshotEntry(
                size=entry["size"],
                mtime_local=entry["mtime_local"],
                mtime_remote=entry["mtime_remote"],
                sha256=entry.get("sha256"),
            )
        else:
            mtime = entry["mtime"]
            snapshot[rel] = SnapshotEntry(size=entry["size"], mtime_local=mtime, mtime_remote=mtime)
    return snapshot


def snapshot_to_raw(snapshot: PersistedSnapshot) -> dict[str, Any]:
    """Inverse of :func:`raw_to_snapshot`: the JSON-safe shape of ``snapshot``."""
    return {
        rel: {
            "size": entry.size,
            "mtime_local": entry.mtime_local,
            "mtime_remote": entry.mtime_remote,
            "sha256": entry.sha256,
        }
        for rel, entry in snapshot.items()
    }


def load_snapshot(pair_name: str) -> PersistedSnapshot:
    """Load the saved snapshot for ``pair_name``, or {} if none exists yet."""
    path = _snapshot_path(pair_name)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return raw_to_snapshot(raw)


def save_snapshot(pair_name: str, snapshot: PersistedSnapshot) -> None:
    """Atomically write ``snapshot`` for ``pair_name`` as JSON."""
    _atomic_write_json(_snapshot_path(pair_name), snapshot_to_raw(snapshot))


def load_pair_meta(pair_name: str) -> dict[str, Any]:
    """Load saved metadata (last_run/last_result/...) for ``pair_name``, or {} if none."""
    path = _meta_path(pair_name)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_pair_meta(pair_name: str, meta: dict[str, Any]) -> None:
    """Atomically write metadata for ``pair_name`` as JSON."""
    _atomic_write_json(_meta_path(pair_name), meta)


def load_pending(pair_name: str) -> list[dict[str, Any]]:
    """Load the pending `conflict: ask` entries for ``pair_name``, or [] if none."""
    path = _pending_path(pair_name)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_pending(pair_name: str, pending: list[dict[str, Any]]) -> None:
    """Atomically overwrite the pending `conflict: ask` entries for ``pair_name``.

    Called after every plan (dry-run or real) with the full current list, so
    it self-heals: an entry that stops being a conflict (resolved outside
    ``resolve``, or a side deleted) simply does not reappear.
    """
    _atomic_write_json(_pending_path(pair_name), pending)


def _all_state_paths(pair_name: str) -> list[Path]:
    return [_snapshot_path(pair_name), _meta_path(pair_name), _pending_path(pair_name)]


def rename_pair_state(old_name: str, new_name: str) -> None:
    """Rename every state file for ``old_name`` to ``new_name`` (a pair rename, task 23b).

    Files that do not exist are skipped silently. Also moves the undo journal
    directory (task 30a), if any.
    """
    for old_path, new_path in zip(_all_state_paths(old_name), _all_state_paths(new_name)):
        if old_path.exists():
            os.replace(old_path, new_path)
    old_undo, new_undo = undo_dir(old_name), undo_dir(new_name)
    if old_undo.is_dir():
        new_undo.parent.mkdir(parents=True, exist_ok=True)
        os.replace(old_undo, new_undo)


def purge_pair_state(pair_name: str) -> None:
    """Delete every state file for ``pair_name`` (``remove-pair --purge-state``, task 23b).

    Also deletes the undo journal directory (task 30a), if any.
    """
    for path in _all_state_paths(pair_name):
        path.unlink(missing_ok=True)
    shutil.rmtree(undo_dir(pair_name), ignore_errors=True)


#: How long a reset pair's old history is kept before it is deleted (task 36d, issue #9).
RESET_KEEP_DAYS = 30


def reset_dir_prefix(pair_name: str) -> str:
    return f"{pair_name}.reset-"


def reset_pair_state(pair_name: str, now: datetime | None = None) -> Path | None:
    """Forget what the pair knew about its files, because its laptop or phone folder was changed (task 36d, #9).

    Moves the pair's snapshot, pending asks and undo journals into ``<state>/<pair>.reset-<timestamp>/`` and returns
    that folder (``None`` if the pair had no state). The next run is then a first run, which never deletes
    anything. Without this, every file the old folder had would look "deleted" in the new one and be removed
    from the other side. The undo journals go too: they describe files in the old folders.
    Leaves the pair's last-result file alone, and deletes reset folders older than ``RESET_KEEP_DAYS``.
    """
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = state_dir() / f"{reset_dir_prefix(pair_name)}{stamp}"
    moved = False
    for path in (_snapshot_path(pair_name), _pending_path(pair_name)):
        if path.exists():
            target.mkdir(parents=True, exist_ok=True)
            os.replace(path, target / path.name)
            moved = True
    journals = undo_dir(pair_name)
    if journals.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        os.replace(journals, target / "undo")
        moved = True
    prune_resets()
    return target if moved else None


def prune_resets(max_age_days: int = RESET_KEEP_DAYS) -> None:
    """Delete ``*.reset-*`` folders older than ``max_age_days``."""
    root = state_dir()
    if not root.is_dir():
        return
    cutoff = time.time() - max_age_days * 86_400
    for entry in root.glob("*.reset-*"):
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue
