"""Big deletes and huge first runs wait for confirmation (task 36d, issue #9)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from androsync import state
from androsync.config import PairConfig, load_config
from androsync.daemon.jobs import JobRunner, job_to_dict
from androsync.executor import run_pair
from androsync.model import Action, ActionKind, FileEntry, Side, SnapshotEntry
from androsync.safety import SafetyLimits, check_plan
from androsync.transport.local import LocalDirTransport

BASE = 1_700_000_000


@pytest.fixture(autouse=True)
def _xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


def deletes(count: int, dst: Side = Side.LOCAL) -> list[Action]:
    return [Action(ActionKind.DELETE, f"f{i}.txt", None, dst, f"deleted on {'phone' if dst is Side.LOCAL else 'laptop'}") for i in range(count)]


def copies(count: int, size: int = 10) -> tuple[list[Action], dict]:
    scan = {f"c{i}.bin": FileEntry(size=size, mtime=float(BASE)) for i in range(count)}
    return [Action(ActionKind.COPY, rel, Side.LOCAL, Side.REMOTE, "new on laptop") for rel in scan], scan


# ---- the pure rule -----------------------------------------------------------------------------------------


def test_the_785_delete_incident_is_stopped_and_names_the_counts_and_examples():
    problems = check_plan(deletes(785), known_files=785, first_run=False, scan_local={}, scan_remote={}, limits=SafetyLimits())
    [problem] = problems
    assert "delete 785 files on the laptop" in problem and "100% of the 785 files" in problem
    assert "f0.txt, f1.txt, f10.txt, f100.txt, f101.txt and 780 more" in problem  # sorted by name, first five


@pytest.mark.parametrize(
    "count, known, blocked",
    [
        (50, 100, False),  # at the count limit: allowed
        (51, 100, True),  # over 50 and over 20% of 100
        (60, 665, False),  # over 50 but only 9% of 665 (and under max(50, 133))
        (133, 665, False),  # exactly 20% of 665
        (134, 665, True),  # just over 20% of 665
        (1, 1, False),  # a tiny pair can never trip the guard
        (49, 49, False),  # deleting everything of a small pair is below the count limit
    ],
)
def test_delete_threshold_is_the_larger_of_the_count_and_the_percentage(count, known, blocked):
    problems = check_plan(deletes(count), known, False, {}, {}, SafetyLimits())
    assert bool(problems) is blocked


def test_each_side_is_judged_on_its_own():
    actions = deletes(40, Side.LOCAL) + deletes(40, Side.REMOTE)
    assert check_plan(actions, 100, False, {}, {}, SafetyLimits()) == []


def test_a_zero_count_switches_the_delete_check_off():
    assert check_plan(deletes(785), 785, False, {}, {}, SafetyLimits(max_delete_count=0)) == []


def test_a_zero_percent_leaves_only_the_count_limit():
    assert check_plan(deletes(51), 10_000, False, {}, {}, SafetyLimits(max_delete_percent=0))


def test_big_first_run_by_file_count():
    actions, scan = copies(5001)
    [problem] = check_plan(actions, 0, True, scan, {}, SafetyLimits())
    assert "first sync would copy 5001 files" in problem
    assert check_plan(*(lambda a, s: (a, 0, True, s, {}))(*copies(5000)), SafetyLimits()) == []


def test_big_first_run_by_size_in_gb():
    actions, scan = copies(2, size=3 * 1024**3)  # 6 GB
    [problem] = check_plan(actions, 0, True, scan, {}, SafetyLimits())
    assert "6.0 GB" in problem


def test_a_big_copy_is_fine_when_the_pair_already_has_history():
    actions, scan = copies(6000)
    assert check_plan(actions, 6000, False, scan, {}, SafetyLimits()) == []


def test_zero_switches_off_the_first_run_checks():
    actions, scan = copies(9000, size=10**9)
    assert check_plan(actions, 0, True, scan, {}, SafetyLimits(big_first_run_files=0, big_first_run_gb=0)) == []


# ---- the executor --------------------------------------------------------------------------------------------


def make_pair(local: Path, remote: Path, **overrides) -> PairConfig:
    fields = dict(
        name="p", local=local, remote=str(remote), direction="two-way", conflict="newest-wins",
        keep_conflict_copies=True, propagate_deletes=True, mtime_tolerance_s=2, ignore=[],
    )
    fields.update(overrides)
    return PairConfig(**fields)


def synced_pair_with_files(tmp_path, count: int, **pair_overrides):
    local, remote = tmp_path / "l", tmp_path / "r"
    local.mkdir()
    remote.mkdir()
    for i in range(count):
        (local / f"f{i}.txt").write_text(str(i))
        import os

        os.utime(local / f"f{i}.txt", (BASE, BASE))
    pair = make_pair(local, remote, **pair_overrides)
    run_pair(pair, LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE), now=datetime.fromtimestamp(BASE + 100))
    return pair, local, remote


def test_a_run_that_would_delete_most_files_stops_before_changing_anything(tmp_path):
    pair, local, remote = synced_pair_with_files(tmp_path, 8, safety_max_delete_count=2, safety_max_delete_percent=20)
    for i in range(6):  # six deleted on the laptop of eight known
        (local / f"f{i}.txt").unlink()
    snapshot_before = state.load_snapshot("p")
    lines: list[str] = []

    summary = run_pair(pair, LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE), log=lines.append)

    assert summary.needs_confirmation is True
    assert "delete 6 files on the phone" in summary.confirmation[0]
    assert sorted(p.name for p in remote.iterdir()) == [f"f{i}.txt" for i in range(8)]  # nothing deleted
    assert state.load_snapshot("p") == snapshot_before  # history untouched
    assert any(line.startswith("STOPPED, nothing was changed.") for line in lines)
    assert "needs confirmation" in state.load_pair_meta("p")["last_result"]
    assert summary.deleted_remote == 0


def test_confirming_lets_the_same_plan_through(tmp_path):
    pair, local, remote = synced_pair_with_files(tmp_path, 8, safety_max_delete_count=2)
    for i in range(6):
        (local / f"f{i}.txt").unlink()
    lines: list[str] = []

    summary = run_pair(
        pair, LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE), log=lines.append, confirm_large=True
    )

    assert summary.needs_confirmation is False and summary.deleted_remote == 6
    assert sorted(p.name for p in remote.iterdir()) == ["f6.txt", "f7.txt"]
    assert "confirmed: going ahead with the large plan" in lines


def test_a_preview_shows_the_warning_and_still_changes_nothing(tmp_path):
    pair, local, remote = synced_pair_with_files(tmp_path, 8, safety_max_delete_count=2)
    for i in range(6):
        (local / f"f{i}.txt").unlink()
    lines: list[str] = []
    summary = run_pair(pair, LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE), dry_run=True, log=lines.append)
    assert summary.needs_confirmation is True
    assert any(line.startswith("!  needs your confirmation: It would delete 6 files") for line in lines)
    assert len(list(remote.iterdir())) == 8


def test_a_first_run_with_too_many_files_stops(tmp_path):
    local, remote = tmp_path / "l", tmp_path / "r"
    local.mkdir()
    remote.mkdir()
    for i in range(6):
        (local / f"f{i}.txt").write_text("x")
    pair = make_pair(local, remote, safety_big_first_run_files=5)

    summary = run_pair(pair, LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE))

    assert summary.needs_confirmation and "first sync would copy 6 files" in summary.confirmation[0]
    assert list(remote.iterdir()) == []


def test_undo_still_works_after_a_confirmed_large_run(tmp_path):
    from androsync.undo import undo_run

    pair, local, remote = synced_pair_with_files(tmp_path, 8, safety_max_delete_count=2)
    for i in range(6):
        (local / f"f{i}.txt").unlink()
    summary = run_pair(pair, LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE), confirm_large=True)
    assert len(list(remote.iterdir())) == 2

    undone = undo_run(pair, summary.run_id, LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE))

    assert undone.errors == 0 and len(list(remote.iterdir())) == 8


# ---- config -------------------------------------------------------------------------------------------------


def test_safety_settings_merge_defaults_then_pair_and_reject_nonsense(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "defaults:\n  safety:\n    max_delete_count: 10\n    big_first_run_gb: 0\n"
        "pairs:\n"
        "  - {name: a, local: /tmp/a, remote: /sdcard/a, direction: two-way}\n"
        "  - {name: b, local: /tmp/b, remote: /sdcard/b, direction: two-way, safety: {max_delete_percent: 5}}\n"
    )
    a, b = load_config(path).pairs
    assert (a.safety_max_delete_count, a.safety_max_delete_percent, a.safety_big_first_run_files, a.safety_big_first_run_gb) == (10, 20, 5000, 0)
    assert (b.safety_max_delete_count, b.safety_max_delete_percent) == (10, 5)
    assert b.safety_limits == SafetyLimits(max_delete_count=10, max_delete_percent=5, big_first_run_files=5000, big_first_run_gb=0)

    from androsync.config import ConfigError

    path.write_text("pairs:\n  - {name: a, local: /tmp/a, remote: /sdcard/a, direction: two-way, safety: {max_delete_count: -1}}\n")
    with pytest.raises(ConfigError, match="negative"):
        load_config(path)


# ---- the job runner and the API -----------------------------------------------------------------------------


def _config_for(tmp_path: Path, local: Path, remote: Path, extra: str = "") -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"pairs:\n  - name: p\n    local: {local}\n    remote: {remote}\n    direction: two-way\n"
        f"    safety: {{max_delete_count: 2}}\n{extra}"
    )
    return path


def wait_until(predicate, timeout: float = 5.0) -> bool:
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_a_blocked_job_ends_in_needs_confirmation_and_a_confirmed_one_runs(tmp_path):
    pair, local, remote = synced_pair_with_files(tmp_path, 8, safety_max_delete_count=2)
    for i in range(6):
        (local / f"f{i}.txt").unlink()
    cfg = _config_for(tmp_path, local, remote)
    runner = JobRunner(lambda: load_config(cfg), lambda _c: (LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE)))

    job = runner.submit()
    assert wait_until(lambda: job.state not in ("queued", "running"))
    assert job.state == "needs_confirmation"
    payload = job_to_dict(job)
    assert list(payload["confirmation"]) == ["p"] and "delete 6 files" in payload["confirmation"]["p"][0]
    assert runner.current() is None  # a finished job: a new one may start
    assert len(list(remote.iterdir())) == 8

    job2 = runner.submit(["p"], confirm_large=True)
    assert wait_until(lambda: job2.state not in ("queued", "running"))
    assert job2.state == "done" and len(list(remote.iterdir())) == 2
    assert job_to_dict(job2)["confirmation"] == {}
