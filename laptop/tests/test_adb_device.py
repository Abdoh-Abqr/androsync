"""Real-device smoke tests for androsync.transport.adb.

Every test here is skipped outright when ``adb devices`` shows no attached
device (checked once at import time, so a missing/broken ``adb`` binary
also just means "skip", not an error). Run these manually with a phone on
the cable and USB debugging authorized:

    .venv/bin/pytest laptop/tests/test_adb_device.py -v
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from androsync.ignore import IgnoreRules
from androsync.transport.adb import Adb, AdbTransport, list_devices, resolve_device


def _no_device() -> bool:
    try:
        devices = list_devices()
    except Exception:
        return True
    return not any(d.state == "device" for d in devices)


no_device = _no_device()
pytestmark = pytest.mark.skipif(no_device, reason="no authorized ADB device attached")


@pytest.fixture(scope="module")
def transport() -> AdbTransport:
    serial = resolve_device("auto")
    return AdbTransport(Adb(serial=serial))


def test_scan_dcim_returns_a_dict(transport):
    snapshot = transport.scan("/sdcard/DCIM", IgnoreRules([]))
    assert isinstance(snapshot, dict)


def test_ls_sdcard_lists_known_top_level_dirs(transport):
    entries = transport.ls("/sdcard")
    names = {name for name, is_dir, _ in entries}
    assert "DCIM" in names
    assert all(isinstance(is_dir, bool) for _, is_dir, _ in entries)


def test_ls_dcim_camera_tells_files_from_folders(transport):
    """Whatever the phone holds in Camera, ls() must agree with the phone's own `ls -p` (a trailing / marks a folder)."""
    import subprocess

    entries = transport.ls("/sdcard/DCIM/Camera")
    assert entries
    listing = subprocess.run(
        ["adb", "shell", "ls -p /sdcard/DCIM/Camera"], capture_output=True, text=True, timeout=30
    ).stdout.splitlines()
    folders = {line.rstrip("/").rstrip("\r") for line in listing if line.rstrip("\r").endswith("/")}
    assert {name for name, is_dir, _ in entries if is_dir} == folders


def test_push_pull_round_trip_preserves_mtime(transport):
    remote_dir = "/sdcard/.androsync-test"
    rel = "roundtrip.txt"
    mtime = time.time() - 3600

    transport.mkdirs(remote_dir, "")
    tmp_local = None
    try:
        fd, name = tempfile.mkstemp(prefix="androsync-roundtrip-")
        tmp_local = Path(name)
        os.close(fd)
        tmp_local.write_bytes(b"androsync round-trip test\n")
        os.utime(tmp_local, (mtime, mtime))

        transport.store(tmp_local, remote_dir, rel, mtime)

        entry = transport.stat(remote_dir, rel)
        assert entry is not None
        assert abs(entry.mtime - mtime) <= 2

        fetched = tmp_local.parent / "androsync-roundtrip-fetched.txt"
        transport.fetch(remote_dir, rel, fetched)
        assert fetched.read_bytes() == b"androsync round-trip test\n"
        assert abs(fetched.stat().st_mtime - mtime) <= 2
        fetched.unlink(missing_ok=True)
    finally:
        transport.delete(remote_dir, rel)
        subprocess.run(
            ["adb", "-s", resolve_device("auto"), "shell", f"rmdir {remote_dir}"],
            capture_output=True,
        )
        if tmp_local is not None:
            tmp_local.unlink(missing_ok=True)
