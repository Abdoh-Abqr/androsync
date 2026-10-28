"""AgentTransport: the phone side of a pair, done by the AndroSync app instead of ``adb`` (task 37b, issue #14).

The laptop cannot call into the phone, so the app (the *agent*) calls the laptop: it long-polls
``GET /v1/agent/next`` for work, does the file operation itself and answers with
``POST /v1/agent/result/<op id>``. File bodies move on their own requests, in both directions, streamed:
``GET /v1/agent/blob/<op id>`` (laptop to phone, for ``store``) and ``PUT /v1/agent/blob/<op id>`` (phone to laptop,
for ``fetch``). The wire format is written down in ``docs/agent-protocol.md``.

Two classes live here:

* :class:`AgentBroker` is the daemon-wide mailbox: a queue of operations the agent has not taken yet, the ones it is
  working on, and a note of when the agent was last heard from (that is what ``/v1/ping`` reports as ``agent``).
* :class:`AgentTransport` implements :class:`~androsync.transport.base.Transport` on top of the broker, so the
  planner, executor, undo and conflicts code run unchanged.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from androsync.ignore import IgnoreRules
from androsync.model import FileEntry, Side, Snapshot
from androsync.phone_path import PhonePathError, check_remote
from androsync.transport.base import Transport, TransportCancelled, TransportError, check_rel

#: How long ``GET /v1/agent/next`` waits for work before answering "nothing" (the agent then asks again).
POLL_SECONDS = 25.0
#: The agent counts as connected if it was heard from this recently (a poll lasts :data:`POLL_SECONDS`).
CONNECTED_GRACE = 45.0
#: While an operation is out with the agent, a long transfer may be silent for longer before we give up.
BUSY_GRACE = 300.0
#: An operation nobody picked up within this many seconds fails.
DISPATCH_TIMEOUT = 60.0
#: Files per ``hash_many`` request, to keep one answer small.
HASH_BATCH = 200

NOT_CONNECTED = (
    "The AndroSync app on the phone is not connected to the laptop. "
    "Open the app, turn on agent mode and check that it shows the laptop as reachable."
)


@dataclass
class _Op:
    name: str
    args: dict[str, Any]
    upload: Path | None = None  # laptop -> phone body, served by GET blob
    download: Path | None = None  # phone -> laptop body, written by PUT blob
    id: str = field(default_factory=lambda: uuid4().hex)
    created: float = 0.0
    agent_id: str = "default"
    dispatched: bool = False
    cancelled: bool = False
    ok: bool = False
    result: Any = None
    error: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


class AgentBusy(Exception):
    """Kept for old imports. Since several phones may be connected at once (Phase W2) nothing raises it any more."""


class AgentCancelled(Exception):
    """Internal: the operation was cancelled (the user pressed Stop); the HTTP layer answers 410."""


#: The agent id of an app that does not send one (an old version): all such apps share one mailbox.
DEFAULT_AGENT = "default"
#: A pair bound to the app of one phone has ``device: agent-<agent id>``.
AGENT_DEVICE_PREFIX = "agent-"
#: An agent that was not heard from for this long is forgotten by :meth:`AgentBroker.agent_infos`.
FORGET_AFTER = 3600.0


def agent_device(agent_id: str) -> str:
    """The ``device`` value (and history key part) of a phone's app: ``agent-<agent id>``."""
    return f"{AGENT_DEVICE_PREFIX}{agent_id}"


def agent_id_from_device(device: str | None) -> str | None:
    """The agent id inside a pair's ``device`` value, or ``None`` for ``any``, empty or an adb phone."""
    if device and device.startswith(AGENT_DEVICE_PREFIX):
        return device[len(AGENT_DEVICE_PREFIX):]
    return None


@dataclass
class _Agent:
    """What the broker knows about one phone's app."""

    id: str
    queue: deque = field(default_factory=deque)
    last_seen: float | None = None
    phone_state: dict[str, Any] = field(default_factory=dict)
    phone_state_at: float | None = None
    route: str | None = None
    name: str | None = None
    model: str | None = None

    def label(self) -> str:
        return self.name or self.model or self.id


class AgentBroker:
    """Mailboxes between the laptop's job threads and the phones' apps that are polling, one mailbox per phone.

    Every phone app has its own agent id, so two or three phones can be connected at once. An operation is queued for
    one agent and only that agent can take it (Phase W2).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cond = threading.Condition()
        self._agents: dict[str, _Agent] = {}
        self._ops: dict[str, _Op] = {}

    # -- state ------------------------------------------------------------------------------------------------

    def _agent(self, agent_id: str | None) -> _Agent:
        """The record of ``agent_id`` (made when new). Call with the lock held."""
        key = agent_id or DEFAULT_AGENT
        agent = self._agents.get(key)
        if agent is None:
            agent = self._agents[key] = _Agent(id=key)
        return agent

    def _is_connected(self, agent: _Agent) -> bool:
        """Heard from recently, or busy with a long transfer. Call with the lock held."""
        if agent.last_seen is None:
            return False
        silent = self._clock() - agent.last_seen
        if silent <= CONNECTED_GRACE:
            return True
        busy = any(op.agent_id == agent.id and op.dispatched and not op.done.is_set() for op in self._ops.values())
        return busy and silent <= BUSY_GRACE

    def _latest(self) -> _Agent | None:
        """The connected agent heard from most recently. Call with the lock held."""
        live = [a for a in self._agents.values() if self._is_connected(a)]
        return max(live, key=lambda a: a.last_seen or 0.0) if live else None

    def touch(self, agent_id: str | None = None) -> None:
        """Note that an agent just did something (any request, or a chunk of a body)."""
        with self._cond:
            agent = self._agent(agent_id) if agent_id else self._latest()
            if agent is not None:
                agent.last_seen = self._clock()

    def connected(self, agent_id: str | None = None) -> bool:
        """Whether ``agent_id``'s app is connected, or (no id) whether any app is."""
        with self._cond:
            if agent_id:
                agent = self._agents.get(agent_id)
                return agent is not None and self._is_connected(agent)
            return any(self._is_connected(a) for a in self._agents.values())

    def connected_ids(self) -> list[str]:
        """The ids of the connected agents, most recently heard first."""
        with self._cond:
            live = [a for a in self._agents.values() if self._is_connected(a)]
            return [a.id for a in sorted(live, key=lambda a: a.last_seen or 0.0, reverse=True)]

    def resolve(self, agent_id: str | None = None) -> str:
        """The agent to talk to: ``agent_id`` itself, or (none given) the only connected one.

        Raises :class:`TransportError` when none is connected, or when several are and the pair is not bound to one.
        """
        if agent_id:
            return agent_id
        with self._cond:
            live = [a for a in self._agents.values() if self._is_connected(a)]
            if not live:
                raise TransportError(NOT_CONNECTED)
            if len(live) > 1:
                names = ", ".join(sorted(a.label() for a in live))
                raise TransportError(
                    f"more than one phone app is connected ({names}). Bind this pair to one phone in its settings. "
                    "Nothing was synced."
                )
            return live[0].id

    def agent_infos(self) -> list[dict[str, Any]]:
        """One row per phone app seen in the last hour, for ``/v1/ping``: id, device value, name, model, route, connected."""
        with self._cond:
            now = self._clock()
            rows = []
            for agent in self._agents.values():
                if agent.last_seen is None or now - agent.last_seen > FORGET_AFTER:
                    continue
                rows.append(
                    {
                        "id": agent.id,
                        "device": agent_device(agent.id),
                        "name": agent.name,
                        "model": agent.model,
                        "route": agent.route,
                        "connected": self._is_connected(agent),
                    }
                )
            return sorted(rows, key=lambda r: (not r["connected"], r["name"] or r["model"] or r["id"]))

    def report_identity(self, agent_id: str | None, name: str | None, model: str | None) -> None:
        """The app says what the phone is called (query parameters ``name`` and ``model`` of ``GET /v1/agent/next``)."""
        with self._cond:
            agent = self._agent(agent_id)
            if name:
                agent.name = name[:40]
            if model:
                agent.model = model[:40]

    def report_route(self, route: str | None, agent_id: str | None = None) -> None:
        with self._cond:
            self._agent(agent_id).route = route

    def route(self, agent_id: str | None = None) -> str | None:
        """``usb``, ``tailscale``, ``lan`` or ``other`` for the agent's last poll (no id: the latest connected one)."""
        with self._cond:
            agent = self._agents.get(agent_id) if agent_id else self._latest()
            return agent.route if agent is not None else None

    # -- agent side -------------------------------------------------------------------------------------------

    def next_op(self, wait: float = POLL_SECONDS, agent_id: str | None = None) -> dict[str, Any] | None:
        """Hand the agent its next operation, waiting up to ``wait`` seconds; ``None`` means "nothing yet"."""
        deadline = self._clock() + wait
        with self._cond:
            agent = self._agent(agent_id)
            agent.last_seen = self._clock()
            while True:
                while agent.queue:
                    op = agent.queue.popleft()
                    if op.cancelled:
                        continue
                    op.dispatched = True
                    agent.last_seen = self._clock()
                    return {
                        "id": op.id,
                        "op": op.name,
                        "args": op.args,
                        "upload": op.upload is not None,  # the agent must GET the body first
                        "download": op.download is not None,  # the agent must PUT the body
                    }
                remaining = deadline - self._clock()
                if remaining <= 0:
                    agent.last_seen = self._clock()
                    return None
                self._cond.wait(min(remaining, 0.5))

    def report_state(self, net: str | None, battery: str | None, agent_id: str | None = None) -> None:
        """The agent says how its phone is connected and powered (query parameters of ``GET /v1/agent/next``)."""
        with self._cond:
            agent = self._agent(agent_id)
            if net in ("wifi", "mobile", "none"):
                agent.phone_state["net"] = net
            if battery in ("charging", "discharging"):
                agent.phone_state["charging"] = battery == "charging"
            agent.phone_state_at = self._clock()

    def phone_state(self, agent_id: str | None = None, max_age: float = 120.0) -> dict[str, Any]:
        """``{"net": ..., "charging": ...}`` as last reported (no id: by the latest connected agent), or ``{}``."""
        with self._cond:
            agent = self._agents.get(agent_id) if agent_id else self._latest()
            if agent is None and not agent_id:  # nobody is connected: whoever reported most recently
                reported = [a for a in self._agents.values() if a.phone_state_at is not None]
                agent = max(reported, key=lambda a: a.phone_state_at or 0.0) if reported else None
            if agent is None or agent.phone_state_at is None or self._clock() - agent.phone_state_at > max_age:
                return {}
            return dict(agent.phone_state)

    def find(self, op_id: str) -> _Op:
        """The live operation ``op_id``; ``KeyError`` if unknown or finished, :class:`AgentCancelled` if stopped."""
        with self._cond:
            op = self._ops.get(op_id)
            if op is None or op.done.is_set() and not op.cancelled:
                raise KeyError(op_id)
            if op.cancelled:
                raise AgentCancelled(op_id)
            self._agent(op.agent_id).last_seen = self._clock()
            return op

    def complete(self, op_id: str, ok: bool, result: Any = None, error: str | None = None) -> None:
        """The agent's answer. Raises like :meth:`find`."""
        with self._cond:
            op = self._ops.get(op_id)
            if op is None or op.done.is_set() and not op.cancelled:
                raise KeyError(op_id)
            if op.cancelled:
                raise AgentCancelled(op_id)
            op.ok = bool(ok)
            op.result = result
            op.error = None if ok else (error or "the agent reported an error")
            self._agent(op.agent_id).last_seen = self._clock()
            op.done.set()

    # -- laptop side ------------------------------------------------------------------------------------------

    def call(
        self,
        name: str,
        args: dict[str, Any],
        *,
        agent_id: str | None = None,
        upload: Path | None = None,
        download: Path | None = None,
        aborted: Callable[[], bool] = lambda: False,
    ) -> Any:
        """Queue one operation for ``agent_id`` (none: the only connected agent) and wait for the answer.

        Raises :class:`TransportCancelled` when ``aborted()`` turns true, :class:`TransportError` when the agent is
        absent, does not pick the operation up, goes silent, or reports an error.
        """
        if aborted():
            raise TransportCancelled("cancelled")
        target = self.resolve(agent_id)
        if not self.connected(target):
            raise TransportError(NOT_CONNECTED)
        op = _Op(name=name, args=args, upload=upload, download=download, created=self._clock(), agent_id=target)
        with self._cond:
            self._ops[op.id] = op
            self._agent(target).queue.append(op)
            self._cond.notify_all()
        try:
            while not op.done.wait(0.2):
                if aborted():
                    self._cancel(op)
                    raise TransportCancelled("cancelled")
                if not op.dispatched and self._clock() - op.created > DISPATCH_TIMEOUT:
                    self._cancel(op)
                    raise TransportError(f"the phone app did not pick up {name!r} within {DISPATCH_TIMEOUT:.0f} s")
                if op.dispatched and not self.connected(target):
                    self._cancel(op)
                    raise TransportError(f"the phone app went silent during {name!r}")
            if op.cancelled:
                raise TransportCancelled("cancelled")
            if not op.ok:
                raise TransportError(f"{name} failed on the phone: {op.error}")
            return op.result
        finally:
            with self._cond:
                self._ops.pop(op.id, None)

    def _cancel(self, op: _Op) -> None:
        with self._cond:
            op.cancelled = True
            try:
                self._agent(op.agent_id).queue.remove(op)
            except ValueError:
                pass
            op.done.set()

    def cancel_ops(self, ops_filter: Callable[[_Op], bool]) -> None:
        """Cancel every unfinished operation ``ops_filter`` accepts (used by :meth:`AgentTransport.abort`)."""
        with self._cond:
            victims = [op for op in self._ops.values() if not op.done.is_set() and ops_filter(op)]
        for op in victims:
            self._cancel(op)


class AgentTransport(Transport):
    """The phone, reached through the app. Same contract as :class:`~androsync.transport.adb.AdbTransport`."""

    def __init__(
        self,
        broker: AgentBroker,
        side: Side = Side.REMOTE,
        *,
        check_root: Callable[[str], str] | None = None,
        agent_id: str | None = None,
    ) -> None:
        super().__init__(side)
        self._broker = broker
        #: The phone app this transport talks to. ``None`` means "the only connected one", decided on first use and
        #: then kept, so a second phone connecting in the middle of a sync can never take over (Phase W1).
        self._agent_id = agent_id
        self._check_root = check_root if check_root is not None else self._default_check
        self._aborted = threading.Event()

    @staticmethod
    def _default_check(root: str) -> str:
        try:
            return check_remote(root)
        except PhonePathError as exc:
            raise TransportError(str(exc)) from exc

    # -- plumbing ---------------------------------------------------------------------------------------------

    def _target(self) -> str:
        if self._agent_id is None:
            self._agent_id = self._broker.resolve(None)
        return self._agent_id

    @property
    def device_key(self) -> str:
        """The history key part of this phone's app (``agent-<id>``), so each phone keeps its own history (Phase W1).

        Raises :class:`TransportError` when no app, or more than one without a choice, can be picked.
        """
        return agent_device(self._target())

    @property
    def claims_legacy_history(self) -> bool:
        """Whether this app may take over a pair's old shared (phone-less) history: only when it is the only app known."""
        return len(self._broker.agent_infos()) <= 1

    def _call(self, name: str, args: dict[str, Any], **kwargs: Any) -> Any:
        return self._broker.call(name, args, agent_id=self._target(), aborted=self._aborted.is_set, **kwargs)

    def abort(self) -> None:
        self._aborted.set()
        agent_id = self._agent_id
        self._broker.cancel_ops(lambda op: agent_id is None or op.agent_id == agent_id)

    def _root(self, root: str) -> str:
        return self._check_root(root)

    # -- Transport --------------------------------------------------------------------------------------------

    def scan(self, root: str, ignore: IgnoreRules) -> Snapshot:
        answer = self._call("scan", {"root": self._root(root)})
        if not answer.get("exists", True):
            return {}
        decided: dict[str, bool] = {}

        def dir_ignored(rel_dir: str) -> bool:
            if rel_dir not in decided:
                decided[rel_dir] = ignore.matches_dir(rel_dir)
            return decided[rel_dir]

        snapshot: Snapshot = {}
        for rel, size, mtime in answer.get("files", []):
            parts = rel.split("/")
            if any(dir_ignored("/".join(parts[: i + 1])) for i in range(len(parts) - 1)):
                continue
            if ignore.matches(rel):
                continue
            snapshot[rel] = FileEntry(size=int(size), mtime=float(mtime))
        return snapshot

    def fetch(self, root: str, rel: str, tmp_path: Path) -> None:
        check_rel(rel)
        answer = self._call("fetch", {"root": self._root(root), "rel": rel}, download=tmp_path)
        if not tmp_path.exists():
            raise TransportError(f"fetch of {rel!r}: the phone sent no data")
        if "size" in answer and tmp_path.stat().st_size != int(answer["size"]):
            raise TransportError(f"fetch of {rel!r}: received {tmp_path.stat().st_size} bytes, phone sent {answer['size']}")
        mtime = float(answer["mtime"])
        os.utime(tmp_path, (mtime, mtime))

    def store(self, tmp_path: Path, root: str, rel: str, mtime: float) -> None:
        check_rel(rel)
        self._call(
            "store",
            {"root": self._root(root), "rel": rel, "mtime": mtime, "size": tmp_path.stat().st_size},
            upload=tmp_path,
        )

    def hash_many(self, root: str, rels: list[str]) -> dict[str, str]:
        root = self._root(root)
        result: dict[str, str] = {}
        for start in range(0, len(rels), HASH_BATCH):
            batch = rels[start : start + HASH_BATCH]
            for rel in batch:
                check_rel(rel)
            answer = self._call("hash_many", {"root": root, "rels": batch})
            hashes = answer.get("hashes", {})
            for rel in batch:
                if rel not in hashes:
                    raise TransportError(f"hash of {rel!r}: the phone returned none")
                result[rel] = hashes[rel]
        return result

    def delete(self, root: str, rel: str) -> None:
        check_rel(rel)
        self._call("delete", {"root": self._root(root), "rel": rel})

    def rename(self, root: str, rel_from: str, rel_to: str) -> None:
        check_rel(rel_from)
        check_rel(rel_to)
        self._call("rename", {"root": self._root(root), "from": rel_from, "to": rel_to})

    def mkdirs(self, root: str, rel_dir: str) -> None:
        if rel_dir:
            check_rel(rel_dir)
        self._call("mkdirs", {"root": self._root(root), "rel_dir": rel_dir})

    def exists(self, root: str) -> bool:
        return bool(self._call("exists", {"root": self._root(root)}).get("exists"))

    def stat(self, root: str, rel: str) -> FileEntry | None:
        check_rel(rel)
        answer = self._call("stat", {"root": self._root(root), "rel": rel})
        if not answer.get("exists"):
            return None
        return FileEntry(size=int(answer["size"]), mtime=float(answer["mtime"]))

    def ls(self, path: str) -> list[tuple[str, bool, int]]:
        """Immediate children of ``path`` as ``(name, is_dir, size)``, like ``AdbTransport.ls``."""
        answer = self._call("ls", {"path": self._root(path)})
        return [(e["name"], bool(e["is_dir"]), int(e.get("size", 0))) for e in answer.get("entries", [])]
