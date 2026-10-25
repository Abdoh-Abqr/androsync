"""End-to-end sync scenarios through ``run_pair`` with two local directories.

Each test drives ``run_pair`` the way the real CLI would: build a
``PairConfig``, two ``LocalDirTransport`` instances standing in for the
laptop and the phone, and inspect the resulting trees on disk plus the
returned ``Summary``. Mtimes are always explicit and spaced by at least
10 seconds so the default 2s tolerance never blurs "changed" into
"unchanged" (or vice versa) by accident.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from androsync.config import PairConfig
from androsync.executor import run_pair
from androsync.model import Side, Summary
from androsync.transport.local import LocalDirTransport

BASE = 1_700_000_000


@pytest.fixture(autouse=True)
def _xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))


@pytest.fixture()
def roots(tmp_path):
    local_root = tmp_path / "local"
    remote_root = tmp_path / "remote"
    local_root.mkdir()
    remote_root.mkdir()
    return local_root, remote_root


def make_pair(local: Path, remote: Path, **overrides) -> PairConfig:
    fields = dict(
        name="p",
        local=local,
        remote=str(remote),
        direction="two-way",
        conflict="newest-wins",
        keep_conflict_copies=True,
        propagate_deletes=True,
        mtime_tolerance_s=2,
        ignore=[],
    )
    fields.update(overrides)
    return PairConfig(**fields)


def write(root: Path, rel: str, text: str, mtime: float) -> Path:
    """Write ``text`` to ``root/rel`` and set its mtime to ``mtime``."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))
    return path


def remove(root: Path, rel: str) -> None:
    (root / rel).unlink()


def tree(root: Path) -> dict[str, str]:
    """Return ``{relpath: content}`` for every regular file under ``root``."""
    out: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            out[rel] = full.read_text()
    return out


def sync(pair: PairConfig, when: float) -> Summary:
    return run_pair(
        pair,
        LocalDirTransport(Side.LOCAL),
        LocalDirTransport(Side.REMOTE),
        now=datetime.fromtimestamp(when),
    )


# --------------------------------------------------------------------------
# (a) first sync merges disjoint trees
# --------------------------------------------------------------------------


def test_first_sync_merges_disjoint_trees(roots):
    local_root, remote_root = roots
    write(local_root, "docs/a.md", "laptop doc", BASE)
    write(remote_root, "photos/b.jpg", "phone photo", BASE + 10)
    pair = make_pair(local_root, remote_root)

    summary = sync(pair, BASE + 100)

    merged = {"docs/a.md": "laptop doc", "photos/b.jpg": "phone photo"}
    assert tree(local_root) == merged
    assert tree(remote_root) == merged
    assert summary.copied_to_remote == 1
    assert summary.copied_to_local == 1


# --------------------------------------------------------------------------
# (b) edits propagate in both directions
# --------------------------------------------------------------------------


def test_edit_on_laptop_propagates_to_phone(roots):
    local_root, remote_root = roots
    write(local_root, "note.txt", "v1", BASE)
    pair = make_pair(local_root, remote_root)
    sync(pair, BASE + 100)

    write(local_root, "note.txt", "v2 from laptop", BASE + 200)
    summary = sync(pair, BASE + 300)

    assert summary.copied_to_remote == 1
    assert tree(remote_root)["note.txt"] == "v2 from laptop"
    assert tree(local_root)["note.txt"] == "v2 from laptop"


def test_edit_on_phone_propagates_to_laptop(roots):
    local_root, remote_root = roots
    write(local_root, "note.txt", "v1", BASE)
    pair = make_pair(local_root, remote_root)
    sync(pair, BASE + 100)

    write(remote_root, "note.txt", "v2 from phone", BASE + 200)
    summary = sync(pair, BASE + 300)

    assert summary.copied_to_local == 1
    assert tree(local_root)["note.txt"] == "v2 from phone"
    assert tree(remote_root)["note.txt"] == "v2 from phone"


# --------------------------------------------------------------------------
# (c) deletes: propagate_deletes True vs False
# --------------------------------------------------------------------------


def test_delete_on_phone_propagates_to_laptop_when_enabled(roots):
    local_root, remote_root = roots
    write(local_root, "gone.txt", "bye", BASE)
    pair = make_pair(local_root, remote_root, propagate_deletes=True)
    sync(pair, BASE + 100)
    assert "gone.txt" in tree(remote_root)

    remove(remote_root, "gone.txt")
    summary = sync(pair, BASE + 200)

    assert summary.deleted_local == 1
    assert "gone.txt" not in tree(local_root)
    assert "gone.txt" not in tree(remote_root)


def test_delete_on_phone_not_propagated_when_disabled(roots):
    local_root, remote_root = roots
    write(local_root, "stays.txt", "bye", BASE)
    pair = make_pair(local_root, remote_root, propagate_deletes=False)
    sync(pair, BASE + 100)
    assert "stays.txt" in tree(remote_root)

    remove(remote_root, "stays.txt")
    summary = sync(pair, BASE + 200)

    assert summary == Summary(run_id=summary.run_id)
    # The file stays on the laptop...
    assert tree(local_root)["stays.txt"] == "bye"
    # ...and is not re-copied back to the phone either.
    assert "stays.txt" not in tree(remote_root)

    # A further run changes nothing further: the deletion is not re-applied
    # and the surviving laptop copy is not pushed back.
    summary2 = sync(pair, BASE + 300)
    assert summary2 == Summary(run_id=summary2.run_id)
    assert "stays.txt" not in tree(remote_root)


# --------------------------------------------------------------------------
# (d) rename on one side = delete + create
# --------------------------------------------------------------------------


def test_rename_on_laptop_is_delete_plus_create(roots):
    local_root, remote_root = roots
    write(local_root, "old_name.txt", "content", BASE)
    pair = make_pair(local_root, remote_root)
    sync(pair, BASE + 100)
    assert "old_name.txt" in tree(remote_root)

    remove(local_root, "old_name.txt")
    write(local_root, "new_name.txt", "content", BASE + 200)
    summary = sync(pair, BASE + 300)

    assert summary.deleted_remote == 1
    assert summary.copied_to_remote == 1
    assert tree(local_root) == {"new_name.txt": "content"}
    assert tree(remote_root) == {"new_name.txt": "content"}


# --------------------------------------------------------------------------
# (e) conflict: newest wins, loser kept as a conflict copy on both sides
# --------------------------------------------------------------------------


def test_conflict_newest_wins_keeps_loser_as_conflict_copy_on_both_sides(roots):
    local_root, remote_root = roots
    write(local_root, "shared.md", "original", BASE)
    pair = make_pair(local_root, remote_root, conflict_copies="beside")
    sync(pair, BASE + 100)

    write(local_root, "shared.md", "laptop version", BASE + 200)
    write(remote_root, "shared.md", "phone version (newer)", BASE + 300)
    summary = sync(pair, BASE + 400)

    assert summary.conflicts == 1
    local_tree = tree(local_root)
    remote_tree = tree(remote_root)

    # Newer (phone) content wins on both sides.
    assert local_tree["shared.md"] == "phone version (newer)"
    assert remote_tree["shared.md"] == "phone version (newer)"

    # The laptop's losing version survives as a conflict copy on both sides.
    conflict_names = [n for n in local_tree if "conflict-laptop-" in n]
    assert len(conflict_names) == 1
    [conflict_name] = conflict_names
    assert local_tree[conflict_name] == "laptop version"
    assert remote_tree[conflict_name] == "laptop version"


# --------------------------------------------------------------------------
# (f) ignore patterns are never copied
# --------------------------------------------------------------------------


def test_ignored_paths_are_never_copied(roots):
    local_root, remote_root = roots
    write(local_root, ".obsidian/workspace.json", "workspace state", BASE)
    write(local_root, ".trash/deleted.md", "should stay gone", BASE)
    write(local_root, "notes/keep.md", "keep me", BASE)
    pair = make_pair(
        local_root,
        remote_root,
        ignore=[".obsidian/workspace.json", ".trash/**"],
    )

    sync(pair, BASE + 100)
    sync(pair, BASE + 200)
    sync(pair, BASE + 300)

    remote_tree = tree(remote_root)
    assert remote_tree == {"notes/keep.md": "keep me"}
    assert ".obsidian/workspace.json" not in remote_tree
    assert ".trash/deleted.md" not in remote_tree


# --------------------------------------------------------------------------
# (g) one-way phone-to-laptop
# --------------------------------------------------------------------------


def test_one_way_phone_to_laptop(roots):
    local_root, remote_root = roots
    # phone-to-laptop is a pull: seed both files on the phone so the first
    # run has something to pull down.
    write(remote_root, "onlylocal.txt", "same", BASE)
    write(remote_root, "onlyremote.txt", "same", BASE)
    pair = make_pair(local_root, remote_root, direction="phone-to-laptop")
    sync(pair, BASE + 100)
    assert tree(local_root) == {"onlylocal.txt": "same", "onlyremote.txt": "same"}

    # A laptop-only edit must not be pushed to the phone.
    write(local_root, "onlylocal.txt", "laptop edit", BASE + 200)
    # A phone-only edit must be pulled down to the laptop.
    write(remote_root, "onlyremote.txt", "phone edit", BASE + 200)
    summary = sync(pair, BASE + 300)

    assert summary.copied_to_local == 1
    assert summary.copied_to_remote == 0
    assert tree(remote_root)["onlylocal.txt"] == "same"
    assert tree(local_root)["onlyremote.txt"] == "phone edit"
    assert tree(local_root)["onlylocal.txt"] == "laptop edit"


# --------------------------------------------------------------------------
# (h) three consecutive runs are stable
# --------------------------------------------------------------------------


def test_three_consecutive_runs_are_stable(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "a", BASE)
    write(remote_root, "b.txt", "b", BASE + 10)
    pair = make_pair(local_root, remote_root)

    first = sync(pair, BASE + 100)
    second = sync(pair, BASE + 200)
    third = sync(pair, BASE + 300)

    assert first != Summary()
    assert second == Summary(run_id=second.run_id)
    assert third == Summary(run_id=third.run_id)
    merged = {"a.txt": "a", "b.txt": "b"}
    assert tree(local_root) == merged
    assert tree(remote_root) == merged
