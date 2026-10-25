"""Pure sync planner: turn two scans and the last known snapshot into actions.

No I/O happens here. ``plan()`` compares each side's ``FileEntry`` against the
last synced snapshot to classify it as unchanged/created/modified/deleted,
then decides what (if anything) to do about it, respecting the pair's
``direction``, ``propagate_deletes`` and conflict policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from androsync.config import PairConfig
from androsync.conflict import CONFLICTS_DIR, conflict_name, is_conflict_copy
from androsync.model import (
    Action,
    ActionKind,
    ChangeKind,
    FileEntry,
    PersistedSnapshot,
    Side,
    Snapshot,
    SnapshotEntry,
)

_SIDE_LABEL = {Side.LOCAL: "laptop", Side.REMOTE: "phone"}

#: Given a list of candidate rels (both sides changed, same size), return
#: ``{rel: (local_sha256, remote_sha256)}``. Called at most once per
#: ``plan()`` call, only when there is at least one candidate.
HasherFn = Callable[[list[str]], dict[str, tuple[str | None, str | None]]]
_POLICY_LABEL = {
    "newest-wins": "newest wins",
    "local-wins": "local wins",
    "remote-wins": "remote wins",
}

_DIRECTION_SIDES = {
    # direction -> (source side, destination side), or None for two-way.
    "two-way": None,
    "laptop-to-phone": (Side.LOCAL, Side.REMOTE),
    "phone-to-laptop": (Side.REMOTE, Side.LOCAL),
}


def classify(entry: FileEntry | None, last: FileEntry | None, tol: float) -> ChangeKind:
    """Classify one side's entry against the last known snapshot entry."""
    if entry is None and last is None:
        return ChangeKind.ABSENT
    if last is None:
        return ChangeKind.CREATED
    if entry is None:
        return ChangeKind.DELETED
    if entry.size == last.size and abs(entry.mtime - last.mtime) <= tol:
        return ChangeKind.UNCHANGED
    return ChangeKind.MODIFIED


def _reduced(kind: ChangeKind) -> str:
    """Collapse a ``ChangeKind`` to "nochange" / "changed" / "deleted".

    UNCHANGED and ABSENT both mean "this side has not moved since the last
    snapshot" (there was nothing before and there is nothing now, or it
    matches what was recorded); CREATED and MODIFIED both mean "this side
    has new content to propagate". This collapse is what lets the same
    decision table serve both the first run (CREATED/ABSENT only, since
    ``last`` has no entry yet) and later runs (UNCHANGED/MODIFIED/DELETED).
    """
    if kind is ChangeKind.DELETED:
        return "deleted"
    if kind in (ChangeKind.CREATED, ChangeKind.MODIFIED):
        return "changed"
    return "nochange"


def _entries_equal(a: FileEntry, b: FileEntry, tol: float) -> bool:
    return a.size == b.size and abs(a.mtime - b.mtime) <= tol


def _other(side: Side) -> Side:
    return Side.REMOTE if side is Side.LOCAL else Side.LOCAL


def _copy_reason(kind: ChangeKind, side: Side) -> str:
    label = _SIDE_LABEL[side]
    if kind is ChangeKind.CREATED:
        return f"new on {label}"
    return f"changed on {label}"


def _pick_winner(policy: str, local_entry: FileEntry, remote_entry: FileEntry) -> Side:
    if policy == "local-wins":
        return Side.LOCAL
    if policy == "remote-wins":
        return Side.REMOTE
    # newest-wins (also the fallback used for already-named conflict copies):
    # larger mtime wins, ties go to LOCAL.
    if remote_entry.mtime > local_entry.mtime:
        return Side.REMOTE
    return Side.LOCAL


def _plan_both_changed(
    rel: str,
    local_entry: FileEntry,
    remote_entry: FileEntry,
    pair: PairConfig,
    tol: float,
    now: datetime,
    src_side: Side | None,
    hashes: dict[str, tuple[str | None, str | None]],
    resynced: list[str],
) -> list[Action]:
    """Both sides have new content since the last snapshot. Resolve or drop."""
    if _entries_equal(local_entry, remote_entry, tol):
        # Same size/mtime on both sides: already in sync, nothing to do.
        return []

    local_hash, remote_hash = hashes.get(rel, (None, None))
    if local_hash is not None and remote_hash is not None and local_hash == remote_hash:
        # Same size, equal content hash: not a real conflict - just a file
        # whose mtimes drifted apart outside androsync (e.g. copied by hand
        # over MTP, which does not preserve timestamps). Record it as synced
        # rather than picking a "winner" and pushing a needless copy.
        resynced.append(rel)
        return []

    if src_side is not None:
        # One-way direction: the source side is authoritative by definition,
        # so this is never a conflict, regardless of the configured policy
        # or of which side happens to be newer. No conflict copy is made.
        dst_side = _other(src_side)
        reason = f"changed on both sides, {_SIDE_LABEL[src_side]} is authoritative"
        return [Action(ActionKind.COPY, rel, src_side, dst_side, reason)]

    if is_conflict_copy(rel):
        # A conflict-copy file that itself changed on both sides is not
        # re-conflicted (that would chain conflict-copy suffixes forever);
        # just keep the newest version.
        winner = _pick_winner("newest-wins", local_entry, remote_entry)
        loser = _other(winner)
        reason = f"conflict copy changed on both sides, newest kept ({_SIDE_LABEL[winner]})"
        return [Action(ActionKind.COPY, rel, winner, loser, reason)]

    if pair.conflict == "ask":
        return [Action(ActionKind.ASK, rel, None, None, "modified on both sides")]

    winner = _pick_winner(pair.conflict, local_entry, remote_entry)
    loser = _other(winner)
    loser_entry = local_entry if loser is Side.LOCAL else remote_entry
    policy_label = _POLICY_LABEL[pair.conflict]

    actions: list[Action] = []
    if pair.keep_conflict_copies:
        if pair.conflict_copies == "beside":
            target = conflict_name(rel, loser, now)
            reason = (
                f"conflict: {policy_label} ({_SIDE_LABEL[winner]}); "
                f"keeping {_SIDE_LABEL[loser]} copy as {target}"
            )
        else:
            target = None
            reason = (
                f"conflict: {policy_label} ({_SIDE_LABEL[winner]}); "
                f"saving {_SIDE_LABEL[loser]} copy to {CONFLICTS_DIR}/"
            )
        actions.append(
            Action(ActionKind.CONFLICT_COPY, rel, loser, loser, reason, entry=loser_entry, target=target)
        )
    actions.append(
        Action(
            ActionKind.COPY,
            rel,
            winner,
            loser,
            f"conflict: {policy_label} ({_SIDE_LABEL[winner]})",
        )
    )
    return actions


def _filter_direction(actions: list[Action], dst_side: Side | None) -> list[Action]:
    """Drop actions that would write to the wrong side for a one-way pair."""
    if dst_side is None:
        return actions
    return [a for a in actions if a.dst is dst_side]


def _last_sides(last_entry: SnapshotEntry | None) -> tuple[FileEntry | None, FileEntry | None]:
    """Split a persisted ``SnapshotEntry`` into per-side ``FileEntry`` values for ``classify``."""
    if last_entry is None:
        return None, None
    last_local = FileEntry(size=last_entry.size, mtime=last_entry.mtime_local)
    last_remote = FileEntry(size=last_entry.size, mtime=last_entry.mtime_remote)
    return last_local, last_remote


def _needs_hash(
    rel: str,
    local_entry: FileEntry | None,
    remote_entry: FileEntry | None,
    local_r: str,
    remote_r: str,
    pair: PairConfig,
    tol: float,
) -> bool:
    """True if ``rel`` is a genuine two-way conflict candidate worth hashing.

    Only "both sides changed, same size, not already equal" rels need a hash
    - a one-way pair's source side is authoritative regardless, and an
    already-named conflict-copy file keeps the plain newest-wins rule, so
    neither needs a content comparison to decide.
    """
    if local_r != "changed" or remote_r != "changed":
        return False
    if local_entry is None or remote_entry is None:
        return False
    if _DIRECTION_SIDES[pair.direction] is not None:
        return False
    if is_conflict_copy(rel):
        return False
    if _entries_equal(local_entry, remote_entry, tol):
        return False
    return local_entry.size == remote_entry.size


def _plan_one(
    rel: str,
    local_entry: FileEntry | None,
    remote_entry: FileEntry | None,
    last_entry: SnapshotEntry | None,
    pair: PairConfig,
    tol: float,
    now: datetime,
    hashes: dict[str, tuple[str | None, str | None]],
    resynced: list[str],
) -> list[Action]:
    last_local, last_remote = _last_sides(last_entry)
    local_kind = classify(local_entry, last_local, tol)
    remote_kind = classify(remote_entry, last_remote, tol)
    local_r = _reduced(local_kind)
    remote_r = _reduced(remote_kind)

    sides = _DIRECTION_SIDES[pair.direction]
    src_side = sides[0] if sides else None
    dst_side = sides[1] if sides else None

    if local_r == "changed" and remote_r == "changed":
        acts = _plan_both_changed(rel, local_entry, remote_entry, pair, tol, now, src_side, hashes, resynced)
        return _filter_direction(acts, dst_side)

    if local_r == "changed" and remote_r == "nochange":
        act = Action(ActionKind.COPY, rel, Side.LOCAL, Side.REMOTE, _copy_reason(local_kind, Side.LOCAL))
        return _filter_direction([act], dst_side)

    if remote_r == "changed" and local_r == "nochange":
        act = Action(ActionKind.COPY, rel, Side.REMOTE, Side.LOCAL, _copy_reason(remote_kind, Side.REMOTE))
        return _filter_direction([act], dst_side)

    if local_r == "deleted" and remote_r == "nochange":
        if not pair.propagate_deletes:
            return []
        act = Action(ActionKind.DELETE, rel, None, Side.REMOTE, f"deleted on {_SIDE_LABEL[Side.LOCAL]}")
        return _filter_direction([act], dst_side)

    if remote_r == "deleted" and local_r == "nochange":
        if not pair.propagate_deletes:
            return []
        act = Action(ActionKind.DELETE, rel, None, Side.LOCAL, f"deleted on {_SIDE_LABEL[Side.REMOTE]}")
        return _filter_direction([act], dst_side)

    if local_r == "deleted" and remote_r == "changed":
        act = Action(
            ActionKind.COPY, rel, Side.REMOTE, Side.LOCAL, "deleted vs modified", entry=remote_entry
        )
        return _filter_direction([act], dst_side)

    if remote_r == "deleted" and local_r == "changed":
        act = Action(
            ActionKind.COPY, rel, Side.LOCAL, Side.REMOTE, "deleted vs modified", entry=local_entry
        )
        return _filter_direction([act], dst_side)

    # Both deleted, both unchanged/absent, or any other remaining combo: nothing to do.
    return []


@dataclass
class PlanResult:
    """``plan()``'s output: the actions to run, plus warnings and quietly-resynced paths."""

    actions: list[Action]
    warnings: list[str] = field(default_factory=list)
    #: Rels that looked like a conflict (both sides changed, same size) but
    #: turned out to have identical content once hashed - no action was
    #: planned for them, but the caller should still record their current
    #: per-side mtimes in the snapshot so they stay quiet next run.
    resynced: list[str] = field(default_factory=list)


def _case_collision_groups(rels: set[str]) -> list[list[str]]:
    """Group ``rels`` that are distinct but equal once lower-cased.

    The phone's shared storage is case-insensitive, so e.g. ``Notes.md`` and
    ``notes.md`` are the same file on that side even though the laptop sees
    two - syncing them independently would have each write clobber the
    other's copy on the phone. Each returned group has 2+ members, sorted.
    """
    by_lower: dict[str, list[str]] = {}
    for rel in rels:
        by_lower.setdefault(rel.lower(), []).append(rel)
    return [sorted(group) for group in by_lower.values() if len(group) > 1]


def plan(
    scan_local: Snapshot,
    scan_remote: Snapshot,
    last: PersistedSnapshot,
    pair: PairConfig,
    now: datetime | None = None,
    hasher: HasherFn | None = None,
) -> PlanResult:
    """Compute the actions to bring ``pair`` in sync, plus any warnings.

    Pure function w.r.t. the filesystem: takes the two current scans and the
    last saved snapshot, returns what should happen, in ``rel`` order. Paths
    that collide once lower-cased (see ``_case_collision_groups``) get no
    action at all - just a warning - regardless of what would otherwise be
    planned for them.

    The one seam that does I/O is ``hasher`` (task 29a): before deciding
    conflicts, every rel where both sides changed to the same size is
    collected and handed to ``hasher`` in one call, so the caller
    (``executor.run_pair``) can hash both sides in as few round-trips as
    possible. Passing ``hasher=None`` skips content hashing entirely - every
    such rel is then resolved as a real conflict, same as before task 29a.
    """
    now = now if now is not None else datetime.now()
    tol = pair.mtime_tolerance_s

    rels = set(scan_local) | set(scan_remote) | set(last)

    collision_groups = _case_collision_groups(rels)
    colliding_rels = {rel for group in collision_groups for rel in group}
    warnings = [
        "case collision on the phone's case-insensitive storage, skipped: " + ", ".join(group)
        for group in collision_groups
    ]

    sorted_rels = sorted(rels - colliding_rels)

    candidates: list[str] = []
    for rel in sorted_rels:
        local_entry = scan_local.get(rel)
        remote_entry = scan_remote.get(rel)
        last_local, last_remote = _last_sides(last.get(rel))
        local_r = _reduced(classify(local_entry, last_local, tol))
        remote_r = _reduced(classify(remote_entry, last_remote, tol))
        if _needs_hash(rel, local_entry, remote_entry, local_r, remote_r, pair, tol):
            candidates.append(rel)

    hashes: dict[str, tuple[str | None, str | None]] = hasher(candidates) if candidates and hasher else {}

    actions: list[Action] = []
    resynced: list[str] = []
    for rel in sorted_rels:
        actions.extend(
            _plan_one(
                rel,
                scan_local.get(rel),
                scan_remote.get(rel),
                last.get(rel),
                pair,
                tol,
                now,
                hashes,
                resynced,
            )
        )
    return PlanResult(actions=actions, warnings=warnings, resynced=resynced)
