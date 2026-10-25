"""Executes a plan through two ``Transport`` objects.

``run_pair`` ties the pure planner to the outside world: it scans both
sides, asks :mod:`androsync.planner` what to do, and carries the actions
out one at a time - fetching each changed file into a laptop-local temp
file and storing it on the destination side, exactly as described in
``transport/base.py``. The snapshot is saved after every successful action
so a crash or a cancelled run never loses more than the action in flight,
and a final reconciliation pass folds in the paths the planner had no
action for (already-in-sync pairs, both-sides-deleted paths) before the
last save.
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from androsync import safety, state, undo, versioning
from androsync.config import PairConfig
from androsync.conflict import CONFLICTS_DIR, conflict_folder_rel, conflict_name
from androsync.ignore import IgnoreRules
from androsync.model import Action, ActionKind, FileEntry, PersistedSnapshot, Side, Snapshot, SnapshotEntry, Summary
from androsync.planner import HasherFn, plan
from androsync.transport.base import Transport, TransportCancelled

_SIDE_LABEL = {Side.LOCAL: "laptop", Side.REMOTE: "phone"}
_ONE_WAY_TO_REMOTE_DIRECTIONS = ("laptop-to-phone", "two-way")


class ExecutorError(Exception):
    """Raised when ``run_pair`` cannot even start (missing root, ...)."""


class SyncLogger(Protocol):
    """Anything callable with a single message string. ``print`` qualifies."""

    def __call__(self, msg: str) -> None: ...


def _entries_equal(a: FileEntry, b: FileEntry, tol: float) -> bool:
    return a.size == b.size and abs(a.mtime - b.mtime) <= tol


def _entry_to_snapshot(entry: FileEntry) -> SnapshotEntry:
    """Build a ``SnapshotEntry`` for a file just copied across: both sides now share its mtime."""
    return SnapshotEntry(size=entry.size, mtime_local=entry.mtime, mtime_remote=entry.mtime)


def _make_hasher(local: Transport, remote: Transport, local_root: str, remote_root: str) -> HasherFn:
    """Build the ``hasher`` callback ``plan()`` calls for its conflict candidates (task 29a).

    Hashing only ever happens for rels the planner has already narrowed down
    to "both sides changed, same size" - never for the whole tree - so this
    is at most one ``hash_many`` round-trip per side per run.
    """

    def hasher(rels: list[str]) -> dict[str, tuple[str | None, str | None]]:
        local_hashes = local.hash_many(local_root, rels)
        remote_hashes = remote.hash_many(remote_root, rels)
        return {rel: (local_hashes.get(rel), remote_hashes.get(rel)) for rel in rels}

    return hasher


def reconcile_snapshot(
    snapshot: PersistedSnapshot, scan_local: Snapshot, scan_remote: Snapshot, tol: float
) -> PersistedSnapshot:
    """Fold paths the planner emitted no action for into ``snapshot`` (in place).

    The planner is silent about two kinds of path: already-in-sync ones
    (equal size/mtime on both sides, nothing to propagate) and ones deleted
    on both sides since the last run. Neither is touched by the execution
    loop, so without this pass they would stay stuck at whatever the last
    snapshot said (or, on a first run, be missing from it entirely).
    """
    for rel in set(scan_local) | set(scan_remote):
        if rel in snapshot:
            continue
        local_entry = scan_local.get(rel)
        remote_entry = scan_remote.get(rel)
        if (
            local_entry is not None
            and remote_entry is not None
            and _entries_equal(local_entry, remote_entry, tol)
        ):
            snapshot[rel] = SnapshotEntry(
                size=local_entry.size, mtime_local=local_entry.mtime, mtime_remote=remote_entry.mtime
            )
    for rel in list(snapshot):
        if rel not in scan_local and rel not in scan_remote:
            del snapshot[rel]
    return snapshot


#: Between the two paths at the end of an action line. The Android app splits on this exact shape (see
#: ``parseActionLine`` in ActionLine.kt), so change both together.
PATHS_SEPARATOR = "  \u00b7  "


def _paths_suffix(rel: str, local_root: str, remote_root: str) -> str:
    """``   (laptop: <full laptop path>  ·  phone: <full phone path>)`` for the file ``rel`` (issue: show both paths)."""
    laptop_full = f"{local_root.rstrip('/')}/{rel}"
    phone_full = f"{remote_root.rstrip('/')}/{rel}"
    return f"   (laptop: {laptop_full}{PATHS_SEPARATOR}phone: {phone_full})"


def _format_action(action: Action, local_root: str, remote_root: str) -> str:
    """Render one action as a single human-readable log line that ends with the file's full path on both sides.

    A log line must stay one line (the live log is sent as server-sent events), so both paths are appended rather
    than printed on lines of their own. Example::

        x phone  notes/a.md   deleted on laptop, so removed from the phone   (laptop: /home/me/vault/notes/a.md  ·  phone: /sdcard/Obsidian/Notes/notes/a.md)
    """
    paths = _paths_suffix(action.rel, local_root, remote_root)
    if action.kind is ActionKind.ASK:
        return f"?  {action.rel}   {action.reason}{paths}"
    if action.kind is ActionKind.CONFLICT_COPY:
        if action.target is not None:
            return f"!  {action.rel} -> {action.target}   {action.reason}{paths}"
        return f"!  {action.rel}   {action.reason}{paths}"
    dst_label = _SIDE_LABEL.get(action.dst, "?")
    if action.kind is ActionKind.DELETE:
        # "deleted on laptop" alone reads as if the laptop were the target; say what happens to which side.
        return f"x  {dst_label}  {action.rel}   {action.reason}, so removed from the {dst_label}{paths}"
    return f"\u2192 {dst_label}  {action.rel}   {action.reason}{paths}"


def _dry_tally(action: Action, summary: Summary) -> None:
    """Update ``summary`` the way executing ``action`` would, without doing it."""
    if action.kind is ActionKind.COPY:
        if action.dst is Side.LOCAL:
            summary.copied_to_local += 1
        else:
            summary.copied_to_remote += 1
    elif action.kind is ActionKind.DELETE:
        if action.dst is Side.LOCAL:
            summary.deleted_local += 1
        else:
            summary.deleted_remote += 1
    elif action.kind is ActionKind.CONFLICT_COPY:
        summary.conflicts += 1
    elif action.kind is ActionKind.ASK:
        summary.asks += 1


def _tmp_file(tmp_dir: Path) -> Path:
    return tmp_dir / f"{uuid4().hex}.tmp"


def _entry_dict(entry: FileEntry | None) -> dict | None:
    return None if entry is None else {"size": entry.size, "mtime": entry.mtime}


def _save_pending_asks(
    pair_name: str, actions: list[Action], scan_local: Snapshot, scan_remote: Snapshot
) -> None:
    """Persist the current ``conflict: ask`` entries so ``resolve`` can read them.

    Called after every plan (dry-run or real) with the full current list of
    ASK actions, replacing whatever was there before - see
    ``state.save_pending``.
    """
    pending = [
        {
            "rel": action.rel,
            "local": _entry_dict(scan_local.get(action.rel)),
            "remote": _entry_dict(scan_remote.get(action.rel)),
            "reason": action.reason,
        }
        for action in actions
        if action.kind is ActionKind.ASK
    ]
    state.save_pending(pair_name, pending)


def _do_copy(
    action: Action,
    transports: dict[Side, Transport],
    roots: dict[Side, str],
    scan_local: Snapshot,
    scan_remote: Snapshot,
    tmp_dir: Path,
    snapshot: PersistedSnapshot,
    summary: Summary,
    journal: "undo.RunJournal | None" = None,
) -> None:
    """Copy ``action.rel`` from ``action.src`` to ``action.dst`` through a temp file."""
    src_scan = scan_local if action.src is Side.LOCAL else scan_remote
    entry = action.entry if action.entry is not None else src_scan.get(action.rel)
    if entry is None:
        raise ExecutorError(f"no source entry for {action.rel!r}")

    tmp_path = _tmp_file(tmp_dir)
    transports[action.src].fetch(roots[action.src], action.rel, tmp_path)
    if journal is not None:
        journal.record_write(action.dst, action.rel, transports[action.dst], roots[action.dst], entry)
    transports[action.dst].store(tmp_path, roots[action.dst], action.rel, entry.mtime)
    tmp_path.unlink(missing_ok=True)

    if transports[action.dst].stat(roots[action.dst], action.rel) is None:
        raise ExecutorError(f"store did not produce {action.rel!r} on {transports[action.dst].label}")

    # The snapshot records the source's fingerprint (size, mtime), which is
    # what ``store`` just set on the destination too - so next run's scan of
    # either side should classify this path as UNCHANGED against it.
    snapshot[action.rel] = _entry_to_snapshot(entry)
    if action.dst is Side.LOCAL:
        summary.copied_to_local += 1
    else:
        summary.copied_to_remote += 1




def _entry_for(action: Action, src_scan: Snapshot) -> FileEntry | None:
    return action.entry if action.entry is not None else src_scan.get(action.rel)


def _do_copy_batch(
    batch: list[Action],
    transports: dict[Side, Transport],
    roots: dict[Side, str],
    scan_local: Snapshot,
    scan_remote: Snapshot,
    tmp_dir: Path,
    snapshot: PersistedSnapshot,
    summary: Summary,
    log: SyncLogger,
    journal: "undo.RunJournal | None" = None,
) -> None:
    """Copy a whole batch of same-direction COPY actions via one fetch_many + store_many.

    The ``fetch_many``/``store_many`` calls are genuinely all-or-nothing as
    far as this function is concerned: either raises (a real transport
    failure - propagated to the caller, which retries the whole batch
    file-by-file) or both return having moved every file. What is *not*
    all-or-nothing is the per-file check after that: unless the destination
    transport declares itself self-verifying
    (:attr:`~androsync.transport.base.Transport.store_many_is_self_verifying`,
    true only for :class:`~androsync.transport.adb.AdbTransport`), ``stat``
    re-confirms each file actually landed at ``rel`` - the case this catches
    is a transport's ``store`` silently succeeding into the wrong place
    (``LocalDirTransport``'s ``shutil.copy2`` copies *into* an existing
    same-named directory instead of raising - confirmed the hard way when
    this batching first shipped) rather than raising outright. A failed
    per-file check is reported and counted right here, file by file - it
    must not raise (that would make the caller re-run every *other*,
    already-good file in the batch too, double-counting them in ``summary``).
    """
    src_side = batch[0].src
    dst_side = batch[0].dst
    src_scan = scan_local if src_side is Side.LOCAL else scan_remote
    dst_transport = transports[dst_side]

    resolved: list[tuple[Action, FileEntry, Path]] = []
    for action in batch:
        entry = _entry_for(action, src_scan)
        if entry is None:
            raise ExecutorError(f"no source entry for {action.rel!r}")
        resolved.append((action, entry, _tmp_file(tmp_dir)))

    if journal is not None:
        journal.record_write_many(dst_side, dst_transport, roots[dst_side], [(a.rel, e) for a, e, _t in resolved])

    transports[src_side].fetch_many([(roots[src_side], a.rel, tmp) for a, _e, tmp in resolved])
    dst_transport.store_many([(tmp, roots[dst_side], a.rel, e.mtime) for a, e, tmp in resolved])

    for action, entry, tmp_path in resolved:
        tmp_path.unlink(missing_ok=True)
        if not dst_transport.store_many_is_self_verifying and dst_transport.stat(roots[dst_side], action.rel) is None:
            log(f"ERROR {action.rel}: store did not produce it on {dst_transport.label}")
            summary.errors += 1
            continue
        snapshot[action.rel] = _entry_to_snapshot(entry)
        if dst_side is Side.LOCAL:
            summary.copied_to_local += 1
        else:
            summary.copied_to_remote += 1


def _flush_copy_batch(
    batch: list[Action],
    transports: dict[Side, Transport],
    roots: dict[Side, str],
    scan_local: Snapshot,
    scan_remote: Snapshot,
    tmp_dir: Path,
    snapshot: PersistedSnapshot,
    summary: Summary,
    log: SyncLogger,
    journal: "undo.RunJournal | None" = None,
) -> None:
    """Execute (and log) one accumulated batch of same-direction COPY actions.

    A single-file "batch" always goes through the plain per-file
    :func:`_do_copy` path - there is no round-trip to save by batching one
    file, and it keeps that common case's error handling exactly as before.
    A real batch is logged upfront (it either all transfers or all falls
    back together) and, if the combined transfer raises for any reason,
    retried one file at a time through :func:`_do_copy` so a single bad file
    still cannot abort the rest of the run.
    """
    if not batch:
        return
    if len(batch) == 1:
        action = batch[0]
        log(_format_action(action, roots[Side.LOCAL], roots[Side.REMOTE]))
        try:
            _do_copy(action, transports, roots, scan_local, scan_remote, tmp_dir, snapshot, summary, journal)
        except KeyboardInterrupt:
            raise
        except TransportCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad action must not abort the run
            log(f"ERROR {action.rel}: {exc}")
            summary.errors += 1
        return

    for action in batch:
        log(_format_action(action, roots[Side.LOCAL], roots[Side.REMOTE]))
    try:
        _do_copy_batch(batch, transports, roots, scan_local, scan_remote, tmp_dir, snapshot, summary, log, journal)
    except KeyboardInterrupt:
        raise
    except TransportCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - the batch must not abort the run either
        log(f"batched transfer of {len(batch)} files failed ({exc}); retrying individually")
        for action in batch:
            try:
                _do_copy(action, transports, roots, scan_local, scan_remote, tmp_dir, snapshot, summary, journal)
            except KeyboardInterrupt:
                raise
            except TransportCancelled:
                raise
            except Exception as exc2:  # noqa: BLE001
                log(f"ERROR {action.rel}: {exc2}")
                summary.errors += 1


def _do_delete(
    action: Action,
    transports: dict[Side, Transport],
    roots: dict[Side, str],
    snapshot: PersistedSnapshot,
    summary: Summary,
    journal: "undo.RunJournal | None" = None,
) -> None:
    """Delete ``action.rel`` from ``action.dst``."""
    if journal is not None:
        journal.record_delete(action.dst, action.rel, transports[action.dst], roots[action.dst])
    transports[action.dst].delete(roots[action.dst], action.rel)
    snapshot.pop(action.rel, None)
    if action.dst is Side.LOCAL:
        summary.deleted_local += 1
    else:
        summary.deleted_remote += 1


def _do_conflict_copy(
    action: Action,
    transports: dict[Side, Transport],
    roots: dict[Side, str],
    tmp_dir: Path,
    snapshot: PersistedSnapshot,
    summary: Summary,
    *,
    conflict_copies: str,
    now: datetime,
    journal: "undo.RunJournal | None" = None,
) -> None:
    """Preserve the losing side's current file before the winner overwrites it.

    ``action.src == action.dst`` is the losing side (see ``model.Action``).

    ``conflict_copies == "folder"`` (default, task 29b): fetch the loser's
    current bytes straight to a timestamped backup under
    ``.androsync/conflicts/`` on the laptop side only - nothing is renamed
    or pushed anywhere, so the subsequent winner COPY action simply
    overwrites ``action.rel`` in place on both sides as if there had been no
    conflict copy at all.

    ``conflict_copies == "beside"`` (the pre-task-29b behaviour): rename the
    loser out of the way in its own tree first, then push that renamed copy
    to the other side too, so the ``*.conflict-*`` file ends up on both
    sides.
    """
    side = action.src

    if conflict_copies == "folder":
        entry = action.entry
        if entry is None:
            entry = transports[side].stat(roots[side], action.rel)
        if entry is None:
            raise ExecutorError(f"conflict source missing at {action.rel!r} on {transports[side].label}")

        tmp_path = _tmp_file(tmp_dir)
        transports[side].fetch(roots[side], action.rel, tmp_path)
        backup_rel = conflict_folder_rel(now, action.rel)
        transports[Side.LOCAL].store(tmp_path, roots[Side.LOCAL], backup_rel, entry.mtime)
        tmp_path.unlink(missing_ok=True)
        if journal is not None:
            journal.record_conflict_saved(backup_rel)

        summary.conflicts += 1
        return

    if action.target is None:
        raise ExecutorError("'beside' conflict copy requires a target name")
    other = Side.REMOTE if side is Side.LOCAL else Side.LOCAL

    pre_rename_entry = action.entry if action.entry is not None else transports[side].stat(roots[side], action.rel)
    if journal is not None and pre_rename_entry is not None:
        journal.record_conflict_rename(side, action.rel, action.target, pre_rename_entry)
    transports[side].rename(roots[side], action.rel, action.target)

    entry = action.entry
    if entry is None:
        entry = transports[side].stat(roots[side], action.target)
    if entry is None:
        raise ExecutorError(f"conflict rename produced no file at {action.target!r}")
    snapshot[action.target] = _entry_to_snapshot(entry)

    tmp_path = _tmp_file(tmp_dir)
    transports[side].fetch(roots[side], action.target, tmp_path)
    if journal is not None:
        journal.record_write(other, action.target, transports[other], roots[other], entry)
    transports[other].store(tmp_path, roots[other], action.target, entry.mtime)
    tmp_path.unlink(missing_ok=True)

    summary.conflicts += 1


def run_pair(
    pair: PairConfig,
    local: Transport,
    remote: Transport,
    *,
    dry_run: bool = False,
    log: SyncLogger = print,
    now: datetime | None = None,
    cancel: Callable[[], bool] | None = None,
    confirm_large: bool = False,
) -> Summary:
    """Bring ``pair`` in sync between ``local`` and ``remote``. Returns a run ``Summary``.

    A plan that would delete a large share of the pair's files, or a first run that would copy a huge amount, is
    stopped before anything is changed and reported through ``Summary.needs_confirmation`` / ``confirmation``
    (see ``safety.py``); pass ``confirm_large=True`` once the user has said yes.

    A Stop (``cancel()`` turning true, or a transport call killed by ``Transport.abort``) ends the run early
    and returns the summary of what was finished, not an error; files already copied stay recorded.

    Raises ``ExecutorError`` if the local root is missing, or the remote
    root is missing on a pair whose direction never writes to it
    (``phone-to-laptop``). For ``two-way``/``laptop-to-phone`` a missing
    remote root is created (skipped under ``dry_run``, which never touches
    disk).
    """
    summary = Summary()
    try:
        return _run_pair(
            pair, local, remote, summary, dry_run=dry_run, log=log, now=now, cancel=cancel, confirm_large=confirm_large
        )
    except TransportCancelled:
        log("cancelled")
        return summary


def _run_pair(
    pair: PairConfig,
    local: Transport,
    remote: Transport,
    summary: Summary,
    *,
    dry_run: bool,
    log: SyncLogger,
    now: datetime | None,
    cancel: Callable[[], bool] | None,
    confirm_large: bool,
) -> Summary:
    """The body of :func:`run_pair`; raises ``TransportCancelled`` when stopped in the middle of a call."""
    run_now = now if now is not None else datetime.now()
    local_root = str(pair.local)
    remote_root = pair.remote

    if not local.exists(local_root):
        raise ExecutorError(f"local root does not exist: {local_root}")

    if not remote.exists(remote_root):
        if pair.direction in _ONE_WAY_TO_REMOTE_DIRECTIONS:
            if not dry_run:
                remote.mkdirs(remote_root, "")
        else:
            raise ExecutorError(f"remote root does not exist: {remote_root}")

    ignore = IgnoreRules(pair.ignore)
    # Progress lines are for real runs only: a dry run's log is the list of planned actions (status, preview).
    note: SyncLogger = (lambda _msg: None) if dry_run else log
    note("scanning the laptop folder...")
    scan_local = local.scan(local_root, ignore)
    note(f"laptop: {len(scan_local)} files; scanning the phone folder...")
    scan_remote = remote.scan(remote_root, ignore)
    note(f"phone: {len(scan_remote)} files; planning...")
    key = state.key_for(pair.name, remote)  # one history per phone (task 33a)
    snapshot: PersistedSnapshot = dict(state.load_snapshot(key))
    snapshot_before_run = dict(snapshot)  # captured before any mutation, for the run's journal (task 30a)

    hasher = _make_hasher(local, remote, local_root, remote_root)
    plan_result = plan(scan_local, scan_remote, snapshot, pair, now=run_now, hasher=hasher)
    actions = plan_result.actions
    for warning in plan_result.warnings:
        log(f"!  {warning}")
    for rel in plan_result.resynced:
        local_entry = scan_local.get(rel)
        remote_entry = scan_remote.get(rel)
        if local_entry is not None and remote_entry is not None:
            log(f"=  {rel}   same content on both sides, mtimes differ; not a conflict")
            snapshot[rel] = SnapshotEntry(
                size=local_entry.size, mtime_local=local_entry.mtime, mtime_remote=remote_entry.mtime
            )
    _save_pending_asks(key, actions, scan_local, scan_remote)
    note(f"plan: {len(actions)} actions")

    problems = safety.check_plan(
        actions, len(snapshot_before_run), not snapshot_before_run, scan_local, scan_remote, pair.safety_limits
    )
    if problems:
        summary.needs_confirmation = True
        summary.confirmation = problems

    if dry_run:
        for action in actions:
            log(_format_action(action, local_root, remote_root))
            _dry_tally(action, summary)
        for problem in problems:
            log(f"!  needs your confirmation: {problem}")
        return summary

    if problems and not confirm_large:
        for problem in problems:
            log(f"STOPPED, nothing was changed. {problem}")
        log("Check the list, then confirm to go ahead (app: Go ahead, command line: --confirm-large).")
        state.save_pair_meta(
            key,
            {
                "last_run": run_now.isoformat(),
                "last_result": f"needs confirmation: {problems[0]}",
                "summary": asdict(summary),
            },
        )
        return summary
    if problems:
        log("confirmed: going ahead with the large plan")
        summary.needs_confirmation = False
        summary.confirmation = []

    transports = {Side.LOCAL: local, Side.REMOTE: remote}
    roots = {Side.LOCAL: local_root, Side.REMOTE: remote_root}

    journal: undo.RunJournal | None = None
    if pair.undo_enabled:
        journal = undo.RunJournal.start(
            key, snapshot_before_run, {"max_file_mb": pair.undo_max_file_mb}, kind="sync", now=run_now
        )
        summary.run_id = journal.run_id

    with tempfile.TemporaryDirectory(prefix="androsync-") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        pending_batch: list[Action] = []
        pending_bytes = 0
        batches_done = 0

        def flush_batch() -> None:
            nonlocal pending_batch, pending_bytes
            if not pending_batch:
                return
            nonlocal batches_done
            batches_done += 1
            if len(pending_batch) > 1:
                log(f"batch {batches_done}: {len(pending_batch)} files, {pending_bytes / (1024 * 1024):.1f} MB")
            _flush_copy_batch(
                pending_batch, transports, roots, scan_local, scan_remote, tmp_dir, snapshot, summary, log, journal
            )
            state.save_snapshot(key, snapshot)
            pending_batch = []
            pending_bytes = 0

        for action in actions:
            if cancel is not None and cancel():
                # Deliberately not flushing: a cancel takes effect between
                # actions, same as before batching - files only accumulated
                # into pending_batch (never yet transferred) are simply
                # replanned next run. The journal (if any) stays unfinished -
                # exactly right, since the run itself did not finish either.
                log("cancelled")
                return summary

            if action.kind is ActionKind.COPY:
                src_scan = scan_local if action.src is Side.LOCAL else scan_remote
                size = (_entry_for(action, src_scan) or FileEntry(size=0, mtime=0.0)).size
                if pending_batch and (
                    pending_batch[0].dst is not action.dst
                    or len(pending_batch) >= pair.transfer_batch_files
                    or pending_bytes + size > pair.transfer_batch_mb * 1024 * 1024
                ):
                    flush_batch()
                pending_batch.append(action)
                pending_bytes += size
                continue

            flush_batch()

            log(_format_action(action, local_root, remote_root))
            try:
                if action.kind is ActionKind.DELETE:
                    _do_delete(action, transports, roots, snapshot, summary, journal)
                elif action.kind is ActionKind.CONFLICT_COPY:
                    _do_conflict_copy(
                        action,
                        transports,
                        roots,
                        tmp_dir,
                        snapshot,
                        summary,
                        conflict_copies=pair.conflict_copies,
                        now=run_now,
                        journal=journal,
                    )
                elif action.kind is ActionKind.ASK:
                    summary.asks += 1
                else:
                    continue
            except KeyboardInterrupt:
                raise
            except TransportCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad action must not abort the run
                log(f"ERROR {action.rel}: {exc}")
                summary.errors += 1
                continue

            state.save_snapshot(key, snapshot)

        flush_batch()

    reconcile_snapshot(snapshot, scan_local, scan_remote, pair.mtime_tolerance_s)
    state.save_snapshot(key, snapshot)
    state.save_pair_meta(
        key,
        {
            "last_run": run_now.isoformat(),
            "last_result": str(summary),
            "summary": asdict(summary),
        },
    )

    if journal is not None:
        journal.finish(summary)
        undo.prune(key, pair.undo_keep_runs, pair.undo_max_total_mb)

    if summary.errors == 0:
        try:
            if versioning.commit_if_configured(pair, summary, run_now):
                log("versioned: committed to the local git repo")
        except versioning.VersioningError as exc:
            # A versioning failure is a bonus feature falling over, not a
            # sync failure - the run above already succeeded and must stay
            # reported that way.
            log(f"versioning: {exc}")

    return summary


_RESOLVE_CHOICES = ("local", "remote", "both")


def resolve_pending(
    pair: PairConfig,
    local: Transport,
    remote: Transport,
    rel: str,
    choice: str,
    *,
    now: datetime | None = None,
) -> None:
    """Apply a manual ``resolve`` decision for one pending ``conflict: ask`` entry.

    Mirrors exactly what the planner would have produced automatically for
    a ``local-wins``/``remote-wins``/``newest-wins``-with-conflict-copies
    policy - just driven by the user's choice instead of the pair's
    configured (``ask``) policy - so the same executor helpers a normal run
    uses do the actual file operations. ``choice`` is one of ``"local"``
    (local wins, remote's version is overwritten), ``"remote"`` (the
    mirror), or ``"both"`` (newest wins, but the loser is kept as a
    ``*.conflict-<side>-<timestamp>`` copy on both sides - reusing
    ``conflict.conflict_name`` and ``_do_conflict_copy`` exactly as an
    automatic ``newest-wins`` conflict resolution would). Raises
    ``ExecutorError`` if ``rel`` no longer exists on both sides. Clears the
    entry from the pending file on success.
    """
    if choice not in _RESOLVE_CHOICES:
        raise ValueError(f"unknown resolve choice: {choice!r}; expected one of {_RESOLVE_CHOICES}")

    run_now = now if now is not None else datetime.now()
    local_root = str(pair.local)
    remote_root = pair.remote

    local_entry = local.stat(local_root, rel)
    remote_entry = remote.stat(remote_root, rel)
    if local_entry is None or remote_entry is None:
        raise ExecutorError(f"{rel!r} no longer exists on both sides; nothing to resolve")

    if choice == "both":
        # Same tie-break as planner._pick_winner("newest-wins", ...).
        winner = Side.REMOTE if remote_entry.mtime > local_entry.mtime else Side.LOCAL
    else:
        winner = Side.LOCAL if choice == "local" else Side.REMOTE
    loser = Side.REMOTE if winner is Side.LOCAL else Side.LOCAL
    winner_entry = local_entry if winner is Side.LOCAL else remote_entry
    loser_entry = local_entry if loser is Side.LOCAL else remote_entry

    transports = {Side.LOCAL: local, Side.REMOTE: remote}
    roots = {Side.LOCAL: local_root, Side.REMOTE: remote_root}
    key = state.key_for(pair.name, remote)
    snapshot: PersistedSnapshot = dict(state.load_snapshot(key))
    summary = Summary()

    with tempfile.TemporaryDirectory(prefix="androsync-resolve-") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        if choice == "both":
            if pair.conflict_copies == "beside":
                target = conflict_name(rel, loser, run_now)
                reason = f"resolved manually: keeping {_SIDE_LABEL[loser]} copy as {target}"
            else:
                target = None
                reason = f"resolved manually: saving {_SIDE_LABEL[loser]} copy to {CONFLICTS_DIR}/"
            conflict_action = Action(
                ActionKind.CONFLICT_COPY,
                rel,
                loser,
                loser,
                reason,
                entry=loser_entry,
                target=target,
            )
            _do_conflict_copy(
                conflict_action,
                transports,
                roots,
                tmp_dir,
                snapshot,
                summary,
                conflict_copies=pair.conflict_copies,
                now=run_now,
            )

        copy_action = Action(
            ActionKind.COPY,
            rel,
            winner,
            loser,
            f"resolved manually ({_SIDE_LABEL[winner]})",
            entry=winner_entry,
        )
        _do_copy(copy_action, transports, roots, {}, {}, tmp_dir, snapshot, summary)

    state.save_snapshot(key, snapshot)
    pending = [entry for entry in state.load_pending(key) if entry["rel"] != rel]
    state.save_pending(key, pending)
