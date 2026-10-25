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
    dispatched: bool = False
    cancelled: bool = False
    ok: bool = False
    result: Any = None
    error: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


class AgentCancelled(Exception):
    """Internal: the operation was cancelled (the user pressed Stop); the HTTP layer answers 410."""


class AgentBroker:
    """Mailbox between the laptop's job threads and the one agent that is polling."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cond = threading.Condition()
        self._queue: deque[_Op] = deque()
        self._ops: dict[str, _Op] = {}
        self._last_seen: float | None = None

    # -- state ------------------------------------------------------------------------------------------------

    def touch(self) -> None:
        """Note that the agent just did something (any request, or a chunk of a body)."""
        with self._cond:
            self._last_seen = self._clock()

    def connected(self) -> bool:
        with self._cond:
            if self._last_seen is None:
                return False
            silent = self._clock() - self._last_seen
            if silent <= CONNECTED_GRACE:
                return True
            busy = any(op.dispatched and not op.done.is_set() for op in self._ops.values())
            return busy and silent <= BUSY_GRACE

    # -- agent side -------------------------------------------------------------------------------------------

    def next_op(self, wait: float = POLL_SECONDS) -> dict[str, Any] | None:
        """Hand the agent its next operation, waiting up to ``wait`` seconds; ``None`` means "nothing yet"."""
        deadline = self._clock() + wait
        with self._cond:
            self._last_seen = self._clock()
            while True:
                while self._queue:
                    op = self._queue.popleft()
                    if op.cancelled:
                        continue
                    op.dispatched = True
                    self._last_seen = self._clock()
                    return {
                        "id": op.id,
                        "op": op.name,
                        "args": op.args,
                        "upload": op.upload is not None,  # the agent must GET the body first
                        "download": op.download is not None,  # the agent must PUT the body
                    }
                remaining = deadline - self._clock()
                if remaining <= 0:
                    self._last_seen = self._clock()
                    return None
                self._cond.wait(min(remaining, 0.5))

    def find(self, op_id: str) -> _Op:
        """The live operation ``op_id``; ``KeyError`` if unknown or finished, :class:`AgentCancelled` if stopped."""
        with self._cond:
            op = self._ops.get(op_id)
            if op is None or op.done.is_set() and not op.cancelled:
                raise KeyError(op_id)
            if op.cancelled:
                raise AgentCancelled(op_id)
            self._last_seen = self._clock()
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
            self._last_seen = self._clock()
            op.done.set()

    # -- laptop side ------------------------------------------------------------------------------------------

    def call(
        self,
        name: str,
        args: dict[str, Any],
        *,
        upload: Path | None = None,
        download: Path | None = None,
        aborted: Callable[[], bool] = lambda: False,
    ) -> Any:
        """Queue one operation and wait for the agent's answer.

        Raises :class:`TransportCancelled` when ``aborted()`` turns true, :class:`TransportError` when the agent is
        absent, does not pick the operation up, goes silent, or reports an error.
        """
        if aborted():
            raise TransportCancelled("cancelled")
        if not self.connected():
            raise TransportError(NOT_CONNECTED)
        op = _Op(name=name, args=args, upload=upload, download=download, created=self._clock())
        with self._cond:
            self._ops[op.id] = op
            self._queue.append(op)
            self._cond.notify_all()
        try:
            while not op.done.wait(0.2):
                if aborted():
                    self._cancel(op)
                    raise TransportCancelled("cancelled")
                if not op.dispatched and self._clock() - op.created > DISPATCH_TIMEOUT:
                    self._cancel(op)
                    raise TransportError(f"the phone app did not pick up {name!r} within {DISPATCH_TIMEOUT:.0f} s")
                if op.dispatched and not self.connected():
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
                self._queue.remove(op)
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
    ) -> None:
        super().__init__(side)
        self._broker = broker
        self._check_root = check_root if check_root is not None else self._default_check
        self._aborted = threading.Event()

    @staticmethod
    def _default_check(root: str) -> str:
        try:
            return check_remote(root)
        except PhonePathError as exc:
            raise TransportError(str(exc)) from exc

    # -- plumbing ---------------------------------------------------------------------------------------------

    def _call(self, name: str, args: dict[str, Any], **kwargs: Any) -> Any:
        return self._broker.call(name, args, aborted=self._aborted.is_set, **kwargs)

    def abort(self) -> None:
        self._aborted.set()
        self._broker.cancel_ops(lambda op: True)

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
