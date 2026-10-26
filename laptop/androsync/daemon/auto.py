"""Automatic sync (Phase T2, public issue #10): a pair with ``auto: true`` syncs by itself a few seconds after a change.

``AutoSync`` never does file work itself. It watches, waits for changes to settle, and then asks the ordinary
:class:`~androsync.daemon.jobs.JobRunner` for a normal job, so the planner, the safety checks, the undo journal and the
Stop button all apply unchanged. The rules that keep it safe:

* the first sync of a pair is never automatic (a person must have run it once);
* it never confirms a big plan: a plan that needs the user's OK pauses that pair's automation and says so;
* three failed automatic syncs in a row pause the pair; a manual sync that works resumes it;
* a phone that is not connected is waited for, not counted as a failure;
* at least :data:`MIN_GAP_S` seconds between two automatic syncs of the same pair, and one job at a time.

The laptop folder is watched with ``watchdog`` when it is installed (``pip install 'androsync[watch]'``), else by looking at
the folder every 30 seconds. The phone cannot report changes through adb, so every ``auto_interval_s`` seconds a check
sync is made (it changes nothing and leaves no trace when nothing differs).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable

from androsync import state
from androsync.config import Config, PairConfig
from androsync.daemon.jobs import Job, JobBusy, JobRunner

_LOG = logging.getLogger(__name__)

#: Fewest seconds between two automatic syncs of one pair.
MIN_GAP_S = 60.0
#: Automatic syncs that may fail in a row before the pair is paused.
MAX_FAILURES = 3
#: How often the folder is looked at when ``watchdog`` is not installed.
POLL_INTERVAL_S = 30.0
_SKIP_DIRS = {".androsync", ".git"}


# --------------------------------------------------------------------------------------------------------------
# watching a folder
# --------------------------------------------------------------------------------------------------------------


def folder_signature(root: str) -> tuple[int, int, float]:
    """Cheap fingerprint of a folder tree: (files, total size, newest modified time). Skips ``.androsync`` and ``.git``."""
    count = size = 0
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            try:
                st = os.stat(os.path.join(dirpath, name))
            except OSError:
                continue
            count += 1
            size += st.st_size
            newest = max(newest, st.st_mtime)
    return count, size, newest


class PollingWatcher(threading.Thread):
    """Calls ``on_change`` when the folder's fingerprint differs from the last look."""

    def __init__(self, root: str, on_change: Callable[[], None], interval: float = POLL_INTERVAL_S) -> None:
        super().__init__(daemon=True, name="androsync-poll-watch")
        self._root = root
        self._on_change = on_change
        self._interval = interval
        self._stop_event = threading.Event()
        self._last: tuple[int, int, float] | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                current = folder_signature(self._root) if os.path.isdir(self._root) else None
            except OSError:
                current = None
            if self._last is not None and current != self._last:
                self._on_change()
            self._last = current
            self._stop_event.wait(self._interval)


class WatchdogWatcher:
    """Instant change events through ``watchdog`` (inotify on Linux)."""

    def __init__(self, root: str, on_change: Callable[[], None]) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event) -> None:  # noqa: ANN001 - watchdog's own type
                path = str(getattr(event, "src_path", ""))
                if not any(f"{os.sep}{skip}" in path for skip in _SKIP_DIRS):
                    on_change()

        self._observer = Observer()
        self._observer.schedule(Handler(), root, recursive=True)

    def start(self) -> None:
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()


def make_watcher(root: str, on_change: Callable[[], None]):
    """The best available watcher for ``root``: ``watchdog`` if installed, else polling."""
    try:
        return WatchdogWatcher(root, on_change)
    except Exception:  # noqa: BLE001 - not installed, or the folder cannot be watched: fall back
        return PollingWatcher(root, on_change)


# --------------------------------------------------------------------------------------------------------------
# deciding when to sync
# --------------------------------------------------------------------------------------------------------------


@dataclass
class _PairAuto:
    last_change: float | None = None
    last_start: float = -1e9
    failures: int = 0
    paused: str | None = None
    next_phone_check: float | None = None
    job_id: str | None = None


def default_first_run_done(pair: PairConfig) -> bool:
    """A pair has been synced by hand once if it has a recorded last run (the first sync is never automatic)."""
    return bool(state.latest_meta(pair.name).get("last_run"))


class AutoSync:
    def __init__(
        self,
        config_loader: Callable[[], Config],
        runner: JobRunner,
        *,
        clock: Callable[[], float] = time.monotonic,
        phone_available: Callable[[PairConfig], bool] = lambda pair: True,
        notify: Callable[[str, str], None] = lambda title, body: None,
        watcher_factory: Callable[[str, Callable[[], None]], object] = make_watcher,
        first_run_done: Callable[[PairConfig], bool] = default_first_run_done,
    ) -> None:
        self._config_loader = config_loader
        self._runner = runner
        self._clock = clock
        self._phone_available = phone_available
        self._notify = notify
        self._watcher_factory = watcher_factory
        self._first_run_done = first_run_done
        self._lock = threading.RLock()
        self._pairs: dict[str, _PairAuto] = {}
        self._watchers: dict[str, tuple[str, object]] = {}  # pair name -> (folder, watcher)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        runner.add_listener(self.on_job_finished)

    # -- lifecycle -------------------------------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="androsync-auto")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        with self._lock:
            for _folder, watcher in self._watchers.values():
                self._stop_watcher(watcher)
            self._watchers.clear()

    def _loop(self) -> None:
        while not self._stop_event.wait(1.0):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - automatic sync must never take the service down
                _LOG.warning("androsync: automatic sync tick failed", exc_info=True)

    # -- events ----------------------------------------------------------------------------------------------

    def mark_dirty(self, pair_name: str) -> None:
        """Something changed in ``pair_name``: sync it once the changes have settled."""
        with self._lock:
            self._st(pair_name).last_change = self._clock()

    def mark_root_dirty(self, root: str) -> None:
        """The phone app saw a change under ``root`` (an agent event): mark the agent pairs that cover it."""
        config = self._config_loader()
        for pair in config.pairs:
            if pair.auto and pair.transport == "agent" and (root == pair.remote or root.startswith(pair.remote.rstrip("/") + "/")):
                self.mark_dirty(pair.name)

    def _st(self, name: str) -> _PairAuto:
        return self._pairs.setdefault(name, _PairAuto())

    # -- the decision ----------------------------------------------------------------------------------------

    def tick(self) -> None:
        """Look at every automatic pair and start at most one sync. Called every second; public so tests drive it."""
        config = self._config_loader()
        now = self._clock()
        self._reconcile_watchers(config)
        with self._lock:
            for pair in config.pairs:
                if not (pair.auto and pair.enabled):
                    continue
                st = self._st(pair.name)
                if st.paused:
                    continue
                if st.next_phone_check is None:
                    st.next_phone_check = now + pair.auto_interval_s
                elif now >= st.next_phone_check:
                    st.next_phone_check = now + pair.auto_interval_s
                    if st.last_change is None:
                        st.last_change = now - pair.auto_delay_s  # a look at the phone is due: check it now
                if st.last_change is None or now - st.last_change < pair.auto_delay_s:
                    continue
                if now - st.last_start < MIN_GAP_S or self._runner.current() is not None:
                    continue
                if not self._first_run_done(pair) or not self._phone_available(pair):
                    continue
                try:
                    job = self._runner.submit([pair.name], origin="auto")
                except (JobBusy, KeyError):
                    continue
                st.last_start, st.last_change, st.job_id = now, None, job.id
                _LOG.info("androsync: automatic sync of %r started (job %s)", pair.name, job.id)
                return  # one job at a time

    def on_job_finished(self, job: Job) -> None:
        """Learn from every finished job: count automatic failures, pause on a plan that needs the user, resume."""
        if job.dry_run or job.kind != "sync":
            return
        with self._lock:
            for name in job.pairs:
                st = self._st(name)
                if job.origin == "auto":
                    self._after_auto_job(name, st, job)
                elif job.state == "done":
                    if st.paused:
                        _LOG.info("androsync: automatic sync of %r resumed after a manual sync", name)
                    st.paused, st.failures = None, 0

    def _after_auto_job(self, name: str, st: _PairAuto, job: Job) -> None:
        if job.state == "done":
            st.failures = 0
        elif job.state == "needs_confirmation":
            reasons = " ".join(job.summaries[name].confirmation) if name in job.summaries else ""
            self._pause(name, st, f"needs your OK before it changes anything. {reasons}".strip())
        elif job.state == "failed":
            st.failures += 1
            if st.failures >= MAX_FAILURES:
                self._pause(name, st, f"paused after {MAX_FAILURES} failed automatic syncs: {job.error or 'see the log'}")
        elif job.state == "cancelled":
            self._pause(name, st, "stopped by you; sync it by hand once to resume")

    def _pause(self, name: str, st: _PairAuto, reason: str) -> None:
        st.paused = reason
        _LOG.warning("androsync: automatic sync of %r paused: %s", name, reason)
        self._notify("AndroSync", f"Automatic sync of {name} is paused: {reason}")

    # -- what the pair list shows ----------------------------------------------------------------------------

    def status(self, pair_name: str) -> dict:
        """``{"state": "off"|"watching"|"waiting"|"paused", "reason": str|None}`` for ``GET /v1/pairs``."""
        config = self._config_loader()
        pair = next((p for p in config.pairs if p.name == pair_name), None)
        if pair is None or not pair.auto:
            return {"state": "off", "reason": None}
        st = self._pairs.get(pair_name)
        if st is not None and st.paused:
            return {"state": "paused", "reason": st.paused}
        if st is not None and st.last_change is not None:
            return {"state": "waiting", "reason": None}
        return {"state": "watching", "reason": None}

    # -- watchers --------------------------------------------------------------------------------------------

    def _reconcile_watchers(self, config: Config) -> None:
        wanted = {p.name: str(p.local) for p in config.pairs if p.auto and p.enabled}
        with self._lock:
            for name in [n for n in self._watchers if n not in wanted or self._watchers[n][0] != wanted[n]]:
                self._stop_watcher(self._watchers.pop(name)[1])
            for name, folder in wanted.items():
                if name in self._watchers:
                    continue
                try:
                    watcher = self._watcher_factory(folder, lambda n=name: self.mark_dirty(n))
                    watcher.start()
                except Exception:  # noqa: BLE001 - one folder that cannot be watched must not stop the others
                    _LOG.warning("androsync: cannot watch %s", folder, exc_info=True)
                    continue
                self._watchers[name] = (folder, watcher)

    @staticmethod
    def _stop_watcher(watcher: object) -> None:
        try:
            watcher.stop()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
