"""Tests for androsync.transport.adb - all subprocess calls are mocked.

No phone is required: every test replaces ``subprocess.run`` (as imported
into ``androsync.transport.adb``) with a small recording fake that returns
canned output for whichever ``adb`` invocation the code under test makes.
The real-device smoke test lives in ``test_adb_device.py`` and is skipped
entirely when ``adb devices`` shows nothing attached.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from androsync.ignore import IgnoreRules
from androsync.model import Side
from androsync.transport import adb as adb_module
from androsync.transport.adb import (
    Adb,
    AdbError,
    AdbTransport,
    DeviceInfo,
    list_devices,
    resolve_device,
)

NO_IGNORE = IgnoreRules([])


def _cp(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


class Recorder:
    """Records every argv passed to the fake ``subprocess.run``."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def record(self, argv):
        self.calls.append(list(argv))

    def joined(self, i: int) -> str:
        return " ".join(self.calls[i])


# ---------------------------------------------------------------------------
# list_devices / resolve_device
# ---------------------------------------------------------------------------

DEVICES_L_MIXED = (
    "List of devices attached\n"
    "ABCD1234EFGH           device usb:1-1 product:redfin model:Pixel_5 device:redfin transport_id:1\n"
    "192.168.1.5:5555       device product:foo model:SM_G991B device:o1s transport_id:3\n"
    "WXYZ00000000           unauthorized usb:1-2\n"
    "\n"
)


def test_list_devices_parses_usb_tcp_and_unauthorized(monkeypatch):
    def fake_run(argv, **kwargs):
        assert argv == ["adb", "devices", "-l"]
        return _cp(argv, 0, DEVICES_L_MIXED, "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    devices = list_devices()

    assert devices == [
        DeviceInfo(serial="ABCD1234EFGH", state="device", model="Pixel_5", transport="usb"),
        DeviceInfo(serial="192.168.1.5:5555", state="device", model="SM_G991B", transport="tcp"),
        DeviceInfo(serial="WXYZ00000000", state="unauthorized", model=None, transport="usb"),
    ]


def test_list_devices_raises_adb_error_on_nonzero_exit(monkeypatch):
    def fake_run(argv, **kwargs):
        return _cp(argv, 1, "", "adb: command not found somehow")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    with pytest.raises(AdbError):
        list_devices()


def _devices_output(*lines: str) -> str:
    return "List of devices attached\n" + "\n".join(lines) + ("\n" if lines else "")


def test_resolve_device_auto_zero_devices(monkeypatch):
    def fake_run(argv, **kwargs):
        return _cp(argv, 0, _devices_output(), "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    with pytest.raises(AdbError, match="no device found"):
        resolve_device("auto")


def test_resolve_device_auto_one_device(monkeypatch):
    def fake_run(argv, **kwargs):
        return _cp(argv, 0, _devices_output("SERIAL123     device"), "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    assert resolve_device("auto") == "SERIAL123"


def test_resolve_device_auto_two_devices(monkeypatch):
    def fake_run(argv, **kwargs):
        return _cp(argv, 0, _devices_output("SERIAL_A     device", "SERIAL_B     device"), "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    with pytest.raises(AdbError, match="more than one phone is attached"):
        resolve_device("auto")


def test_resolve_device_auto_only_unauthorized_mentions_hint(monkeypatch):
    def fake_run(argv, **kwargs):
        return _cp(argv, 0, _devices_output("SERIAL_A     unauthorized"), "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    with pytest.raises(AdbError, match="unauthorized"):
        resolve_device("auto")


def test_resolve_device_wifi_connects_and_returns_host_port(monkeypatch):
    """`wifi:IP:PORT` is now `auto` + that target (task 32b): nothing USB/TCP is
    connected, so it falls through to `adb connect`."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[:2] == ["adb", "devices"]:
            return _cp(argv, 0, _devices_output(), "")
        assert argv == ["adb", "connect", "192.168.1.20:5555"]
        return _cp(argv, 0, "connected to 192.168.1.20:5555", "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    assert resolve_device("wifi:192.168.1.20:5555") == "192.168.1.20:5555"
    assert calls == [["adb", "devices", "-l"], ["adb", "connect", "192.168.1.20:5555"]]


def test_resolve_device_wifi_connect_failure_raises(monkeypatch):
    def fake_run(argv, **kwargs):
        return _cp(argv, 0, "unable to connect to 192.168.1.20:5555: Connection refused", "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    with pytest.raises(AdbError, match="unable to connect"):
        resolve_device("wifi:192.168.1.20:5555")


# ---------------------------------------------------------------------------
# resolve_device: USB/TCP-already-connected preferred over a Wi-Fi target
# (task 32b - the actual bug: a pinned `wifi:` target used to always `adb
# connect`, ignoring a phone sitting right there on the USB cable)
# ---------------------------------------------------------------------------


def test_resolve_device_prefers_usb_over_wifi_target_no_connect_attempted(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _cp(argv, 0, _devices_output("USBSERIAL     device"), "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    assert resolve_device("auto", wifi_target="192.168.1.20:5555") == "USBSERIAL"
    assert calls == [["adb", "devices", "-l"]]  # no `adb connect` at all


def test_resolve_device_prefers_already_connected_tcp_over_wifi_target(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _cp(argv, 0, _devices_output("192.168.1.20:5555     device"), "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    assert resolve_device("auto", wifi_target="192.168.1.20:5555") == "192.168.1.20:5555"
    assert calls == [["adb", "devices", "-l"]]  # already connected, no `adb connect` needed


def test_resolve_device_no_usb_no_tcp_falls_back_to_adb_connect(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[:2] == ["adb", "devices"]:
            return _cp(argv, 0, _devices_output(), "")
        return _cp(argv, 0, "connected to 192.168.1.20:5555", "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    assert resolve_device("auto", wifi_target="192.168.1.20:5555") == "192.168.1.20:5555"
    assert calls == [["adb", "devices", "-l"], ["adb", "connect", "192.168.1.20:5555"]]


def test_resolve_device_connect_failure_message_names_the_target(monkeypatch):
    def fake_run(argv, **kwargs):
        if argv[:2] == ["adb", "devices"]:
            return _cp(argv, 0, _devices_output(), "")
        return _cp(argv, 1, "", "failed to connect to '192.168.1.20:5555': Connection refused")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    with pytest.raises(AdbError, match="192.168.1.20:5555"):
        resolve_device("auto", wifi_target="192.168.1.20:5555")


def test_resolve_device_auto_no_wifi_target_and_nothing_connected_names_what_was_tried(monkeypatch):
    def fake_run(argv, **kwargs):
        return _cp(argv, 0, _devices_output(), "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    with pytest.raises(AdbError, match="no device found"):
        resolve_device("auto")


def test_resolve_device_literal_serial_found_and_ready(monkeypatch):
    def fake_run(argv, **kwargs):
        return _cp(argv, 0, _devices_output("MYSERIAL     device"), "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    assert resolve_device("MYSERIAL") == "MYSERIAL"


def test_resolve_device_literal_serial_not_found(monkeypatch):
    def fake_run(argv, **kwargs):
        return _cp(argv, 0, _devices_output("OTHER     device"), "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    with pytest.raises(AdbError, match="not found"):
        resolve_device("MYSERIAL")


def test_resolve_device_literal_serial_unauthorized(monkeypatch):
    def fake_run(argv, **kwargs):
        return _cp(argv, 0, _devices_output("MYSERIAL     unauthorized"), "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)

    with pytest.raises(AdbError, match="unauthorized"):
        resolve_device("MYSERIAL")


# ---------------------------------------------------------------------------
# AdbTransport.scan
# ---------------------------------------------------------------------------


def _make_transport(recorder: Recorder, monkeypatch) -> AdbTransport:
    adb = Adb(serial="DEV1")

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        raise AssertionError(f"unhandled call in this test: {argv} kwargs={kwargs}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    return AdbTransport(adb)


def test_scan_root_missing_returns_empty(monkeypatch):
    recorder = Recorder()

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        assert argv[-2:] == ["-d", "/sdcard/Missing"] or "test -d" in argv[-1]
        return _cp(argv, 1, "", "")  # test -d fails: root does not exist

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    result = transport.scan("/sdcard/Missing", NO_IGNORE)

    assert result == {}
    assert len(recorder.calls) == 1  # only the existence check, nothing else


def test_scan_parses_printf_nul_output_with_space_unicode_and_ignores(monkeypatch):
    recorder = Recorder()
    entries = [
        (100, 1699999999.123456, "plain.txt"),
        (200, 1700000000.5, "a folder/file with space.txt"),
        (300, 1700000001.0, "café_日本語.txt"),
        (50, 1700000002.0, "notes.tmp"),  # ignored by pattern
        (10, 1700000003.0, "cache/inner/data.bin"),  # ignored: whole dir pruned
    ]
    payload = b"".join(
        f"{size}\t{mtime}\t{name}".encode("utf-8") + b"\0" for size, mtime, name in entries
    )

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "test -d" in joined:
            return _cp(argv, 0, "", "")
        if "-maxdepth 0" in joined:
            return _cp(argv, 0, b"/sdcard\0", b"")
        if "-type f -printf" in joined:
            assert argv[:3] == ["adb", "-s", "DEV1"]
            assert "exec-out" in argv
            return _cp(argv, 0, payload, b"")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)
    ignore = IgnoreRules(["*.tmp", "cache/**"])

    snapshot = transport.scan("/sdcard/DCIM", ignore)

    assert set(snapshot) == {
        "plain.txt",
        "a folder/file with space.txt",
        "café_日本語.txt",
    }
    assert snapshot["plain.txt"].size == 100
    assert snapshot["plain.txt"].mtime == pytest.approx(1699999999.123456)
    assert snapshot["a folder/file with space.txt"].size == 200


def test_scan_falls_back_to_stat_when_printf_unsupported(monkeypatch):
    recorder = Recorder()
    stat_output = (
        "120 1699999999 /sdcard/DCIM/plain.txt\n"
        "240 1700000000 /sdcard/DCIM/sub dir/two words.txt\n"
    )

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "test -d" in joined:
            return _cp(argv, 0, "", "")
        if "-maxdepth 0" in joined:
            return _cp(argv, 1, b"", b"find: unrecognized: -printf")  # unsupported
        if "-exec stat" in joined:
            assert argv[1] == "shell" or (argv[0] == "adb" and "shell" in argv)
            return _cp(argv, 0, stat_output, "")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    snapshot = transport.scan("/sdcard/DCIM", NO_IGNORE)

    assert set(snapshot) == {"plain.txt", "sub dir/two words.txt"}
    assert snapshot["plain.txt"].size == 120
    assert snapshot["plain.txt"].mtime == 1699999999.0
    assert snapshot["sub dir/two words.txt"].size == 240


def test_scan_adds_trailing_slash_to_find_root(monkeypatch):
    """Regression: Samsung toybox ``find`` silently matches nothing when the
    root is a mount point (e.g. ``/sdcard``) given without a trailing slash.
    """
    recorder = Recorder()

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "test -d" in joined:
            return _cp(argv, 0, "", "")
        if "-maxdepth 0" in joined:
            return _cp(argv, 0, b"/sdcard\0", b"")
        if "-type f -printf" in joined:
            assert "find /sdcard/ " in joined
            return _cp(argv, 0, b"", b"")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.scan("/sdcard", NO_IGNORE)


# ---------------------------------------------------------------------------
# ls
# ---------------------------------------------------------------------------


def test_ls_parses_mode_string_not_gnu_type_char(monkeypatch):
    """Regression: toybox ``find -printf`` rejects ``%y`` ("bad -printf %y");
    only GNU find has that specifier. Use ``%M`` (the ls-style mode string)
    and derive is_dir from its leading character instead.
    """
    recorder = Recorder()
    entries = [
        ("drwxr-xr-x", 4096, "DCIM"),
        ("-rw-r--r--", 12345, "notes.txt"),
    ]
    payload = b"".join(
        f"{mode}\t{size}\t{name}".encode("utf-8") + b"\0" for mode, size, name in entries
    )

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "-maxdepth 0" in joined:
            return _cp(argv, 0, b"/sdcard\0", b"")
        if "-mindepth 1 -maxdepth 1 -printf" in joined:
            assert "find /sdcard/ " in joined
            assert "%y" not in joined
            return _cp(argv, 0, payload, b"")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    result = transport.ls("/sdcard")

    assert result == [("DCIM", True, 4096), ("notes.txt", False, 12345)]


# ---------------------------------------------------------------------------
# store: mkdir -p, push, touch only when mtime is not preserved
# ---------------------------------------------------------------------------


def test_store_skips_touch_when_push_preserves_mtime(monkeypatch, tmp_path):
    recorder = Recorder()
    target_mtime = 1_650_000_000.0
    tmp_file = tmp_path / "incoming.bin"
    tmp_file.write_bytes(b"data")

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if argv[-2] == "push" if len(argv) >= 2 else False:
            pass
        if "push" in argv and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "stat -c" in joined and ".androsync-selftest" in joined:
            # push preserved the pushed mtime (1_000_000_000)
            return _cp(argv, 0, "19 1000000000", "")
        if "rm -f" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "mkdir -p" in joined:
            return _cp(argv, 0, "", "")
        if "push" in argv:
            return _cp(argv, 0, "", "")
        if "mv -f" in " ".join(argv) or "rm -rf" in " ".join(argv):  # safe-push moves (task 36a)
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call in store(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.store(tmp_file, "/sdcard/Notes", "sub/pic.jpg", target_mtime)

    touch_calls = [c for c in recorder.calls if "touch -m" in " ".join(c)]
    assert touch_calls == []
    mkdir_calls = [c for c in recorder.calls if "mkdir -p" in " ".join(c)]
    assert any("/sdcard/Notes/sub" in " ".join(c) for c in mkdir_calls)
    # The push lands in the hidden incoming folder and is then moved into place (task 36a).
    push_calls = [c for c in recorder.calls if "push" in c]
    assert any(str(tmp_file) in c and c[-1].startswith("/sdcard/Notes/.androsync/incoming/") for c in push_calls)
    assert any(c[-1].startswith("mv -f /sdcard/Notes/.androsync/incoming/") and c[-1].endswith(" /sdcard/Notes/sub/pic.jpg") for c in recorder.calls)


def test_store_touches_mtime_when_push_does_not_preserve_it(monkeypatch, tmp_path):
    recorder = Recorder()
    target_mtime = 1_650_000_000.0
    tmp_file = tmp_path / "incoming.bin"
    tmp_file.write_bytes(b"data")

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "push" in argv and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "stat -c" in joined and ".androsync-selftest" in joined:
            # mtime was NOT preserved: reports "now" instead of the pushed value.
            return _cp(argv, 0, "19 1999999999", "")
        if "rm -f" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "mkdir -p" in joined:
            return _cp(argv, 0, "", "")
        if "touch -m -d @" in joined:
            return _cp(argv, 0, "", "")
        if "push" in argv:
            return _cp(argv, 0, "", "")
        if "mv -f" in " ".join(argv) or "rm -rf" in " ".join(argv):  # safe-push moves (task 36a)
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call in store(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.store(tmp_file, "/sdcard/Notes", "sub/pic.jpg", target_mtime)

    touch_calls = [c for c in recorder.calls if "touch -m -d @" in " ".join(c)]
    assert len(touch_calls) == 1
    assert f"@{int(target_mtime)}" in " ".join(touch_calls[0])


def test_store_touch_fallback_to_dash_t_when_dash_d_epoch_fails(monkeypatch, tmp_path):
    recorder = Recorder()
    target_mtime = 1_650_000_000.0
    tmp_file = tmp_path / "incoming.bin"
    tmp_file.write_bytes(b"data")

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "push" in argv and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "stat -c" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "19 1999999999", "")
        if "rm -f" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "mkdir -p" in joined:
            return _cp(argv, 0, "", "")
        if "touch -m -d @" in joined:
            return _cp(argv, 1, "", "touch: unrecognized option '-d'")
        if "touch -m -t" in joined:
            return _cp(argv, 0, "", "")
        if "push" in argv:
            return _cp(argv, 0, "", "")
        if "mv -f" in " ".join(argv) or "rm -rf" in " ".join(argv):  # safe-push moves (task 36a)
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call in store(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.store(tmp_file, "/sdcard/Notes", "sub/pic.jpg", target_mtime)

    fallback_calls = [c for c in recorder.calls if "touch -m -t" in " ".join(c)]
    assert len(fallback_calls) == 1
    assert "TZ=UTC" in " ".join(fallback_calls[0])


# ---------------------------------------------------------------------------
# store: media scanner broadcast (task 24)
# ---------------------------------------------------------------------------


def test_store_broadcasts_media_scan_for_dcim_path(monkeypatch, tmp_path):
    recorder = Recorder()
    target_mtime = 1_650_000_000.0
    tmp_file = tmp_path / "incoming.jpg"
    tmp_file.write_bytes(b"data")

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "push" in argv and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "stat -c" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "19 1000000000", "")  # mtime preserved
        if "rm -f" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "mkdir -p" in joined:
            return _cp(argv, 0, "", "")
        if "push" in argv:
            return _cp(argv, 0, "", "")
        if "am broadcast" in joined:
            return _cp(argv, 0, "", "")
        if "mv -f" in " ".join(argv) or "rm -rf" in " ".join(argv):  # safe-push moves (task 36a)
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call in store(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.store(tmp_file, "/sdcard/DCIM", "Camera/pic.jpg", target_mtime)

    broadcast_calls = [c for c in recorder.calls if "am broadcast" in " ".join(c)]
    assert len(broadcast_calls) == 1
    assert "android.intent.action.MEDIA_SCANNER_SCAN_FILE" in " ".join(broadcast_calls[0])
    assert "file:///sdcard/DCIM/Camera/pic.jpg" in " ".join(broadcast_calls[0])


@pytest.mark.parametrize(
    "root",
    ["/sdcard/DCIM", "/sdcard/Pictures", "/sdcard/Movies", "/sdcard/Music", "/sdcard/Download"],
)
def test_store_broadcasts_for_every_media_root(monkeypatch, tmp_path, root):
    recorder = Recorder()
    tmp_file = tmp_path / "incoming.bin"
    tmp_file.write_bytes(b"data")

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "push" in argv and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "stat -c" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "19 1000000000", "")
        if "rm -f" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "mkdir -p" in joined or "push" in argv or "am broadcast" in joined:
            return _cp(argv, 0, "", "")
        if "mv -f" in " ".join(argv) or "rm -rf" in " ".join(argv):  # safe-push moves (task 36a)
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call in store(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.store(tmp_file, root, "f.bin", 1_650_000_000.0)

    assert any("am broadcast" in " ".join(c) for c in recorder.calls)


def test_store_does_not_broadcast_for_non_media_path(monkeypatch, tmp_path):
    recorder = Recorder()
    tmp_file = tmp_path / "incoming.md"
    tmp_file.write_bytes(b"data")

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "push" in argv and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "stat -c" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "19 1000000000", "")
        if "rm -f" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "mkdir -p" in joined or "push" in argv:
            return _cp(argv, 0, "", "")
        if "mv -f" in " ".join(argv) or "rm -rf" in " ".join(argv):  # safe-push moves (task 36a)
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call in store(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.store(tmp_file, "/sdcard/Obsidian/Notes", "note.md", 1_650_000_000.0)

    assert not any("am broadcast" in " ".join(c) for c in recorder.calls)


def test_store_does_not_broadcast_for_a_dcim_lookalike_path(monkeypatch, tmp_path):
    """``/sdcard/DCIMBackup/...`` is not actually under ``/sdcard/DCIM/`` -
    a bare prefix match without the trailing slash would wrongly treat it
    as one.
    """
    recorder = Recorder()
    tmp_file = tmp_path / "incoming.bin"
    tmp_file.write_bytes(b"data")

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "push" in argv and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "stat -c" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "19 1000000000", "")
        if "rm -f" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "mkdir -p" in joined or "push" in argv:
            return _cp(argv, 0, "", "")
        if "mv -f" in " ".join(argv) or "rm -rf" in " ".join(argv):  # safe-push moves (task 36a)
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call in store(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.store(tmp_file, "/sdcard/DCIMBackup", "f.bin", 1_650_000_000.0)

    assert not any("am broadcast" in " ".join(c) for c in recorder.calls)


def test_store_many_broadcasts_media_scan_combined_with_touch_script(monkeypatch, tmp_path):
    recorder = Recorder()
    a, b = _make_two_files(tmp_path)

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "push" in argv and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "stat -c" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "19 1999999999", "")  # not preserved: touch needed too
        if "rm -f" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "push" in argv:
            return _cp(argv, 0, "", "")
        if "touch -m -d @" in joined:
            return _cp(argv, 0, "", "")
        if "mv -f" in " ".join(argv) or "rm -rf" in " ".join(argv):  # safe-push moves (task 36a)
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call in store_many(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.store_many(
        [
            (a, "/sdcard/DCIM", "Camera/a.jpg", 1_650_000_000.0),
            (b, "/sdcard/DCIM", "Camera/b.jpg", 1_650_000_001.0),
        ]
    )

    # One shell call carries both the touch lines and the broadcast lines.
    shell_calls = [c for c in recorder.calls if "touch -m -d @" in " ".join(c)]
    assert len(shell_calls) == 1
    script = shell_calls[0][-1]
    assert script.count("am broadcast") == 2
    assert "file:///sdcard/DCIM/Camera/a.jpg" in script
    assert "file:///sdcard/DCIM/Camera/b.jpg" in script


def test_store_many_broadcasts_only_for_media_files_in_a_mixed_batch(monkeypatch, tmp_path):
    recorder = Recorder()
    a, b = _make_two_files(tmp_path)

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "push" in argv and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "stat -c" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "19 1000000000", "")  # preserved: no touch, only the broadcast call
        if "rm -f" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "push" in argv or "am broadcast" in joined:
            return _cp(argv, 0, "", "")
        if "mv -f" in " ".join(argv) or "rm -rf" in " ".join(argv):  # safe-push moves (task 36a)
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call in store_many(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.store_many(
        [
            (a, "/sdcard/DCIM", "Camera/a.jpg", 1_650_000_000.0),
            (b, "/sdcard/Obsidian/Notes", "note.md", 1_650_000_001.0),
        ]
    )

    broadcast_calls = [c for c in recorder.calls if "am broadcast" in " ".join(c)]
    assert len(broadcast_calls) == 1  # one root's batch has a media file, the other doesn't
    assert "Camera/a.jpg" in " ".join(broadcast_calls[0])
    assert "note.md" not in " ".join(broadcast_calls[0])


# ---------------------------------------------------------------------------
# store_many / fetch_many: batching (task 24)
# ---------------------------------------------------------------------------


def _make_two_files(tmp_path):
    a = tmp_path / "a.bin"
    a.write_bytes(b"aaa")
    b = tmp_path / "b.bin"
    b.write_bytes(b"bbbbb")
    return a, b


def test_store_many_pushes_once_and_stages_files_at_their_rel_paths(monkeypatch, tmp_path):
    recorder = Recorder()
    a, b = _make_two_files(tmp_path)
    staged: dict[str, bytes] = {}

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "push" in argv and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "stat -c" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "19 1000000000", "")  # mtime preserved
        if "rm -f" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "push" in argv:
            staging_arg = argv[argv.index("push") + 1]
            staging_dir = Path(staging_arg[:-2])  # strip trailing "/."
            for path in staging_dir.rglob("*"):
                if path.is_file():
                    staged[str(path.relative_to(staging_dir))] = path.read_bytes()
            return _cp(argv, 0, "", "")
        if "mv -f" in " ".join(argv) or "rm -rf" in " ".join(argv):  # safe-push moves (task 36a)
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call in store_many(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.store_many(
        [
            (a, "/sdcard/Notes", "sub/a.bin", 1_650_000_000.0),
            (b, "/sdcard/Notes", "b.bin", 1_650_000_001.0),
        ]
    )

    push_calls = [c for c in recorder.calls if "push" in c and ".androsync-selftest" not in " ".join(c)]
    assert len(push_calls) == 1  # one push for the whole batch
    assert staged == {"sub/a.bin": b"aaa", "b.bin": b"bbbbb"}
    touch_calls = [c for c in recorder.calls if "touch -m" in " ".join(c)]
    assert touch_calls == []  # mtime preserved: no touch needed


def test_store_many_touches_all_files_in_one_shell_call(monkeypatch, tmp_path):
    recorder = Recorder()
    a, b = _make_two_files(tmp_path)

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "push" in argv and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "stat -c" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "19 1999999999", "")  # not preserved
        if "rm -f" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "push" in argv:
            return _cp(argv, 0, "", "")
        if "touch -m -d @" in joined:
            return _cp(argv, 0, "", "")
        if "mv -f" in " ".join(argv) or "rm -rf" in " ".join(argv):  # safe-push moves (task 36a)
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call in store_many(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.store_many(
        [
            (a, "/sdcard/Notes", "sub/a.bin", 1_650_000_000.0),
            (b, "/sdcard/Notes", "b.bin", 1_650_000_001.0),
        ]
    )

    touch_calls = [c for c in recorder.calls if "touch -m -d @" in " ".join(c)]
    assert len(touch_calls) == 1  # one shell script touching both files
    script = touch_calls[0][-1]
    assert "/sdcard/Notes/sub/a.bin" in script
    assert "/sdcard/Notes/b.bin" in script
    assert "@1650000000" in script
    assert "@1650000001" in script


def test_store_many_falls_back_per_file_when_batch_touch_script_fails(monkeypatch, tmp_path):
    recorder = Recorder()
    a, b = _make_two_files(tmp_path)

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "push" in argv and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "stat -c" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "19 1999999999", "")
        if "rm -f" in joined and ".androsync-selftest" in joined:
            return _cp(argv, 0, "", "")
        if "push" in argv:
            return _cp(argv, 0, "", "")
        if joined.count("touch -m -d @") > 1:  # the batched script: fail it
            return _cp(argv, 1, "", "some file vanished mid-script")
        if "touch -m -d @" in joined:  # per-file fallback calls: succeed
            return _cp(argv, 0, "", "")
        if "mv -f" in " ".join(argv) or "rm -rf" in " ".join(argv):  # safe-push moves (task 36a)
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call in store_many(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.store_many(
        [
            (a, "/sdcard/Notes", "sub/a.bin", 1_650_000_000.0),
            (b, "/sdcard/Notes", "b.bin", 1_650_000_001.0),
        ]
    )

    single_touch_calls = [c for c in recorder.calls if " ".join(c).count("touch -m -d @") == 1]
    assert len(single_touch_calls) == 2  # fell back to one call per file


def test_store_many_empty_list_does_nothing(monkeypatch):
    def fake_run(argv, **kwargs):
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    AdbTransport(Adb(serial="DEV1")).store_many([])


def test_fetch_many_pulls_whole_directory_above_80_percent_threshold(monkeypatch, tmp_path):
    recorder = Recorder()
    tmp_a = tmp_path / "dest_a.bin"
    tmp_b = tmp_path / "dest_b.bin"

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "find" in joined and "wc -l" in joined:
            return _cp(argv, 0, "2\n", "")  # directory has exactly 2 files, both wanted: 100%
        if argv[-3:-1] == ["-a", "/sdcard/DCIM/Camera"]:
            staging = Path(argv[-1])
            pulled = staging / "Camera"
            pulled.mkdir(parents=True)
            (pulled / "a.jpg").write_bytes(b"AAA")
            (pulled / "b.jpg").write_bytes(b"BBB")
            return _cp(argv, 0, "2 files pulled", "")
        raise AssertionError(f"unexpected call in fetch_many(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.fetch_many(
        [
            ("/sdcard/DCIM", "Camera/a.jpg", tmp_a),
            ("/sdcard/DCIM", "Camera/b.jpg", tmp_b),
        ]
    )

    pull_calls = [c for c in recorder.calls if "pull" in c]
    assert len(pull_calls) == 1  # one directory pull, not two file pulls
    assert tmp_a.read_bytes() == b"AAA"
    assert tmp_b.read_bytes() == b"BBB"


def test_fetch_many_falls_back_to_individual_pulls_below_threshold(monkeypatch, tmp_path):
    recorder = Recorder()
    tmp_a = tmp_path / "dest_a.bin"

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "find" in joined and "wc -l" in joined:
            return _cp(argv, 0, "10\n", "")  # 10 files total, only 2 wanted: 20% < 80%
        if "pull" in argv and "-a" in argv and argv[-2].startswith("/sdcard/DCIM/Camera/"):
            Path(argv[-1]).write_bytes(b"AAA")
            return _cp(argv, 0, "1 file pulled", "")
        raise AssertionError(f"unexpected call in fetch_many(): {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.fetch_many(
        [
            ("/sdcard/DCIM", "Camera/a.jpg", tmp_a),
            ("/sdcard/DCIM", "Camera/z.jpg", tmp_path / "dest_z.bin"),
        ]
    )

    dir_pull_calls = [c for c in recorder.calls if "pull" in c and "Camera/a.jpg" not in " ".join(c) and "Camera/z.jpg" not in " ".join(c)]
    assert dir_pull_calls == []  # no whole-directory pull attempted
    assert tmp_a.read_bytes() == b"AAA"


def test_fetch_many_single_file_group_skips_threshold_check(monkeypatch, tmp_path):
    recorder = Recorder()
    tmp_a = tmp_path / "dest_a.bin"

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        if "wc -l" in " ".join(argv):
            raise AssertionError("should not check directory population for a single-item group")
        if "pull" in argv:
            Path(argv[-1]).write_bytes(b"AAA")
            return _cp(argv, 0, "1 file pulled", "")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.fetch_many([("/sdcard/DCIM", "Camera/a.jpg", tmp_a)])

    assert tmp_a.read_bytes() == b"AAA"


def test_fetch_many_empty_list_does_nothing(monkeypatch):
    def fake_run(argv, **kwargs):
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    AdbTransport(Adb(serial="DEV1")).fetch_many([])


# ---------------------------------------------------------------------------
# hash_many: sha256sum / md5sum fallback, spaces, unicode (task 29a)
# ---------------------------------------------------------------------------


def test_hash_many_parses_sha256sum_output_with_spaces_and_unicode(monkeypatch):
    recorder = Recorder()
    sha_a = "a" * 64
    sha_b = "b" * 64
    sha_c = "c" * 64
    payload = (
        f"{sha_a}  plain.txt\n"
        f"{sha_b}  a folder/file with space.txt\n"
        f"{sha_c}  café_日本語.txt\n"
    ).encode("utf-8")

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "command -v sha256sum" in joined:
            return _cp(argv, 0, "/system/bin/sha256sum\n", "")
        if "sha256sum --" in joined:
            assert "exec-out" in argv
            assert "cd /sdcard/DCIM &&" in joined
            return _cp(argv, 0, payload, b"")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    transport = AdbTransport(Adb(serial="DEV1"))

    result = transport.hash_many(
        "/sdcard/DCIM", ["plain.txt", "a folder/file with space.txt", "café_日本語.txt"]
    )

    assert result == {
        "plain.txt": sha_a,
        "a folder/file with space.txt": sha_b,
        "café_日本語.txt": sha_c,
    }


def test_hash_many_falls_back_to_md5sum_when_sha256sum_missing(monkeypatch):
    md5_hex = "d" * 32
    payload = f"{md5_hex}  plain.txt\n".encode("utf-8")

    def fake_run(argv, **kwargs):
        joined = " ".join(argv)
        if "command -v sha256sum" in joined:
            return _cp(argv, 1, "", "")  # not found
        if "md5sum --" in joined:
            return _cp(argv, 0, payload, b"")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    transport = AdbTransport(Adb(serial="DEV1"))

    result = transport.hash_many("/sdcard/DCIM", ["plain.txt"])

    assert result == {"plain.txt": f"md5:{md5_hex}"}


def test_hash_many_batches_at_200_paths_per_call(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        joined = " ".join(argv)
        if "command -v sha256sum" in joined:
            return _cp(argv, 0, "/system/bin/sha256sum\n", "")
        if "sha256sum --" in joined:
            calls.append(argv)
            names = shlex.split(joined.split("&&", 1)[1])[2:]  # drop "sha256sum --"
            payload = "".join(f"{'e' * 64}  {name}\n" for name in names).encode("utf-8")
            return _cp(argv, 0, payload, b"")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    transport = AdbTransport(Adb(serial="DEV1"))
    rels = [f"f{i}.txt" for i in range(250)]

    result = transport.hash_many("/sdcard/DCIM", rels)

    assert len(calls) == 2  # 200 + 50
    assert len(result) == 250


def test_hash_many_empty_list_does_nothing(monkeypatch):
    def fake_run(argv, **kwargs):
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    assert AdbTransport(Adb(serial="DEV1")).hash_many("/sdcard/DCIM", []) == {}


# ---------------------------------------------------------------------------
# delete: rm then rmdir chain
# ---------------------------------------------------------------------------


def test_delete_removes_file_then_prunes_empty_parents(monkeypatch):
    recorder = Recorder()

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "rm -f" in joined:
            return _cp(argv, 0, "", "")
        if "rmdir" in joined:
            # both /sdcard/DCIM/a/b and /sdcard/DCIM/a are empty and removable
            return _cp(argv, 0, "", "")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.delete("/sdcard/DCIM", "a/b/c.txt")

    rm_calls = [c for c in recorder.calls if "rm -f" in " ".join(c)]
    rmdir_calls = [" ".join(c) for c in recorder.calls if "rmdir" in " ".join(c)]
    assert len(rm_calls) == 1
    assert any("/sdcard/DCIM/a/b" in c for c in rmdir_calls)
    assert any("/sdcard/DCIM/a" in c and "/sdcard/DCIM/a/b" not in c for c in rmdir_calls)
    # never tries to rmdir the root itself
    assert not any(c.endswith("rmdir '/sdcard/DCIM'") for c in rmdir_calls)


def test_delete_stops_pruning_at_first_nonempty_parent(monkeypatch):
    recorder = Recorder()
    call_count = {"rmdir": 0}

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if "rm -f" in joined:
            return _cp(argv, 0, "", "")
        if "rmdir" in joined:
            call_count["rmdir"] += 1
            # first rmdir (deepest dir) succeeds, second (has sibling) fails
            return _cp(argv, 0 if call_count["rmdir"] == 1 else 1, "", "Directory not empty")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.delete("/sdcard/DCIM", "a/b/c.txt")

    assert call_count["rmdir"] == 2


# ---------------------------------------------------------------------------
# quoting
# ---------------------------------------------------------------------------


def test_paths_with_space_and_quote_are_shell_quoted(monkeypatch):
    recorder = Recorder()
    tricky_root = "/sdcard/My Folder's Stuff"

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        return _cp(argv, 0, "", "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.mkdirs(tricky_root, "")

    shell_arg = recorder.calls[-1][-1]
    # The quoted argument must shlex-split back to the exact original path.
    parts = shlex.split(shell_arg)
    assert tricky_root in parts


def test_delete_path_with_space_and_quote(monkeypatch):
    recorder = Recorder()

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        return _cp(argv, 0, "", "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="DEV1")
    transport = AdbTransport(adb)

    transport.delete("/sdcard/It's Complicated", "a b/c'd.txt")

    rm_call = next(c for c in recorder.calls if "rm -f" in " ".join(c))
    shell_arg = rm_call[-1]
    parts = shlex.split(shell_arg)
    assert "/sdcard/It's Complicated/a b/c'd.txt" in parts


# ---------------------------------------------------------------------------
# Adb.run / shell / exec_out basics
# ---------------------------------------------------------------------------


def test_adb_run_prepends_serial(monkeypatch):
    recorder = Recorder()

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        return _cp(argv, 0, "ok", "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="SER1")
    adb.run(["shell", "echo hi"])

    assert recorder.calls[0] == ["adb", "-s", "SER1", "shell", "echo hi"]


def test_adb_run_without_serial_omits_dash_s(monkeypatch):
    recorder = Recorder()

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        return _cp(argv, 0, "ok", "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb()
    adb.run(["devices"])

    assert recorder.calls[0] == ["adb", "devices"]


def test_adb_run_raises_adb_error_with_stderr_and_hint(monkeypatch):
    def fake_run(argv, **kwargs):
        return _cp(argv, 1, "", "error: device unauthorized.")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="SER1")

    with pytest.raises(AdbError, match="unauthorized"):
        adb.run(["shell", "echo hi"])


def test_adb_run_check_false_does_not_raise(monkeypatch):
    def fake_run(argv, **kwargs):
        return _cp(argv, 1, "", "boom")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="SER1")

    result = adb.run(["shell", "false"], check=False)
    assert result.returncode == 1


def test_adb_shell_returns_stdout_text(monkeypatch):
    def fake_run(argv, **kwargs):
        assert kwargs.get("text") is True
        return _cp(argv, 0, "hello\n", "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="SER1")

    assert adb.shell("echo hello") == "hello\n"


def test_adb_exec_out_returns_bytes(monkeypatch):
    def fake_run(argv, **kwargs):
        assert kwargs.get("text") is False
        return _cp(argv, 0, b"\x00\x01binary", b"")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="SER1")

    assert adb.exec_out("cat somefile") == b"\x00\x01binary"


def test_adb_run_logs_command_when_log_callable_given(monkeypatch):
    logged = []

    def fake_run(argv, **kwargs):
        return _cp(argv, 0, "", "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    adb = Adb(serial="SER1", log=logged.append)
    adb.run(["shell", "echo hi"])

    assert len(logged) == 1
    assert "shell" in logged[0]


def test_transport_side_and_label():
    adb = Adb(serial="SER1")
    transport = AdbTransport(adb)
    assert transport.side is Side.REMOTE
    assert transport.label == "phone"


# ---------------------------------------------------------------------------
# phone folder rules (task 36c, issue #8)
# ---------------------------------------------------------------------------


def _dirs_that_exist(monkeypatch, existing: set[str]):
    """Fake adb where `test -d <path>` succeeds only for ``existing``; everything else succeeds."""
    recorder = Recorder()

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        command = argv[-1]
        if command.startswith("test -d "):
            return _cp(argv, 0 if shlex.split(command)[-1] in existing else 1)
        return _cp(argv, 0)

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    return recorder, AdbTransport(Adb(serial="DEV1"))


@pytest.mark.parametrize("root", ["/", "/data", "/system/app", "/storage", "/sdcard/Android/data"])
def test_phone_transport_refuses_roots_outside_shared_storage(monkeypatch, tmp_path, root):
    recorder, transport = _dirs_that_exist(monkeypatch, set())
    with pytest.raises(AdbError):
        transport.exists(root)
    with pytest.raises(AdbError):
        transport.mkdirs(root, "")
    with pytest.raises(AdbError):
        transport.delete(root, "a.txt")
    with pytest.raises(AdbError):
        transport.rename(root, "a.txt", "b.txt")
    source = tmp_path / "f"
    source.write_text("x")
    with pytest.raises(AdbError):
        transport.store(source, root, "a.txt", 1.0)
    with pytest.raises(AdbError):
        transport.store_many([(source, root, "a.txt", 1.0)])
    assert recorder.calls == []  # nothing reached the phone


def test_phone_transport_maps_the_storage_label_before_touching_the_phone(monkeypatch):
    recorder, transport = _dirs_that_exist(monkeypatch, {"/sdcard/Inbox"})
    assert transport.exists("/Internal storage/Inbox/") is True
    assert shlex.split(recorder.calls[-1][-1])[-1] == "/sdcard/Inbox"


def test_mkdirs_of_a_pair_root_needs_an_existing_parent(monkeypatch):
    recorder, transport = _dirs_that_exist(monkeypatch, {"/sdcard"})
    transport.mkdirs("/sdcard/Inbox", "")
    assert recorder.calls[-1][-1] == "mkdir -p /sdcard/Inbox"


def test_mkdirs_of_a_pair_root_allows_two_new_levels(monkeypatch):
    recorder, transport = _dirs_that_exist(monkeypatch, {"/sdcard"})
    transport.mkdirs("/sdcard/Documents/Vault", "")
    assert recorder.calls[-1][-1] == "mkdir -p /sdcard/Documents/Vault"


def test_mkdirs_of_a_pair_root_refuses_three_new_levels(monkeypatch):
    recorder, transport = _dirs_that_exist(monkeypatch, {"/sdcard"})
    with pytest.raises(AdbError, match="Create the parent folder"):
        transport.mkdirs("/sdcard/A/B/C", "")
    assert not any(call[-1].startswith("mkdir") for call in recorder.calls)


def test_mkdirs_of_a_sd_card_folder_checks_only_inside_the_card(monkeypatch):
    recorder, transport = _dirs_that_exist(monkeypatch, {"/storage/ABCD-1234"})
    transport.mkdirs("/storage/ABCD-1234/Music", "")
    assert recorder.calls[-1][-1] == "mkdir -p /storage/ABCD-1234/Music"


# ---------------------------------------------------------------------------
# Stop: killing an adb call in flight, and never leaving a truncated file (task 36a, issue #6)
# ---------------------------------------------------------------------------


def test_abort_kills_the_running_adb_process_and_raises_cancelled():
    """A real (fake-adb) process: abort() from another thread ends the call in well under its 30 s of sleep."""
    import sys
    import threading
    import time

    from androsync.transport.adb import AdbCancelled

    adb = Adb(serial=None, adb_path=sys.executable)
    # `python -c "import time; time.sleep(30)"` stands in for a long adb pull; _argv adds no serial here.
    done = {}

    def worker():
        started = time.monotonic()
        try:
            adb.run(["-c", "import time; time.sleep(30)"], timeout=60)
            done["result"] = "returned"
        except AdbCancelled:
            done["result"] = "cancelled"
        done["seconds"] = time.monotonic() - started

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.5)
    adb.abort()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert done["result"] == "cancelled"
    assert done["seconds"] < 8


def test_after_abort_every_call_raises_cancelled_without_starting_a_process(monkeypatch):
    from androsync.transport.adb import AdbCancelled

    started = []
    monkeypatch.setattr(adb_module, "_run_process", lambda argv, **kw: started.append(argv))
    adb = Adb(serial="DEV1")
    adb.abort()
    with pytest.raises(AdbCancelled):
        adb.run(["shell", "ls"])
    assert started == []


def test_adb_cancelled_is_a_transport_cancel_and_an_adb_error():
    from androsync.transport.adb import AdbCancelled
    from androsync.transport.base import TransportCancelled

    assert issubclass(AdbCancelled, AdbError) and issubclass(AdbCancelled, TransportCancelled)


def test_transport_abort_reaches_the_adb_wrapper(monkeypatch):
    monkeypatch.setattr(adb_module, "_run_process", lambda argv, **kw: _cp(argv, 0, "", ""))
    transport = AdbTransport(Adb(serial="DEV1"))
    transport.abort()
    with pytest.raises(adb_module.AdbCancelled):
        transport.exists("/sdcard")


def test_a_killed_push_never_reaches_the_final_path(monkeypatch, tmp_path):
    """Stop during the push leaves nothing at the destination: the move into place never runs."""
    recorder = Recorder()
    source = tmp_path / "f.bin"
    source.write_bytes(b"x" * 10)
    transport = AdbTransport(Adb(serial="DEV1"))

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        if "push" in argv:
            transport.abort()  # the user presses Stop while the push is running
            return _cp(argv, -15, "", "")
        return _cp(argv, 0, "", "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    with pytest.raises(adb_module.AdbCancelled):
        transport.store(source, "/sdcard/Notes", "sub/f.bin", 1.0)

    assert not any(c[-1].startswith("mv -f") for c in recorder.calls)
    assert not any(c[-1] == "/sdcard/Notes/sub/f.bin" for c in recorder.calls)


def test_store_many_moves_files_into_place_only_after_the_push(monkeypatch, tmp_path):
    recorder = Recorder()
    a = tmp_path / "a"
    a.write_bytes(b"a")
    b = tmp_path / "b"
    b.write_bytes(b"b")

    def fake_run(argv, **kwargs):
        recorder.record(argv)
        joined = " ".join(argv)
        if ".androsync-selftest" in joined and "stat -c" in joined:
            return _cp(argv, 0, "19 1000000000", "")
        return _cp(argv, 0, "", "")

    monkeypatch.setattr(adb_module, "_run_process", fake_run)
    AdbTransport(Adb(serial="DEV1")).store_many([(a, "/sdcard/Notes", "x/a.txt", 1.0), (b, "/sdcard/Notes", "b.txt", 2.0)])

    pushes = [i for i, c in enumerate(recorder.calls) if "push" in c and "/.androsync/incoming/" in c[-1]]
    moves = [i for i, c in enumerate(recorder.calls) if c[-1].startswith("mkdir -p /sdcard/Notes/x") and "mv -f" in c[-1]]
    assert pushes and moves and max(pushes) < min(moves)
    script = recorder.calls[moves[0]][-1]
    assert "mv -f /sdcard/Notes/.androsync/incoming/" in script and script.rstrip().endswith("/sdcard/Notes/.androsync/incoming/" + script.rsplit("/", 1)[-1])
    assert "rm -rf /sdcard/Notes/.androsync/incoming/" in script
