"""Per-run journal for `androsync undo` (Phase I, task 30a).

``RunJournal`` records, as a sync runs, everything needed to put both sides
back exactly how they were before it: the pre-run snapshot, and one entry
per file touched, backing up whatever was there *before* the write so it
can be restored. Layout under ``state.undo_dir(pair)/<run_id>/``::

    meta.json            run_id, kind ("sync"|"undo"), started/finished, summary
    snapshot_before.json the pair's snapshot as of just before this run
    journal.jsonl         one JSON object per touched file, appended as it happens
    files/local/<rel>     backed-up laptop-side bytes (only when overwritten/deleted)
    files/remote/<rel>    backed-up phone-side bytes

``journal.jsonl`` is appended to (not held open) so a run that crashes
partway through still leaves a journal usable as far as it got - see
``record()``. Actually replaying a journal backwards (``androsync undo``)
is task 30b; this module only writes it and reads it back (:func:`list_runs`,
:func:`load`), plus :func:`prune` for retention.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from androsync import state
from androsync.config import PairConfig
from androsync.model import FileEntry, PersistedSnapshot, Side, Summary
from androsync.transport.base import Transport, TransportCancelled

_SIDE_LABEL = {Side.LOCAL: "local", Side.REMOTE: "remote"}
_SIDE_HUMAN = {Side.LOCAL: "laptop", Side.REMOTE: "phone"}

LogFn = Callable[[str], None]


class UndoError(Exception):
    """Raised when a run/journal cannot be found or is malformed."""


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def _entry_dict(entry: FileEntry | None) -> dict | None:
    return None if entry is None else {"size": entry.size, "mtime": entry.mtime}


def new_run_id(now: datetime | None = None) -> str:
    """``YYYYMMDD-HHMMSS-<4 hex>`` - sortable by time, unique per run."""
    now = now if now is not None else datetime.now()
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:4]}"


@dataclass
class RunJournal:
    """An in-progress (or just-finished) run's journal. Build one with :meth:`start`."""

    pair_name: str
    run_id: str
    kind: str
    dir: Path
    max_file_bytes: int
    _meta: dict = field(default_factory=dict)

    @classmethod
    def start(
        cls,
        pair_name: str,
        snapshot_before: PersistedSnapshot,
        settings: dict,
        *,
        kind: str = "sync",
        now: datetime | None = None,
    ) -> "RunJournal":
        """Begin a new run's journal: creates its directory and writes the pre-run snapshot.

        ``settings`` is the pair's undo config (``max_file_mb`` is the only
        key read here; ``keep_runs``/``max_total_mb`` are used by
        :func:`prune`, called separately once the run is done).
        """
        now = now if now is not None else datetime.now()
        run_id = new_run_id(now)
        run_dir = state.undo_dir(pair_name) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        journal = cls(
            pair_name=pair_name,
            run_id=run_id,
            kind=kind,
            dir=run_dir,
            max_file_bytes=int(settings.get("max_file_mb", 500)) * 1024 * 1024,
        )
        _atomic_write_json(run_dir / "snapshot_before.json", state.snapshot_to_raw(snapshot_before))
        journal._meta = {
            "pair": pair_name,
            "run_id": run_id,
            "kind": kind,
            "started": now.isoformat(),
            "finished": False,
        }
        journal._write_meta()
        return journal

    def _write_meta(self) -> None:
        _atomic_write_json(self.dir / "meta.json", self._meta)

    def backup(self, side: Side, rel: str, transport: Transport, root: str, entry: FileEntry) -> str:
        """Fetch the *current* bytes at ``root/rel`` into this run's backup tree.

        Returns ``"saved"``, or ``"skipped"`` (without touching the
        transport at all) when ``entry.size`` exceeds ``max_file_mb``.
        """
        if entry.size > self.max_file_bytes:
            return "skipped"
        dest = self.dir / "files" / _SIDE_LABEL[side] / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        transport.fetch(root, rel, dest)
        return "saved"

    def backup_many(
        self, side: Side, transport: Transport, root: str, entries: list[tuple[str, FileEntry]]
    ) -> dict[str, str]:
        """Batched :meth:`backup`: one ``fetch_many`` for everything under the size limit.

        Used by the executor's batched-copy path (task 24) so backing up a
        whole batch's existing destinations costs one round-trip, not one
        per file. Returns ``{rel: "saved"|"skipped"}``.
        """
        statuses: dict[str, str] = {}
        to_fetch: list[tuple[str, str, Path]] = []
        for rel, entry in entries:
            if entry.size > self.max_file_bytes:
                statuses[rel] = "skipped"
                continue
            dest = self.dir / "files" / _SIDE_LABEL[side] / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            to_fetch.append((root, rel, dest))
            statuses[rel] = "saved"
        if to_fetch:
            transport.fetch_many(to_fetch)
        return statuses

    def record_write_many(
        self, side: Side, transport: Transport, root: str, entries: list[tuple[str, FileEntry]]
    ) -> None:
        """Batched :meth:`record_write`: one :meth:`backup_many` for a whole about-to-be-written batch.

        ``entries`` is ``[(rel, new_entry), ...]`` for a batch the caller is
        about to hand to ``store_many`` - call this first, over the same
        destination ``transport``/``root``, so the pre-write state is
        captured before anything changes.
        """
        old_entries: dict[str, FileEntry] = {}
        for rel, _new_entry in entries:
            old = transport.stat(root, rel)
            if old is not None:
                old_entries[rel] = old

        statuses = self.backup_many(side, transport, root, list(old_entries.items())) if old_entries else {}

        for rel, new_entry in entries:
            if rel in old_entries:
                self.record(
                    {
                        "op": "overwrite",
                        "side": _SIDE_LABEL[side],
                        "rel": rel,
                        "old": _entry_dict(old_entries[rel]),
                        "new": _entry_dict(new_entry),
                        "backup": statuses[rel],
                    }
                )
            else:
                self.record({"op": "create", "side": _SIDE_LABEL[side], "rel": rel, "new": _entry_dict(new_entry)})

    def record(self, entry: dict) -> None:
        """Append one journal entry as a JSON line, durably (open-write-close, not held open)."""
        with open(self.dir / "journal.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def record_write(self, side: Side, rel: str, transport: Transport, root: str, new_entry: FileEntry) -> None:
        """Journal a file about to be written at ``root/rel`` on ``side``.

        Stats the destination *before* the caller writes to it: ``"create"``
        if nothing is there yet (nothing to back up), ``"overwrite"``
        (backed up first) otherwise. Call this before performing the write.
        """
        old = transport.stat(root, rel)
        if old is None:
            self.record({"op": "create", "side": _SIDE_LABEL[side], "rel": rel, "new": _entry_dict(new_entry)})
            return
        status = self.backup(side, rel, transport, root, old)
        self.record(
            {
                "op": "overwrite",
                "side": _SIDE_LABEL[side],
                "rel": rel,
                "old": _entry_dict(old),
                "new": _entry_dict(new_entry),
                "backup": status,
            }
        )

    def record_conflict_saved(self, backup_rel: str) -> None:
        """Journal a folder-mode conflict backup just written (task 29b): undo just deletes it again."""
        self.record({"op": "conflict_saved", "path": backup_rel})

    def record_conflict_rename(self, side: Side, rel: str, target: str, old_entry: FileEntry) -> None:
        """Journal a beside-mode conflict rename (``rel`` -> ``target``) about to happen on ``side``.

        No backup is needed: the bytes stay reachable at ``target`` after
        the rename, so undo can just rename ``target`` back to ``rel``.
        """
        self.record(
            {
                "op": "conflict_rename",
                "side": _SIDE_LABEL[side],
                "rel": rel,
                "target": target,
                "old": _entry_dict(old_entry),
            }
        )

    def record_delete(self, side: Side, rel: str, transport: Transport, root: str) -> None:
        """Journal a file about to be deleted at ``root/rel`` on ``side``. Call before deleting."""
        old = transport.stat(root, rel)
        if old is None:
            return
        status = self.backup(side, rel, transport, root, old)
        self.record(
            {"op": "delete", "side": _SIDE_LABEL[side], "rel": rel, "old": _entry_dict(old), "backup": status}
        )

    def finish(self, summary: Summary) -> None:
        """Mark the run complete: writes the final ``meta.json`` with the summary."""
        self._meta = {
            **self._meta,
            "finished": True,
            "finished_at": datetime.now().isoformat(),
            "summary": asdict(summary),
        }
        self._write_meta()


def _run_sort_key(meta: dict) -> str:
    """Sort key for ordering runs newest-first.

    ``meta["started"]`` (a ``datetime.isoformat()``, microsecond precision)
    orders two runs correctly even when they land in the same second - the
    ``run_id`` alone cannot, since its own timestamp component is
    second-resolution and its random suffix breaks ties arbitrarily.
    """
    return meta.get("started") or meta["run_id"]


def list_runs(pair_name: str) -> list[dict]:
    """List every run's ``meta.json`` for ``pair_name``, newest first."""
    base = state.undo_dir(pair_name)
    if not base.is_dir():
        return []
    runs: list[dict] = []
    for run_dir in base.iterdir():
        meta_path = run_dir / "meta.json"
        if not meta_path.exists():
            continue
        with open(meta_path, "r", encoding="utf-8") as f:
            runs.append(json.load(f))
    runs.sort(key=_run_sort_key, reverse=True)
    return runs


def load(pair_name: str, run_id: str) -> dict:
    """Load one run's full journal: ``{"meta", "snapshot_before", "entries"}``.

    ``entries`` is the list of journal lines (in the order they were
    recorded); a crashed run's journal simply ends early. Raises
    :class:`UndoError` if ``run_id`` does not exist for ``pair_name``.
    """
    run_dir = state.undo_dir(pair_name) / run_id
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        raise UndoError(f"no run {run_id!r} for pair {pair_name!r}")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    snapshot_before: PersistedSnapshot = {}
    snap_path = run_dir / "snapshot_before.json"
    if snap_path.exists():
        with open(snap_path, "r", encoding="utf-8") as f:
            snapshot_before = state.raw_to_snapshot(json.load(f))

    entries: list[dict] = []
    journal_path = run_dir / "journal.jsonl"
    if journal_path.exists():
        with open(journal_path, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]

    return {"meta": meta, "snapshot_before": snapshot_before, "entries": entries}


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _run_dir_sort_key(run_dir: Path) -> str:
    meta_path = run_dir / "meta.json"
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return _run_sort_key(json.load(f))
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    return run_dir.name


def prune(pair_name: str, keep_runs: int, max_total_mb: int) -> None:
    """Delete the oldest run directories beyond ``keep_runs`` or ``max_total_mb`` total size.

    Run directories are ordered newest-first the same way :func:`list_runs`
    orders runs (by ``meta.json``'s ``started``, not the directory name -
    see :func:`_run_sort_key`). Called after every run.
    """
    base = state.undo_dir(pair_name)
    if not base.is_dir():
        return

    run_dirs = sorted((d for d in base.iterdir() if d.is_dir()), key=_run_dir_sort_key, reverse=True)
    kept = run_dirs[:keep_runs]
    to_delete = run_dirs[keep_runs:]

    max_bytes = max_total_mb * 1024 * 1024
    total = sum(_dir_size(d) for d in kept)
    while total > max_bytes and kept:
        oldest = kept.pop()  # kept is newest-first; the last entry is the oldest.
        to_delete.append(oldest)
        total -= _dir_size(oldest)

    for run_dir in to_delete:
        shutil.rmtree(run_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# undo_run: replay a journal backwards (task 30b)
# ---------------------------------------------------------------------------


@dataclass
class UndoSummary:
    """Counters describing the outcome of an `undo_run` call."""

    restored_local: int = 0
    restored_remote: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: int = 0
    #: This undo's own run_id (it is journaled too - undo of an undo is redo).
    #: None when the pair has undo disabled, or the undo was a dry run.
    run_id: str | None = None


def _side_of(entry: dict) -> Side:
    return Side.LOCAL if entry["side"] == "local" else Side.REMOTE


def _bump(summary: UndoSummary, side: Side) -> None:
    if side is Side.LOCAL:
        summary.restored_local += 1
    else:
        summary.restored_remote += 1


def _matches(transport: Transport, root: str, rel: str, expected: dict | None, tol: float) -> bool:
    """True if the file currently at ``root/rel`` still matches the journaled ``expected`` fingerprint."""
    current = transport.stat(root, rel)
    if expected is None:
        return current is None
    if current is None:
        return False
    return current.size == expected["size"] and abs(current.mtime - expected["mtime"]) <= tol


def _skip(summary: UndoSummary, rel: str, reason: str, log: LogFn) -> None:
    summary.skipped.append(rel)
    log(f"skip {rel}: {reason}, left as is")


def _undo_create(
    entry: dict,
    transports: dict[Side, Transport],
    roots: dict[Side, str],
    tol: float,
    force: bool,
    dry_run: bool,
    journal: "RunJournal | None",
    summary: UndoSummary,
    log: LogFn,
) -> None:
    """Reverse of a "create": the file did not exist before the run, so undo deletes it."""
    side = _side_of(entry)
    rel = entry["rel"]
    transport, root = transports[side], roots[side]

    if not force and not _matches(transport, root, rel, entry["new"], tol):
        _skip(summary, rel, "modified after the sync", log)
        return
    label = _SIDE_HUMAN[side]
    if dry_run:
        log(f"would delete {rel} on {label}")
        _bump(summary, side)
        return
    if journal is not None:
        journal.record_delete(side, rel, transport, root)
    transport.delete(root, rel)
    _bump(summary, side)
    log(f"deleted {rel} on {label}")


def _undo_overwrite(
    entry: dict,
    transports: dict[Side, Transport],
    roots: dict[Side, str],
    tol: float,
    force: bool,
    dry_run: bool,
    journal: "RunJournal | None",
    summary: UndoSummary,
    log: LogFn,
    orig_run_dir: Path,
) -> None:
    """Reverse of an "overwrite": put the backed-up old bytes back."""
    side = _side_of(entry)
    rel = entry["rel"]
    transport, root = transports[side], roots[side]

    if entry.get("backup") == "skipped":
        _skip(summary, rel, "no backup was saved (file exceeded max_file_mb)", log)
        return
    if not force and not _matches(transport, root, rel, entry["new"], tol):
        _skip(summary, rel, "modified after the sync", log)
        return

    old = entry["old"]
    label = _SIDE_HUMAN[side]
    if dry_run:
        log(f"would restore {rel} on {label}")
        _bump(summary, side)
        return

    backup_path = orig_run_dir / "files" / _SIDE_LABEL[side] / rel
    if journal is not None:
        journal.record_write(side, rel, transport, root, FileEntry(size=old["size"], mtime=old["mtime"]))
    transport.store(backup_path, root, rel, old["mtime"])
    _bump(summary, side)
    log(f"restored {rel} on {label}")


def _undo_delete(
    entry: dict,
    transports: dict[Side, Transport],
    roots: dict[Side, str],
    force: bool,
    dry_run: bool,
    journal: "RunJournal | None",
    summary: UndoSummary,
    log: LogFn,
    orig_run_dir: Path,
) -> None:
    """Reverse of a "delete": recreate the file from its backup."""
    side = _side_of(entry)
    rel = entry["rel"]
    transport, root = transports[side], roots[side]

    if entry.get("backup") == "skipped":
        _skip(summary, rel, "no backup was saved (file exceeded max_file_mb)", log)
        return
    if not force and transport.stat(root, rel) is not None:
        _skip(summary, rel, "modified after the sync", log)
        return

    old = entry["old"]
    label = _SIDE_HUMAN[side]
    if dry_run:
        log(f"would recreate {rel} on {label}")
        _bump(summary, side)
        return

    backup_path = orig_run_dir / "files" / _SIDE_LABEL[side] / rel
    if journal is not None:
        journal.record_write(side, rel, transport, root, FileEntry(size=old["size"], mtime=old["mtime"]))
    transport.store(backup_path, root, rel, old["mtime"])
    _bump(summary, side)
    log(f"recreated {rel} on {label}")


def _undo_conflict_saved(
    entry: dict,
    local: Transport,
    local_root: str,
    dry_run: bool,
    journal: "RunJournal | None",
    log: LogFn,
) -> None:
    """Reverse of a folder-mode conflict backup: remove the saved version again."""
    path = entry["path"]
    if dry_run:
        log(f"would remove saved conflict version {path}")
        return
    if journal is not None:
        journal.record_delete(Side.LOCAL, path, local, local_root)
    local.delete(local_root, path)
    log(f"removed saved conflict version {path}")


def _undo_conflict_rename(
    entry: dict,
    transports: dict[Side, Transport],
    roots: dict[Side, str],
    dry_run: bool,
    journal: "RunJournal | None",
    summary: UndoSummary,
    log: LogFn,
) -> None:
    """Reverse of a beside-mode conflict rename: rename the conflict-copy back to its original name."""
    side = _side_of(entry)
    rel, target = entry["rel"], entry["target"]
    transport, root = transports[side], roots[side]
    label = _SIDE_HUMAN[side]

    if transport.stat(root, target) is None:
        _skip(summary, rel, "the saved conflict copy is missing", log)
        return
    if dry_run:
        log(f"would rename {target} back to {rel} on {label}")
        _bump(summary, side)
        return

    old_at_target = transport.stat(root, target)
    if journal is not None and old_at_target is not None:
        journal.record_conflict_rename(side, target, rel, old_at_target)
    transport.rename(root, target, rel)
    _bump(summary, side)
    log(f"renamed {target} back to {rel} on {label}")


def undo_run(
    pair: PairConfig,
    run_id: str,
    local: Transport,
    remote: Transport,
    *,
    dry_run: bool = False,
    force: bool = False,
    log: LogFn = print,
    cancel: Callable[[], bool] | None = None,
) -> UndoSummary:
    """Put ``pair`` back exactly how it was before the run ``run_id``.

    Replays that run's journal *backwards* through the normal transports:
    each entry's inverse action, last-recorded first (necessary for a
    beside-mode conflict, where the rename must be undone after the write
    it enabled is undone first - see ``_undo_conflict_rename``). A file
    changed since the run (its current fingerprint no longer matches what
    the journal recorded as the run's own result) is left alone and
    reported, unless ``force``.

    The undo is itself journaled (``kind="undo"``) through the exact same
    ``RunJournal.record_write``/``record_delete`` calls a normal run uses -
    so a further ``undo_run`` targeting *this* run's id replays it
    backwards too, i.e. redoes the original run. Raises ``UndoError`` if
    ``run_id`` does not exist for ``pair.name``.
    """
    loaded = load(pair.name, run_id)
    entries = loaded["entries"]
    tol = pair.mtime_tolerance_s
    orig_run_dir = state.undo_dir(pair.name) / run_id

    transports = {Side.LOCAL: local, Side.REMOTE: remote}
    roots = {Side.LOCAL: str(pair.local), Side.REMOTE: pair.remote}

    summary = UndoSummary()
    journal: RunJournal | None = None
    if not dry_run and pair.undo_enabled:
        current_snapshot = dict(state.load_snapshot(pair.name))
        journal = RunJournal.start(
            pair.name, current_snapshot, {"max_file_mb": pair.undo_max_file_mb}, kind="undo"
        )
        summary.run_id = journal.run_id

    cancelled = False
    for entry in reversed(entries):
        if cancel is not None and cancel():
            log("cancelled")
            cancelled = True
            break
        op = entry.get("op")
        try:
            if op == "create":
                _undo_create(entry, transports, roots, tol, force, dry_run, journal, summary, log)
            elif op == "overwrite":
                _undo_overwrite(entry, transports, roots, tol, force, dry_run, journal, summary, log, orig_run_dir)
            elif op == "delete":
                _undo_delete(entry, transports, roots, force, dry_run, journal, summary, log, orig_run_dir)
            elif op == "conflict_saved":
                _undo_conflict_saved(entry, local, str(pair.local), dry_run, journal, log)
            elif op == "conflict_rename":
                _undo_conflict_rename(entry, transports, roots, dry_run, journal, summary, log)
        except KeyboardInterrupt:
            raise
        except TransportCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad entry must not abort the rest of the undo
            log(f"ERROR undoing {entry.get('rel', entry.get('path'))!r}: {exc}")
            summary.errors += 1

    # A stopped undo is only partly done, so the pair's history must not be reset to "before the run".
    if not dry_run and summary.errors == 0 and not cancelled:
        state.save_snapshot(pair.name, loaded["snapshot_before"])

    if journal is not None:
        journal.finish(summary)

    return summary
