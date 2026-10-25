"""Tests for androsync.executor.run_pair against two LocalDirTransport roots."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from androsync import state
from androsync.config import PairConfig
from androsync.executor import ExecutorError, reconcile_snapshot, resolve_pending, run_pair
from androsync.model import FileEntry, Side, SnapshotEntry, Summary
from androsync.transport.local import LocalDirTransport

BASE = 1_700_000_000


@pytest.fixture(autouse=True)
def _xdg_data_home(tmp_path, monkeypatch):
    # Isolate the JSON snapshot/meta store per test, same pattern as test_state.py.
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
# Preconditions
# --------------------------------------------------------------------------


def test_missing_local_root_raises(tmp_path):
    pair = make_pair(tmp_path / "missing-local", tmp_path / "remote")
    with pytest.raises(ExecutorError):
        run_pair(pair, local_t(), remote_t())


def test_missing_remote_root_created_for_two_way(tmp_path, roots):
    local_root, remote_root = roots
    remote_root.rmdir()
    pair = make_pair(local_root, remote_root)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE))
    assert remote_root.is_dir()


def test_missing_remote_root_raises_for_phone_to_laptop(tmp_path, roots):
    local_root, remote_root = roots
    remote_root.rmdir()
    pair = make_pair(local_root, remote_root, direction="phone-to-laptop")
    with pytest.raises(ExecutorError):
        run_pair(pair, local_t(), remote_t())


def test_missing_remote_root_not_created_under_dry_run(tmp_path, roots):
    local_root, remote_root = roots
    remote_root.rmdir()
    pair = make_pair(local_root, remote_root)
    run_pair(pair, local_t(), remote_t(), dry_run=True)
    assert not remote_root.exists()


# --------------------------------------------------------------------------
# First run / steady state
# --------------------------------------------------------------------------


def test_first_run_merges_disjoint_trees(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "from laptop", BASE)
    write(remote_root, "b.txt", "from phone", BASE + 10)
    pair = make_pair(local_root, remote_root)

    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    assert tree(local_root) == {"a.txt": "from laptop", "b.txt": "from phone"}
    assert tree(remote_root) == {"a.txt": "from laptop", "b.txt": "from phone"}
    assert summary.copied_to_remote == 1
    assert summary.copied_to_local == 1
    assert summary.errors == 0


def test_second_run_is_a_no_op(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "from laptop", BASE)
    write(remote_root, "b.txt", "from phone", BASE + 10)
    pair = make_pair(local_root, remote_root)
    now = datetime.fromtimestamp(BASE + 100)

    run_pair(pair, local_t(), remote_t(), now=now)
    summary = run_pair(pair, local_t(), remote_t(), now=now)

    assert summary == Summary(run_id=summary.run_id)


def test_identical_content_different_mtimes_is_not_a_conflict(roots):
    """Regression for the real ``memes`` pair bug (Phase H): files copied by hand
    (e.g. over MTP, which does not preserve timestamps) land on both sides with
    identical content but different mtimes. The first run must not treat this as
    a conflict (no copy, no conflict-copy file, no duplicate), and the second run
    must be a complete no-op once each side's own mtime is in the snapshot.
    """
    local_root, remote_root = roots
    write(local_root, "photo.jpg", "same bytes", BASE)
    write(remote_root, "photo.jpg", "same bytes", BASE + 5000)  # same content, very different mtime
    pair = make_pair(local_root, remote_root)
    now = datetime.fromtimestamp(BASE + 100000)

    summary = run_pair(pair, local_t(), remote_t(), now=now)

    assert summary.copied_to_local == 0
    assert summary.copied_to_remote == 0
    assert summary.conflicts == 0
    assert summary.errors == 0
    assert tree(local_root) == {"photo.jpg": "same bytes"}
    assert tree(remote_root) == {"photo.jpg": "same bytes"}
    assert not any(name != "photo.jpg" for name in tree(local_root))

    second = run_pair(pair, local_t(), remote_t(), now=now)
    assert second == Summary(run_id=second.run_id)


def test_dry_run_changes_nothing_and_saves_no_snapshot(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "from laptop", BASE)
    pair = make_pair(local_root, remote_root)

    summary = run_pair(pair, local_t(), remote_t(), dry_run=True, now=datetime.fromtimestamp(BASE + 100))

    assert tree(remote_root) == {}
    assert summary.copied_to_remote == 1
    assert state.load_snapshot(pair.name) == {}
    assert state.load_pair_meta(pair.name) == {}


# --------------------------------------------------------------------------
# Edits and conflicts
# --------------------------------------------------------------------------


def test_edit_both_sides_beside_mode_produces_winner_and_conflict_copy_on_both(roots):
    local_root, remote_root = roots
    write(local_root, "note.md", "v1", BASE)
    write(remote_root, "note.md", "v1", BASE)
    pair = make_pair(local_root, remote_root, conflict_copies="beside")
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.md", "laptop edit", BASE + 200)
    write(remote_root, "note.md", "phone edit", BASE + 300)  # newer -> phone wins
    when = datetime(2026, 9, 5, 15, 30, 0)
    summary = run_pair(pair, local_t(), remote_t(), now=when)

    assert summary.conflicts == 1
    assert summary.errors == 0
    local_tree = tree(local_root)
    remote_tree = tree(remote_root)
    assert local_tree["note.md"] == "phone edit"
    assert remote_tree["note.md"] == "phone edit"

    conflict_names = [n for n in local_tree if "conflict-laptop-" in n]
    assert len(conflict_names) == 1
    conflict_name = conflict_names[0]
    assert local_tree[conflict_name] == "laptop edit"
    assert remote_tree[conflict_name] == "laptop edit"


def test_edit_both_sides_default_folder_mode_saves_backup_no_conflict_file(roots):
    """The default mode (task 29b): no `*.conflict-*` file anywhere, backup saved locally."""
    local_root, remote_root = roots
    write(local_root, "note.md", "v1", BASE)
    write(remote_root, "note.md", "v1", BASE)
    pair = make_pair(local_root, remote_root)  # conflict_copies defaults to "folder"
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.md", "laptop edit", BASE + 200)
    write(remote_root, "note.md", "phone edit", BASE + 300)  # newer -> phone wins
    when = datetime(2026, 9, 5, 15, 30, 0)
    summary = run_pair(pair, local_t(), remote_t(), now=when)

    assert summary.conflicts == 1
    assert summary.errors == 0
    local_tree = tree(local_root)
    remote_tree = tree(remote_root)
    assert local_tree["note.md"] == "phone edit"
    assert remote_tree["note.md"] == "phone edit"
    assert not any("conflict-" in n for n in local_tree)
    assert not any("conflict-" in n for n in remote_tree)

    backup = local_root / ".androsync" / "conflicts" / "20260905-153000" / "note.md"
    assert backup.read_text() == "laptop edit"


# --------------------------------------------------------------------------
# Case collisions (task 24)
# --------------------------------------------------------------------------


def test_case_collision_is_logged_and_skipped(roots):
    local_root, remote_root = roots
    write(local_root, "Notes.md", "from laptop", BASE)
    write(remote_root, "notes.md", "from phone", BASE)
    pair = make_pair(local_root, remote_root)

    log_lines: list[str] = []
    summary = run_pair(pair, local_t(), remote_t(), log=log_lines.append, now=datetime.fromtimestamp(BASE + 100))

    assert summary == Summary(run_id=summary.run_id)  # no action taken for either colliding path
    assert any("case collision" in line and "Notes.md" in line and "notes.md" in line for line in log_lines)
    assert tree(local_root) == {"Notes.md": "from laptop"}
    assert tree(remote_root) == {"notes.md": "from phone"}


# --------------------------------------------------------------------------
# Errors and cancellation
# --------------------------------------------------------------------------


def test_action_error_is_counted_and_other_actions_still_run(roots):
    local_root, remote_root = roots
    write(local_root, "good.txt", "ok", BASE)
    write(local_root, "bad.txt", "conflict target", BASE)
    # Make the destination path for "bad.txt" a directory, so storing a file
    # there fails with an "is a directory" OSError.
    (remote_root / "bad.txt").mkdir()

    pair = make_pair(local_root, remote_root)
    log_lines: list[str] = []
    summary = run_pair(
        pair, local_t(), remote_t(), log=log_lines.append, now=datetime.fromtimestamp(BASE + 100)
    )

    assert summary.errors == 1
    assert any(line.startswith("ERROR bad.txt:") for line in log_lines)
    assert tree(remote_root).get("good.txt") == "ok"
    # The failed path must not be recorded as synced.
    assert "bad.txt" not in state.load_snapshot(pair.name)


def test_cancel_before_any_batch_flushes_copies_nothing(roots):
    """``a.txt`` and ``b.txt`` are both new-on-local -> COPY-to-remote, so
    (task 24) they land in the same accumulating batch and are not actually
    transferred until it flushes. Cancelling while still accumulating (the
    check runs once per action, before it joins the batch) discards that
    whole pending batch rather than transferring what was decided so far -
    same principle as before batching (a cancel takes effect *between*
    units of work, never mid-transfer), just at batch granularity now.
    """
    local_root, remote_root = roots
    write(local_root, "a.txt", "a", BASE)
    write(local_root, "b.txt", "b", BASE)
    pair = make_pair(local_root, remote_root)

    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    summary = run_pair(pair, local_t(), remote_t(), cancel=cancel, now=datetime.fromtimestamp(BASE + 100))

    assert summary == Summary(run_id=summary.run_id)
    assert tree(remote_root) == {}
    # No final reconciliation/meta write happens on a cancelled run.
    assert state.load_pair_meta(pair.name) == {}


def test_cancel_after_a_batch_flushed_keeps_that_batchs_work(roots, monkeypatch):
    """With the batch cap forced down to 1 file, each of ``a.txt``/``b.txt``/
    ``c.txt`` forms its own batch. A full batch (size 1) only actually
    flushes once *another* action needs to start a new one (or the run
    ends) - so ``a.txt``'s batch flushes as a side effect of reaching
    ``b.txt``, and cancelling right after that (before ``c.txt`` is
    reached, so before ``b.txt``'s batch ever flushes) must keep ``a.txt``
    and discard ``b.txt``.
    """
    local_root, remote_root = roots
    write(local_root, "a.txt", "a", BASE)
    write(local_root, "b.txt", "b", BASE)
    write(local_root, "c.txt", "c", BASE)
    pair = make_pair(local_root, remote_root, transfer_batch_files=1)

    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 2  # false for a.txt and b.txt's own checks, true from c.txt's onward

    summary = run_pair(pair, local_t(), remote_t(), cancel=cancel, now=datetime.fromtimestamp(BASE + 100))

    assert summary.copied_to_remote == 1
    assert tree(remote_root) == {"a.txt": "a"}


# --------------------------------------------------------------------------
# reconcile_snapshot
# --------------------------------------------------------------------------


def test_reconcile_snapshot_adds_equal_pairs_and_drops_both_absent():
    snapshot = {"stale": SnapshotEntry(1, 1.0, 1.0)}
    scan_local = {"a": FileEntry(5, 100.0)}
    scan_remote = {"a": FileEntry(5, 101.0)}
    result = reconcile_snapshot(snapshot, scan_local, scan_remote, tol=2)
    assert result == {"a": SnapshotEntry(5, 100.0, 101.0)}


# --------------------------------------------------------------------------
# Pending `conflict: ask` entries (task 23)
# --------------------------------------------------------------------------


def test_ask_conflict_is_persisted_to_pending(roots):
    local_root, remote_root = roots
    write(local_root, "note.md", "v1", BASE)
    write(remote_root, "note.md", "v1", BASE)
    pair = make_pair(local_root, remote_root, conflict="ask")
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.md", "laptop edit", BASE + 200)
    write(remote_root, "note.md", "phone edit", BASE + 300)
    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 400))

    assert summary.asks == 1
    pending = state.load_pending(pair.name)
    assert len(pending) == 1
    assert pending[0]["rel"] == "note.md"
    assert pending[0]["local"] == {"size": len("laptop edit"), "mtime": BASE + 200}
    assert pending[0]["remote"] == {"size": len("phone edit"), "mtime": BASE + 300}
    # Neither side was touched - an ASK action does not copy anything.
    assert tree(local_root)["note.md"] == "laptop edit"
    assert tree(remote_root)["note.md"] == "phone edit"


def test_pending_persisted_even_on_dry_run(roots):
    local_root, remote_root = roots
    write(local_root, "note.md", "v1", BASE)
    write(remote_root, "note.md", "v1", BASE)
    pair = make_pair(local_root, remote_root, conflict="ask")
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.md", "laptop edit", BASE + 200)
    write(remote_root, "note.md", "phone edit", BASE + 300)
    run_pair(pair, local_t(), remote_t(), dry_run=True, now=datetime.fromtimestamp(BASE + 400))

    assert len(state.load_pending(pair.name)) == 1


def test_pending_clears_once_no_longer_conflicting(roots):
    local_root, remote_root = roots
    write(local_root, "note.md", "v1", BASE)
    write(remote_root, "note.md", "v1", BASE)
    pair = make_pair(local_root, remote_root, conflict="ask")
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.md", "laptop edit", BASE + 200)
    write(remote_root, "note.md", "phone edit", BASE + 300)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 400))
    assert len(state.load_pending(pair.name)) == 1

    # The user (or `resolve`) made both sides equal outside of a run.
    write(local_root, "note.md", "agreed", BASE + 500)
    write(remote_root, "note.md", "agreed", BASE + 500)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 600))

    assert state.load_pending(pair.name) == []


# --------------------------------------------------------------------------
# resolve_pending (task 23)
# --------------------------------------------------------------------------


def _make_ask_conflict(local_root, remote_root, **overrides):
    write(local_root, "note.md", "v1", BASE)
    write(remote_root, "note.md", "v1", BASE)
    pair = make_pair(local_root, remote_root, conflict="ask", **overrides)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    write(local_root, "note.md", "laptop edit", BASE + 200)
    write(remote_root, "note.md", "phone edit", BASE + 300)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 400))
    return pair


def test_resolve_local_overwrites_remote_no_conflict_copy(roots):
    local_root, remote_root = roots
    pair = _make_ask_conflict(local_root, remote_root)

    resolve_pending(pair, local_t(), remote_t(), "note.md", "local", now=datetime.fromtimestamp(BASE + 500))

    assert tree(local_root) == {"note.md": "laptop edit"}
    assert tree(remote_root) == {"note.md": "laptop edit"}
    assert state.load_pending(pair.name) == []


def test_resolve_remote_overwrites_local_no_conflict_copy(roots):
    local_root, remote_root = roots
    pair = _make_ask_conflict(local_root, remote_root)

    resolve_pending(pair, local_t(), remote_t(), "note.md", "remote", now=datetime.fromtimestamp(BASE + 500))

    assert tree(local_root) == {"note.md": "phone edit"}
    assert tree(remote_root) == {"note.md": "phone edit"}
    assert state.load_pending(pair.name) == []


def test_resolve_both_beside_mode_keeps_newest_as_winner_and_loser_as_conflict_copy(roots):
    local_root, remote_root = roots
    # remote (BASE+300) is newer than local (BASE+200)
    pair = _make_ask_conflict(local_root, remote_root, conflict_copies="beside")

    resolve_pending(pair, local_t(), remote_t(), "note.md", "both", now=datetime(2026, 9, 5, 15, 30, 0))

    local_tree = tree(local_root)
    remote_tree = tree(remote_root)
    assert local_tree["note.md"] == "phone edit"
    assert remote_tree["note.md"] == "phone edit"
    conflict_names = [n for n in local_tree if "conflict-laptop-" in n]
    assert len(conflict_names) == 1
    assert local_tree[conflict_names[0]] == "laptop edit"
    assert remote_tree[conflict_names[0]] == "laptop edit"
    assert state.load_pending(pair.name) == []


def test_resolve_both_default_folder_mode_saves_backup_no_conflict_file(roots):
    local_root, remote_root = roots
    pair = _make_ask_conflict(local_root, remote_root)  # conflict_copies defaults to "folder"

    resolve_pending(pair, local_t(), remote_t(), "note.md", "both", now=datetime(2026, 9, 5, 15, 30, 0))

    local_tree = tree(local_root)
    remote_tree = tree(remote_root)
    assert local_tree["note.md"] == "phone edit"
    assert remote_tree["note.md"] == "phone edit"
    assert not any("conflict-" in n for n in local_tree)
    assert not any("conflict-" in n for n in remote_tree)

    backup = local_root / ".androsync" / "conflicts" / "20260905-153000" / "note.md"
    assert backup.read_text() == "laptop edit"
    assert state.load_pending(pair.name) == []


def test_resolve_clears_only_the_resolved_entry(roots):
    local_root, remote_root = roots
    pair = _make_ask_conflict(local_root, remote_root)
    write(local_root, "other.md", "laptop other", BASE + 200)
    write(remote_root, "other.md", "phone other", BASE + 300)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 400))
    assert len(state.load_pending(pair.name)) == 2

    resolve_pending(pair, local_t(), remote_t(), "note.md", "local")

    remaining = state.load_pending(pair.name)
    assert [e["rel"] for e in remaining] == ["other.md"]


def test_resolve_raises_when_file_missing_on_both_sides(roots):
    local_root, remote_root = roots
    pair = _make_ask_conflict(local_root, remote_root)
    (local_root / "note.md").unlink()
    (remote_root / "note.md").unlink()

    with pytest.raises(ExecutorError):
        resolve_pending(pair, local_t(), remote_t(), "note.md", "local")


def test_resolve_rejects_unknown_choice(roots):
    local_root, remote_root = roots
    pair = _make_ask_conflict(local_root, remote_root)

    with pytest.raises(ValueError):
        resolve_pending(pair, local_t(), remote_t(), "note.md", "sideways")


# --------------------------------------------------------------------------
# versioning: git (task 26)
# --------------------------------------------------------------------------

def _git_log(local: Path) -> list[str]:
    result = subprocess.run(["git", "log", "--oneline"], cwd=local, capture_output=True, text=True, check=True)
    return result.stdout.strip().splitlines()


@pytest.fixture(autouse=True)
def _git_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "androsync-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "androsync-test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "androsync-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "androsync-test@example.com")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_two_runs_with_changes_make_two_commits(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "v1", BASE)
    pair = make_pair(local_root, remote_root, versioning="git")

    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))
    write(local_root, "b.txt", "v1", BASE + 200)
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 300))

    assert len(_git_log(local_root)) == 2


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_no_change_run_makes_no_commit(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "v1", BASE)
    pair = make_pair(local_root, remote_root, versioning="git")

    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))
    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 200))  # nothing changed

    assert len(_git_log(local_root)) == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_dry_run_makes_no_commit(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "v1", BASE)
    pair = make_pair(local_root, remote_root, versioning="git")

    run_pair(pair, local_t(), remote_t(), dry_run=True, now=datetime.fromtimestamp(BASE + 100))

    assert not (local_root / ".git").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_versioning_none_makes_no_commit(roots):
    local_root, remote_root = roots
    write(local_root, "a.txt", "v1", BASE)
    pair = make_pair(local_root, remote_root)  # versioning defaults to "none"

    run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    assert not (local_root / ".git").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_run_with_errors_still_commits_only_the_successful_part(roots):
    """A run that has errors must not be versioned - the spec's "no errors"
    condition - to avoid a commit whose message advertises a summary that
    includes files that actually failed.
    """
    local_root, remote_root = roots
    write(local_root, "good.txt", "ok", BASE)
    write(local_root, "bad.txt", "conflict target", BASE)
    (remote_root / "bad.txt").mkdir()  # forces a real per-file error, see the error test above
    pair = make_pair(local_root, remote_root, versioning="git")

    summary = run_pair(pair, local_t(), remote_t(), now=datetime.fromtimestamp(BASE + 100))

    assert summary.errors == 1
    assert not (local_root / ".git").exists()


# ---------------------------------------------------------------------------
# Stop: a transport call killed by abort() ends the run as cancelled (task 36a, issue #6)
# ---------------------------------------------------------------------------


class _StopsOnSecondFetch(LocalDirTransport):
    """Fetches the first file normally, then behaves as if Stop killed the next adb call."""

    def __init__(self, side):
        super().__init__(side)
        self.fetches = 0

    def fetch(self, root, rel, tmp_path):
        from androsync.transport.base import TransportCancelled

        self.fetches += 1
        if self.fetches >= 2:
            raise TransportCancelled("cancelled")
        super().fetch(root, rel, tmp_path)


def test_a_killed_transport_call_ends_the_run_as_cancelled_not_as_an_error(roots):
    local_root, remote_root = roots
    for name in ("a.txt", "b.txt", "c.txt"):
        write(local_root, name, name, BASE)
    pair = make_pair(local_root, remote_root, transfer_batch_files=1)
    lines: list[str] = []

    summary = run_pair(
        pair, _StopsOnSecondFetch(Side.LOCAL), remote_t(), log=lines.append, now=datetime.fromtimestamp(BASE + 100)
    )

    assert summary.errors == 0
    assert "cancelled" in lines
    assert not any(line.startswith("ERROR") for line in lines)
    assert not any("retrying individually" in line for line in lines)  # a cancel is never retried file by file
    assert summary.copied_to_remote == 1
    assert tree(remote_root) == {"a.txt": "a.txt"}
    # what finished stays recorded, what did not is simply planned again next run
    assert set(state.load_snapshot(pair.name)) == {"a.txt"}
    meta = state.load_pair_meta(pair.name)
    assert meta == {}  # the run did not finish, so no "last result" was written


def test_a_run_stopped_during_the_scan_returns_without_error(roots):
    from androsync.transport.base import TransportCancelled

    class StopsOnScan(LocalDirTransport):
        def scan(self, root, ignore):
            raise TransportCancelled("cancelled")

    local_root, remote_root = roots
    write(local_root, "a.txt", "a", BASE)
    lines: list[str] = []
    summary = run_pair(make_pair(local_root, remote_root), StopsOnScan(Side.LOCAL), remote_t(), log=lines.append)
    assert summary == Summary()
    assert lines[-1] == "cancelled"


def test_real_runs_log_scan_and_batch_progress_but_dry_runs_log_only_actions(roots):
    local_root, remote_root = roots
    for i in range(3):
        write(local_root, f"f{i}.txt", "x", BASE)
    pair = make_pair(local_root, remote_root, transfer_batch_files=2)

    dry: list[str] = []
    run_pair(pair, local_t(), remote_t(), dry_run=True, log=dry.append)
    assert all("scanning" not in line and not line.startswith("batch") and not line.startswith("plan:") for line in dry)

    real: list[str] = []
    run_pair(pair, local_t(), remote_t(), log=real.append, now=datetime.fromtimestamp(BASE + 100))
    assert "scanning the laptop folder..." in real
    assert any(line.startswith("phone: 0 files") for line in real)
    assert "plan: 3 actions" in real
    assert any(line.startswith("batch 1: 2 files") for line in real)  # 3 files, 2 per batch


def test_transfer_batch_size_comes_from_the_pair_config(tmp_path):
    from androsync.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "defaults:\n  transfer:\n    batch_files: 7\n"
        "pairs:\n"
        "  - {name: a, local: /tmp/a, remote: /sdcard/a, direction: two-way}\n"
        "  - {name: b, local: /tmp/b, remote: /sdcard/b, direction: two-way, transfer: {batch_files: 3, batch_mb: 5}}\n"
    )
    a, b = load_config(path).pairs
    assert (a.transfer_batch_files, a.transfer_batch_mb) == (7, 64)
    assert (b.transfer_batch_files, b.transfer_batch_mb) == (3, 5)


# ---------------------------------------------------------------------------
# every action line names the file on BOTH sides (full laptop path and full phone path)
# ---------------------------------------------------------------------------


def _line(action, local_root="/home/me/vault", remote_root="/sdcard/Obsidian/Notes"):
    from androsync.executor import _format_action

    return _format_action(action, local_root, remote_root)


def test_a_copy_line_ends_with_the_full_path_on_both_sides():
    from androsync.model import Action, ActionKind

    action = Action(ActionKind.COPY, "notes/a.md", Side.LOCAL, Side.REMOTE, "new on laptop")
    assert _line(action) == (
        "→ phone  notes/a.md   new on laptop"
        "   (laptop: /home/me/vault/notes/a.md  ·  phone: /sdcard/Obsidian/Notes/notes/a.md)"
    )


def test_a_delete_on_the_phone_says_what_happened_where_and_shows_both_paths():
    from androsync.model import Action, ActionKind

    action = Action(ActionKind.DELETE, "notes/a.md", None, Side.REMOTE, "deleted on laptop")
    assert _line(action) == (
        "x  phone  notes/a.md   deleted on laptop, so removed from the phone"
        "   (laptop: /home/me/vault/notes/a.md  ·  phone: /sdcard/Obsidian/Notes/notes/a.md)"
    )


def test_a_delete_on_the_laptop_is_worded_the_other_way_round():
    from androsync.model import Action, ActionKind

    action = Action(ActionKind.DELETE, "b.txt", None, Side.LOCAL, "deleted on phone")
    line = _line(action)
    assert line.startswith("x  laptop  b.txt   deleted on phone, so removed from the laptop")
    assert line.endswith("(laptop: /home/me/vault/b.txt  ·  phone: /sdcard/Obsidian/Notes/b.txt)")


def test_roots_with_a_trailing_slash_do_not_double_the_slash():
    from androsync.model import Action, ActionKind

    action = Action(ActionKind.COPY, "a.txt", Side.REMOTE, Side.LOCAL, "new on phone")
    line = _line(action, local_root="/home/me/inbox/", remote_root="/sdcard/Inbox/")
    assert "(laptop: /home/me/inbox/a.txt  ·  phone: /sdcard/Inbox/a.txt)" in line


def test_conflict_and_ask_lines_also_show_both_paths():
    from androsync.model import Action, ActionKind

    conflict = Action(ActionKind.CONFLICT_COPY, "n.md", Side.REMOTE, Side.REMOTE, "conflict: newest wins (laptop)")
    ask = Action(ActionKind.ASK, "n.md", None, None, "modified on both sides")
    for line in (_line(conflict), _line(ask)):
        assert line.endswith("(laptop: /home/me/vault/n.md  ·  phone: /sdcard/Obsidian/Notes/n.md)")
    assert _line(ask).startswith("?  n.md   modified on both sides")


def test_a_real_dry_run_lists_both_paths_for_each_planned_action(roots):
    local_root, remote_root = roots
    write(local_root, "docs/a.md", "a", BASE)
    lines: list[str] = []
    run_pair(make_pair(local_root, remote_root), local_t(), remote_t(), dry_run=True, log=lines.append)
    [line] = lines
    assert f"(laptop: {local_root}/docs/a.md  ·  phone: {remote_root}/docs/a.md)" in line
    assert "\n" not in line  # one log line per action: the live log is sent line by line
