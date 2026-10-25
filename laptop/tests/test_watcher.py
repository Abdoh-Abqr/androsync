"""Tests for androsync.daemon.watcher (DeviceWatcher frame parsing/restart, Daemon callbacks).

No real ``adb`` process or device is ever touched: DeviceWatcher tests feed
it a fake ``Popen`` whose ``stdout`` is an in-memory ``BytesIO`` of
hand-built ``adb track-devices`` frames, and Daemon tests monkeypatch
``subprocess.run`` (for the ``adb reverse`` call) plus ``transport_factory``
(to two ``LocalDirTransport``s, same trick as test_jobs.py/test_server.py)
and ``watcher_factory`` (to a no-op stand-in, so starting the daemon never
spawns a real background ``adb track-devices`` thread).
"""

from __future__ import annotations

import io
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from androsync.config import Config, load_config
from androsync.daemon import watcher as watcher_mod
from androsync.daemon.watcher import Daemon, DeviceWatcher, WifiReconnector, _parse_device_list, _read_frame
from androsync.model import Side
from androsync.transport.local import LocalDirTransport


@pytest.fixture(autouse=True)
def _xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg-runtime"))


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class SleepyLocalTransport(LocalDirTransport):
    """LocalDirTransport whose ``fetch`` sleeps first, to keep a job "running" long enough to
    observe an in-flight cancellation decision (same trick as test_jobs.py/test_server.py)."""

    def __init__(self, side: Side, delay: float) -> None:
        super().__init__(side)
        self.delay = delay
        self._aborted = threading.Event()

    def abort(self) -> None:
        """Like the real phone transport: a Stop interrupts the transfer that is in flight (task 36a)."""
        self._aborted.set()

    def fetch(self, root, rel, tmp_path) -> None:  # noqa: ANN001 - match base signature
        from androsync.transport.base import TransportCancelled

        if self._aborted.wait(self.delay):
            raise TransportCancelled("cancelled")
        super().fetch(root, rel, tmp_path)


def _encode_frame(text: str) -> bytes:
    data = text.encode("utf-8")
    return f"{len(data):04x}".encode("ascii") + data


class FakePopen:
    """Stands in for subprocess.Popen: a readable ``stdout`` plus poll/terminate."""

    def __init__(self, payload: bytes) -> None:
        self.stdout = io.BytesIO(payload)
        self._terminated = False
        self.returncode: int | None = None

    def poll(self):
        return 0 if self._terminated else None

    def terminate(self):
        self._terminated = True
        self.returncode = -15


# ---------------------------------------------------------------------------
# frame parsing helpers
# ---------------------------------------------------------------------------


def test_read_frame_parses_length_prefixed_payload():
    stream = io.BytesIO(_encode_frame("serial1\tdevice\n") + _encode_frame("x"))
    first = _read_frame(stream)
    assert first == b"serial1\tdevice\n"
    second = _read_frame(stream)
    assert second == b"x"
    assert _read_frame(stream) is None  # clean EOF


def test_read_frame_returns_none_on_short_read():
    # Header says 10 bytes but only 3 are actually there -> EOF partway through.
    stream = io.BytesIO(b"000aabc")
    assert _read_frame(stream) is None


def test_parse_device_list():
    parsed = _parse_device_list("serial1\tdevice\nserial2\tunauthorized\n\n")
    assert parsed == {"serial1": "device", "serial2": "unauthorized"}


# ---------------------------------------------------------------------------
# DeviceWatcher: connect/disconnect + restart
# ---------------------------------------------------------------------------


def test_watcher_reports_connect_then_disconnect():
    frames = _encode_frame("serial1\tdevice\nserial2\tdevice\n") + _encode_frame("serial1\tdevice\n")
    popen = FakePopen(frames)
    events: list[tuple[str, str]] = []
    disconnected = threading.Event()

    def on_connect(serial):
        events.append(("connect", serial))

    def on_disconnect(serial):
        events.append(("disconnect", serial))
        disconnected.set()

    dw = DeviceWatcher(
        on_connect=on_connect,
        on_disconnect=on_disconnect,
        popen_factory=lambda *a, **k: popen,
        restart_delay=100.0,  # never actually restart during this test
    )
    dw.start()
    try:
        assert disconnected.wait(2.0)
    finally:
        dw.stop()
        dw.join(timeout=2.0)

    assert ("connect", "serial1") in events
    assert ("connect", "serial2") in events
    assert ("disconnect", "serial2") in events
    assert ("disconnect", "serial1") not in events  # serial1 stayed connected throughout


def test_watcher_does_not_report_non_device_states():
    frames = _encode_frame("serial1\tunauthorized\n")
    popen = FakePopen(frames)
    events: list[tuple[str, str]] = []

    dw = DeviceWatcher(
        on_connect=lambda s: events.append(("connect", s)),
        on_disconnect=lambda s: events.append(("disconnect", s)),
        popen_factory=lambda *a, **k: popen,
        restart_delay=100.0,
    )
    dw.start()
    try:
        wait_until(lambda: dw.devices.get("serial1") == "unauthorized")
    finally:
        dw.stop()
        dw.join(timeout=2.0)

    assert events == []
    assert dw.devices == {"serial1": "unauthorized"}


def test_watcher_restarts_process_after_it_exits():
    calls: list[int] = []
    second_call = threading.Event()

    def factory(*args, **kwargs):
        calls.append(1)
        if len(calls) >= 2:
            second_call.set()
        return FakePopen(_encode_frame("serial1\tdevice\n"))

    dw = DeviceWatcher(popen_factory=factory, restart_delay=0.05)
    dw.start()
    try:
        assert second_call.wait(3.0)
    finally:
        dw.stop()
        dw.join(timeout=2.0)

    assert len(calls) >= 2


def test_watcher_stop_is_idempotent_and_thread_exits():
    dw = DeviceWatcher(popen_factory=lambda *a, **k: FakePopen(b""), restart_delay=100.0)
    dw.start()
    wait_until(lambda: not dw.is_alive() or dw._proc is not None)
    dw.stop()
    dw.stop()  # must not raise
    dw.join(timeout=2.0)
    assert not dw.is_alive()


# ---------------------------------------------------------------------------
# Daemon: on_connect / on_disconnect wiring
# ---------------------------------------------------------------------------


class NullWatcher:
    """A DeviceWatcher stand-in that never starts a real background thread."""

    def __init__(self, on_connect=None, on_disconnect=None) -> None:
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.devices: dict[str, str] = {}

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def join(self, timeout=None) -> None:
        pass


def build_config(
    tmp_path: Path,
    *,
    auto_sync: bool,
    token: str = "s3cr3t",
    wifi_target: str | None = None,
    enable_tcpip_on_usb: bool = True,
) -> Config:
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    (local / "a.txt").write_text("hello")
    text = f"""\
device: auto
service:
  listen: [127.0.0.1]
  port: 8765
  token: {token}
  auto_sync_on_connect: {"true" if auto_sync else "false"}
  notify: false
pairs:
  - name: p1
    local: {local}
    remote: {remote}
    direction: two-way
"""
    path = tmp_path / "config.yaml"
    path.write_text(text)
    config = load_config(path)
    config.service.port = 0  # ask the OS for a free port instead of risking a clash
    config.wifi_target = wifi_target
    config.service.enable_tcpip_on_usb = enable_tcpip_on_usb
    return config


def make_daemon(config: Config, config_path: Path = Path("/nonexistent/config.yaml")) -> Daemon:
    daemon = Daemon(lambda: config, "0.1.0-test", config_path=config_path)
    daemon.transport_factory = lambda cfg: (LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE))
    daemon.watcher_factory = lambda **kwargs: NullWatcher(**kwargs)
    return daemon


def test_daemon_writes_and_removes_pid_file(tmp_path):
    """Task 31a: `service_state.detect_state` relies on this pid file existing while running."""
    from androsync import service_state

    config = build_config(tmp_path, auto_sync=False)
    daemon = make_daemon(config)

    daemon.start()
    try:
        pid_path = service_state.pid_file_path()
        assert pid_path.exists()
        assert int(pid_path.read_text()) == os.getpid()
    finally:
        daemon.stop()

    assert not service_state.pid_file_path().exists()


def test_daemon_start_requires_a_token(tmp_path):
    config = build_config(tmp_path, auto_sync=False, token="")
    daemon = make_daemon(config)
    from androsync.config import ConfigError

    with pytest.raises(ConfigError):
        daemon.start()


def test_on_connect_runs_adb_reverse_and_submits_when_auto_sync_on(tmp_path, monkeypatch):
    config = build_config(tmp_path, auto_sync=True)
    daemon = make_daemon(config)

    reverse_calls = []

    def fake_run(argv, **kwargs):
        reverse_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(watcher_mod.subprocess, "run", fake_run)

    daemon.start()
    try:
        daemon.on_connect("USBSERIAL1")

        assert reverse_calls, "adb reverse should have been attempted"
        assert reverse_calls[0][:3] == ["adb", "-s", "USBSERIAL1"]
        assert "reverse" in reverse_calls[0]

        assert wait_until(lambda: len(daemon._runner.list()) == 1)
        assert daemon.device_info() == {"transport": "usb", "device": "USBSERIAL1", "devices": []}
    finally:
        daemon.stop()


def test_on_connect_enables_tcpip_5555_when_wifi_target_uses_that_port(tmp_path, monkeypatch):
    config = build_config(tmp_path, auto_sync=False, wifi_target="100.64.0.20:5555")
    daemon = make_daemon(config)

    calls = []
    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(list(argv)) or subprocess.CompletedProcess(argv, 0, "", ""),
    )

    daemon.start()
    try:
        daemon.on_connect("USBSERIAL1")
    finally:
        daemon.stop()

    tcpip_calls = [c for c in calls if "tcpip" in c]
    assert tcpip_calls == [["adb", "-s", "USBSERIAL1", "tcpip", "5555"]]


def test_on_connect_skips_tcpip_when_disabled(tmp_path, monkeypatch):
    config = build_config(
        tmp_path, auto_sync=False, wifi_target="100.64.0.20:5555", enable_tcpip_on_usb=False
    )
    daemon = make_daemon(config)

    calls = []
    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(list(argv)) or subprocess.CompletedProcess(argv, 0, "", ""),
    )

    daemon.start()
    try:
        daemon.on_connect("USBSERIAL1")
    finally:
        daemon.stop()

    assert not [c for c in calls if "tcpip" in c]


def test_on_connect_skips_tcpip_when_wifi_target_port_is_not_5555(tmp_path, monkeypatch):
    config = build_config(tmp_path, auto_sync=False, wifi_target="100.64.0.20:37123")
    daemon = make_daemon(config)

    calls = []
    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(list(argv)) or subprocess.CompletedProcess(argv, 0, "", ""),
    )

    daemon.start()
    try:
        daemon.on_connect("USBSERIAL1")
    finally:
        daemon.stop()

    assert not [c for c in calls if "tcpip" in c]


def test_on_connect_skips_tcpip_when_no_wifi_target_configured(tmp_path, monkeypatch):
    config = build_config(tmp_path, auto_sync=False)
    daemon = make_daemon(config)

    calls = []
    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(list(argv)) or subprocess.CompletedProcess(argv, 0, "", ""),
    )

    daemon.start()
    try:
        daemon.on_connect("USBSERIAL1")
    finally:
        daemon.stop()

    assert not [c for c in calls if "tcpip" in c]


def test_on_connect_does_not_submit_when_auto_sync_off(tmp_path, monkeypatch):
    config = build_config(tmp_path, auto_sync=False)
    daemon = make_daemon(config)

    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )

    daemon.start()
    try:
        daemon.on_connect("USBSERIAL1")
        time.sleep(0.2)  # give a wrongly-submitted job a chance to appear
        assert daemon._runner.list() == []
    finally:
        daemon.stop()


def test_on_disconnect_clears_device_info_and_cancels_running_job(tmp_path, monkeypatch):
    config = build_config(tmp_path, auto_sync=True)
    daemon = make_daemon(config)

    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )

    daemon.start()
    try:
        daemon.on_connect("USBSERIAL1")
        assert wait_until(lambda: len(daemon._runner.list()) == 1)

        daemon.on_disconnect("USBSERIAL1")
        assert daemon.device_info() == {"transport": None, "device": None, "devices": []}
    finally:
        daemon.stop()


def test_on_disconnect_logs_at_info_level(tmp_path, monkeypatch, caplog):
    """Regression: on_disconnect used to log nothing at all - only on_connect
    did (via the `adb reverse` line) - so `journalctl` showed connects but no
    trace of a disconnect ever happening.
    """
    config = build_config(tmp_path, auto_sync=False)
    daemon = make_daemon(config)

    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )

    with caplog.at_level("INFO", logger=watcher_mod._LOG.name):
        daemon.on_connect("USBSERIAL1")
        daemon.on_disconnect("USBSERIAL1")

    assert any("USBSERIAL1" in record.getMessage() and "disconnect" in record.getMessage().lower() for record in caplog.records)


def test_device_info_reports_tcp_for_wifi_serial(tmp_path, monkeypatch):
    config = build_config(tmp_path, auto_sync=False)
    daemon = make_daemon(config)
    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )
    daemon.start()
    try:
        daemon.on_connect("192.168.1.20:5555")
        assert daemon.device_info() == {"transport": "tcp", "device": "192.168.1.20:5555", "devices": []}
    finally:
        daemon.stop()


def test_on_connect_skips_adb_reverse_for_a_tcp_serial(tmp_path, monkeypatch):
    """Task 25: a wifi/TCP device reaches the API directly over Tailscale/LAN,
    so `adb reverse` (only meaningful for the USB loopback path) must not
    be attempted for it.
    """
    config = build_config(tmp_path, auto_sync=False)
    daemon = make_daemon(config)

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(watcher_mod.subprocess, "run", fake_run)

    daemon.start()
    try:
        daemon.on_connect("100.64.0.20:5555")
        assert not any("reverse" in c for c in calls)
    finally:
        daemon.stop()


# ---------------------------------------------------------------------------
# WifiReconnector (task 25)
# ---------------------------------------------------------------------------


def test_wifi_reconnector_connects_when_not_connected(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="connected", stderr="")

    monkeypatch.setattr(watcher_mod.subprocess, "run", fake_run)

    reconnector = WifiReconnector("100.64.0.20:5555", is_connected=lambda: False, interval=0.05)
    reconnector.start()
    try:
        assert wait_until(lambda: len(calls) >= 2, timeout=1.0)
        assert calls[0] == ["adb", "connect", "100.64.0.20:5555"]
    finally:
        reconnector.stop()
        reconnector.join(timeout=2.0)


def test_wifi_reconnector_skips_connect_when_already_connected(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(watcher_mod.subprocess, "run", fake_run)

    reconnector = WifiReconnector("100.64.0.20:5555", is_connected=lambda: True, interval=0.05)
    reconnector.start()
    try:
        time.sleep(0.2)  # a few ticks' worth
        assert calls == []
    finally:
        reconnector.stop()
        reconnector.join(timeout=2.0)


def test_wifi_reconnector_logs_first_disconnect_at_info_then_repeats_at_debug(monkeypatch, caplog):
    monkeypatch.setattr(
        watcher_mod.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", "")
    )

    reconnector = WifiReconnector("100.64.0.20:5555", is_connected=lambda: False, interval=0.05)
    with caplog.at_level("DEBUG", logger="androsync.daemon.watcher"):
        reconnector.start()
        try:
            assert wait_until(
                lambda: sum(1 for r in caplog.records if "not connected" in r.message) >= 3, timeout=1.0
            )
        finally:
            reconnector.stop()
            reconnector.join(timeout=2.0)

    not_connected = [r for r in caplog.records if "not connected" in r.message]
    assert not_connected[0].levelname == "INFO"
    assert all(r.levelname == "DEBUG" for r in not_connected[1:])


def test_wifi_reconnector_logs_reconnect_at_info(monkeypatch, caplog):
    monkeypatch.setattr(
        watcher_mod.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", "")
    )
    connected = {"value": False}

    reconnector = WifiReconnector(
        "100.64.0.20:5555", is_connected=lambda: connected["value"], interval=0.05
    )
    with caplog.at_level("DEBUG", logger="androsync.daemon.watcher"):
        reconnector.start()
        try:
            assert wait_until(lambda: any("not connected" in r.message for r in caplog.records), timeout=1.0)
            connected["value"] = True
            assert wait_until(lambda: any("reconnected" in r.message for r in caplog.records), timeout=1.0)
        finally:
            reconnector.stop()
            reconnector.join(timeout=2.0)

    reconnect_records = [r for r in caplog.records if "reconnected" in r.message]
    assert reconnect_records and reconnect_records[0].levelname == "INFO"


def test_wifi_reconnector_stop_is_prompt():
    reconnector = WifiReconnector("100.64.0.20:5555", is_connected=lambda: True, interval=30.0)
    reconnector.start()
    reconnector.stop()
    reconnector.join(timeout=2.0)
    assert not reconnector.is_alive()


def build_wifi_config(tmp_path: Path, *, token: str = "s3cr3t") -> Config:
    local = tmp_path / "local"
    remote = tmp_path / "remote"
    local.mkdir()
    remote.mkdir()
    (local / "a.txt").write_text("hello")
    text = f"""\
device: wifi:100.64.0.20:5555
service:
  listen: [127.0.0.1]
  port: 8765
  token: {token}
  auto_sync_on_connect: false
  notify: false
pairs:
  - name: p1
    local: {local}
    remote: {remote}
    direction: two-way
"""
    path = tmp_path / "config.yaml"
    path.write_text(text)
    config = load_config(path)
    config.service.port = 0
    return config


def test_daemon_starts_wifi_reconnector_for_a_wifi_device(tmp_path, monkeypatch):
    config = build_wifi_config(tmp_path)
    daemon = make_daemon(config)

    calls = []
    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )

    daemon.start()
    try:
        assert daemon._wifi_reconnector is not None
        assert daemon._wifi_reconnector.is_alive()
        # NullWatcher never reports the target connected, so the reconnector's
        # immediate first check attempts a connect right away.
        assert wait_until(lambda: ["adb", "connect", "100.64.0.20:5555"] in calls)
    finally:
        daemon.stop()

    assert daemon._wifi_reconnector is None


def test_daemon_does_not_start_wifi_reconnector_for_a_usb_device(tmp_path):
    config = build_config(tmp_path, auto_sync=False)  # device: auto
    daemon = make_daemon(config)

    daemon.start()
    try:
        assert daemon._wifi_reconnector is None
    finally:
        daemon.stop()


def test_daemon_wifi_reconnector_sees_watcher_state(tmp_path, monkeypatch):
    """The reconnector's is_connected callback reads the live DeviceWatcher
    state, so it stops trying once track-devices reports the target ready -
    and resumes if it later disappears (a dropped Wi-Fi connection).
    """
    config = build_wifi_config(tmp_path)
    daemon = make_daemon(config)

    calls = []
    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )

    daemon.start()
    try:
        daemon._wifi_reconnector.stop()
        daemon._wifi_reconnector.join(timeout=2.0)

        assert daemon._is_wifi_target_connected("100.64.0.20:5555") is False
        daemon._watcher.devices = {"100.64.0.20:5555": "device"}
        assert daemon._is_wifi_target_connected("100.64.0.20:5555") is True
    finally:
        daemon.stop()


# ---------------------------------------------------------------------------
# on_disconnect: TCP grace period (regression - a real Wi-Fi blip used to
# cancel an in-progress job even though it reconnected 280ms later)
# ---------------------------------------------------------------------------


def test_tcp_disconnect_that_reconnects_within_grace_period_does_not_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher_mod, "_TCP_DISCONNECT_GRACE_S", 0.1)
    config = build_config(tmp_path, auto_sync=True)
    daemon = make_daemon(config)
    # Slow enough that the job is still "current" when the grace-period timer
    # fires (0.1s later) - otherwise this test would trivially pass even
    # without the fix, since a finished job is never cancellable either way.
    daemon.transport_factory = lambda cfg: (SleepyLocalTransport(Side.LOCAL, 1.0), LocalDirTransport(Side.REMOTE))
    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )

    daemon.start()
    try:
        daemon.on_connect("100.64.0.20:5555")
        assert wait_until(lambda: len(daemon._runner.list()) == 1)

        daemon.on_disconnect("100.64.0.20:5555")
        daemon.on_connect("100.64.0.20:5555")  # reconnects well within the grace period
        time.sleep(0.3)  # let the grace-period timer fire

        job = daemon._runner.list()[0]
        assert job.state == "running"  # still going - specifically not cancelled
    finally:
        daemon.stop()


def test_tcp_disconnect_still_disconnected_after_grace_period_cancels(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher_mod, "_TCP_DISCONNECT_GRACE_S", 0.1)
    config = build_config(tmp_path, auto_sync=True)
    # A second file so there is a cancellation checkpoint (run_pair checks
    # cancel() *between* actions) after the first one's sleep - cancelling
    # mid-fetch itself isn't a thing this executor supports, matching task
    # 24's batching trade-off notes.
    (config.pairs[0].local / "b.txt").write_text("world")
    daemon = make_daemon(config)
    daemon.transport_factory = lambda cfg: (SleepyLocalTransport(Side.LOCAL, 0.3), LocalDirTransport(Side.REMOTE))
    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )

    daemon.start()
    try:
        daemon.on_connect("100.64.0.20:5555")
        assert wait_until(lambda: len(daemon._runner.list()) == 1)
        job_id = daemon._runner.list()[0].id

        daemon.on_disconnect("100.64.0.20:5555")  # never reconnects
        assert wait_until(lambda: daemon._runner.get(job_id).state == "cancelled", timeout=2.0)
    finally:
        daemon.stop()


def test_usb_disconnect_cancels_immediately_no_grace_period(tmp_path, monkeypatch):
    """A USB unplug is unambiguous - it must not wait out the TCP grace period."""
    monkeypatch.setattr(watcher_mod, "_TCP_DISCONNECT_GRACE_S", 30.0)  # would time the test out if used
    config = build_config(tmp_path, auto_sync=True)
    daemon = make_daemon(config)
    daemon.transport_factory = lambda cfg: (SleepyLocalTransport(Side.LOCAL, 2.0), LocalDirTransport(Side.REMOTE))
    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )

    daemon.start()
    try:
        daemon.on_connect("USBSERIAL1")
        assert wait_until(lambda: len(daemon._runner.list()) == 1)
        job_id = daemon._runner.list()[0].id

        daemon.on_disconnect("USBSERIAL1")
        assert wait_until(lambda: daemon._runner.get(job_id).state == "cancelled", timeout=1.0)
    finally:
        daemon.stop()


def test_disconnect_of_a_different_serial_than_current_device_does_not_use_grace_period(tmp_path, monkeypatch):
    """A stale disconnect event for a serial that is not the currently
    tracked device (e.g. it already changed) has no meaningful transport to
    grace-period against - it must fall back to cancelling immediately,
    matching the pre-task-25 behavior for this edge case.
    """
    monkeypatch.setattr(watcher_mod, "_TCP_DISCONNECT_GRACE_S", 30.0)
    config = build_config(tmp_path, auto_sync=True)
    daemon = make_daemon(config)
    daemon.transport_factory = lambda cfg: (SleepyLocalTransport(Side.LOCAL, 2.0), LocalDirTransport(Side.REMOTE))
    monkeypatch.setattr(
        watcher_mod.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )

    daemon.start()
    try:
        daemon.on_connect("100.64.0.20:5555")
        assert wait_until(lambda: len(daemon._runner.list()) == 1)
        job_id = daemon._runner.list()[0].id

        daemon.on_disconnect("some-other-stale-serial:5555")
        assert wait_until(lambda: daemon._runner.get(job_id).state == "cancelled", timeout=1.0)
    finally:
        daemon.stop()


# ---------------------------------------------------------------------------
# the ping lists every phone adb knows, with its state (task 37f, issue #10)
# ---------------------------------------------------------------------------


def test_device_info_lists_every_known_device_with_its_state(tmp_path):
    import types

    daemon = make_daemon(build_config(tmp_path, auto_sync=False))
    daemon._watcher = types.SimpleNamespace(
        devices={"RZ123": "unauthorized", "192.168.1.20:5555": "device", "ZZ9": "offline"}
    )
    info = daemon.device_info()
    assert info["devices"] == [
        {"serial": "192.168.1.20:5555", "state": "device", "transport": "tcp", "id": "192.168.1.20:5555", "name": None},
        {"serial": "RZ123", "state": "unauthorized", "transport": "usb", "id": "RZ123", "name": None},
        {"serial": "ZZ9", "state": "offline", "transport": "usb", "id": "ZZ9", "name": None},
    ]


def test_device_info_gives_a_named_phone_its_name_and_hardware_id(tmp_path):
    import types

    from androsync.config import PhoneConfig

    config = build_config(tmp_path, auto_sync=False)
    config.phones["HW_A"] = PhoneConfig(name="S24 FE")
    daemon = make_daemon(config)
    daemon._config = config
    daemon._hardware_serials["192.168.1.20:5555"] = "HW_A"
    daemon._watcher = types.SimpleNamespace(devices={"192.168.1.20:5555": "device", "USB_B": "device"})
    rows = {r["serial"]: r for r in daemon.device_info()["devices"]}
    assert (rows["192.168.1.20:5555"]["id"], rows["192.168.1.20:5555"]["name"]) == ("HW_A", "S24 FE")
    assert (rows["USB_B"]["id"], rows["USB_B"]["name"]) == ("USB_B", None)


def test_device_info_has_an_empty_device_list_before_the_watcher_exists(tmp_path):
    assert make_daemon(build_config(tmp_path, auto_sync=False)).device_info()["devices"] == []
