"""Detects whether androsync is currently running, and where (Phase J, task 31a).

Backs both ``androsync service status`` and the GUI's Service panel (task
31b): the same daemon can be running as the systemd user service, "in
process" (this very Python process started it - the GUI's "Run here" mode),
in some other separately-started process (``androsync serve`` in a plain
terminal, or another instance of the GUI), or not at all.

Distinguishing "in process" from "another process" only needs one extra
comparison against ``os.getpid()`` - the pid file (written by
:meth:`~androsync.daemon.watcher.Daemon.start`, removed by ``.stop()``) does
not itself know which of those two cases it is; whoever wrote it either is,
or isn't, the process calling :func:`detect_state` right now.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from androsync.config import Config, ConfigError, load_config
from androsync.service import UNIT_NAME

LogFn = Callable[[str], None]
Mode = Literal["systemd", "inprocess", "other", "none"]

_PING_TIMEOUT_S = 2.0
_SYSTEMCTL_TIMEOUT_S = 5.0


@dataclass
class ServiceState:
    """Snapshot returned by :func:`detect_state`."""

    mode: Mode
    pid: int | None = None
    autostart: bool = False
    since: str | None = None
    #: The `GET /v1/ping` body, when a running daemon answered it.
    ping: dict | None = None

    @property
    def running(self) -> bool:
        return self.mode != "none"


# ---------------------------------------------------------------------------
# pid file (written by Daemon.start, removed by Daemon.stop)
# ---------------------------------------------------------------------------


def runtime_dir() -> Path:
    """``$XDG_RUNTIME_DIR/androsync``, or ``/run/user/<uid>/androsync`` as the fallback."""
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(xdg) if xdg else Path(f"/run/user/{os.getuid()}")
    return base / "androsync"


def pid_file_path() -> Path:
    return runtime_dir() / "daemon.pid"


def write_pid_file() -> None:
    """Record this process as the one currently running the daemon."""
    path = pid_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    """Safe to call even if the file was never written or is already gone."""
    try:
        pid_file_path().unlink()
    except FileNotFoundError:
        pass


def _read_pid_file() -> int | None:
    try:
        text = pid_file_path().read_text().strip()
    except (FileNotFoundError, OSError):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    return True


# ---------------------------------------------------------------------------
# daemon log file (a RotatingFileHandler is attached to it by cli.setup_logging)
# ---------------------------------------------------------------------------


def daemon_log_path() -> Path:
    """``$XDG_STATE_HOME/androsync/daemon.log``, or the ``~/.local/state`` fallback."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "androsync" / "daemon.log"


def tail_daemon_log(n: int = 200) -> list[str]:
    """The last ``n`` lines of the daemon log file, oldest first; ``[]`` if it does not exist."""
    try:
        text = daemon_log_path().read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    lines = text.splitlines()
    return lines[-n:] if n > 0 else lines


# ---------------------------------------------------------------------------
# GET /v1/ping (does the API actually answer, not just "is a pid alive")
# ---------------------------------------------------------------------------


def ping(port: int, token: str, *, timeout: float = _PING_TIMEOUT_S) -> dict | None:
    """``GET /v1/ping`` on ``127.0.0.1:<port>``; ``None`` on any failure."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/ping", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# systemctl --user is-active/is-enabled/show
# ---------------------------------------------------------------------------


def _systemctl(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["systemctl", "--user", *args], capture_output=True, text=True, timeout=_SYSTEMCTL_TIMEOUT_S
        )
    except Exception:  # noqa: BLE001 - no systemd/session bus available
        return None


def _systemd_active() -> bool:
    result = _systemctl("is-active", UNIT_NAME)
    return result is not None and result.stdout.strip() == "active"


def is_autostart_enabled() -> bool:
    """``systemctl --user is-enabled androsync`` == ``"enabled"``."""
    result = _systemctl("is-enabled", UNIT_NAME)
    return result is not None and result.stdout.strip() == "enabled"


def _systemd_since() -> str | None:
    result = _systemctl("show", UNIT_NAME, "-p", "ActiveEnterTimestamp", "--value")
    if result is None or result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def start_background(*, log: LogFn = print) -> subprocess.CompletedProcess:
    """``systemctl --user start androsync`` (the unit must already be installed)."""
    argv = ["systemctl", "--user", "start", UNIT_NAME]
    log("+ " + " ".join(argv))
    return subprocess.run(argv, capture_output=True, text=True, timeout=15)


def stop_background(*, log: LogFn = print) -> subprocess.CompletedProcess:
    """``systemctl --user stop androsync``."""
    argv = ["systemctl", "--user", "stop", UNIT_NAME]
    log("+ " + " ".join(argv))
    return subprocess.run(argv, capture_output=True, text=True, timeout=15)


def set_autostart(on: bool, *, log: LogFn = print) -> None:
    """Enable/disable the unit and toggle ``loginctl enable-linger`` together.

    Linger is what makes the unit start at boot rather than only at the next
    login - both halves are needed for "start at boot" to actually mean that.
    """
    action = "enable" if on else "disable"
    argv = ["systemctl", "--user", action, UNIT_NAME]
    log("+ " + " ".join(argv))
    subprocess.run(argv, capture_output=True, text=True, timeout=15)

    linger_action = "enable-linger" if on else "disable-linger"
    linger_argv = ["loginctl", linger_action, getpass.getuser()]
    log("+ " + " ".join(linger_argv))
    subprocess.run(linger_argv, capture_output=True, text=True, timeout=15)


# ---------------------------------------------------------------------------
# detect_state
# ---------------------------------------------------------------------------


def detect_state(config: Config | None = None) -> ServiceState:
    """Figure out whether androsync is running right now, and how.

    ``config`` is used only to ping the local API (port + token); when not
    given, the default config path is tried and silently ignored if it does
    not load - the systemd/pid-file detection below does not need it.
    """
    if config is None:
        try:
            config = load_config()
        except (ConfigError, OSError):
            config = None
    body = ping(config.service.port, config.service.token) if config and config.service.token else None

    if _systemd_active():
        return ServiceState(mode="systemd", autostart=is_autostart_enabled(), since=_systemd_since(), ping=body)

    pid = _read_pid_file()
    if pid is not None and _pid_alive(pid) and body is not None:
        mode: Mode = "inprocess" if pid == os.getpid() else "other"
        return ServiceState(mode=mode, pid=pid, autostart=is_autostart_enabled(), ping=body)

    return ServiceState(mode="none", autostart=is_autostart_enabled())
