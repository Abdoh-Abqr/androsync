"""Core data model shared by the planner, executor and transports.

Defines the on-disk file fingerprint (``FileEntry``), the snapshot type,
the enums used to classify changes and describe planned actions, and a
run-summary counter object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


@dataclass(frozen=True)
class FileEntry:
    """A file's fingerprint as recorded by a scan: size, mtime, and (when known) content hash."""

    size: int
    mtime: float
    sha256: str | None = None


# relpath (posix-style, relative to the pair root) -> FileEntry, as produced
# by Transport.scan() for one side.
Snapshot = dict[str, FileEntry]


@dataclass(frozen=True)
class SnapshotEntry:
    """A file's fingerprint as recorded in the persisted "last synced" snapshot.

    Each side's mtime is stored separately: a file copied by hand outside
    androsync (e.g. over MTP, which does not preserve timestamps) can have
    identical content but different mtimes on the two sides, and storing one
    shared mtime would make it look changed - and so a false conflict - on
    every run. ``sha256`` is filled in only when a content-hash comparison
    was actually made (task 29a); it is not proactively computed.
    """

    size: int
    mtime_local: float
    mtime_remote: float
    sha256: str | None = None


# relpath -> SnapshotEntry: the persisted "last synced" state for a pair.
PersistedSnapshot = dict[str, SnapshotEntry]


class ChangeKind(Enum):
    """How a file on one side compares to the last snapshot."""

    UNCHANGED = auto()
    CREATED = auto()
    MODIFIED = auto()
    DELETED = auto()
    # Not present in the snapshot and not present on this side either.
    ABSENT = auto()


class Side(Enum):
    """Which end of a pair a file entry or action refers to."""

    LOCAL = auto()
    REMOTE = auto()


class ActionKind(Enum):
    """What the executor should do for a given relative path."""

    COPY = auto()
    DELETE = auto()
    CONFLICT_COPY = auto()
    ASK = auto()
    NOOP = auto()


@dataclass
class Action:
    """A single planned operation for one relative path."""

    kind: ActionKind
    rel: str
    src: Side | None
    dst: Side | None
    reason: str
    entry: FileEntry | None = None
    # For CONFLICT_COPY actions: the new relative path the loser is renamed to.
    target: str | None = None


@dataclass
class Summary:
    """Counters describing the outcome of a sync run."""

    copied_to_local: int = 0
    copied_to_remote: int = 0
    deleted_local: int = 0
    deleted_remote: int = 0
    conflicts: int = 0
    asks: int = 0
    errors: int = 0
    #: Set when the run was journaled (task 30a): the id `androsync undo` replays.
    #: None for a dry run, or a real run with undo disabled for the pair.
    run_id: str | None = None
    #: Set when the plan was stopped before changing anything because it needs the user's OK (task 36d, issue #9):
    #: ``confirmation`` holds one sentence per reason. A dry run sets them too, so a preview shows the warning.
    needs_confirmation: bool = False
    confirmation: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"copied_to_local={self.copied_to_local} "
            f"copied_to_remote={self.copied_to_remote} "
            f"deleted_local={self.deleted_local} "
            f"deleted_remote={self.deleted_remote} "
            f"conflicts={self.conflicts} "
            f"asks={self.asks} "
            f"errors={self.errors}"
        )
