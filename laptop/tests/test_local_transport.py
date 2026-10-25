"""Tests for androsync.transport.local.LocalDirTransport."""

from __future__ import annotations

import os
import time

import pytest

from androsync.ignore import IgnoreRules
from androsync.model import Side
from androsync.transport.base import TransportError
from androsync.transport.local import LocalDirTransport

NO_IGNORE = IgnoreRules([])


def _write(path, content: bytes = b"hello") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _symlink_or_skip(target, link_path):
    try:
        os.symlink(target, link_path)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not supported on this filesystem/platform")


def test_transport_label_and_side():
    t = LocalDirTransport(Side.REMOTE)
    assert t.side is Side.REMOTE
    assert t.label == "phone"
    t2 = LocalDirTransport()
    assert t2.side is Side.LOCAL
    assert t2.label == "laptop"


def test_scan_finds_nested_files_with_size_and_mtime(tmp_path):
    _write(tmp_path / "a.txt", b"12345")
    _write(tmp_path / "sub" / "dir" / "b.txt", b"1234567890")
    mtime = time.time() - 100
    os.utime(tmp_path / "a.txt", (mtime, mtime))

    transport = LocalDirTransport()
    snapshot = transport.scan(str(tmp_path), NO_IGNORE)

    assert set(snapshot) == {"a.txt", "sub/dir/b.txt"}
    assert snapshot["a.txt"].size == 5
    assert snapshot["a.txt"].mtime == pytest.approx(mtime, abs=0.01)
    assert snapshot["sub/dir/b.txt"].size == 10


def test_scan_on_missing_root_returns_empty(tmp_path):
    transport = LocalDirTransport()
    assert transport.scan(str(tmp_path / "missing"), NO_IGNORE) == {}


def test_scan_prunes_ignored_directory_without_walking_it(tmp_path):
    _write(tmp_path / "keep.txt")
    _write(tmp_path / ".git" / "config")
    _write(tmp_path / ".git" / "objects" / "ab" / "cdef")
    _write(tmp_path / "notes.tmp")

    ignore = IgnoreRules([".git/**", "*.tmp"])
    transport = LocalDirTransport()
    snapshot = transport.scan(str(tmp_path), ignore)

    assert set(snapshot) == {"keep.txt"}


def test_scan_skips_symlinked_file_and_directory(tmp_path):
    real_file = tmp_path / "real.txt"
    _write(real_file)
    _symlink_or_skip(str(real_file), tmp_path / "link.txt")

    real_dir = tmp_path / "realdir"
    _write(real_dir / "inner.txt")
    _symlink_or_skip(str(real_dir), tmp_path / "linkdir")

    transport = LocalDirTransport()
    snapshot = transport.scan(str(tmp_path), NO_IGNORE)

    assert set(snapshot) == {"real.txt", "realdir/inner.txt"}


def test_fetch_preserves_mtime(tmp_path):
    root = tmp_path / "root"
    _write(root / "a.txt", b"content")
    mtime = time.time() - 500
    os.utime(root / "a.txt", (mtime, mtime))

    dest = tmp_path / "fetched.txt"
    transport = LocalDirTransport()
    transport.fetch(str(root), "a.txt", dest)

    assert dest.read_bytes() == b"content"
    assert os.stat(dest).st_mtime == pytest.approx(mtime, abs=0.01)


def test_store_creates_parent_dirs_and_sets_mtime(tmp_path):
    tmp_file = tmp_path / "incoming.txt"
    _write(tmp_file, b"payload")

    root = tmp_path / "root"
    target_mtime = time.time() - 1000

    transport = LocalDirTransport()
    transport.store(tmp_file, str(root), "a/b/c.txt", target_mtime)

    dst = root / "a" / "b" / "c.txt"
    assert dst.read_bytes() == b"payload"
    assert os.stat(dst).st_mtime == pytest.approx(target_mtime, abs=0.01)


def test_delete_removes_file_and_empty_parents_but_not_root(tmp_path):
    root = tmp_path / "root"
    _write(root / "a" / "b" / "c.txt")

    transport = LocalDirTransport()
    transport.delete(str(root), "a/b/c.txt")

    assert not (root / "a" / "b" / "c.txt").exists()
    assert not (root / "a" / "b").exists()
    assert not (root / "a").exists()
    assert root.exists()


def test_delete_keeps_nonempty_parent(tmp_path):
    root = tmp_path / "root"
    _write(root / "a" / "b" / "c.txt")
    _write(root / "a" / "sibling.txt")

    transport = LocalDirTransport()
    transport.delete(str(root), "a/b/c.txt")

    assert not (root / "a" / "b").exists()
    assert (root / "a").exists()
    assert (root / "a" / "sibling.txt").exists()


def test_delete_missing_file_is_a_noop(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    transport = LocalDirTransport()
    transport.delete(str(root), "missing.txt")  # must not raise


def test_rename(tmp_path):
    root = tmp_path / "root"
    _write(root / "old.txt", b"data")

    transport = LocalDirTransport()
    transport.rename(str(root), "old.txt", "sub/new.txt")

    assert not (root / "old.txt").exists()
    assert (root / "sub" / "new.txt").read_bytes() == b"data"


def test_mkdirs(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    transport = LocalDirTransport()
    transport.mkdirs(str(root), "a/b/c")
    assert (root / "a" / "b" / "c").is_dir()


def test_mkdirs_empty_rel_dir_creates_root(tmp_path):
    root = tmp_path / "root"
    transport = LocalDirTransport()
    transport.mkdirs(str(root), "")
    assert root.is_dir()


def test_exists(tmp_path):
    transport = LocalDirTransport()
    assert not transport.exists(str(tmp_path / "missing"))
    assert transport.exists(str(tmp_path))


def test_stat(tmp_path):
    root = tmp_path / "root"
    _write(root / "a.txt", b"12345")
    transport = LocalDirTransport()

    entry = transport.stat(str(root), "a.txt")
    assert entry is not None
    assert entry.size == 5

    assert transport.stat(str(root), "missing.txt") is None


@pytest.mark.parametrize(
    "bad_rel",
    ["../escape.txt", "a/../../escape.txt", "a/../b.txt", "", "a//b.txt", "."],
)
def test_dotdot_and_malformed_rel_rejected(tmp_path, bad_rel):
    root = tmp_path / "root"
    root.mkdir()
    transport = LocalDirTransport()
    with pytest.raises(TransportError):
        transport.stat(str(root), bad_rel)


def test_close_is_a_noop():
    LocalDirTransport().close()
