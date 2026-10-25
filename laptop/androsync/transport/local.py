"""LocalDirTransport: Transport implementation for a plain filesystem directory.

Used for the laptop side of every pair, and (since any local directory
works the same way regardless of which side it represents) to stand in for
the phone side in tests, keeping the whole sync engine testable without a
device attached.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from androsync.ignore import IgnoreRules
from androsync.model import FileEntry, Side, Snapshot
from androsync.transport.base import Transport, TransportError, check_rel, hash_file


def _full_path(root: str, rel: str) -> str:
    check_rel(rel)
    return os.path.join(root, *rel.split("/"))


class LocalDirTransport(Transport):
    """Transport backed by a normal directory on the local filesystem."""

    def __init__(self, side: Side = Side.LOCAL) -> None:
        super().__init__(side)

    def scan(self, root: str, ignore: IgnoreRules) -> Snapshot:
        snapshot: Snapshot = {}
        if not os.path.isdir(root):
            return snapshot

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            rel_dir = os.path.relpath(dirpath, root)
            rel_dir_posix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")

            # Prune symlinked and ignored subdirectories before descending,
            # so a big ignored tree (e.g. ".git/**") is never walked at all.
            kept_dirs = []
            for name in dirnames:
                child_full = os.path.join(dirpath, name)
                if os.path.islink(child_full):
                    continue
                child_rel = name if rel_dir_posix == "" else f"{rel_dir_posix}/{name}"
                if ignore.matches_dir(child_rel):
                    continue
                kept_dirs.append(name)
            dirnames[:] = kept_dirs

            for name in filenames:
                full = os.path.join(dirpath, name)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                rel = name if rel_dir_posix == "" else f"{rel_dir_posix}/{name}"
                if ignore.matches(rel):
                    continue
                st = os.stat(full)
                snapshot[rel] = FileEntry(size=st.st_size, mtime=st.st_mtime)

        return snapshot

    def fetch(self, root: str, rel: str, tmp_path: Path) -> None:
        src = _full_path(root, rel)
        try:
            shutil.copy2(src, tmp_path)
        except OSError as exc:
            raise TransportError(f"fetch failed for {rel!r}: {exc}") from exc

    def store(self, tmp_path: Path, root: str, rel: str, mtime: float) -> None:
        dst = _full_path(root, rel)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(tmp_path, dst)
            os.utime(dst, (mtime, mtime))
        except OSError as exc:
            raise TransportError(f"store failed for {rel!r}: {exc}") from exc

    def hash_many(self, root: str, rels: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for rel in rels:
            path = _full_path(root, rel)
            try:
                result[rel] = hash_file(Path(path))
            except OSError as exc:
                raise TransportError(f"hash failed for {rel!r}: {exc}") from exc
        return result

    def delete(self, root: str, rel: str) -> None:
        path = _full_path(root, rel)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise TransportError(f"delete failed for {rel!r}: {exc}") from exc
        self._prune_empty_parents(root, path)

    @staticmethod
    def _prune_empty_parents(root: str, deleted_path: str) -> None:
        root_abs = os.path.abspath(root)
        parent = os.path.dirname(deleted_path)
        while os.path.abspath(parent) != root_abs:
            try:
                if os.listdir(parent):
                    break
                os.rmdir(parent)
            except OSError:
                break
            parent = os.path.dirname(parent)

    def rename(self, root: str, rel_from: str, rel_to: str) -> None:
        src = _full_path(root, rel_from)
        dst = _full_path(root, rel_to)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
        except OSError as exc:
            raise TransportError(f"rename failed {rel_from!r} -> {rel_to!r}: {exc}") from exc

    def mkdirs(self, root: str, rel_dir: str) -> None:
        path = _full_path(root, rel_dir) if rel_dir else root
        os.makedirs(path, exist_ok=True)

    def exists(self, root: str) -> bool:
        return os.path.isdir(root)

    def stat(self, root: str, rel: str) -> FileEntry | None:
        path = _full_path(root, rel)
        try:
            st = os.stat(path)
        except OSError:
            return None
        if not os.path.isfile(path):
            return None
        return FileEntry(size=st.st_size, mtime=st.st_mtime)
