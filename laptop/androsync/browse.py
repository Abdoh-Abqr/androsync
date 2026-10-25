"""Local- and remote-side folder browsing for the ``/v1/browse``/``/v1/mkdir`` API (task 23b).

Backs the phone app's folder pickers when adding or editing a pair: the
laptop side is browsed directly (``os.scandir``, fenced to
``service.browse_roots``), the phone side through the same
``AdbTransport.ls`` the sync engine itself uses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from androsync.phone_path import normalize_remote, remote_parent, validate_remote
from androsync.transport.adb import AdbTransport


class BrowseError(Exception):
    """Raised when ``path`` cannot be listed (not a directory, adb failure, ...)."""


class BrowseForbidden(Exception):
    """Raised when ``path`` falls outside the allowed local browse roots."""


@dataclass
class BrowseEntry:
    name: str
    is_dir: bool
    size: int
    hidden: bool


@dataclass
class BrowseResult:
    path: str
    parent: str | None
    entries: list[BrowseEntry]
    writable: bool


def _expand_root(root: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(root))).resolve()


def _is_within_roots(path: Path, roots: list[str]) -> bool:
    resolved = path.resolve()
    for root in roots:
        root_path = _expand_root(root)
        if resolved == root_path or root_path in resolved.parents:
            return True
    return False


def _local_parent(path: Path) -> str | None:
    return None if path.parent == path else str(path.parent)


def browse_local(path: str, roots: list[str]) -> BrowseResult:
    """List ``path`` (an absolute local directory), or the configured roots when ``path`` is empty."""
    if not path:
        entries = []
        for root in roots:
            root_path = _expand_root(root)
            if root_path.is_dir():
                entries.append(BrowseEntry(name=str(root_path), is_dir=True, size=0, hidden=False))
        return BrowseResult(path="", parent=None, entries=entries, writable=False)

    target = Path(path)
    if not _is_within_roots(target, roots):
        raise BrowseForbidden(f"{path} is outside the allowed browse roots")
    if not target.is_dir():
        raise BrowseError(f"{path} is not a directory")

    entries: list[BrowseEntry] = []
    with os.scandir(target) as it:
        for de in it:
            try:
                is_dir = de.is_dir(follow_symlinks=False)
                size = 0 if is_dir else de.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            entries.append(BrowseEntry(name=de.name, is_dir=is_dir, size=size, hidden=de.name.startswith(".")))
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))

    return BrowseResult(
        path=str(target), parent=_local_parent(target), entries=entries, writable=os.access(target, os.W_OK)
    )


def _checked_remote(path: str) -> str:
    """Normalize a phone path and refuse anything outside shared storage (task 36c)."""
    normalized = normalize_remote(path) if path else "/sdcard"
    message = validate_remote(normalized)
    if message is not None:
        raise BrowseForbidden(message)
    return normalized


def browse_remote(transport: AdbTransport, path: str) -> BrowseResult:
    """List ``path`` on the phone (default ``/sdcard`` when ``path`` is empty).

    Only shared storage can be browsed, and "up" stops at ``/sdcard`` or at the top of an SD card.
    Raises :class:`BrowseForbidden` for any other path.
    """
    root = _checked_remote(path)
    entries = [
        BrowseEntry(name=name, is_dir=is_dir, size=size, hidden=name.startswith("."))
        for name, is_dir, size in transport.ls(root)
    ]
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return BrowseResult(path=root, parent=remote_parent(root), entries=entries, writable=True)


def mkdir_local(path: str, roots: list[str]) -> None:
    target = Path(path)
    if not _is_within_roots(target, roots):
        raise BrowseForbidden(f"{path} is outside the allowed browse roots")
    target.mkdir(parents=True, exist_ok=True)


def mkdir_remote(transport: AdbTransport, path: str) -> str:
    """Create a folder on the phone and return its normalized path. Raises :class:`BrowseForbidden`."""
    normalized = _checked_remote(path)
    transport.mkdirs(normalized, "")
    return normalized
