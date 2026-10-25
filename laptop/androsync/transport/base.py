"""Transport ABC: the file-operations interface implemented by each side.

A ``Transport`` performs all filesystem work for one side of a pair - the
laptop's local filesystem (``LocalDirTransport``), or later the phone via
ADB (``AdbTransport``). The planner and executor never touch files
directly; they operate only through this interface, so the whole sync
engine can be tested with two local directories standing in for "laptop"
and "phone" (see ``LocalDirTransport``).

Copying a file from side A to side B is always ``A.fetch(...)`` into a
laptop-local temp file, then ``B.store(...)`` from that temp file - so a
transport only ever needs to move bytes between its own root and a path on
the laptop, never directly between two remote roots.
"""

from __future__ import annotations

import hashlib
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import uuid4

from androsync.ignore import IgnoreRules
from androsync.model import FileEntry, Side, Snapshot

_SIDE_LABEL = {Side.LOCAL: "laptop", Side.REMOTE: "phone"}


def hash_file(path: Path) -> str:
    """Return the sha256 hex digest of the file at ``path``, read in 1 MB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class TransportError(Exception):
    """Raised for any transport-level failure: bad path, I/O error, ..."""


class TransportCancelled(TransportError):
    """Raised when a running operation was cut short by :meth:`Transport.abort` (the user pressed Stop).

    Not a failure: the executor treats it as "cancelled" and never counts it as an error or retries.
    """


def check_rel(rel: str) -> None:
    """Reject a relative path that could escape ``root`` or is malformed.

    Shared by every ``Transport`` implementation (``LocalDirTransport``,
    ``AdbTransport``) so the "no absolute paths, no ``..`` segments, no
    empty/duplicate slashes" rule is enforced identically on both sides.
    """
    if not rel:
        raise TransportError("empty relative path")
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise TransportError(f"unsafe relative path: {rel!r}")


class Transport(ABC):
    """One side of a pair: scan, fetch/store single files, manage the tree."""

    #: True if a failed :meth:`store_many` is guaranteed to raise on its
    #: own - including a destination path colliding with an existing
    #: directory, not just an I/O error - so the executor's batched copy
    #: path can skip its own per-file post-store ``stat`` verification for
    #: this transport (task 24). False (the default, safe for every
    #: transport unless it opts in) means "always verify".
    store_many_is_self_verifying: bool = False

    def __init__(self, side: Side) -> None:
        self.side: Side = side
        self.label: str = _SIDE_LABEL[side]

    @abstractmethod
    def scan(self, root: str, ignore: IgnoreRules) -> Snapshot:
        """Walk ``root`` and return ``{relpath: FileEntry}`` for every file.

        ``relpath`` is posix-style (``/``-separated) and relative to
        ``root``. Only regular files are included; symlinks and other
        non-regular entries are skipped. Any path matched by ``ignore`` is
        skipped, including whole directories pruned via
        :meth:`IgnoreRules.matches_dir` before descending into them.
        """

    @abstractmethod
    def fetch(self, root: str, rel: str, tmp_path: Path) -> None:
        """Copy the file at ``root/rel`` to ``tmp_path`` on the laptop.

        The source file's mtime is preserved on the copy.
        """

    @abstractmethod
    def store(self, tmp_path: Path, root: str, rel: str, mtime: float) -> None:
        """Write ``tmp_path`` to ``root/rel``, creating parent directories.

        The destination's mtime is set to ``mtime`` (seconds since the
        epoch) regardless of ``tmp_path``'s own mtime.
        """

    def store_many(self, items: list[tuple[Path, str, str, float]]) -> None:
        """Batched :meth:`store`: ``items`` is a list of ``(tmp_path, root, rel, mtime)``.

        Default implementation just loops over :meth:`store`; a transport
        with real per-call round-trip overhead (:class:`~androsync.transport.adb.AdbTransport`)
        overrides this to do far fewer of them (task 24).
        """
        for tmp_path, root, rel, mtime in items:
            self.store(tmp_path, root, rel, mtime)

    def fetch_many(self, items: list[tuple[str, str, Path]]) -> None:
        """Batched :meth:`fetch`: ``items`` is a list of ``(root, rel, tmp_path)``.

        Default implementation just loops over :meth:`fetch`; see :meth:`store_many`.
        """
        for root, rel, tmp_path in items:
            self.fetch(root, rel, tmp_path)

    def hash_many(self, root: str, rels: list[str]) -> dict[str, str]:
        """Return ``{rel: sha256_hex}`` for each of ``rels`` under ``root`` (task 29a).

        Default implementation fetches each file to a laptop-local temp path
        and hashes it there; a transport that can hash on its own side more
        cheaply (:class:`~androsync.transport.local.LocalDirTransport`,
        :class:`~androsync.transport.adb.AdbTransport`) overrides this.
        """
        result: dict[str, str] = {}
        with tempfile.TemporaryDirectory(prefix="androsync-hash-") as tmp_dir_name:
            tmp_dir = Path(tmp_dir_name)
            for rel in rels:
                tmp_path = tmp_dir / f"{uuid4().hex}.tmp"
                self.fetch(root, rel, tmp_path)
                result[rel] = hash_file(tmp_path)
        return result

    def abort(self) -> None:
        """Stop whatever this transport is doing right now, from any thread (task 36a, issue #6).

        After this, a running operation raises :class:`TransportCancelled` and every later call on this
        transport raises it at once. The default does nothing, for transports whose operations are short.
        """

    @abstractmethod
    def delete(self, root: str, rel: str) -> None:
        """Remove the file at ``root/rel``.

        Afterwards, remove now-empty parent directories walking up towards
        (but never including) ``root``.
        """

    @abstractmethod
    def rename(self, root: str, rel_from: str, rel_to: str) -> None:
        """Rename ``root/rel_from`` to ``root/rel_to``, creating parents as needed."""

    @abstractmethod
    def mkdirs(self, root: str, rel_dir: str) -> None:
        """Create ``root/rel_dir`` and any missing parent directories."""

    @abstractmethod
    def exists(self, root: str) -> bool:
        """Return True if ``root`` exists as a directory on this side."""

    @abstractmethod
    def stat(self, root: str, rel: str) -> FileEntry | None:
        """Return the ``FileEntry`` for ``root/rel``, or None if it is absent."""

    def close(self) -> None:
        """Release any held resources. No-op by default."""
