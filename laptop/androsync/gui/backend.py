"""Bridges GUI actions to either the daemon API or the sync engine directly.

PLAN.md Phase J: "Runs go through the daemon API when it is running (so the
phone app sees them too) and through `run_pair` directly when it is not."
Every action that can block (a sync, an undo, a preview) runs on its own
background thread and reports back through a thread-safe `queue.Queue` of
``(kind, ...)`` tuples that ``gui/app.py`` drains via ``root.after`` - the
window itself must never block waiting on ADB or the network.

Only one action runs at a time (mirroring the daemon's own one-job-at-a-time
rule): callers check :attr:`Backend.busy` before starting another.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

from androsync import config_edit, undo as undo_mod
from androsync.apiclient import ApiClient, ApiError
from androsync.cli import open_pair_transports
from androsync.config import Config
from androsync.daemon.jobs import pairs_status
from androsync.executor import ExecutorError, run_pair
from androsync.service_state import ServiceState

#: Events pushed to the queue: ("log", line) | ("done", ok, message) | ("pairs_changed",)
Event = tuple

_JOB_POLL_INTERVAL_S = 1.0

#: The states in which the daemon is done with a job. ("failed" used to be missing here, issue #27, so the
#: window kept polling a failed job forever and stayed busy.)
TERMINAL_JOB_STATES = ("done", "failed", "cancelled", "needs_confirmation")


def list_pairs(config: Config) -> list[dict]:
    """Always a direct, local read - correct regardless of whether a daemon is running."""
    return pairs_status(config)


def reachable_client(config: Config, state: ServiceState) -> ApiClient | None:
    """An :class:`ApiClient` for the locally-running daemon, or ``None`` if there isn't one."""
    if not state.running or state.ping is None or not config.service.token:
        return None
    return ApiClient(f"http://127.0.0.1:{config.service.port}", config.service.token)


class Backend:
    """Owns the one background worker thread the Pairs panel's actions run on."""

    def __init__(self) -> None:
        self.events: queue.Queue[Event] = queue.Queue()
        self._thread: threading.Thread | None = None
        #: Id of the daemon job this window is currently following (started here or by another device).
        self.following_job_id: str | None = None
        #: Set while a sync runs in this process without a daemon; Stop uses it (task 36b).
        self._direct_stop: threading.Event | None = None
        self._direct_transports: tuple = ()

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def running_directly(self) -> bool:
        """A sync is running in this process (no daemon), so Stop must act on it here."""
        return self.busy and self._direct_stop is not None

    def _start(self, target: Callable[[], None]) -> None:
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def _log(self, line: str) -> None:
        self.events.put(("log", line))

    def _done(self, ok: bool, message: str) -> None:
        self.events.put(("done", ok, message))
        self.events.put(("pairs_changed",))

    # -- sync -----------------------------------------------------------------

    def run(
        self,
        config: Config,
        state: ServiceState,
        pair_name: str | None,
        *,
        dry_run: bool = False,
        confirm_large: bool = False,
        pairs: list[str] | None = None,
    ) -> None:
        """Start a sync. ``pairs`` (several names) wins over ``pair_name``; ``confirm_large`` is the user's OK
        for a plan the safety check stopped (task 36d, issue #9)."""
        if self.busy:
            return
        client = reachable_client(config, state)
        if client is not None:
            self._start(lambda: self._run_via_api(client, pair_name, dry_run, confirm_large, pairs))
        else:
            self._start(lambda: self._run_direct(config, pair_name, dry_run, confirm_large, pairs))

    def _run_via_api(
        self, client: ApiClient, pair_name: str | None, dry_run: bool, confirm_large: bool, pairs: list[str] | None
    ) -> None:
        try:
            response = client.run(pair_name, dry_run=dry_run, confirm_large=confirm_large, pairs=pairs)
            job_id = response["job_id"]
        except ApiError as exc:
            self._log(f"error: {exc.message}")
            self._done(False, exc.message)
            return
        self._finish_followed(client, job_id)

    # -- following a daemon job (started here or on another device) -------------------

    def follow(self, client: ApiClient, job_id: str) -> None:
        """Show the live log of a job somebody else started (the phone, a script) and track it to the end."""
        if self.busy:
            return
        self._start(lambda: self._finish_followed(client, job_id))

    def _finish_followed(self, client: ApiClient, job_id: str) -> None:
        self.following_job_id = job_id
        try:
            job = self._follow(client, job_id)
        except ApiError as exc:
            self._log(f"error: {exc.message}")
            self._done(False, exc.message)
            return
        finally:
            self.following_job_id = None
        self._report_confirmation(job.get("confirmation") or {})
        ok = job.get("state") == "done" and not job.get("error")
        self._done(ok, job.get("error") or job.get("state", ""))

    def _report_confirmation(self, confirmation: dict[str, list[str]]) -> None:
        """A run was stopped before changing anything: ask the window to offer "Go ahead" (task 36d, issue #9)."""
        if confirmation:
            self.events.put(("confirm", list(confirmation), [f"{n}: {line}" for n, ls in confirmation.items() for line in ls]))

    def _follow(self, client: ApiClient, job_id: str) -> dict:
        """Print the job's full log as it is written and return the finished job (a dict from ``GET /v1/jobs/<id>``)."""
        try:
            for kind, value in client.stream_job_log(job_id):
                if kind == "line":
                    self._log(value)
                else:
                    break
        except (OSError, ValueError, ApiError):
            # The stream broke (daemon restarted, network hiccup): fall back to polling the last lines.
            return self._poll_until_terminal(client, job_id)
        job = client.job(job_id)
        deadline = time.monotonic() + 15
        while job.get("state") not in TERMINAL_JOB_STATES and time.monotonic() < deadline:
            time.sleep(0.3)
            job = client.job(job_id)
        return job

    def _poll_until_terminal(self, client: ApiClient, job_id: str) -> dict:
        seen = 0
        while True:
            job = client.job(job_id)
            tail = job.get("log_tail") or []
            for line in tail[seen:]:
                self._log(line)
            seen = len(tail)
            if job.get("state") in TERMINAL_JOB_STATES:
                return job
            time.sleep(_JOB_POLL_INTERVAL_S)

    # -- Stop ---------------------------------------------------------------------------

    def stop(self, client: ApiClient | None, job_id: str | None) -> None:
        """The Stop button: cancel the daemon's job, or the sync running in this process. Never blocks the window."""
        if self.running_directly:
            self._direct_stop.set()
            for transport in self._direct_transports:
                try:
                    transport.abort()
                except Exception:  # noqa: BLE001 - best effort; the flag is also checked between actions
                    pass
            self._log("stopping...")
            return
        if client is None or not job_id:
            return

        def work() -> None:
            try:
                client.cancel(job_id)
                self._log("stopping...")
            except ApiError as exc:
                self._log(f"error: could not stop: {exc.message}")

        threading.Thread(target=work, daemon=True).start()

    def _run_direct(
        self, config: Config, pair_name: str | None, dry_run: bool, confirm_large: bool = False, pairs: list[str] | None = None
    ) -> None:
        stop = threading.Event()
        self._direct_stop = stop
        try:
            self._run_direct_inner(config, pair_name, dry_run, stop, confirm_large, pairs)
        finally:
            self._direct_stop = None
            self._direct_transports = ()

    def _run_direct_inner(
        self,
        config: Config,
        pair_name: str | None,
        dry_run: bool,
        stop: threading.Event,
        confirm_large: bool,
        names: list[str] | None,
    ) -> None:
        if names is not None:
            pairs = [p for p in config.pairs if p.name in names]
        else:
            pairs = [p for p in config.pairs if p.name == pair_name] if pair_name else list(config.pairs)
        if pair_name and not pairs:
            self._log(f"error: unknown pair {pair_name!r}")
            self._done(False, "unknown pair")
            return
        # Pairs set to the phone app's agent need the running service (task 37d); this direct path has no link to it.
        for agent_pair in [p for p in pairs if p.transport == "agent"]:
            self._log(f"== {agent_pair.name} == skipped: it uses the phone app's agent; run the service first")
        pairs = [p for p in pairs if p.transport != "agent"]
        if not pairs:
            self._done(False, "nothing to run directly")
            return
        try:
            local, remote = open_pair_transports(config, log=self._log)
        except Exception as exc:  # noqa: BLE001 - reported to the log panel, not raised into Tk
            self._log(f"error: {exc}")
            self._done(False, str(exc))
            return
        self._direct_transports = (local, remote)

        had_issue = False
        blocked: dict[str, list[str]] = {}
        for pair in pairs:
            self._log(f"== {pair.name} ==")
            if stop.is_set():
                break
            try:
                summary = run_pair(
                    pair, local, remote, dry_run=dry_run, log=self._log, cancel=stop.is_set, confirm_large=confirm_large
                )
            except ExecutorError as exc:
                self._log(f"error: {exc}")
                had_issue = True
                continue
            self._log(str(summary))
            if summary.needs_confirmation and not dry_run:
                blocked[pair.name] = list(summary.confirmation)
            if summary.errors or summary.asks:
                had_issue = True
        self._report_confirmation(blocked)
        self._done(not had_issue and not stop.is_set() and not blocked, "cancelled" if stop.is_set() else "done")

    # -- undo -------------------------------------------------------------------

    def undo(
        self,
        config: Config,
        state: ServiceState,
        pair_name: str,
        run_id: str | None,
        *,
        force: bool = False,
        pause: bool = False,
    ) -> None:
        if self.busy:
            return
        client = reachable_client(config, state)
        if client is not None:
            self._start(lambda: self._undo_via_api(client, pair_name, run_id, force, pause))
        else:
            self._start(lambda: self._undo_direct(config, pair_name, run_id, force, pause))

    def _undo_via_api(self, client: ApiClient, pair_name: str, run_id: str | None, force: bool, pause: bool) -> None:
        try:
            response = client.undo(pair_name, run_id=run_id, force=force, pause=pause)
            job_id = response["job_id"]
        except ApiError as exc:
            self._log(f"error: {exc.message}")
            self._done(False, exc.message)
            return

        self.following_job_id = job_id
        try:
            job = self._follow(client, job_id)
        except ApiError as exc:
            self._log(f"error: {exc.message}")
            self._done(False, exc.message)
            return
        finally:
            self.following_job_id = None
        summary = job.get("undo_summary") or {}
        self._log(
            f"restored {summary.get('restored_local', 0)} file(s) on laptop, "
            f"{summary.get('restored_remote', 0)} on phone"
        )
        ok = job.get("state") == "done" and not job.get("error") and not summary.get("errors")
        self._done(ok, job.get("error") or job.get("state", ""))

    def _undo_direct(self, config: Config, pair_name: str, run_id: str | None, force: bool, pause: bool) -> None:
        pair = next((p for p in config.pairs if p.name == pair_name), None)
        if pair is None:
            self._log(f"error: unknown pair {pair_name!r}")
            self._done(False, "unknown pair")
            return

        if pair.transport == "agent":
            self._log("error: this pair uses the phone app's agent; undo it while the service is running")
            self._done(False, "agent pair")
            return

        resolved_run_id = run_id
        if resolved_run_id is None:
            runs = undo_mod.list_runs_all(pair.name)
            if not runs:
                self._log("error: no runs recorded for this pair")
                self._done(False, "no runs")
                return
            resolved_run_id = runs[0]["run_id"]

        try:
            local, remote = open_pair_transports(config, log=self._log)
            summary = undo_mod.undo_run(pair, resolved_run_id, local, remote, force=force, log=self._log)
        except Exception as exc:  # noqa: BLE001 - reported to the log panel, not raised into Tk
            self._log(f"error: {exc}")
            self._done(False, str(exc))
            return

        self._log(f"restored {summary.restored_local} file(s) on laptop, {summary.restored_remote} on phone")
        if pause:
            _pause_pair(config, pair.name)
        self._done(not summary.errors, "done")


def _pause_pair(config: Config, name: str) -> None:
    """Set ``enabled: false`` on ``name``, merging so no other field is lost (see task 30b)."""
    from androsync.config import default_config_path

    config_path = default_config_path()
    raw = config_edit.load_raw(config_path)
    current = next((p for p in raw.get("pairs") or [] if p.get("name") == name), None)
    pair_dict = dict(current) if current is not None else {"name": name}
    pair_dict["enabled"] = False
    raw = config_edit.update_pair(raw, name, pair_dict)
    config_edit.save_raw(config_path, raw)
