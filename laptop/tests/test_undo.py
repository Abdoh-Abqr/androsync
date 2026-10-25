"""Tests for androsync.undo: RunJournal writing/reading, prune (Phase I, task 30a)."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from androsync import state, undo
from androsync.config import PairConfig
from androsync.executor import run_pair
from androsync.model import FileEntry, Side
from androsync.transport.local import LocalDirTransport
from androsync.undo import undo_run

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
# RunJournal.start / meta / snapshot_before
# --------------------------------------------------------------------------


def test_start_writes_snapshot_before_and_unfinished_meta():
    from androsync.model import SnapshotEntry

    snapshot = {"a.txt": SnapshotEntry(size=1, mtime_local=1.0, mtime_remote=1.0)}
    now = datetime(2026, 9, 7, 15, 30, 0)

    journal = undo.RunJournal.start("p", snapshot, {"max_file_mb": 500}, now=now)

    assert journal.run_id.startswith("20260907-153000-")
    loaded = undo.load("p", journal.run_id)
    assert loaded["snapshot_before"] == snapshot
    assert loaded["meta"]["finished"] is False
    assert loaded["meta"]["kind"] == "sync"


def test_finish_marks_meta_finished_with_summary():
    from androsync.model import Summary

    journal = undo.RunJournal.start("p", {}, {"max_file_mb": 500})
    journal.finish(Summary(copied_to_local=3, run_id=journal.run_id))

    loaded = undo.load("p", journal.run_id)
    assert loaded["meta"]["finished"] is True
    assert loaded["meta"]["summary"]["copied_to_local"] == 3


# --------------------------------------------------------------------------
# record_write / record_delete / backup
# --------------------------------------------------------------------------


def test_record_write_create_when_nothing_there_yet(tmp_path):
    root = tmp_path / "root"
    root.mkdir()  # "new.txt" deliberately not created yet - record_write() runs *before* the write.
    transport = local_t()

    journal = undo.RunJournal.start("p", {}, {"max_file_mb": 500})
    journal.record_write(Side.LOCAL, "new.txt", transport, str(root), FileEntry(5, float(BASE)))

    entries = undo.load("p", journal.run_id)["entries"]
    assert entries == [{"op": "create", "side": "local", "rel": "new.txt", "new": {"size": 5, "mtime": float(BASE)}}]
    assert not (journal.dir / "files" / "local" / "new.txt").exists()


def test_record_write_overwrite_backs_up_old_bytes(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    write(root, "existing.txt", "old content", BASE)
    transport = local_t()

    journal = undo.RunJournal.start("p", {}, {"max_file_mb": 500})
    journal.record_write(Side.LOCAL, "existing.txt", transport, str(root), FileEntry(11, float(BASE + 100)))

    entries = undo.load("p", journal.run_id)["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["op"] == "overwrite"
    assert entry["side"] == "local"
    assert entry["old"] == {"size": 11, "mtime": float(BASE)}
    assert entry["new"] == {"size": 11, "mtime": float(BASE + 100)}
    assert entry["backup"] == "saved"

    backup_path = journal.dir / "files" / "local" / "existing.txt"
    assert backup_path.read_text() == "old content"


def test_record_delete_backs_up_old_bytes(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    write(root, "gone.txt", "bye", BASE)
    transport = local_t()

    journal = undo.RunJournal.start("p", {}, {"max_file_mb": 500})
    journal.record_delete(Side.REMOTE, "gone.txt", transport, str(root))

    entries = undo.load("p", journal.run_id)["entries"]
    assert entries == [
        {"op": "delete", "side": "remote", "rel": "gone.txt", "old": {"size": 3, "mtime": float(BASE)}, "backup": "saved"}
    ]
    assert (journal.dir / "files" / "remote" / "gone.txt").read_text() == "bye"


def test_record_delete_of_nothing_records_nothing(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    transport = local_t()

    journal = undo.RunJournal.start("p", {}, {"max_file_mb": 500})
    journal.record_delete(Side.LOCAL, "never-existed.txt", transport, str(root))

    assert undo.load("p", journal.run_id)["entries"] == []


def test_backup_skipped_above_max_file_mb(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    write(root, "big.bin", "x" * 100, BASE)
    transport = local_t()

    # max_file_mb=0 -> max_file_bytes=0, so any nonzero-size file is skipped.
    journal = undo.RunJournal.start("p", {}, {"max_file_mb": 0})
    journal.record_write(Side.LOCAL, "big.bin", transport, str(root), FileEntry(100, float(BASE + 1)))

    entries = undo.load("p", journal.run_id)["entries"]
    assert entries[0]["backup"] == "skipped"
    assert not (journal.dir / "files" / "local" / "big.bin").exists()


def test_record_conflict_saved_and_rename():
    journal = undo.RunJournal.start("p", {}, {"max_file_mb": 500})
    journal.record_conflict_saved(".androsync/conflicts/20260907-000000/a.txt")
    journal.record_conflict_rename(Side.LOCAL, "a.txt", "a.conflict-laptop-20260907-000000.txt", FileEntry(1, 1.0))

    entries = undo.load("p", journal.run_id)["entries"]
    assert entries[0] == {"op": "conflict_saved", "path": ".androsync/conflicts/20260907-000000/a.txt"}
    assert entries[1] == {
        "op": "conflict_rename",
        "side": "local",
        "rel": "a.txt",
        "target": "a.conflict-laptop-20260907-000000.txt",
        "old": {"size": 1, "mtime": 1.0},
    }


def test_record_write_many_matches_record_write_per_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    write(root, "a.txt", "aaa", BASE)  # exists -> overwrite
    # "b.txt" does not exist -> create
    transport = local_t()

    journal = undo.RunJournal.start("p", {}, {"max_file_mb": 500})
    journal.record_write_many(
        Side.LOCAL,
        transport,
        str(root),
        [("a.txt", FileEntry(3, float(BASE + 10))), ("b.txt", FileEntry(1, float(BASE + 10)))],
    )

    entries = {e["rel"]: e for e in undo.load("p", journal.run_id)["entries"]}
    assert entries["a.txt"]["op"] == "overwrite"
    assert entries["a.txt"]["backup"] == "saved"
    assert (journal.dir / "files" / "local" / "a.txt").read_text() == "aaa"
    assert entries["b.txt"]["op"] == "create"


def test_journal_survives_without_finish_being_called(tmp_path):
    """Simulates a crash mid-run: entries recorded so far stay loadable even
    though finish() never ran - meta.json's finished stays False."""
    root = tmp_path / "root"
    root.mkdir()
    write(root, "a.txt", "one", BASE)
    write(root, "b.txt", "two", BASE)
    transport = local_t()

    journal = undo.RunJournal.start("p", {}, {"max_file_mb": 500})
    journal.record_write(Side.LOCAL, "a.txt", transport, str(root), FileEntry(3, float(BASE + 1)))
    journal.record_write(Side.LOCAL, "b.txt", transport, str(root), FileEntry(3, float(BASE + 1)))
    # ... run "crashes" here, finish() is never called.

    runs = undo.list_runs("p")
    assert len(runs) == 1
    assert runs[0]["finished"] is False

    loaded = undo.load("p", journal.run_id)
    assert len(loaded["entries"]) == 2
    assert {e["rel"] for e in loaded["entries"]} == {"a.txt", "b.txt"}


# --------------------------------------------------------------------------
# list_runs / load
# --------------------------------------------------------------------------


def test_list_runs_empty_for_unknown_pair():
    assert undo.list_runs("nope") == []


def test_list_runs_newest_first():
    undo.RunJournal.start("p", {}, {"max_file_mb": 500}, now=datetime(2026, 9, 5, 10, 0, 0))
    undo.RunJournal.start("p", {}, {"max_file_mb": 500}, now=datetime(2026, 9, 7, 10, 0, 0))
    undo.RunJournal.start("p", {}, {"max_file_mb": 500}, now=datetime(2026, 9, 6, 10, 0, 0))

    runs = undo.list_runs("p")
    run_ids = [r["run_id"] for r in runs]
    assert run_ids == sorted(run_ids, reverse=True)
    assert run_ids[0].startswith("20260907")


def test_load_unknown_run_raises():
    with pytest.raises(undo.UndoError):
        undo.load("p", "20260101-000000-0000")


# --------------------------------------------------------------------------
# prune
# --------------------------------------------------------------------------


def test_prune_keeps_n_newest():
    for day in range(1, 6):  # 5 runs
        undo.RunJournal.start("p", {}, {"max_file_mb": 500}, now=datetime(2026, 9, day, 10, 0, 0))

    undo.prune("p", keep_runs=2, max_total_mb=2048)

    runs = undo.list_runs("p")
    assert len(runs) == 2
    assert runs[0]["run_id"].startswith("20260905")
    assert runs[1]["run_id"].startswith("20260904")


def test_prune_enforces_max_total_mb(tmp_path):
    # Real MB-scale backups, since max_total_mb only has whole-megabyte granularity:
    # three ~1.5 MB runs, a 2 MB budget should keep only the single newest one.
    root = tmp_path / "root"
    root.mkdir()
    transport = local_t()
    payload = "x" * 1_500_000

    for day in range(1, 4):
        write(root, "big.bin", payload, BASE)
        journal = undo.RunJournal.start("p", {}, {"max_file_mb": 500}, now=datetime(2026, 9, day, 10, 0, 0))
        journal.record_write(Side.LOCAL, "big.bin", transport, str(root), FileEntry(len(payload), float(BASE)))

    undo.prune("p", keep_runs=10, max_total_mb=2)

    runs = undo.list_runs("p")
    assert len(runs) == 1
    assert runs[0]["run_id"].startswith("20260903")  # the newest of the three survives


def test_prune_missing_pair_does_nothing():
    undo.prune("nope", keep_runs=10, max_total_mb=2048)  # no directory yet; must not raise


# --------------------------------------------------------------------------
# run_pair integration: real journals from real syncs (two LocalDirTransports)
# --------------------------------------------------------------------------


def test_run_pair_sets_run_id_and_journals_a_create(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "hello", BASE)
    pair = make_pair(local_root, remote_root)

    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    assert summary.run_id is not None
    loaded = undo.load(pair.name, summary.run_id)
    assert loaded["meta"]["finished"] is True
    assert loaded["meta"]["summary"]["copied_to_remote"] == 1
    [entry] = loaded["entries"]
    assert entry == {"op": "create", "side": "remote", "rel": "a.txt", "new": {"size": 5, "mtime": float(BASE)}}


def test_run_pair_journals_an_overwrite_with_recoverable_backup(roots):
    local_root, remote_root = roots
    write(local_root, "note.txt", "v1", BASE)
    pair = make_pair(local_root, remote_root)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.txt", "v2 from laptop", BASE + 200)
    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 300))

    loaded = undo.load(pair.name, summary.run_id)
    [entry] = loaded["entries"]
    assert entry["op"] == "overwrite"
    assert entry["side"] == "remote"
    assert entry["backup"] == "saved"

    backup = state.undo_dir(pair.name) / summary.run_id / "files" / "remote" / "note.txt"
    assert backup.read_text() == "v1"


def test_run_pair_journals_a_delete(roots):
    local_root, remote_root = roots
    write(local_root, "gone.txt", "bye", BASE)
    pair = make_pair(local_root, remote_root, propagate_deletes=True)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    (remote_root / "gone.txt").unlink()
    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 200))

    loaded = undo.load(pair.name, summary.run_id)
    [entry] = loaded["entries"]
    assert entry["op"] == "delete"
    assert entry["side"] == "local"
    assert entry["backup"] == "saved"
    backup = state.undo_dir(pair.name) / summary.run_id / "files" / "local" / "gone.txt"
    assert backup.read_text() == "bye"


def test_run_pair_journals_conflict_folder_mode_as_conflict_saved(roots):
    local_root, remote_root = roots
    write(local_root, "note.md", "v1", BASE)
    write(remote_root, "note.md", "v1", BASE)
    pair = make_pair(local_root, remote_root)  # conflict_copies defaults to "folder"
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.md", "laptop edit", BASE + 200)
    write(remote_root, "note.md", "phone edit", BASE + 300)
    when = datetime(2026, 9, 5, 15, 30, 0)
    summary = run_pair(pair, local_t(), remote_t(), now=when)

    loaded = undo.load(pair.name, summary.run_id)
    ops = {e["op"] for e in loaded["entries"]}
    assert "conflict_saved" in ops
    saved = next(e for e in loaded["entries"] if e["op"] == "conflict_saved")
    assert saved["path"] == ".androsync/conflicts/20260905-153000/note.md"
    # The winner overwriting note.md afterwards is journaled too, as an ordinary overwrite.
    overwrite = next(e for e in loaded["entries"] if e["op"] == "overwrite")
    assert overwrite["rel"] == "note.md"


def test_run_pair_journals_conflict_beside_mode_as_rename_plus_create(roots):
    local_root, remote_root = roots
    write(local_root, "note.md", "v1", BASE)
    write(remote_root, "note.md", "v1", BASE)
    pair = make_pair(local_root, remote_root, conflict_copies="beside")
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.md", "laptop edit", BASE + 200)
    write(remote_root, "note.md", "phone edit", BASE + 300)  # newer -> phone wins, laptop is the loser
    when = datetime(2026, 9, 5, 15, 30, 0)
    summary = run_pair(pair, local_t(), remote_t(), now=when)

    loaded = undo.load(pair.name, summary.run_id)
    ops_by_kind = {e["op"]: e for e in loaded["entries"] if e["op"] == "conflict_rename"}
    assert "conflict_rename" in ops_by_kind
    rename_entry = ops_by_kind["conflict_rename"]
    assert rename_entry["side"] == "local"
    assert rename_entry["rel"] == "note.md"
    assert rename_entry["target"].startswith("note.conflict-laptop-")
    # The push of that renamed copy to the phone is journaled as an ordinary create.
    creates = [e for e in loaded["entries"] if e["op"] == "create" and e["side"] == "remote"]
    assert any(e["rel"] == rename_entry["target"] for e in creates)


def test_run_pair_batched_copy_journals_every_file(roots):
    """Force the task-24 batched path (>=2 same-direction COPY actions) and confirm
    each file still gets its own journal entry via one batched backup call."""
    local_root, remote_root = roots
    write(local_root, "a.txt", "aaa", BASE)
    write(local_root, "b.txt", "bbb", BASE)
    pair = make_pair(local_root, remote_root)

    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    loaded = undo.load(pair.name, summary.run_id)
    rels = {e["rel"] for e in loaded["entries"]}
    assert rels == {"a.txt", "b.txt"}
    assert all(e["op"] == "create" for e in loaded["entries"])


def test_undo_disabled_skips_journal_entirely(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "hello", BASE)
    pair = make_pair(local_root, remote_root, undo_enabled=False)

    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    assert summary.run_id is None
    assert undo.list_runs(pair.name) == []


def test_dry_run_never_creates_a_journal(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "hello", BASE)
    pair = make_pair(local_root, remote_root)

    summary = run_pair(pair, local_t(), remote_t(), dry_run=True, now=datetime.fromtimestamp(BASE + 100))

    assert summary.run_id is None
    assert undo.list_runs(pair.name) == []


def test_backup_skipped_above_max_file_mb_through_run_pair(roots):
    local_root, remote_root = roots
    write(local_root, "note.txt", "v1", BASE)
    pair = make_pair(local_root, remote_root, undo_max_file_mb=0)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.txt", "v2, definitely more than zero bytes", BASE + 200)
    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 300))

    loaded = undo.load(pair.name, summary.run_id)
    [entry] = loaded["entries"]
    assert entry["backup"] == "skipped"
    backup = state.undo_dir(pair.name) / summary.run_id / "files" / "remote" / "note.txt"
    assert not backup.exists()


# --------------------------------------------------------------------------
# undo_run: reversing a real sync (task 30b)
# --------------------------------------------------------------------------


def test_undo_first_sync_leaves_both_trees_and_snapshot_exactly_as_before(roots):
    local_root, remote_root = roots
    write(local_root, "docs/a.md", "laptop doc", BASE)
    write(remote_root, "photos/b.jpg", "phone photo", BASE + 10)
    pair = make_pair(local_root, remote_root)

    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))
    assert tree(local_root) == {"docs/a.md": "laptop doc", "photos/b.jpg": "phone photo"}
    assert tree(remote_root) == {"docs/a.md": "laptop doc", "photos/b.jpg": "phone photo"}

    undo_summary = undo_run(pair, summary.run_id, local_t(), remote_t())

    assert undo_summary.errors == 0
    assert undo_summary.skipped == []
    assert tree(local_root) == {"docs/a.md": "laptop doc"}
    assert tree(remote_root) == {"photos/b.jpg": "phone photo"}
    assert state.load_snapshot(pair.name) == {}


def test_undo_overwrite_and_delete_restores_content_and_mtime(roots):
    local_root, remote_root = roots
    write(local_root, "note.txt", "v1", BASE)
    write(local_root, "gone.txt", "bye", BASE)
    pair = make_pair(local_root, remote_root, propagate_deletes=True)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.txt", "v2 from laptop", BASE + 200)
    (remote_root / "gone.txt").unlink()
    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 300))
    assert tree(remote_root)["note.txt"] == "v2 from laptop"
    assert "gone.txt" not in tree(local_root)

    undo_summary = undo_run(pair, summary.run_id, local_t(), remote_t())

    assert undo_summary.errors == 0
    assert tree(remote_root)["note.txt"] == "v1"
    assert os.stat(remote_root / "note.txt").st_mtime == pytest.approx(float(BASE))
    assert tree(local_root)["gone.txt"] == "bye"
    assert os.stat(local_root / "gone.txt").st_mtime == pytest.approx(float(BASE))


def test_undo_skips_a_file_modified_after_the_sync_without_force(roots):
    local_root, remote_root = roots
    write(local_root, "note.txt", "v1", BASE)
    pair = make_pair(local_root, remote_root)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.txt", "v2 from laptop", BASE + 200)
    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 300))
    assert tree(remote_root)["note.txt"] == "v2 from laptop"

    # Someone edits the phone's copy after the sync, before the undo runs.
    write(remote_root, "note.txt", "edited after sync", BASE + 400)

    undo_summary = undo_run(pair, summary.run_id, local_t(), remote_t())

    assert undo_summary.skipped == ["note.txt"]
    assert tree(remote_root)["note.txt"] == "edited after sync"  # left alone


def test_undo_with_force_overrides_the_modified_check(roots):
    local_root, remote_root = roots
    write(local_root, "note.txt", "v1", BASE)
    pair = make_pair(local_root, remote_root)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.txt", "v2 from laptop", BASE + 200)
    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 300))
    write(remote_root, "note.txt", "edited after sync", BASE + 400)

    undo_summary = undo_run(pair, summary.run_id, local_t(), remote_t(), force=True)

    assert undo_summary.skipped == []
    assert tree(remote_root)["note.txt"] == "v1"


def test_undo_dry_run_reports_without_changing_anything(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "hello", BASE)
    pair = make_pair(local_root, remote_root)
    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))
    assert "a.txt" in tree(remote_root)

    undo_summary = undo_run(pair, summary.run_id, local_t(), remote_t(), dry_run=True)

    assert undo_summary.restored_remote == 1
    assert undo_summary.run_id is None  # dry runs never journal themselves
    assert "a.txt" in tree(remote_root)  # untouched
    assert state.load_snapshot(pair.name) != {}  # untouched
    assert undo.list_runs(pair.name) == [
        r for r in undo.list_runs(pair.name) if r["kind"] == "sync"
    ]  # no "undo" journal was created


def test_undo_of_undo_is_redo(roots):
    local_root, remote_root = roots
    write(local_root, "note.txt", "v1", BASE)
    pair = make_pair(local_root, remote_root)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.txt", "v2 from laptop", BASE + 200)
    sync_summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 300))
    assert tree(remote_root)["note.txt"] == "v2 from laptop"

    undo_summary = undo_run(pair, sync_summary.run_id, local_t(), remote_t())
    assert tree(remote_root)["note.txt"] == "v1"
    assert undo_summary.run_id is not None

    redo_summary = undo_run(pair, undo_summary.run_id, local_t(), remote_t())
    assert tree(remote_root)["note.txt"] == "v2 from laptop"
    assert tree(local_root)["note.txt"] == "v2 from laptop"


def test_undo_unknown_run_raises():
    pair = make_pair(Path("/tmp/nope-local"), Path("/tmp/nope-remote"))
    with pytest.raises(undo.UndoError):
        undo_run(pair, "20260101-000000-0000", local_t(), remote_t())


def test_undo_conflict_folder_mode_removes_the_saved_backup(roots):
    local_root, remote_root = roots
    write(local_root, "note.md", "v1", BASE)
    write(remote_root, "note.md", "v1", BASE)
    pair = make_pair(local_root, remote_root)  # conflict_copies defaults to "folder"
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.md", "laptop edit", BASE + 200)
    write(remote_root, "note.md", "phone edit", BASE + 300)
    summary = run_pair(pair, local_t(), remote_t(), now=datetime(2026, 9, 5, 15, 30, 0))
    backup = local_root / ".androsync" / "conflicts" / "20260905-153000" / "note.md"
    assert backup.exists()

    undo_run(pair, summary.run_id, local_t(), remote_t())

    assert not backup.exists()
    # The phone (the conflict's winner) was never itself written to by the
    # conflict handling - only the laptop (the loser) was overwritten with
    # the phone's content, so only the laptop needs restoring.
    assert tree(local_root)["note.md"] == "laptop edit"
    assert tree(remote_root)["note.md"] == "phone edit"


def test_undo_conflict_beside_mode_removes_conflict_copy_and_restores_original(roots):
    local_root, remote_root = roots
    write(local_root, "note.md", "v1", BASE)
    write(remote_root, "note.md", "v1", BASE)
    pair = make_pair(local_root, remote_root, conflict_copies="beside")
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.md", "laptop edit", BASE + 200)
    write(remote_root, "note.md", "phone edit", BASE + 300)  # newer -> phone wins, laptop is the loser
    summary = run_pair(pair, local_t(), remote_t(), now=datetime(2026, 9, 5, 15, 30, 0))
    local_conflict_names = [n for n in tree(local_root) if "conflict-laptop-" in n]
    assert len(local_conflict_names) == 1

    undo_run(pair, summary.run_id, local_t(), remote_t())

    # Same reasoning as the folder-mode case: the phone (winner) keeps its
    # own "phone edit" content throughout - only the laptop (loser) and the
    # conflict-copy file pushed to the phone need undoing.
    assert tree(local_root) == {"note.md": "laptop edit"}
    assert tree(remote_root) == {"note.md": "phone edit"}


def test_crash_mid_run_leaves_a_usable_partial_journal_for_the_successful_file(roots):
    """One file's store() raises; the run must not abort, and the journal must still
    have a correct, loadable entry for the file that *did* succeed."""
    local_root, remote_root = roots
    write(local_root, "good.txt", "ok", BASE)
    write(local_root, "bad.txt", "boom", BASE)
    pair = make_pair(local_root, remote_root, direction="laptop-to-phone", propagate_deletes=False)

    class FlakyRemote(LocalDirTransport):
        def store(self, tmp_path, root, rel, mtime):
            if rel == "bad.txt":
                raise OSError("simulated device failure")
            super().store(tmp_path, root, rel, mtime)

    summary = run_pair(pair, local_t(), FlakyRemote(Side.REMOTE), now=datetime.fromtimestamp(BASE + 100))

    assert summary.errors >= 1
    assert summary.run_id is not None
    loaded = undo.load(pair.name, summary.run_id)
    good_entries = [e for e in loaded["entries"] if e["rel"] == "good.txt"]
    assert good_entries and good_entries[0]["op"] == "create"


def test_a_stopped_undo_does_not_reset_the_pairs_history(roots):
    """Stopping an undo leaves it partly done, so the pair's snapshot must not be rolled back to "before the run"."""
    local_root, remote_root = roots
    write(local_root, "a.txt", "a", BASE)
    write(local_root, "b.txt", "b", BASE)
    pair = make_pair(local_root, remote_root)
    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))
    snapshot_after_run = state.load_snapshot(pair.name)
    assert set(snapshot_after_run) == {"a.txt", "b.txt"}

    lines: list[str] = []
    undo_summary = undo_run(pair, summary.run_id, local_t(), remote_t(), log=lines.append, cancel=lambda: True)

    assert "cancelled" in lines
    assert undo_summary.restored_remote == 0
    assert state.load_snapshot(pair.name) == snapshot_after_run  # not rolled back to the empty "before" snapshot
