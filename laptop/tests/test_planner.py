"""Table-driven tests for androsync.planner and androsync.conflict."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from androsync.config import PairConfig
from androsync.conflict import conflict_name, is_conflict_copy
from androsync.model import Action, ActionKind, ChangeKind, FileEntry, Side, SnapshotEntry
from androsync.planner import classify, plan

NOW = datetime(2026, 9, 5, 15, 30, 0)


def make_pair(**overrides) -> PairConfig:
    fields = dict(
        name="p",
        local=Path("/local"),
        remote="/sdcard/x",
        direction="two-way",
        conflict="newest-wins",
        keep_conflict_copies=True,
        propagate_deletes=True,
        mtime_tolerance_s=2,
        ignore=[],
    )
    fields.update(overrides)
    return PairConfig(**fields)


# --------------------------------------------------------------------------
# classify()
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry, last, tol, expected",
    [
        (None, None, 2, ChangeKind.ABSENT),
        (FileEntry(10, 100.0), None, 2, ChangeKind.CREATED),
        (None, FileEntry(10, 100.0), 2, ChangeKind.DELETED),
        (FileEntry(10, 100.0), FileEntry(10, 100.0), 2, ChangeKind.UNCHANGED),
        (FileEntry(10, 102.0), FileEntry(10, 100.0), 2, ChangeKind.UNCHANGED),  # |diff| == tol
        (FileEntry(10, 102.1), FileEntry(10, 100.0), 2, ChangeKind.MODIFIED),  # |diff| == tol + 0.1
        (FileEntry(10, 98.0), FileEntry(10, 100.0), 2, ChangeKind.UNCHANGED),  # negative diff, == tol
        (FileEntry(20, 100.0), FileEntry(10, 100.0), 2, ChangeKind.MODIFIED),  # size differs
    ],
)
def test_classify(entry, last, tol, expected):
    assert classify(entry, last, tol) == expected


# --------------------------------------------------------------------------
# plan(): single-change scenarios (later runs, last snapshot non-empty)
# --------------------------------------------------------------------------


def test_created_local_copies_to_remote():
    last = {"other": SnapshotEntry(1, 1.0, 1.0)}
    local = {"other": FileEntry(1, 1.0), "f": FileEntry(10, 100.0)}
    remote = {"other": FileEntry(1, 1.0)}
    actions = plan(local, remote, last, make_pair()).actions
    assert actions == [Action(ActionKind.COPY, "f", Side.LOCAL, Side.REMOTE, "new on laptop")]


def test_created_remote_copies_to_local():
    last = {}
    local = {}
    remote = {"f": SnapshotEntry(10, 100.0, 100.0)}
    actions = plan(local, remote, last, make_pair()).actions
    assert actions == [Action(ActionKind.COPY, "f", Side.REMOTE, Side.LOCAL, "new on phone")]


def test_modified_local_copies_to_remote():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(10, 200.0)}
    remote = {"f": FileEntry(10, 100.0)}
    actions = plan(local, remote, last, make_pair()).actions
    assert actions == [Action(ActionKind.COPY, "f", Side.LOCAL, Side.REMOTE, "changed on laptop")]


def test_modified_remote_copies_to_local():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(10, 100.0)}
    remote = {"f": FileEntry(10, 200.0)}
    actions = plan(local, remote, last, make_pair()).actions
    assert actions == [Action(ActionKind.COPY, "f", Side.REMOTE, Side.LOCAL, "changed on phone")]


def test_deleted_local_propagates_delete_when_enabled():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {}
    remote = {"f": FileEntry(10, 100.0)}
    actions = plan(local, remote, last, make_pair(propagate_deletes=True)).actions
    assert actions == [Action(ActionKind.DELETE, "f", None, Side.REMOTE, "deleted on laptop")]


def test_deleted_local_no_action_when_propagate_disabled():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {}
    remote = {"f": FileEntry(10, 100.0)}
    actions = plan(local, remote, last, make_pair(propagate_deletes=False)).actions
    assert actions == []


def test_deleted_remote_propagates_delete_when_enabled():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(10, 100.0)}
    remote = {}
    actions = plan(local, remote, last, make_pair(propagate_deletes=True)).actions
    assert actions == [Action(ActionKind.DELETE, "f", None, Side.LOCAL, "deleted on phone")]


def test_deleted_remote_no_action_when_propagate_disabled():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(10, 100.0)}
    remote = {}
    actions = plan(local, remote, last, make_pair(propagate_deletes=False)).actions
    assert actions == []


def test_deleted_local_vs_modified_remote_recreates_local():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {}
    remote = {"f": FileEntry(15, 300.0)}
    actions = plan(local, remote, last, make_pair(propagate_deletes=True)).actions
    assert actions == [
        Action(
            ActionKind.COPY, "f", Side.REMOTE, Side.LOCAL, "deleted vs modified", entry=FileEntry(15, 300.0)
        )
    ]


def test_modified_local_vs_deleted_remote_recreates_remote():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(15, 300.0)}
    remote = {}
    actions = plan(local, remote, last, make_pair(propagate_deletes=True)).actions
    assert actions == [
        Action(
            ActionKind.COPY, "f", Side.LOCAL, Side.REMOTE, "deleted vs modified", entry=FileEntry(15, 300.0)
        )
    ]


def test_both_deleted_no_action():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    actions = plan({}, {}, last, make_pair()).actions
    assert actions == []


def test_both_unchanged_no_action():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(10, 100.0)}
    remote = {"f": FileEntry(10, 101.0)}
    actions = plan(local, remote, last, make_pair()).actions
    assert actions == []


def test_both_modified_identical_no_action():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(20, 500.5)}
    actions = plan(local, remote, last, make_pair()).actions
    assert actions == []


# --------------------------------------------------------------------------
# plan(): first run (rel absent from `last`)
# --------------------------------------------------------------------------


def test_first_run_equal_no_action():
    actions = plan({"f": FileEntry(10, 100.0)}, {"f": FileEntry(10, 100.5)}, {}, make_pair()).actions
    assert actions == []


def test_first_run_unequal_is_conflict():
    local = {"f": FileEntry(10, 100.0)}
    remote = {"f": FileEntry(20, 999.0)}
    actions = plan(local, remote, {}, make_pair(conflict="newest-wins", keep_conflict_copies=False), now=NOW).actions
    # remote is newer -> remote wins, local is the loser.
    assert actions == [Action(ActionKind.COPY, "f", Side.REMOTE, Side.LOCAL, "conflict: newest wins (phone)")]


def test_first_run_one_side_only_copies_never_deletes():
    local = {"f": FileEntry(10, 100.0)}
    remote = {}
    actions = plan(local, remote, {}, make_pair()).actions
    assert actions == [Action(ActionKind.COPY, "f", Side.LOCAL, Side.REMOTE, "new on laptop")]
    assert all(a.kind != ActionKind.DELETE for a in actions)


# --------------------------------------------------------------------------
# plan(): conflict policies
# --------------------------------------------------------------------------


def test_both_modified_newest_wins_with_conflict_copy_beside_mode():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(30, 900.0)}  # remote newer -> remote wins, local is loser
    pair = make_pair(conflict="newest-wins", keep_conflict_copies=True, conflict_copies="beside")
    actions = plan(local, remote, last, pair, now=NOW).actions
    target = conflict_name("f", Side.LOCAL, NOW)
    assert actions == [
        Action(
            ActionKind.CONFLICT_COPY,
            "f",
            Side.LOCAL,
            Side.LOCAL,
            f"conflict: newest wins (phone); keeping laptop copy as {target}",
            entry=FileEntry(20, 500.0),
            target=target,
        ),
        Action(ActionKind.COPY, "f", Side.REMOTE, Side.LOCAL, "conflict: newest wins (phone)"),
    ]


def test_both_modified_newest_wins_with_conflict_copy_default_folder_mode():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(30, 900.0)}  # remote newer -> remote wins, local is loser
    pair = make_pair(conflict="newest-wins", keep_conflict_copies=True)  # conflict_copies defaults to "folder"
    actions = plan(local, remote, last, pair, now=NOW).actions
    assert actions == [
        Action(
            ActionKind.CONFLICT_COPY,
            "f",
            Side.LOCAL,
            Side.LOCAL,
            "conflict: newest wins (phone); saving laptop copy to .androsync/conflicts/",
            entry=FileEntry(20, 500.0),
            target=None,
        ),
        Action(ActionKind.COPY, "f", Side.REMOTE, Side.LOCAL, "conflict: newest wins (phone)"),
    ]


def test_both_modified_newest_wins_without_conflict_copy():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(30, 900.0)}
    pair = make_pair(conflict="newest-wins", keep_conflict_copies=False)
    actions = plan(local, remote, last, pair, now=NOW).actions
    assert actions == [Action(ActionKind.COPY, "f", Side.REMOTE, Side.LOCAL, "conflict: newest wins (phone)")]


def test_both_modified_local_wins():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(30, 900.0)}  # remote newer, but local-wins forces LOCAL anyway
    pair = make_pair(conflict="local-wins", keep_conflict_copies=True, conflict_copies="beside")
    actions = plan(local, remote, last, pair, now=NOW).actions
    target = conflict_name("f", Side.REMOTE, NOW)
    assert actions == [
        Action(
            ActionKind.CONFLICT_COPY,
            "f",
            Side.REMOTE,
            Side.REMOTE,
            f"conflict: local wins (laptop); keeping phone copy as {target}",
            entry=FileEntry(30, 900.0),
            target=target,
        ),
        Action(ActionKind.COPY, "f", Side.LOCAL, Side.REMOTE, "conflict: local wins (laptop)"),
    ]


def test_both_modified_remote_wins():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}  # local newer, but remote-wins forces REMOTE anyway
    remote = {"f": FileEntry(30, 900.0)}
    pair = make_pair(conflict="remote-wins", keep_conflict_copies=False)
    actions = plan(local, remote, last, pair, now=NOW).actions
    assert actions == [Action(ActionKind.COPY, "f", Side.REMOTE, Side.LOCAL, "conflict: remote wins (phone)")]


def test_both_modified_ask():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(30, 900.0)}
    pair = make_pair(conflict="ask")
    actions = plan(local, remote, last, pair, now=NOW).actions
    assert actions == [Action(ActionKind.ASK, "f", None, None, "modified on both sides")]


def test_conflict_newest_wins_tie_goes_local():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(30, 500.0)}  # same mtime, different size -> conflict, tie -> LOCAL
    pair = make_pair(conflict="newest-wins", keep_conflict_copies=False)
    actions = plan(local, remote, last, pair, now=NOW).actions
    assert actions == [Action(ActionKind.COPY, "f", Side.LOCAL, Side.REMOTE, "conflict: newest wins (laptop)")]


# --------------------------------------------------------------------------
# plan(): conflict-copy paths are never re-conflicted
# --------------------------------------------------------------------------


def test_conflict_copy_path_not_treated_as_conflict():
    rel = "notes/a.conflict-phone-20260905-153000.md"
    last = {rel: SnapshotEntry(10, 100.0, 100.0)}
    local = {rel: FileEntry(20, 500.0)}
    remote = {rel: FileEntry(30, 900.0)}
    # Even with conflict="ask", a conflict-copy file is resolved (newest wins)
    # rather than asked about, and no nested conflict copy is made.
    pair = make_pair(conflict="ask", keep_conflict_copies=True)
    actions = plan(local, remote, last, pair, now=NOW).actions
    assert actions == [
        Action(ActionKind.COPY, rel, Side.REMOTE, Side.LOCAL, "conflict copy changed on both sides, newest kept (phone)")
    ]


# --------------------------------------------------------------------------
# plan(): direction
# --------------------------------------------------------------------------


def test_direction_laptop_to_phone_drops_reverse_copy():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(10, 100.0)}  # unchanged
    remote = {"f": FileEntry(10, 200.0)}  # changed on phone
    pair = make_pair(direction="laptop-to-phone")
    actions = plan(local, remote, last, pair).actions
    assert actions == []


def test_direction_laptop_to_phone_keeps_forward_copy():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(10, 200.0)}  # changed on laptop
    remote = {"f": FileEntry(10, 100.0)}
    pair = make_pair(direction="laptop-to-phone")
    actions = plan(local, remote, last, pair).actions
    assert actions == [Action(ActionKind.COPY, "f", Side.LOCAL, Side.REMOTE, "changed on laptop")]


def test_direction_laptop_to_phone_drops_reverse_delete():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(10, 100.0)}
    remote = {}  # deleted on phone
    pair = make_pair(direction="laptop-to-phone", propagate_deletes=True)
    actions = plan(local, remote, last, pair).actions
    assert actions == []


def test_direction_laptop_to_phone_both_changed_laptop_authoritative_no_conflict_copy():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(30, 900.0)}  # phone is "newer" but is not the source
    pair = make_pair(direction="laptop-to-phone", conflict="newest-wins", keep_conflict_copies=True)
    actions = plan(local, remote, last, pair, now=NOW).actions
    assert actions == [
        Action(
            ActionKind.COPY,
            "f",
            Side.LOCAL,
            Side.REMOTE,
            "changed on both sides, laptop is authoritative",
        )
    ]


def test_direction_phone_to_laptop_drops_reverse_copy():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(10, 200.0)}  # changed on laptop, must not flow to phone
    remote = {"f": FileEntry(10, 100.0)}
    pair = make_pair(direction="phone-to-laptop")
    actions = plan(local, remote, last, pair).actions
    assert actions == []


def test_direction_phone_to_laptop_keeps_forward_copy():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(10, 100.0)}
    remote = {"f": FileEntry(10, 200.0)}  # changed on phone
    pair = make_pair(direction="phone-to-laptop")
    actions = plan(local, remote, last, pair).actions
    assert actions == [Action(ActionKind.COPY, "f", Side.REMOTE, Side.LOCAL, "changed on phone")]


def test_direction_phone_to_laptop_both_changed_phone_authoritative():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}  # laptop is "newer" but is not the source
    remote = {"f": FileEntry(30, 900.0)}
    pair = make_pair(direction="phone-to-laptop", conflict="newest-wins", keep_conflict_copies=True)
    actions = plan(local, remote, last, pair, now=NOW).actions
    assert actions == [
        Action(
            ActionKind.COPY,
            "f",
            Side.REMOTE,
            Side.LOCAL,
            "changed on both sides, phone is authoritative",
        )
    ]


def test_direction_first_run_one_side_only_reverse_dropped():
    # A file that only exists on the phone, first run, laptop-to-phone: the
    # laptop is not the source of this file, so nothing should happen.
    local = {}
    remote = {"f": FileEntry(10, 100.0)}
    pair = make_pair(direction="laptop-to-phone")
    actions = plan(local, remote, {}, pair).actions
    assert actions == []


# --------------------------------------------------------------------------
# plan(): output is sorted by rel
# --------------------------------------------------------------------------


def test_actions_sorted_by_rel():
    last = {}
    local = {"z.txt": FileEntry(1, 1.0), "a.txt": FileEntry(1, 1.0), "m.txt": FileEntry(1, 1.0)}
    remote = {}
    actions = plan(local, remote, last, make_pair()).actions
    assert [a.rel for a in actions] == ["a.txt", "m.txt", "z.txt"]


# --------------------------------------------------------------------------
# conflict.py: conflict_name / is_conflict_copy
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel, side, expected",
    [
        ("notes/a.md", Side.REMOTE, "notes/a.conflict-phone-20260905-153000.md"),
        ("notes/a.md", Side.LOCAL, "notes/a.conflict-laptop-20260905-153000.md"),
        ("a.md", Side.LOCAL, "a.conflict-laptop-20260905-153000.md"),
        ("notes/README", Side.REMOTE, "notes/README.conflict-phone-20260905-153000"),
        (".env", Side.REMOTE, ".env.conflict-phone-20260905-153000"),
        ("dir/.env", Side.LOCAL, "dir/.env.conflict-laptop-20260905-153000"),
    ],
)
def test_conflict_name(rel, side, expected):
    assert conflict_name(rel, side, NOW) == expected


@pytest.mark.parametrize(
    "rel, expected",
    [
        ("notes/a.conflict-phone-20260905-153000.md", True),
        ("a.conflict-laptop-20260905-153000", True),
        (".env.conflict-phone-20260905-153000", True),
        ("notes/a.md", False),
        ("a.conflictual-name.md", False),
        ("a.conflict-tablet-20260905-153000.md", False),  # not a recognised side
    ],
)
def test_is_conflict_copy(rel, expected):
    assert is_conflict_copy(rel) is expected


# --------------------------------------------------------------------------
# plan(): case collisions (task 24)
# --------------------------------------------------------------------------


def test_case_collision_produces_no_actions_and_a_warning():
    local = {"Notes.md": FileEntry(10, 100.0)}
    remote = {"notes.md": FileEntry(20, 200.0)}
    result = plan(local, remote, {}, make_pair())

    assert result.actions == []
    assert len(result.warnings) == 1
    assert "Notes.md" in result.warnings[0]
    assert "notes.md" in result.warnings[0]


def test_case_collision_does_not_affect_unrelated_paths():
    local = {"Notes.md": FileEntry(10, 100.0), "other.txt": FileEntry(5, 50.0)}
    remote = {"notes.md": FileEntry(20, 200.0)}
    result = plan(local, remote, {}, make_pair())

    assert [a.rel for a in result.actions] == ["other.txt"]
    assert len(result.warnings) == 1


def test_no_warnings_when_no_collision():
    local = {"a.md": FileEntry(10, 100.0)}
    remote = {}
    result = plan(local, remote, {}, make_pair())

    assert result.warnings == []
    assert len(result.actions) == 1


def test_case_collision_with_three_variants_grouped_into_one_warning():
    local = {"Notes.md": FileEntry(1, 1.0), "NOTES.md": FileEntry(2, 2.0)}
    remote = {"notes.md": FileEntry(3, 3.0)}
    result = plan(local, remote, {}, make_pair())

    assert result.actions == []
    assert len(result.warnings) == 1
    for name in ("Notes.md", "NOTES.md", "notes.md"):
        assert name in result.warnings[0]


def test_same_case_same_name_is_not_a_collision():
    local = {"notes.md": FileEntry(10, 100.0)}
    remote = {"notes.md": FileEntry(10, 100.0)}
    result = plan(local, remote, {}, make_pair())

    assert result.warnings == []


# --------------------------------------------------------------------------
# plan(): content-hash resync for same-size conflict candidates (task 29a)
# --------------------------------------------------------------------------


def test_both_changed_same_size_same_hash_is_resynced_not_conflicted():
    """Same content, different mtimes (e.g. copied by hand over MTP): no conflict."""
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(20, 900.0)}

    def hasher(rels):
        assert rels == ["f"]
        return {"f": ("samehash", "samehash")}

    result = plan(local, remote, last, make_pair(), now=NOW, hasher=hasher)
    assert result.actions == []
    assert result.resynced == ["f"]


def test_both_changed_same_size_different_hash_is_a_real_conflict():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(20, 900.0)}  # remote newer -> remote wins

    def hasher(rels):
        return {"f": ("hash-a", "hash-b")}

    result = plan(local, remote, last, make_pair(keep_conflict_copies=False), now=NOW, hasher=hasher)
    assert result.actions == [Action(ActionKind.COPY, "f", Side.REMOTE, Side.LOCAL, "conflict: newest wins (phone)")]
    assert result.resynced == []


def test_both_changed_different_size_never_calls_hasher():
    """Different size can never mean identical content, so no hashing is done for it."""
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(30, 900.0)}

    def hasher(rels):
        raise AssertionError("hasher should not be called for a size mismatch")

    result = plan(local, remote, last, make_pair(keep_conflict_copies=False), now=NOW, hasher=hasher)
    assert result.actions == [Action(ActionKind.COPY, "f", Side.REMOTE, Side.LOCAL, "conflict: newest wins (phone)")]


def test_no_hasher_falls_back_to_pre_29a_conflict_behaviour():
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(20, 900.0)}

    result = plan(local, remote, last, make_pair(keep_conflict_copies=False), now=NOW, hasher=None)
    assert result.actions == [Action(ActionKind.COPY, "f", Side.REMOTE, Side.LOCAL, "conflict: newest wins (phone)")]
    assert result.resynced == []


def test_one_way_direction_never_hashes_or_resyncs():
    """A one-way pair's source side is authoritative regardless of content - no hash check needed."""
    last = {"f": SnapshotEntry(10, 100.0, 100.0)}
    local = {"f": FileEntry(20, 500.0)}
    remote = {"f": FileEntry(20, 900.0)}

    def hasher(rels):
        raise AssertionError("hasher should not be called for a one-way pair")

    result = plan(local, remote, last, make_pair(direction="laptop-to-phone"), now=NOW, hasher=hasher)
    assert result.actions == [
        Action(ActionKind.COPY, "f", Side.LOCAL, Side.REMOTE, "changed on both sides, laptop is authoritative")
    ]
