"""Tests for androsync.conflicts: list/restore folder-mode backups, clean up old duplicates."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from androsync.config import PairConfig
from androsync.conflicts import ConflictsError, clean, find_duplicates, list_conflicts, restore
from androsync.executor import run_pair
from androsync.model import Side
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
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))
    return path


def tree(root: Path) -> dict[str, str]:
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            out[rel] = full.read_text()
    return out


def local_t() -> LocalDirTransport:
    return LocalDirTransport(Side.LOCAL)


def remote_t() -> LocalDirTransport:
    return LocalDirTransport(Side.REMOTE)


# --------------------------------------------------------------------------
# list_conflicts / restore
# --------------------------------------------------------------------------


def test_list_conflicts_empty_when_no_androsync_dir(roots):
    local_root, _remote_root = roots
    assert list_conflicts(make_pair(local_root, _remote_root)) == []


def _make_folder_mode_conflict(local_root, remote_root, when=datetime(2026, 9, 5, 15, 30, 0)):
    write(local_root, "note.md", "v1", BASE)
    write(remote_root, "note.md", "v1", BASE)
    pair = make_pair(local_root, remote_root)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.md", "laptop edit", BASE + 200)
    write(remote_root, "note.md", "phone edit", BASE + 300)  # newer -> phone wins, laptop is the loser
    run_pair(pair, local_t(), remote_t(), now=when)
    return pair


def test_list_conflicts_finds_backup_after_a_real_conflict(roots):
    local_root, remote_root = roots
    pair = _make_folder_mode_conflict(local_root, remote_root)

    versions = list_conflicts(pair)

    assert len(versions) == 1
    assert versions[0].when == "20260905-153000"
    assert versions[0].rel == "note.md"


def test_restore_copies_saved_version_back_over_the_live_file(roots):
    local_root, remote_root = roots
    pair = _make_folder_mode_conflict(local_root, remote_root)
    assert tree(local_root)["note.md"] == "phone edit"

    when = restore(pair, local_t(), "note.md")

    assert when == "20260905-153000"
    assert tree(local_root)["note.md"] == "laptop edit"
    # The remote side is untouched by restore(); a sync run would propagate it.
    assert tree(remote_root)["note.md"] == "phone edit"


def test_restore_raises_when_nothing_saved(roots):
    local_root, remote_root = roots
    pair = make_pair(local_root, remote_root)
    with pytest.raises(ConflictsError):
        restore(pair, local_t(), "nope.md")


def test_restore_with_explicit_when_picks_that_version(roots):
    local_root, remote_root = roots
    pair = _make_folder_mode_conflict(local_root, remote_root, when=datetime(2026, 9, 5, 15, 30, 0))

    # A second conflict on the same rel, saved under a later timestamp.
    write(local_root, "note.md", "laptop edit 2", BASE + 400)
    write(remote_root, "note.md", "phone edit 2", BASE + 500)
    run_pair(pair, local_t(), remote_t(), now=datetime(2026, 9, 6, 10, 0, 0))

    versions = list_conflicts(pair)
    assert {v.when for v in versions} == {"20260905-153000", "20260906-100000"}

    when = restore(pair, local_t(), "note.md", when="20260905-153000")
    assert when == "20260905-153000"
    assert tree(local_root)["note.md"] == "laptop edit"


# --------------------------------------------------------------------------
# find_duplicates / clean (decision 4: pre-task-29b "beside" duplicates)
# --------------------------------------------------------------------------


def test_find_duplicates_ignores_files_without_conflict_marker(roots):
    local_root, remote_root = roots
    write(local_root, "plain.txt", "x", BASE)
    write(remote_root, "plain.txt", "x", BASE)
    pair = make_pair(local_root, remote_root)

    assert find_duplicates(pair, local_t(), remote_t()) == []


def test_find_duplicates_finds_beside_style_copy_on_both_sides(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "original", BASE)
    write(remote_root, "a.txt", "original", BASE)
    write(local_root, "a.conflict-laptop-20260101-000000.txt", "loser", BASE)
    write(remote_root, "a.conflict-laptop-20260101-000000.txt", "loser", BASE)
    pair = make_pair(local_root, remote_root)

    dups = find_duplicates(pair, local_t(), remote_t())

    assert len(dups) == 1
    assert dups[0].rel == "a.conflict-laptop-20260101-000000.txt"
    assert dups[0].original_rel == "a.txt"
    assert dups[0].on_local is True
    assert dups[0].on_remote is True


def test_clean_dry_run_changes_nothing(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "original", BASE)
    write(remote_root, "a.txt", "original", BASE)
    write(local_root, "a.conflict-laptop-20260101-000000.txt", "original", BASE)
    write(remote_root, "a.conflict-laptop-20260101-000000.txt", "original", BASE)
    pair = make_pair(local_root, remote_root)

    result = clean(pair, local_t(), remote_t(), dry_run=True)

    assert len(result.actions) == 1
    assert result.actions[0].outcome == "would_delete"
    assert result.actions[0].identical_to_original is True
    assert "a.conflict-laptop-20260101-000000.txt" in tree(local_root)
    assert "a.conflict-laptop-20260101-000000.txt" in tree(remote_root)


def test_clean_deletes_duplicate_identical_to_original_on_both_sides(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "original", BASE)
    write(remote_root, "a.txt", "original", BASE)
    write(local_root, "a.conflict-laptop-20260101-000000.txt", "original", BASE)
    write(remote_root, "a.conflict-laptop-20260101-000000.txt", "original", BASE)
    pair = make_pair(local_root, remote_root)

    result = clean(pair, local_t(), remote_t(), dry_run=False)

    assert result.actions[0].outcome == "deleted"
    assert "a.conflict-laptop-20260101-000000.txt" not in tree(local_root)
    assert "a.conflict-laptop-20260101-000000.txt" not in tree(remote_root)
    assert tree(local_root) == {"a.txt": "original"}
    assert tree(remote_root) == {"a.txt": "original"}


def test_clean_archives_and_deletes_duplicate_that_differs_from_original(roots):
    local_root, remote_root = roots
    write(local_root, "b.txt", "current content", BASE)
    write(remote_root, "b.txt", "current content", BASE)
    write(local_root, "b.conflict-laptop-20260101-000000.txt", "old losing content", BASE)
    write(remote_root, "b.conflict-laptop-20260101-000000.txt", "old losing content", BASE)
    pair = make_pair(local_root, remote_root)
    now = datetime(2026, 9, 7, 12, 0, 0)

    result = clean(pair, local_t(), remote_t(), dry_run=False, now=now)

    assert result.actions[0].outcome == "archived_and_deleted"
    assert result.actions[0].identical_to_original is False
    assert "b.conflict-laptop-20260101-000000.txt" not in tree(local_root)
    assert "b.conflict-laptop-20260101-000000.txt" not in tree(remote_root)
    assert tree(local_root)["b.txt"] == "current content"

    backup = local_root / ".androsync" / "conflicts" / "20260907-120000" / "b.txt"
    assert backup.read_text() == "old losing content"


def test_clean_one_pass_leaves_nothing_for_a_second_pass(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "original", BASE)
    write(remote_root, "a.txt", "original", BASE)
    write(local_root, "a.conflict-laptop-20260101-000000.txt", "different", BASE)
    write(remote_root, "a.conflict-laptop-20260101-000000.txt", "different", BASE)
    pair = make_pair(local_root, remote_root)

    clean(pair, local_t(), remote_t(), dry_run=False)
    second = clean(pair, local_t(), remote_t(), dry_run=False)

    assert second.actions == []
