"""Background job runner: one sync run at a time, with a live log and status.

``JobRunner`` is the piece the HTTP API (``daemon/server.py``, a later
task) and the CLI's ``run`` command sit on top of. ``submit`` starts a
sync of one or more pairs on a daemon thread and returns immediately with
a :class:`Job` handle; the thread calls :func:`androsync.executor.run_pair`
for each requested pair in turn, feeding it a :class:`LogBuffer` to log
into and a cancel flag it polls between actions.

Only one job may be queued or running at a time - ``submit`` raises
:class:`JobBusy` otherwise - matching the "409 if a job is running"
behaviour the HTTP API is expected to expose.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable, Literal
from uuid import uuid4

from androsync import state
from androsync.config import Config, PairConfig
from androsync.daemon.problems import explain_failure
from androsync.executor import run_pair
from androsync.model import Side, Summary
from androsync.runlog import RunLog
from androsync.runlog import tee as _tee_log
from androsync.transport.agent import AgentBroker, AgentTransport
from androsync.transport.base import Transport, TransportCancelled, TransportError
from androsync.transport.local import LocalDirTransport
from androsync.undo import UndoError, list_runs_all, undo_run

_LOG = logging.getLogger(__name__)

JobState = Literal["queued", "running", "cancelling", "done", "failed", "cancelled", "needs_confirmation"]
JobKind = Literal["sync", "undo"]

_ACTIVE_STATES = ("queued", "running", "cancelling")

#: How long a Stop may take before the job is shown as cancelled anyway (the abort normally lands in under 2 s).
CANCEL_GRACE_S = 10.0

# How many most-recent jobs JobRunner keeps in memory (older ones are dropped).
_MAX_JOBS = 50

# LogBuffer ring size.
_MAX_LOG_LINES = 5000


class JobBusy(Exception):
    """Raised by ``JobRunner.submit`` when a job is already queued or running."""


class LogBuffer:
    """Thread-safe ring buffer of ``(seq, line)`` pairs with blocking reads.

    ``append`` is meant to be handed straight to :func:`run_pair` as its
    ``log`` callback. ``wait`` lets an SSE streamer block until a line past
    a given sequence number shows up, instead of polling.
    """

    def __init__(self, maxlen: int = _MAX_LOG_LINES) -> None:
        self._lines: deque[tuple[int, str]] = deque(maxlen=maxlen)
        self._next_seq = 0
        self._cond = threading.Condition()

    def append(self, line: str) -> None:
        """Record ``line``, prefixed with a ``HH:MM:SS `` timestamp."""
        stamped = f"{datetime.now():%H:%M:%S} {line}"
        with self._cond:
            self._lines.append((self._next_seq, stamped))
            self._next_seq += 1
            self._cond.notify_all()

    def since(self, seq: int) -> list[tuple[int, str]]:
        """Return every recorded line with sequence number >= ``seq``.

        A ``seq`` older than the oldest line still held (because the ring
        wrapped) simply yields everything that is left - callers cannot
        tell they missed lines this way, which matches a plain log tail.
        """
        with self._cond:
            return [entry for entry in self._lines if entry[0] >= seq]

    def tail(self, n: int) -> list[tuple[int, str]]:
        """Return the last ``n`` recorded lines (fewer if there aren't that many)."""
        with self._cond:
            if n <= 0:
                return []
            return list(self._lines)[-n:]

    def wait(self, seq: int, timeout: float | None = None) -> list[tuple[int, str]]:
        """Block until a line with sequence number >= ``seq`` exists, then return them.

        Returns immediately (no blocking) if such a line already exists.
        Returns an empty list if ``timeout`` elapses with nothing new.
        """
        with self._cond:
            pending = [entry for entry in self._lines if entry[0] >= seq]
            if pending:
                return pending
            self._cond.wait(timeout)
            return [entry for entry in self._lines if entry[0] >= seq]


@dataclass
class Job:
    """One requested sync (or undo) run and its live/finished status."""

    id: str
    pairs: list[str]
    dry_run: bool
    state: JobState = "queued"
    kind: JobKind = "sync"
    #: Set only for kind="undo": the run being undone, and whether to force past drift checks.
    undo_run_id: str | None = None
    undo_force: bool = False
    #: The user has confirmed a plan the safety check would otherwise stop (task 36d, issue #9).
    confirm_large: bool = False
    #: "manual" (a person asked) or "auto" (automatic sync noticed a change, Phase T2).
    origin: str = "manual"
    created: float = 0.0
    started: float | None = None
    finished: float | None = None
    summaries: dict[str, Summary] = field(default_factory=dict)
    #: Set only for kind="undo", once it finishes: the UndoSummary as a plain dict.
    undo_summary: dict | None = None
    error: str | None = None
    log: LogBuffer = field(default_factory=LogBuffer)
    # Set by JobRunner.cancel(); polled by the run loop and passed to run_pair
    # as its ``cancel`` callback so it is also checked between actions.
    cancel_requested: bool = False
    #: The two transports of a running job, so :meth:`JobRunner.cancel` can abort a call in flight (task 36a).
    transports: list = field(default_factory=list)


TransportFactory = Callable[[Config], tuple[Transport, Transport]]
ConfigLoader = Callable[[], Config]


def _mark_pair_failed(pair_name: str, exc: Exception) -> None:
    """Record a failed attempt in the pair's meta (task 32c), so it is visible
    without reading daemon.log: the Android app and GUI pairs panels show
    ``last_result`` directly, and previously a failure here just left
    whatever ``last_result`` the last *successful* run wrote, silently
    hiding that the most recent attempt actually failed.
    """
    state.save_pair_meta(
        pair_name,
        {"last_run": datetime.now().isoformat(), "last_result": f"FAILED: {exc}", "last_error": str(exc)},
    )


@dataclass
class OpenedTransports:
    """The transports a job (or a preview) needs: the laptop side plus a phone side per way of reaching a phone.

    A pair with ``transport: adb`` uses the adb transport of the phone it is bound to (``device``, "any" by default),
    one with ``transport: agent`` uses :attr:`agent_remote` (task 37d, issue #16; per phone: task 33a). A job that
    only has agent pairs never opens adb, so it works with no phone attached.
    """

    local: Transport
    #: adb transports by the pair's ``device`` value ("any" or a phone's serial).
    adb_remotes: dict[str, Transport] = field(default_factory=dict)
    agent_remote: Transport | None = None
    #: Why adb could not be opened for a device, when some other pair in the same job did not need it.
    adb_errors: dict[str, Exception] = field(default_factory=dict)

    @property
    def adb_error(self) -> Exception | None:
        return next(iter(self.adb_errors.values()), None)

    def remote_for(self, pair: PairConfig) -> Transport:
        if pair.transport == "agent":
            if self.agent_remote is None:
                raise TransportError("agent mode is not available here")
            return self.agent_remote
        device = pair.device or "any"
        remote = self.adb_remotes.get(device)
        if remote is None:
            raise self.adb_errors.get(device) or TransportError("no connection to the phone")
        return remote

    def all(self) -> list[Transport]:
        return [t for t in (self.local, *self.adb_remotes.values(), self.agent_remote) if t is not None]


class JobRunner:
    """Runs sync jobs one at a time on a background thread."""

    def __init__(
        self,
        config_loader: ConfigLoader,
        transport_factory: TransportFactory,
        agent_broker: AgentBroker | None = None,
        device_transport_factory: Callable[[Config, str], tuple[Transport, Transport]] | None = None,
    ) -> None:
        self._config_loader = config_loader
        self._transport_factory = transport_factory
        self._agent_broker = agent_broker
        #: Opens the adb transport of one named phone, for pairs bound to it (task 33a).
        self._device_transport_factory = device_transport_factory
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []  # oldest -> newest job id
        self._current_id: str | None = None
        self._listeners: list[Callable[[Job], None]] = []

    def submit(
        self,
        pairs: list[str] | None = None,
        dry_run: bool = False,
        confirm_large: bool = False,
        origin: str = "manual",
    ) -> Job:
        """Start a job syncing ``pairs`` (``None`` = every configured pair).

        Raises ``JobBusy`` if a job is already queued or running, or
        ``KeyError`` naming the first pair in ``pairs`` that is not in the
        current config. ``confirm_large=True`` lets a plan through that the
        safety check (``safety.py``) would otherwise stop and report.
        """
        with self._lock:
            self._raise_if_busy()
            config = self._config_loader()
            known = [p.name for p in config.pairs]
            if pairs is None:
                selected = list(known)
            else:
                for name in pairs:
                    if name not in known:
                        raise KeyError(name)
                selected = list(pairs)

            job = Job(
                id=uuid4().hex[:8],
                pairs=selected,
                dry_run=dry_run,
                confirm_large=confirm_large,
                origin=origin,
                created=time.time(),
            )
            return self._register_and_start(job)

    def submit_undo(
        self, pair: str, run_id: str | None = None, *, force: bool = False, dry_run: bool = False
    ) -> Job:
        """Start a job undoing ``run_id`` (``None`` = the pair's most recent run) for ``pair``.

        Raises ``JobBusy`` if a job is already queued or running,
        ``KeyError`` if ``pair`` is not in the current config, or
        ``UndoError`` if ``run_id`` is omitted and the pair has no runs yet.
        """
        with self._lock:
            self._raise_if_busy()
            config = self._config_loader()
            if pair not in {p.name for p in config.pairs}:
                raise KeyError(pair)

            resolved_run_id = run_id
            if resolved_run_id is None:
                runs = list_runs_all(pair)
                if not runs:
                    raise UndoError(f"no runs recorded for pair {pair!r}")
                resolved_run_id = runs[0]["run_id"]

            job = Job(
                id=uuid4().hex[:8],
                pairs=[pair],
                dry_run=dry_run,
                kind="undo",
                undo_run_id=resolved_run_id,
                undo_force=force,
                created=time.time(),
            )
            return self._register_and_start(job)

    def _raise_if_busy(self) -> None:
        """Caller must hold ``self._lock``."""
        current = self._jobs.get(self._current_id) if self._current_id else None
        if current is not None and current.state in _ACTIVE_STATES:
            raise JobBusy(f"job {current.id} is {current.state}")

    def _register_and_start(self, job: Job) -> Job:
        """Caller must hold ``self._lock``."""
        self._jobs[job.id] = job
        self._order.append(job.id)
        while len(self._order) > _MAX_JOBS:
            oldest = self._order.pop(0)
            self._jobs.pop(oldest, None)
        self._current_id = job.id

        thread = threading.Thread(target=self._run, args=(job,), daemon=True, name=f"androsync-job-{job.id}")
        thread.start()
        return job

    def cancel(self, job_id: str) -> None:
        """Ask the job ``job_id`` to stop. No-op if it does not exist or already finished.

        The job moves to ``cancelling``, any adb call in flight is killed, and the run ends as ``cancelled``
        within seconds. If the job thread still has not finished after ``CANCEL_GRACE_S`` the job is shown as
        ``cancelled`` anyway; its transports are already aborted, so it can do no further work on the phone.
        """
        job = self.get(job_id)
        if job is None or job.state not in _ACTIVE_STATES:
            return
        first_request = not job.cancel_requested
        job.cancel_requested = True
        if job.state in ("queued", "running"):
            job.state = "cancelling"
        self._abort_transports(job)
        if first_request:
            timer = threading.Timer(CANCEL_GRACE_S, self._force_cancelled, args=(job,))
            timer.daemon = True
            timer.start()

    @staticmethod
    def _abort_transports(job: Job) -> None:
        for transport in list(job.transports):
            try:
                transport.abort()
            except Exception:  # noqa: BLE001 - best effort; the cancel flag is checked between actions anyway
                _LOG.debug("androsync: abort failed for job %s", job.id, exc_info=True)

    @staticmethod
    def _force_cancelled(job: Job) -> None:
        if job.state == "cancelling":
            job.state = "cancelled"
            job.finished = job.finished or time.time()
            job.log.append("stopped: the sync did not answer in time and was cut off")
            _LOG.warning("androsync: job %s still busy %.0f s after Stop; marked cancelled", job.id, CANCEL_GRACE_S)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def current(self) -> Job | None:
        """Return the queued/running job, or None if the runner is idle."""
        with self._lock:
            job = self._jobs.get(self._current_id) if self._current_id else None
        if job is not None and job.state in _ACTIVE_STATES:
            return job
        return None

    def list(self, limit: int = 20) -> list[Job]:
        """Return up to ``limit`` most-recently-submitted jobs, newest first."""
        with self._lock:
            ids = list(reversed(self._order))
        return [self._jobs[job_id] for job_id in ids[:limit] if job_id in self._jobs]

    def open_transports(self, config: Config, pairs: list[PairConfig | None]) -> OpenedTransports:
        """Open what ``pairs`` need (``None`` = a pair that is no longer configured, treated as adb, any phone).

        Raises the adb error when adb is needed, cannot be opened for any phone and no agent pair could still run.
        """
        needs_agent = any(p is not None and p.transport == "agent" for p in pairs)
        devices = {(p.device if p is not None else "any") or "any" for p in pairs if p is None or p.transport != "agent"}
        if not pairs:
            devices = {"any"}
        local: Transport | None = None
        adb_remotes: dict[str, Transport] = {}
        adb_errors: dict[str, Exception] = {}
        for device in sorted(devices):
            try:
                if device == "any":
                    opened_local, remote = self._transport_factory(config)
                elif self._device_transport_factory is not None:
                    opened_local, remote = self._device_transport_factory(config, device)
                else:
                    raise TransportError(f"this daemon cannot open a specific phone ({device!r})")
            except Exception as exc:  # noqa: BLE001 - kept for the pairs that need it
                adb_errors[device] = exc
                continue
            local = local if local is not None else opened_local
            adb_remotes[device] = remote
        if adb_errors and not adb_remotes and not needs_agent:
            raise next(iter(adb_errors.values()))
        agent_remote = AgentTransport(self._agent_broker) if needs_agent and self._agent_broker is not None else None
        return OpenedTransports(
            local=local if local is not None else LocalDirTransport(Side.LOCAL),
            adb_remotes=adb_remotes,
            agent_remote=agent_remote,
            adb_errors=adb_errors,
        )

    def _explain(self, pair_transport: str, exc: Exception) -> Exception:
        """``exc``, or a copy whose text starts with the wrong-connection sentence (Phase V3, see ``problems``)."""
        broker = self._agent_broker
        if broker is None:
            return exc
        text = explain_failure(
            str(exc),
            pair_transport=pair_transport,
            agent_connected=broker.connected(),
            agent_route=broker.route(),
        )
        return exc if text == str(exc) else TransportError(text)

    def add_listener(self, listener: Callable[[Job], None]) -> None:
        """Call ``listener(job)`` whenever a job has finished (any outcome). Used by automatic sync (Phase T2)."""
        self._listeners.append(listener)

    def _run(self, job: Job) -> None:
        try:
            self._run_inner(job)
        finally:
            if job.origin == "auto" and job.state == "done" and _changed_nothing(job):
                # A quiet automatic check must not push real syncs out of the list of recent jobs.
                with self._lock:
                    self._jobs.pop(job.id, None)
                    if job.id in self._order:
                        self._order.remove(job.id)
            for listener in list(self._listeners):
                try:
                    listener(job)
                except Exception:  # noqa: BLE001 - a listener must never break the runner
                    _LOG.warning("androsync: a job listener failed", exc_info=True)

    def _run_inner(self, job: Job) -> None:
        job.started = time.time()
        if job.state == "queued":  # a Stop pressed before the thread started leaves it at "cancelling"
            job.state = "running"

        run_log = RunLog()
        log = _tee_log(job.log.append, run_log)

        try:
            config = self._config_loader()
        except Exception as exc:  # noqa: BLE001 - reported through job.error, not raised
            job.error = str(exc)
            job.state = "failed"
            log(f"ERROR: could not start job: {exc}")
            _LOG.warning("androsync: job %s could not load config: %s", job.id, exc)
            run_log.close()
            job.finished = time.time()
            return

        pair_by_name = {p.name: p for p in config.pairs}
        try:
            opened = self.open_transports(config, [pair_by_name.get(n) for n in job.pairs])
        except Exception as exc:  # noqa: BLE001 - reported through job.error, not raised
            exc = self._explain("adb", exc)
            job.error = str(exc)
            log(f"ERROR: could not start job: {exc}")
            _LOG.warning("androsync: job %s could not start (pairs %s): %s", job.id, job.pairs, exc)
            pair_names = job.pairs if job.pairs else [p.name for p in config.pairs]
            for name in pair_names:
                _mark_pair_failed(name, exc)
            run_log.close()
            job.finished = time.time()
            # Last, so whoever sees the job end (the app's pair list, a test) finds each pair's FAILED result already saved.
            job.state = "failed"
            return

        job.transports = opened.all()
        for device, error in opened.adb_errors.items():
            which = "the phone" if device == "any" else f"the phone {device}"
            log(f"ERROR: {which} cannot be reached over adb (pairs that need it will fail): {error}")
        if job.cancel_requested:  # Stop arrived while the transports were being opened
            self._abort_transports(job)

        try:
            if job.kind == "undo":
                self._run_undo(job, config, opened, log)
            else:
                self._run_sync(job, config, opened, log)
        finally:
            if job.state in _ACTIVE_STATES:  # never leave a finished job looking busy
                job.state = "cancelled" if job.cancel_requested else "failed"
            for transport in opened.all():
                try:
                    transport.close()
                except Exception:  # noqa: BLE001 - closing must never mask the job result
                    pass
            run_log.close()
            job.finished = time.time()

    def _run_sync(
        self, job: Job, config: Config, opened: OpenedTransports, log: Callable[[str], None]
    ) -> None:
        any_error = False
        pair_by_name = {p.name: p for p in config.pairs}
        for name in job.pairs:
            if job.cancel_requested:
                break

            pair = pair_by_name.get(name)
            if pair is None:
                # Config changed underneath a queued job; log and move on.
                log(f"ERROR {name}: pair no longer in config")
                job.error = f"pair no longer in config: {name}"
                any_error = True
                continue

            if not pair.enabled:
                # Phase I "--pause": a paused pair is skipped even when named
                # explicitly, unlike the CLI (see cli._skip_disabled) - the
                # daemon only ever runs unattended (auto-sync) or from the
                # app, neither of which should silently repeat whatever sync
                # a pause was meant to stop.
                log(f"skipped {name}: pair is paused (disabled)")
                continue

            try:
                summary = run_pair(
                    pair,
                    opened.local,
                    opened.remote_for(pair),
                    dry_run=job.dry_run,
                    log=log,
                    cancel=lambda: job.cancel_requested,
                    confirm_large=job.confirm_large,
                )
                job.summaries[name] = summary
            except Exception as exc:  # noqa: BLE001 - one pair failing must not abort the rest
                exc = self._explain(pair.transport, exc)
                log(f"ERROR {name}: {exc}")
                job.error = str(exc)
                any_error = True
                _LOG.warning("androsync: job %s pair %s failed: %s", job.id, name, exc)
                _mark_pair_failed(name, exc)

            if job.cancel_requested:
                break

        if job.cancel_requested:
            job.state = "cancelled"
            if not job.log.tail(1) or job.log.tail(1)[0][1].split(" ", 1)[-1] != "cancelled":
                log("cancelled")  # run_pair already says so when it was the one that stopped
        elif any_error:
            job.state = "failed"
        elif not job.dry_run and any(s.needs_confirmation for s in job.summaries.values()):
            job.state = "needs_confirmation"  # nothing was changed for those pairs; the user can confirm
        else:
            job.state = "done"

    def _run_undo(
        self, job: Job, config: Config, opened: OpenedTransports, log: Callable[[str], None]
    ) -> None:
        name = job.pairs[0]
        pair = next((p for p in config.pairs if p.name == name), None)
        if pair is None:
            log(f"ERROR {name}: pair no longer in config")
            job.error = f"pair no longer in config: {name}"
            job.state = "failed"
            return

        try:
            summary = undo_run(
                pair,
                job.undo_run_id,
                opened.local,
                opened.remote_for(pair),
                dry_run=job.dry_run,
                force=job.undo_force,
                log=log,
                cancel=lambda: job.cancel_requested,
            )
        except TransportCancelled:
            log("cancelled")
            job.state = "cancelled"
            return
        except UndoError as exc:
            log(f"ERROR {name}: {exc}")
            job.error = str(exc)
            _LOG.warning("androsync: job %s undo of pair %s failed: %s", job.id, name, exc)
            _mark_pair_failed(name, exc)
            job.state = "failed"  # after the pair's result is saved, see _run
            return
        except Exception as exc:  # noqa: BLE001 - reported through job.error, not raised
            log(f"ERROR {name}: {exc}")
            job.error = str(exc)
            _LOG.warning("androsync: job %s undo of pair %s failed: %s", job.id, name, exc)
            _mark_pair_failed(name, exc)
            job.state = "failed"
            return

        job.undo_summary = asdict(summary)
        if job.cancel_requested:
            job.state = "cancelled"
            log("cancelled")
        elif summary.errors:
            job.state = "failed"
        else:
            job.state = "done"


def _changed_nothing(job: Job) -> bool:
    """True when every pair of a finished sync job had nothing to do (no copies, deletes, conflicts or errors)."""
    if not job.summaries:
        return False
    for s in job.summaries.values():
        if (
            s.copied_to_local or s.copied_to_remote or s.deleted_local or s.deleted_remote
            or s.conflicts or s.asks or s.errors or s.needs_confirmation
        ):
            return False
    return True


def current_job_dict(job: Job | None) -> dict | None:
    """Small summary of the running job for ``GET /v1/ping`` (task 36b, issue #7), or ``None`` when idle.

    Every client polls the ping every few seconds, so a sync started on one device is visible, with its latest log
    line and a Stop button, on every other device. ``last_line`` has the ``HH:MM:SS`` stamp removed.
    """
    if job is None:
        return None
    tail = job.log.tail(1)
    last_line = tail[0][1].split(" ", 1)[-1] if tail else None
    return {
        "id": job.id,
        "pairs": list(job.pairs),
        "kind": job.kind,
        "origin": job.origin,
        "state": job.state,
        "dry_run": job.dry_run,
        "started": job.started,
        "last_line": last_line,
    }


def job_to_dict(job: Job) -> dict:
    """Render ``job`` as a JSON-safe dict, as returned by the HTTP API."""
    return {
        "id": job.id,
        "pairs": list(job.pairs),
        "dry_run": job.dry_run,
        "kind": job.kind,
        "state": job.state,
        "created": job.created,
        "started": job.started,
        "finished": job.finished,
        "summaries": {name: asdict(summary) for name, summary in job.summaries.items()},
        "run_ids": {name: summary.run_id for name, summary in job.summaries.items() if summary.run_id is not None},
        "undo_run_id": job.undo_run_id,
        "undo_summary": job.undo_summary,
        "error": job.error,
        "confirmation": {name: list(s.confirmation) for name, s in job.summaries.items() if s.needs_confirmation},
        "cancel_requested": job.cancel_requested,
        "log_tail": [line for _seq, line in job.log.tail(20)],
    }


def pairs_status(config: Config, auto_status: Callable[[str], dict] | None = None) -> list[dict]:
    """Return one status dict per configured pair, for ``GET /v1/pairs``.

    ``auto_status`` (automatic sync, Phase T2) names a pair's automation state for the ``auto_state`` field.
    """
    result = []
    for pair in config.pairs:
        meta = state.latest_meta(pair.name)
        result.append(
            {
                "name": pair.name,
                "local": str(pair.local),
                "remote": pair.remote,
                "direction": pair.direction,
                "conflict": pair.conflict,
                "transport": pair.transport,
                "device": pair.device,
                "auto": pair.auto,
                "run_conditions": {
                    "wifi_only": pair.run_conditions.wifi_only,
                    "charging_only": pair.run_conditions.charging_only,
                    "schedule": (
                        f"{pair.run_conditions.schedule[0]:%H:%M}-{pair.run_conditions.schedule[1]:%H:%M}"
                        if pair.run_conditions.schedule
                        else None
                    ),
                },
                "auto_state": auto_status(pair.name) if auto_status is not None else {"state": "off", "reason": None},
                "enabled": pair.enabled,
                "last_run": meta.get("last_run"),
                "last_result": meta.get("last_result"),
            }
        )
    return result
