"""Automatic sync: when it starts, when it must not, and how it stops itself (Phase T2)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from androsync.config import ConfigError, load_config
from androsync.daemon import auto as auto_module
from androsync.daemon.auto import MAX_FAILURES, MIN_GAP_S, AutoSync, PollingWatcher, folder_signature
from androsync.daemon.jobs import Job, JobBusy, JobRunner
from androsync.model import Side, Summary
from androsync.transport.local import LocalDirTransport


@pytest.fixture(autouse=True)
def _xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRunner:
    """Just enough of JobRunner: records submits, can be busy, lets the test finish jobs."""

    def __init__(self) -> None:
        self.submitted: list[tuple[list[str], str]] = []
        self.busy = False
        self.listeners = []

    def add_listener(self, fn) -> None:
        self.listeners.append(fn)

    def current(self):
        return object() if self.busy else None

    def submit(self, pairs=None, dry_run=False, confirm_large=False, origin="manual"):
        if self.busy:
            raise JobBusy("busy")
        self.submitted.append((list(pairs), origin))
        return Job(id=f"j{len(self.submitted)}", pairs=list(pairs), dry_run=dry_run, origin=origin)

    def finish(self, job: Job, state: str, *, confirmation: list[str] | None = None, error: str | None = None) -> None:
        job.state = state
        job.error = error
        if confirmation:
            job.summaries[job.pairs[0]] = Summary(needs_confirmation=True, confirmation=confirmation)
        for fn in self.listeners:
            fn(job)


def write_config(tmp_path: Path, *, auto: bool = True, enabled: bool = True, extra: str = "") -> Path:
    local = tmp_path / "local"
    local.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "config.yaml"
    path.write_text(
        "device: auto\npairs:\n"
        f"  - name: notes\n    local: {local}\n    remote: /sdcard/notes\n    direction: two-way\n"
        f"    auto: {str(auto).lower()}\n    enabled: {str(enabled).lower()}\n    auto_delay_s: 10\n    auto_interval_s: 300\n{extra}"
    )
    return path


class Harness:
    def __init__(self, tmp_path: Path, **config_kw) -> None:
        self.path = write_config(tmp_path, **config_kw)
        self.clock = Clock()
        self.runner = FakeRunner()
        self.notices: list[str] = []
        self.first_run = True
        self.phone = True
        self.auto = AutoSync(
            lambda: load_config(self.path),
            self.runner,
            clock=self.clock,
            phone_available=lambda pair: self.phone,
            notify=lambda title, body: self.notices.append(body),
            watcher_factory=lambda root, on_change: _NoWatcher(),
            first_run_done=lambda pair: self.first_run,
        )

    def tick(self, seconds: float = 0) -> None:
        self.clock.advance(seconds)
        self.auto.tick()


class _NoWatcher:
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def test_a_change_starts_one_automatic_sync_after_the_delay(tmp_path):
    h = Harness(tmp_path)
    h.tick()
    h.auto.mark_dirty("notes")
    h.tick(5)
    assert h.runner.submitted == []  # still settling
    h.tick(6)
    assert h.runner.submitted == [(["notes"], "auto")]
    h.tick(30)
    assert len(h.runner.submitted) == 1  # nothing new changed


def test_more_changes_while_settling_push_the_start_back(tmp_path):
    h = Harness(tmp_path)
    h.auto.mark_dirty("notes")
    h.tick(8)
    h.auto.mark_dirty("notes")  # another change just now
    h.tick(8)
    assert h.runner.submitted == []
    h.tick(3)
    assert len(h.runner.submitted) == 1


def test_a_pair_without_auto_or_that_is_paused_is_left_alone(tmp_path):
    off = Harness(tmp_path / "a", auto=False)
    off.auto.mark_dirty("notes")
    off.tick(60)
    assert off.runner.submitted == []
    paused = Harness(tmp_path / "b", enabled=False)
    paused.auto.mark_dirty("notes")
    paused.tick(60)
    assert paused.runner.submitted == []


def test_it_waits_while_another_job_runs(tmp_path):
    h = Harness(tmp_path)
    h.runner.busy = True
    h.auto.mark_dirty("notes")
    h.tick(30)
    assert h.runner.submitted == []
    h.runner.busy = False
    h.tick(1)
    assert len(h.runner.submitted) == 1


def test_the_first_sync_of_a_pair_is_never_automatic(tmp_path):
    h = Harness(tmp_path)
    h.first_run = False
    h.auto.mark_dirty("notes")
    h.tick(120)
    assert h.runner.submitted == []
    h.first_run = True  # a person ran it once
    h.tick(1)
    assert len(h.runner.submitted) == 1


def test_a_phone_that_is_not_connected_is_waited_for_not_counted_as_a_failure(tmp_path):
    h = Harness(tmp_path)
    h.phone = False
    h.auto.mark_dirty("notes")
    h.tick(120)
    assert h.runner.submitted == []
    assert h.auto.status("notes")["state"] == "waiting"
    h.phone = True
    h.tick(1)
    assert len(h.runner.submitted) == 1


def test_two_automatic_syncs_of_one_pair_are_at_least_a_minute_apart(tmp_path):
    h = Harness(tmp_path)
    h.auto.mark_dirty("notes")
    h.tick(11)
    assert len(h.runner.submitted) == 1
    h.auto.mark_dirty("notes")
    h.tick(11)
    assert len(h.runner.submitted) == 1  # too soon
    h.tick(MIN_GAP_S)
    assert len(h.runner.submitted) == 2


def test_a_plan_that_needs_the_user_pauses_the_pair_and_says_so(tmp_path):
    h = Harness(tmp_path)
    h.auto.mark_dirty("notes")
    h.tick(11)
    job = Job(id="j1", pairs=["notes"], dry_run=False, origin="auto")
    h.runner.finish(job, "needs_confirmation", confirmation=["It would delete 785 files on the laptop."])
    status = h.auto.status("notes")
    assert status["state"] == "paused" and "785 files" in status["reason"]
    assert h.notices and "paused" in h.notices[0]
    h.auto.mark_dirty("notes")
    h.tick(200)
    assert len(h.runner.submitted) == 1  # nothing more while paused


def test_a_manual_sync_that_works_resumes_a_paused_pair(tmp_path):
    h = Harness(tmp_path)
    h.auto.mark_dirty("notes")
    h.tick(11)
    h.runner.finish(Job(id="j1", pairs=["notes"], dry_run=False, origin="auto"), "needs_confirmation", confirmation=["x"])
    assert h.auto.status("notes")["state"] == "paused"
    h.runner.finish(Job(id="m1", pairs=["notes"], dry_run=False, origin="manual"), "done")
    assert h.auto.status("notes")["state"] == "watching"


def test_a_preview_does_not_resume_a_paused_pair(tmp_path):
    h = Harness(tmp_path)
    h.auto.mark_dirty("notes")
    h.tick(11)
    h.runner.finish(Job(id="j1", pairs=["notes"], dry_run=False, origin="auto"), "needs_confirmation", confirmation=["x"])
    h.runner.finish(Job(id="p1", pairs=["notes"], dry_run=True, origin="manual"), "done")
    assert h.auto.status("notes")["state"] == "paused"


def test_three_failed_automatic_syncs_in_a_row_pause_the_pair_and_a_success_resets_the_count(tmp_path):
    h = Harness(tmp_path)
    for round_number in range(MAX_FAILURES - 1):
        h.auto.mark_dirty("notes")
        h.tick(MIN_GAP_S + 11)
        h.runner.finish(h.runner.submit(["notes"], origin="auto"), "failed", error="boom")
    assert h.auto.status("notes")["state"] != "paused"
    h.runner.finish(Job(id="ok", pairs=["notes"], dry_run=False, origin="auto"), "done")  # resets the count
    for round_number in range(MAX_FAILURES):
        h.runner.finish(Job(id=f"f{round_number}", pairs=["notes"], dry_run=False, origin="auto"), "failed", error="boom")
    status = h.auto.status("notes")
    assert status["state"] == "paused" and "boom" in status["reason"]


def test_a_stop_pressed_by_the_user_pauses_the_pair(tmp_path):
    h = Harness(tmp_path)
    h.runner.finish(Job(id="j", pairs=["notes"], dry_run=False, origin="auto"), "cancelled")
    assert "stopped by you" in h.auto.status("notes")["reason"]


def test_the_phone_is_looked_at_every_interval_even_without_a_change(tmp_path):
    h = Harness(tmp_path)
    h.tick()  # arms the timer
    h.tick(299)
    assert h.runner.submitted == []
    h.tick(2)  # the 300 s interval is up: a check is due, and its delay is already over
    assert len(h.runner.submitted) == 1


def test_an_event_from_the_phone_app_marks_the_agent_pairs_that_cover_the_folder(tmp_path):
    h = Harness(tmp_path, extra="    transport: agent\n")
    h.auto.mark_root_dirty("/sdcard/notes/sub")
    h.tick(11)
    assert len(h.runner.submitted) == 1
    h2 = Harness(tmp_path / "other", extra="    transport: agent\n")
    h2.auto.mark_root_dirty("/sdcard/elsewhere")
    h2.tick(60)
    assert h2.runner.submitted == []


def test_the_status_says_what_automation_is_doing(tmp_path):
    off = Harness(tmp_path / "a", auto=False)
    assert off.auto.status("notes")["state"] == "off"
    h = Harness(tmp_path / "b")
    assert h.auto.status("notes")["state"] == "watching"
    h.auto.mark_dirty("notes")
    assert h.auto.status("notes")["state"] == "waiting"
    assert h.auto.status("missing")["state"] == "off"


# --- config -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra, message",
    [("    auto_delay_s: 0\n", "auto_delay_s"), ("    auto_interval_s: 10\n", "auto_interval_s"), ("    auto_delay_s: x\n", "whole numbers")],
)
def test_bad_automatic_sync_settings_are_refused(tmp_path, extra, message):
    path = tmp_path / "c.yaml"
    path.write_text(
        f"device: auto\npairs:\n  - name: a\n    local: /x\n    remote: /sdcard/a\n    direction: two-way\n{extra}"
    )
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_the_defaults_are_off_ten_seconds_and_five_minutes(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("device: auto\npairs:\n  - name: a\n    local: /x\n    remote: /sdcard/a\n    direction: two-way\n")
    pair = load_config(path).pairs[0]
    assert (pair.auto, pair.auto_delay_s, pair.auto_interval_s) == (False, 10, 300)


# --- watching a real folder -----------------------------------------------------------------------------------


def test_the_folder_fingerprint_changes_with_files_and_ignores_the_hidden_work_folders(tmp_path):
    base = folder_signature(str(tmp_path))
    (tmp_path / ".androsync").mkdir()
    (tmp_path / ".androsync" / "x").write_text("ignored")
    assert folder_signature(str(tmp_path)) == base
    (tmp_path / "new.txt").write_text("hello")
    assert folder_signature(str(tmp_path)) != base


def test_the_polling_watcher_reports_a_new_file(tmp_path):
    changes: list[int] = []
    watcher = PollingWatcher(str(tmp_path), lambda: changes.append(1), interval=0.05)
    watcher.start()
    try:
        time.sleep(0.2)  # the first look sets the baseline
        (tmp_path / "a.txt").write_text("x")
        deadline = time.time() + 3
        while not changes and time.time() < deadline:
            time.sleep(0.05)
        assert changes
    finally:
        watcher.stop()


# --- with the real job runner -----------------------------------------------------------------------------


def wait_until(predicate, timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_a_quiet_automatic_check_leaves_no_job_behind_and_no_undo_entry(tmp_path):
    local, remote = tmp_path / "local", tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    (local / "a.txt").write_text("a")
    os.utime(local / "a.txt", (1_700_000_000, 1_700_000_000))
    path = tmp_path / "config.yaml"
    path.write_text(
        f"device: auto\npairs:\n  - name: notes\n    local: {local}\n    remote: {remote}\n    direction: two-way\n    auto: true\n"
    )
    runner = JobRunner(lambda: load_config(path), lambda c: (LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE)))
    finished: list[Job] = []
    runner.add_listener(finished.append)

    first = runner.submit()  # the manual first sync
    assert wait_until(lambda: first.state == "done")
    assert runner.get(first.id) is not None  # a real sync stays in the list
    from androsync import undo

    runs_before = len(undo.list_runs_all("notes"))

    clock = Clock()
    auto = AutoSync(lambda: load_config(path), runner, clock=clock, watcher_factory=lambda r, cb: _NoWatcher())
    auto.tick()
    auto.mark_dirty("notes")
    clock.advance(30)
    auto.tick()
    assert wait_until(lambda: len(finished) == 2)
    quiet = finished[-1]
    assert quiet.origin == "auto" and quiet.state == "done"
    assert runner.get(quiet.id) is None  # dropped from the recent-jobs list
    assert len(undo.list_runs_all("notes")) == runs_before  # and no empty undo entry
