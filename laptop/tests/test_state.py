"""Tests for androsync.state snapshot/meta persistence."""

from pathlib import Path

import pytest

from androsync import state
from androsync.model import SnapshotEntry


@pytest.fixture(autouse=True)
def _xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path


def test_missing_snapshot_returns_empty_dict():
    assert state.load_snapshot("nope") == {}


def test_snapshot_round_trip():
    snap = {
        "a.txt": SnapshotEntry(size=10, mtime_local=123.5, mtime_remote=124.0, sha256="deadbeef"),
        "dir/b.txt": SnapshotEntry(size=0, mtime_local=1.0, mtime_remote=1.0),
    }
    state.save_snapshot("pair1", snap)
    loaded = state.load_snapshot("pair1")
    assert loaded == snap


def test_snapshot_tmp_file_not_left_behind(_xdg_data_home):
    state.save_snapshot("pair2", {"x": SnapshotEntry(size=1, mtime_local=1.0, mtime_remote=1.0)})
    from androsync.config import state_dir

    files = list(state_dir().glob("*"))
    names = [f.name for f in files]
    assert "pair2.json" in names
    assert not any(name.endswith(".tmp") for name in names)


def test_snapshot_migrates_old_shared_mtime_shape(_xdg_data_home):
    """A snapshot written before task 29a has one ``mtime`` shared by both sides."""
    import json

    from androsync.config import state_dir

    path = state_dir() / "legacy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"a.txt": {"size": 10, "mtime": 100.0}}))

    loaded = state.load_snapshot("legacy")
    assert loaded == {"a.txt": SnapshotEntry(size=10, mtime_local=100.0, mtime_remote=100.0)}


def test_missing_meta_returns_empty_dict():
    assert state.load_pair_meta("nope") == {}


def test_meta_round_trip():
    meta = {"last_run": "2026-09-06T10:00:00", "last_result": "ok"}
    state.save_pair_meta("pair1", meta)
    assert state.load_pair_meta("pair1") == meta


def test_meta_tmp_file_not_left_behind():
    state.save_pair_meta("pair3", {"a": 1})
    from androsync.config import state_dir

    files = list(state_dir().glob("*"))
    names = [f.name for f in files]
    assert "pair3.meta.json" in names
    assert not any(name.endswith(".tmp") for name in names)


def test_missing_pending_returns_empty_list():
    assert state.load_pending("nope") == []


def test_pending_round_trip():
    pending = [
        {"rel": "a.md", "local": {"size": 1, "mtime": 1.0}, "remote": {"size": 2, "mtime": 2.0}, "reason": "x"},
    ]
    state.save_pending("pair1", pending)
    assert state.load_pending("pair1") == pending


def test_pending_overwrite_replaces_entirely():
    state.save_pending("pair1", [{"rel": "a.md", "local": None, "remote": None, "reason": "x"}])
    state.save_pending("pair1", [])
    assert state.load_pending("pair1") == []


# ---------------------------------------------------------------------------
# history reset when a pair's folders change (task 36d, issue #9)
# ---------------------------------------------------------------------------


def test_reset_moves_snapshot_pending_and_undo_journals_aside_and_keeps_the_last_result(tmp_path, monkeypatch):
    from datetime import datetime

    from androsync import state
    from androsync.config import state_dir
    from androsync.model import SnapshotEntry

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    state.save_snapshot("vault", {"a.md": SnapshotEntry(size=1, mtime_local=1.0, mtime_remote=1.0)})
    state.save_pending("vault", [{"rel": "a.md"}])
    state.save_pair_meta("vault", {"last_run": "2026-01-01T00:00:00", "last_result": "ok"})
    (state.undo_dir("vault") / "run1").mkdir(parents=True)
    (state.undo_dir("vault") / "run1" / "journal.jsonl").write_text("{}")

    target = state.reset_pair_state("vault", now=datetime(2026, 10, 18, 12, 0, 0))

    assert target == state_dir() / "vault.reset-20261018-120000"
    assert state.load_snapshot("vault") == {}  # next run is a first run
    assert state.load_pending("vault") == []
    assert not state.undo_dir("vault").exists()
    assert (target / "vault.json").exists() and (target / "undo" / "run1" / "journal.jsonl").exists()
    assert state.load_pair_meta("vault")["last_result"] == "ok"


def test_reset_of_a_pair_without_history_does_nothing(tmp_path, monkeypatch):
    from androsync import state
    from androsync.config import state_dir

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert state.reset_pair_state("never-synced") is None
    assert not list(state_dir().glob("*.reset-*")) if state_dir().exists() else True


def test_old_reset_folders_are_deleted_after_30_days_and_new_ones_kept(tmp_path, monkeypatch):
    import os
    import time

    from androsync import state
    from androsync.config import state_dir

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    old = state_dir() / "a.reset-20250101-000000"
    recent = state_dir() / "a.reset-20261001-000000"
    for folder in (old, recent):
        folder.mkdir(parents=True)
    forty_days_ago = time.time() - 40 * 86_400
    os.utime(old, (forty_days_ago, forty_days_ago))

    state.prune_resets()

    assert not old.exists() and recent.exists()
