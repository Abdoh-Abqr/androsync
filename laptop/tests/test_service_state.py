"""Tests for androsync.service_state (task 31a: detect the four run states).

`systemctl`/`loginctl` calls go through `subprocess.run` (monkeypatched, same
trick as test_service.py) and the HTTP ping through `urllib.request.urlopen`
(monkeypatched too) - nothing here ever shells out or opens a real socket.
"""

from __future__ import annotations

import json
import os
import subprocess
from io import BytesIO

import pytest

from androsync import service_state


@pytest.fixture(autouse=True)
def _xdg_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg-runtime"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    # detect_state() now pings using the default config when none is passed
    # explicitly - redirect this too, or a test with no explicit `config`
    # would load (and ping) the real user's ~/.config/androsync/config.yaml.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


# ---------------------------------------------------------------------------
# pid file
# ---------------------------------------------------------------------------


def test_write_pid_file_then_remove_round_trips():
    service_state.write_pid_file()
    assert int(service_state.pid_file_path().read_text()) == os.getpid()

    service_state.remove_pid_file()
    assert not service_state.pid_file_path().exists()


def test_remove_pid_file_is_a_no_op_when_missing():
    service_state.remove_pid_file()  # must not raise


# ---------------------------------------------------------------------------
# daemon log tail
# ---------------------------------------------------------------------------


def test_tail_daemon_log_missing_file_returns_empty_list():
    assert service_state.tail_daemon_log() == []


def test_tail_daemon_log_returns_last_n_lines():
    path = service_state.daemon_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"line {i}" for i in range(10)) + "\n")

    assert service_state.tail_daemon_log(3) == ["line 7", "line 8", "line 9"]


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_ping_returns_parsed_body_on_success(monkeypatch):
    monkeypatch.setattr(
        service_state.urllib.request,
        "urlopen",
        lambda request, timeout: _FakeResponse({"name": "androsync", "device": "SERIAL1"}),
    )
    body = service_state.ping(8765, "tok")
    assert body == {"name": "androsync", "device": "SERIAL1"}


def test_ping_returns_none_on_connection_error(monkeypatch):
    def raise_it(request, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(service_state.urllib.request, "urlopen", raise_it)
    assert service_state.ping(8765, "tok") is None


# ---------------------------------------------------------------------------
# detect_state: the four modes
# ---------------------------------------------------------------------------


def test_detect_state_none_when_nothing_running(monkeypatch):
    monkeypatch.setattr(service_state, "_systemd_active", lambda: False)
    monkeypatch.setattr(service_state, "is_autostart_enabled", lambda: False)

    state = service_state.detect_state()
    assert state.mode == "none"
    assert not state.running


def test_detect_state_systemd_when_unit_active(monkeypatch):
    monkeypatch.setattr(service_state, "_systemd_active", lambda: True)
    monkeypatch.setattr(service_state, "is_autostart_enabled", lambda: True)
    monkeypatch.setattr(service_state, "_systemd_since", lambda: "Sun 2026-09-13 08:00:11 CEST")

    state = service_state.detect_state()
    assert state.mode == "systemd"
    assert state.running
    assert state.autostart is True
    assert state.since == "Sun 2026-09-13 08:00:11 CEST"


def test_detect_state_stale_pid_file_is_ignored(monkeypatch):
    """A pid file naming a dead process must not be reported as running."""
    monkeypatch.setattr(service_state, "_systemd_active", lambda: False)
    monkeypatch.setattr(service_state, "is_autostart_enabled", lambda: False)
    service_state.write_pid_file()
    monkeypatch.setattr(service_state, "_pid_alive", lambda pid: False)

    state = service_state.detect_state()
    assert state.mode == "none"


def test_detect_state_inprocess_when_pid_file_names_this_process(monkeypatch):
    from androsync.config import Config, ServiceConfig

    monkeypatch.setattr(service_state, "_systemd_active", lambda: False)
    monkeypatch.setattr(service_state, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(service_state, "ping", lambda port, token, timeout=2.0: {"device": None})
    service_state.write_pid_file()  # writes os.getpid()

    config = Config(service=ServiceConfig(token="tok", port=8765), defaults={}, pairs=[])
    state = service_state.detect_state(config)
    assert state.mode == "inprocess"
    assert state.pid == os.getpid()


def test_detect_state_other_when_pid_file_names_a_different_live_process(monkeypatch):
    from androsync.config import Config, ServiceConfig

    monkeypatch.setattr(service_state, "_systemd_active", lambda: False)
    monkeypatch.setattr(service_state, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(service_state, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(service_state, "ping", lambda port, token, timeout=2.0: {"device": "SERIAL1"})
    service_state.pid_file_path().parent.mkdir(parents=True, exist_ok=True)
    other_pid = os.getpid() + 1
    service_state.pid_file_path().write_text(str(other_pid))

    config = Config(service=ServiceConfig(token="tok", port=8765), defaults={}, pairs=[])
    state = service_state.detect_state(config)
    assert state.mode == "other"
    assert state.pid == other_pid
    assert state.ping == {"device": "SERIAL1"}


def test_detect_state_pid_alive_but_ping_fails_reports_none(monkeypatch):
    """A pid file surviving from a crash, with nothing actually answering, must not read as running."""
    from androsync.config import Config, ServiceConfig

    monkeypatch.setattr(service_state, "_systemd_active", lambda: False)
    monkeypatch.setattr(service_state, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(service_state, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(service_state, "ping", lambda port, token, timeout=2.0: None)
    service_state.write_pid_file()

    config = Config(service=ServiceConfig(token="tok", port=8765), defaults={}, pairs=[])
    state = service_state.detect_state(config)
    assert state.mode == "none"


# ---------------------------------------------------------------------------
# start_background / stop_background / set_autostart
# ---------------------------------------------------------------------------


def test_start_background_runs_systemctl_start(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        service_state.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(list(argv)) or subprocess.CompletedProcess(argv, 0),
    )
    service_state.start_background(log=lambda _line: None)
    assert calls == [["systemctl", "--user", "start", service_state.UNIT_NAME]]


def test_stop_background_runs_systemctl_stop(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        service_state.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(list(argv)) or subprocess.CompletedProcess(argv, 0),
    )
    service_state.stop_background(log=lambda _line: None)
    assert calls == [["systemctl", "--user", "stop", service_state.UNIT_NAME]]


def test_set_autostart_on_enables_and_lingers(monkeypatch):
    import getpass

    calls: list[list[str]] = []
    monkeypatch.setattr(
        service_state.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(list(argv)) or subprocess.CompletedProcess(argv, 0),
    )
    service_state.set_autostart(True, log=lambda _line: None)
    assert calls == [
        ["systemctl", "--user", "enable", service_state.UNIT_NAME],
        ["loginctl", "enable-linger", getpass.getuser()],
    ]


def test_set_autostart_off_disables_and_unlingers(monkeypatch):
    import getpass

    calls: list[list[str]] = []
    monkeypatch.setattr(
        service_state.subprocess,
        "run",
        lambda argv, **kwargs: calls.append(list(argv)) or subprocess.CompletedProcess(argv, 0),
    )
    service_state.set_autostart(False, log=lambda _line: None)
    assert calls == [
        ["systemctl", "--user", "disable", service_state.UNIT_NAME],
        ["loginctl", "disable-linger", getpass.getuser()],
    ]
