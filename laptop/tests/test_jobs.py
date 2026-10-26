"""Tests for androsync.daemon.jobs (JobRunner, LogBuffer, pairs_status)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from androsync import state
from androsync.config import Config, load_config
from androsync.daemon.jobs import JobBusy, JobRunner, LogBuffer, job_to_dict, pairs_status
from androsync.model import Side
from androsync.transport.local import LocalDirTransport


@pytest.fixture(autouse=True)
def _xdg_data_home(tmp_path, monkeypatch):
    # Isolate the JSON snapshot/meta store per test, same pattern as test_executor.py.
    # Background job threads must finish before this reverts at teardown - every
    # test below waits for its job(s) to reach a terminal state.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    # Isolate ~/.local/state/androsync/last-run.log too (leftover L1).
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    """Poll ``predicate`` until it is true or ``timeout`` seconds have passed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def build_config(tmp_path: Path, pair_specs: list[dict]) -> Config:
    """Write a small YAML config to ``tmp_path`` and load it, like the CLI does."""
    lines = [
        "device: auto",
        "service:",
        "  port: 8765",
        "defaults:",
        "  conflict: newest-wins",
        "  mtime_tolerance_s: 2",
        "pairs:",
    ]
    for spec in pair_specs:
        lines.append(f"  - name: {spec['name']}")
        lines.append(f"    local: {spec['local']}")
        lines.append(f"    remote: {spec['remote']}")
        lines.append(f"    direction: {spec.get('direction', 'two-way')}")
        if "enabled" in spec:
            lines.append(f"    enabled: {str(spec['enabled']).lower()}")
        if "batch_files" in spec:
            lines.append("    transfer:")
            lines.append(f"      batch_files: {spec['batch_files']}")
    path = tmp_path / "config.yaml"
    path.write_text("\n".join(lines) + "\n")
    return load_config(path)


def fast_transport_factory(_config: Config) -> tuple[LocalDirTransport, LocalDirTransport]:
    return LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE)


class SleepyLocalTransport(LocalDirTransport):
    """LocalDirTransport whose ``fetch`` sleeps first, to make a job take a while."""

    def __init__(self, side: Side, delay: float) -> None:
        super().__init__(side)
        self.delay = delay

    def fetch(self, root, rel, tmp_path) -> None:  # noqa: ANN001 - match base signature
        time.sleep(self.delay)
        super().fetch(root, rel, tmp_path)


# --------------------------------------------------------------------------
# JobRunner
# --------------------------------------------------------------------------


def test_submit_runs_and_reaches_done(tmp_path):
    alpha_local = tmp_path / "alpha_local"
    alpha_local.mkdir()
    (alpha_local / "a.txt").write_text("hello")
    beta_local = tmp_path / "beta_local"
    beta_local.mkdir()
    (beta_local / "b.txt").write_text("world")

    config = build_config(
        tmp_path,
        [
            {"name": "alpha", "local": str(alpha_local), "remote": str(tmp_path / "alpha_remote")},
            {"name": "beta", "local": str(beta_local), "remote": str(tmp_path / "beta_remote")},
        ],
    )
    runner = JobRunner(lambda: config, fast_transport_factory)

    job = runner.submit()

    assert wait_until(lambda: job.state not in ("queued", "running"))
    assert job.state == "done"
    assert job.started is not None and job.finished is not None and job.finished >= job.started
    assert set(job.summaries) == {"alpha", "beta"}
    assert job.summaries["alpha"].copied_to_remote == 1
    assert job.summaries["beta"].copied_to_remote == 1
    assert (tmp_path / "alpha_remote" / "a.txt").exists()
    assert (tmp_path / "beta_remote" / "b.txt").exists()
    assert runner.get(job.id) is job
    assert runner.list()[0] is job
    assert runner.current() is None  # finished, so no longer "current"


def test_second_submit_while_running_raises_job_busy(tmp_path):
    bulk_local = tmp_path / "bulk_local"
    bulk_local.mkdir()
    for i in range(10):
        (bulk_local / f"f{i}.txt").write_text(f"file {i}")

    config = build_config(
        tmp_path,
        [{"name": "bulk", "local": str(bulk_local), "remote": str(tmp_path / "bulk_remote")}],
    )

    def slow_transport_factory(_config: Config):
        return SleepyLocalTransport(Side.LOCAL, 0.2), LocalDirTransport(Side.REMOTE)

    runner = JobRunner(lambda: config, slow_transport_factory)

    job1 = runner.submit()
    assert job1.state in ("queued", "running")

    with pytest.raises(JobBusy):
        runner.submit()

    # Let the slow job finish before the XDG_DATA_HOME fixture reverts at teardown.
    assert wait_until(lambda: job1.state not in ("queued", "running"))
    assert job1.state == "done"
    assert job1.summaries["bulk"].copied_to_remote == 10

    # Once finished, submitting again is fine.
    job2 = runner.submit(dry_run=True)
    assert wait_until(lambda: job2.state not in ("queued", "running"))
    assert job2.state == "done"


def test_cancel_mid_run_stops_before_all_files_copied(tmp_path, monkeypatch):
    # Task 24: consecutive same-direction COPY actions are batched into one
    # fetch_many/store_many call, which has no cancellation checkpoints of
    # its own - cancelling mid-*batch* no longer stops it partway (the whole
    # batch either transfers or it doesn't; see executor.py's
    # _do_copy_batch). Forcing the cap down to 1 file restores a per-file
    # checkpoint between "batches" so this test can still observe a run
    # actually stopping partway through a bulk job, matching real usage
    # where a batch this small also has nothing to gain from being merged.
    bulk_local = tmp_path / "bulk_local"
    bulk_local.mkdir()
    for i in range(10):
        (bulk_local / f"f{i}.txt").write_text(f"file {i}")
    bulk_remote = tmp_path / "bulk_remote"

    config = build_config(
        tmp_path,
        [{"name": "bulk", "local": str(bulk_local), "remote": str(bulk_remote), "batch_files": 1}],
    )

    def slow_transport_factory(_config: Config):
        return SleepyLocalTransport(Side.LOCAL, 0.2), LocalDirTransport(Side.REMOTE)

    runner = JobRunner(lambda: config, slow_transport_factory)

    job = runner.submit()
    time.sleep(0.05)  # let it start, before the first 0.2s fetch completes
    runner.cancel(job.id)

    assert job.state in ("cancelling", "cancelled")  # Stop is acknowledged at once
    assert wait_until(lambda: job.state not in ("queued", "running", "cancelling"))
    assert job.state == "cancelled"

    copied = list(bulk_remote.glob("*.txt")) if bulk_remote.exists() else []
    assert len(copied) < 10


def test_submit_unknown_pair_raises_key_error(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    config = build_config(
        tmp_path, [{"name": "alpha", "local": str(local), "remote": str(tmp_path / "remote")}]
    )
    runner = JobRunner(lambda: config, fast_transport_factory)

    with pytest.raises(KeyError):
        runner.submit(["does-not-exist"])

    assert runner.current() is None


def test_transport_factory_failure_marks_job_failed(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    config = build_config(
        tmp_path, [{"name": "alpha", "local": str(local), "remote": str(tmp_path / "remote")}]
    )

    def broken_transport_factory(_config: Config):
        raise RuntimeError("no device attached")

    runner = JobRunner(lambda: config, broken_transport_factory)

    job = runner.submit()

    assert wait_until(lambda: job.state not in ("queued", "running"))
    assert job.state == "failed"
    assert job.error is not None and "no device attached" in job.error


def test_transport_factory_failure_is_logged_and_writes_failed_meta(tmp_path, caplog):
    """task 32c: this used to be visible nowhere but the job's own in-memory log."""
    local = tmp_path / "local"
    local.mkdir()
    config = build_config(
        tmp_path, [{"name": "alpha", "local": str(local), "remote": str(tmp_path / "remote")}]
    )

    def broken_transport_factory(_config: Config):
        raise RuntimeError("no device attached")

    runner = JobRunner(lambda: config, broken_transport_factory)

    with caplog.at_level("WARNING", logger="androsync.daemon.jobs"):
        job = runner.submit()
        assert wait_until(lambda: job.state not in ("queued", "running"))

    assert any("no device attached" in record.message for record in caplog.records)

    meta = state.load_pair_meta("alpha")
    assert meta["last_result"] == "FAILED: no device attached"
    assert meta["last_error"] == "no device attached"
    assert "last_run" in meta


def test_pair_failure_during_sync_is_logged_and_writes_failed_meta(tmp_path, caplog):
    local = tmp_path / "local"
    local.mkdir()
    config = build_config(
        tmp_path, [{"name": "alpha", "local": str(local), "remote": str(tmp_path / "doesnotexist")}]
    )
    # A phone-to-laptop pair never auto-creates the remote root, so a missing
    # remote root for it raises ExecutorError mid-run - same class of failure
    # as a file-level adb error, just easier to trigger without mocking adb.
    config.pairs[0].direction = "phone-to-laptop"

    runner = JobRunner(lambda: config, fast_transport_factory)

    with caplog.at_level("WARNING", logger="androsync.daemon.jobs"):
        job = runner.submit()
        assert wait_until(lambda: job.state not in ("queued", "running"))

    assert job.state == "failed"
    assert any("alpha" in record.message for record in caplog.records)

    meta = state.load_pair_meta("alpha")
    assert meta["last_result"].startswith("FAILED:")
    assert "last_error" in meta


# --------------------------------------------------------------------------
# LogBuffer
# --------------------------------------------------------------------------


def test_log_buffer_since_returns_only_new_lines():
    buf = LogBuffer()
    buf.append("one")
    buf.append("two")
    buf.append("three")

    all_lines = buf.since(0)
    assert [seq for seq, _ in all_lines] == [0, 1, 2]
    assert all(line.endswith(("one", "two", "three")) for _seq, line in all_lines)

    later = buf.since(2)
    assert [seq for seq, _ in later] == [2]
    assert later[0][1].endswith("three")

    assert buf.since(99) == []


def test_log_buffer_tail_and_ring_eviction():
    buf = LogBuffer(maxlen=3)
    for i in range(5):
        buf.append(str(i))

    tail = buf.tail(10)
    assert [line[-1] for _seq, line in tail] == ["2", "3", "4"]
    # Sequence numbers keep counting up even though old entries were evicted.
    assert [seq for seq, _ in tail] == [2, 3, 4]
    assert buf.since(0) == tail


def test_log_buffer_wait_returns_immediately_when_already_available():
    buf = LogBuffer()
    buf.append("first")
    result = buf.wait(0, timeout=1.0)
    assert len(result) == 1


def test_log_buffer_wait_blocks_until_append():
    buf = LogBuffer()
    result: list = []

    def waiter():
        result.extend(buf.wait(0, timeout=2.0))

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.1)
    assert result == []  # still waiting
    buf.append("hello")
    t.join(timeout=2.0)

    assert not t.is_alive()
    assert len(result) == 1
    assert result[0][1].endswith("hello")


def test_log_buffer_wait_times_out_with_nothing_new():
    buf = LogBuffer()
    result = buf.wait(0, timeout=0.1)
    assert result == []


# --------------------------------------------------------------------------
# job_to_dict / pairs_status
# --------------------------------------------------------------------------


def test_job_to_dict_is_json_serialisable(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "a.txt").write_text("hi")
    config = build_config(
        tmp_path, [{"name": "alpha", "local": str(local), "remote": str(tmp_path / "remote")}]
    )
    runner = JobRunner(lambda: config, fast_transport_factory)

    job = runner.submit()
    assert wait_until(lambda: job.state not in ("queued", "running"))

    as_dict = job_to_dict(job)
    encoded = json.dumps(as_dict)  # must not raise
    decoded = json.loads(encoded)

    assert decoded["id"] == job.id
    assert decoded["state"] == "done"
    assert decoded["summaries"]["alpha"]["copied_to_remote"] == 1
    assert isinstance(decoded["log_tail"], list)
    assert len(decoded["log_tail"]) <= 20


def test_pairs_status_reports_enabled_flag(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    paused_local = tmp_path / "paused_local"
    paused_local.mkdir()
    config = build_config(
        tmp_path,
        [
            {"name": "alpha", "local": str(local), "remote": str(tmp_path / "remote")},
            {"name": "paused", "local": str(paused_local), "remote": str(tmp_path / "paused_remote"), "enabled": False},
        ],
    )

    by_name = {row["name"]: row for row in pairs_status(config)}

    assert by_name["alpha"]["enabled"] is True
    assert by_name["paused"]["enabled"] is False


def test_pairs_status_reports_last_run_after_finished_job(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "a.txt").write_text("hi")
    config = build_config(
        tmp_path,
        [
            {"name": "alpha", "local": str(local), "remote": str(tmp_path / "remote")},
            {"name": "never-run", "local": str(tmp_path / "other_local"), "remote": str(tmp_path / "other_remote")},
        ],
    )
    (tmp_path / "other_local").mkdir()

    before = pairs_status(config)
    by_name = {row["name"]: row for row in before}
    assert by_name["alpha"]["last_run"] is None
    assert by_name["never-run"]["last_run"] is None

    runner = JobRunner(lambda: config, fast_transport_factory)
    job = runner.submit(["alpha"])
    assert wait_until(lambda: job.state not in ("queued", "running"))
    assert job.state == "done"

    after = pairs_status(config)
    by_name = {row["name"]: row for row in after}
    assert by_name["alpha"]["last_run"] is not None
    assert by_name["alpha"]["last_result"] is not None
    assert by_name["alpha"]["local"] == str(local)
    assert by_name["alpha"]["remote"] == str(tmp_path / "remote")
    assert by_name["alpha"]["direction"] == "two-way"
    # Matches what run_pair itself persisted via state.save_pair_meta.
    assert by_name["alpha"]["last_run"] == state.load_pair_meta("alpha")["last_run"]
    assert by_name["never-run"]["last_run"] is None


# --------------------------------------------------------------------------
# Phase I: submit_undo, disabled pairs (task 30b)
# --------------------------------------------------------------------------


def test_submit_skips_disabled_pair_with_a_note(tmp_path):
    alpha_local = tmp_path / "alpha_local"
    alpha_local.mkdir()
    (alpha_local / "a.txt").write_text("hello")
    paused_local = tmp_path / "paused_local"
    paused_local.mkdir()
    (paused_local / "b.txt").write_text("world")

    config = build_config(
        tmp_path,
        [
            {"name": "alpha", "local": str(alpha_local), "remote": str(tmp_path / "alpha_remote")},
            {
                "name": "paused",
                "local": str(paused_local),
                "remote": str(tmp_path / "paused_remote"),
                "enabled": False,
            },
        ],
    )
    runner = JobRunner(lambda: config, fast_transport_factory)

    job = runner.submit()

    assert wait_until(lambda: job.state not in ("queued", "running"))
    assert job.state == "done"
    assert set(job.summaries) == {"alpha"}  # "paused" never ran
    assert not (tmp_path / "paused_remote").exists()
    assert any("paused" in line and "skipped" in line for _seq, line in job.log.tail(50))


def test_submit_explicit_name_still_skips_disabled_pair(tmp_path):
    """Unlike the CLI (explicit name overrides a pause), the daemon always honours it."""
    paused_local = tmp_path / "paused_local"
    paused_local.mkdir()
    (paused_local / "b.txt").write_text("world")

    config = build_config(
        tmp_path,
        [{"name": "paused", "local": str(paused_local), "remote": str(tmp_path / "paused_remote"), "enabled": False}],
    )
    runner = JobRunner(lambda: config, fast_transport_factory)

    job = runner.submit(["paused"])

    assert wait_until(lambda: job.state not in ("queued", "running"))
    assert job.state == "done"
    assert "paused" not in job.summaries
    assert not (tmp_path / "paused_remote").exists()


def test_submit_undo_undoes_the_most_recent_run(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "a.txt").write_text("hello")
    config = build_config(tmp_path, [{"name": "p1", "local": str(local), "remote": str(tmp_path / "remote")}])
    runner = JobRunner(lambda: config, fast_transport_factory)

    sync_job = runner.submit()
    assert wait_until(lambda: sync_job.state not in ("queued", "running"))
    assert (tmp_path / "remote" / "a.txt").exists()

    undo_job = runner.submit_undo("p1")

    assert wait_until(lambda: undo_job.state not in ("queued", "running"))
    assert undo_job.state == "done"
    assert undo_job.kind == "undo"
    assert not (tmp_path / "remote" / "a.txt").exists()
    as_dict = job_to_dict(undo_job)
    assert as_dict["undo_summary"]["restored_remote"] == 1  # one file changed on the phone (removed, in this case)
    assert as_dict["undo_run_id"] == sync_job.summaries["p1"].run_id


def test_submit_undo_unknown_pair_raises_key_error(tmp_path):
    config = build_config(tmp_path, [])
    runner = JobRunner(lambda: config, fast_transport_factory)

    with pytest.raises(KeyError):
        runner.submit_undo("nope")


def test_submit_undo_no_runs_yet_raises_undo_error(tmp_path):
    from androsync.undo import UndoError

    local = tmp_path / "local"
    local.mkdir()
    config = build_config(tmp_path, [{"name": "p1", "local": str(local), "remote": str(tmp_path / "remote")}])
    runner = JobRunner(lambda: config, fast_transport_factory)

    with pytest.raises(UndoError):
        runner.submit_undo("p1")


def test_submit_writes_last_run_log(tmp_path):
    from androsync.runlog import log_path

    alpha_local = tmp_path / "alpha_local"
    alpha_local.mkdir()
    (alpha_local / "a.txt").write_text("hello")
    config = build_config(tmp_path, [{"name": "alpha", "local": str(alpha_local), "remote": str(tmp_path / "alpha_remote")}])
    runner = JobRunner(lambda: config, fast_transport_factory)

    job = runner.submit()
    assert wait_until(lambda: job.state not in ("queued", "running"))

    assert "a.txt" in log_path().read_text()


def test_submit_undo_while_busy_raises_job_busy(tmp_path):
    bulk_local = tmp_path / "bulk_local"
    bulk_local.mkdir()
    for i in range(10):
        (bulk_local / f"f{i}.txt").write_text(f"file {i}")

    config = build_config(tmp_path, [{"name": "bulk", "local": str(bulk_local), "remote": str(tmp_path / "bulk_remote")}])

    def slow_transport_factory(_config: Config):
        return SleepyLocalTransport(Side.LOCAL, 0.2), LocalDirTransport(Side.REMOTE)

    runner = JobRunner(lambda: config, slow_transport_factory)
    job = runner.submit()
    assert wait_until(lambda: job.state == "running")

    with pytest.raises(JobBusy):
        runner.submit_undo("bulk")

    assert wait_until(lambda: job.state not in ("queued", "running"))


# --------------------------------------------------------------------------
# Stop that really stops (task 36a, issue #6)
# --------------------------------------------------------------------------


class _BlockingTransport(LocalDirTransport):
    """``fetch`` blocks like a long adb pull until ``abort()`` is called, then raises like a killed call."""

    def __init__(self, side: Side, honour_abort: bool = True) -> None:
        super().__init__(side)
        self.aborted = threading.Event()
        self.in_fetch = threading.Event()
        #: Lets a test look at the "cancelling" state before the killed call is allowed to return.
        self.release = threading.Event()
        self.honour_abort = honour_abort

    def abort(self) -> None:
        self.aborted.set()

    def fetch(self, root, rel, tmp_path) -> None:  # noqa: ANN001
        from androsync.transport.base import TransportCancelled

        self.in_fetch.set()
        if self.honour_abort:
            if self.aborted.wait(timeout=20):
                self.release.wait(timeout=20)
                raise TransportCancelled("cancelled")
        else:
            time.sleep(1.5)
        super().fetch(root, rel, tmp_path)


def _one_file_config(tmp_path: Path) -> Config:
    local = tmp_path / "l"
    local.mkdir(exist_ok=True)  # the runner loads the config more than once
    (local / "a.txt").write_text("a")
    return build_config(tmp_path, [{"name": "p", "local": str(local), "remote": str(tmp_path / "r")}])


def test_stop_aborts_the_transport_and_the_job_ends_cancelled_within_seconds(tmp_path):
    blocking = _BlockingTransport(Side.LOCAL)
    runner = JobRunner(lambda: _one_file_config(tmp_path), lambda _c: (blocking, LocalDirTransport(Side.REMOTE)))
    job = runner.submit()
    assert blocking.in_fetch.wait(5)

    started = time.monotonic()
    runner.cancel(job.id)
    assert job.state == "cancelling"  # acknowledged at once, and still counted as busy
    assert runner.current() is job
    assert blocking.aborted.is_set()
    blocking.release.set()
    assert wait_until(lambda: job.state == "cancelled", timeout=5)

    assert time.monotonic() - started < 3
    assert blocking.aborted.is_set()
    assert job.error is None  # a Stop is not a failure
    assert not any(line.startswith("ERROR") for _seq, line in job.log.since(0))
    assert runner.current() is None


def test_a_job_that_ignores_stop_is_shown_cancelled_after_the_grace_period(tmp_path, monkeypatch):
    import androsync.daemon.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "CANCEL_GRACE_S", 0.3)
    stubborn = _BlockingTransport(Side.LOCAL, honour_abort=False)
    runner = JobRunner(lambda: _one_file_config(tmp_path), lambda _c: (stubborn, LocalDirTransport(Side.REMOTE)))
    job = runner.submit()
    assert stubborn.in_fetch.wait(5)

    runner.cancel(job.id)
    assert job.state == "cancelling"
    assert wait_until(lambda: job.state == "cancelled", timeout=3)
    assert any("cut off" in line for _seq, line in job.log.since(0))


def test_pressing_stop_twice_is_harmless(tmp_path):
    blocking = _BlockingTransport(Side.LOCAL)
    runner = JobRunner(lambda: _one_file_config(tmp_path), lambda _c: (blocking, LocalDirTransport(Side.REMOTE)))
    job = runner.submit()
    assert blocking.in_fetch.wait(5)
    runner.cancel(job.id)
    runner.cancel(job.id)
    blocking.release.set()
    assert wait_until(lambda: job.state == "cancelled", timeout=5)
    runner.cancel(job.id)  # after the end: no change
    assert job.state == "cancelled"


def test_cancelling_state_is_reported_by_the_api_dict_and_counts_as_busy(tmp_path):
    blocking = _BlockingTransport(Side.LOCAL)
    runner = JobRunner(lambda: _one_file_config(tmp_path), lambda _c: (blocking, LocalDirTransport(Side.REMOTE)))
    job = runner.submit()
    assert blocking.in_fetch.wait(5)
    runner.cancel(job.id)
    payload = job_to_dict(job)
    assert payload["state"] == "cancelling" and payload["cancel_requested"] is True
    with pytest.raises(JobBusy):
        runner.submit()
    blocking.release.set()
    assert wait_until(lambda: job.state == "cancelled", timeout=5)


def test_a_failed_job_never_shows_as_finished_before_its_pairs_failure_is_saved(tmp_path):
    """The race behind a red CI run: `failed` was set first and each pair's FAILED result written a moment later."""
    local = tmp_path / "local"
    local.mkdir()

    def broken_transport_factory(_config: Config):
        raise RuntimeError("no device attached")

    for round_number in range(25):
        config = build_config(
            tmp_path, [{"name": f"p{round_number}", "local": str(local), "remote": str(tmp_path / "remote")}]
        )
        runner = JobRunner(lambda: config, broken_transport_factory)
        job = runner.submit()
        while job.state in ("queued", "running"):
            pass  # no sleep on purpose: read the pair the very moment the state flips
        assert state.load_pair_meta(f"p{round_number}").get("last_result") == "FAILED: no device attached"
