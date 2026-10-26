"""Device watcher + foreground daemon: ``androsync serve``.

Two pieces live here:

- :class:`DeviceWatcher` runs ``adb track-devices`` on a background thread
  and calls back whenever a device enters or leaves state ``device``
  (plugged in and authorized / unplugged or revoked). ``adb track-devices``
  writes one *frame* per device-list change to its stdout: a 4 hex digit
  length prefix followed by exactly that many bytes of ``serial\\tstate\\n``
  lines - the same framing ``adb`` uses on its own sync/host protocol. If
  the process exits (most commonly because something killed the adb
  server), the watcher waits a couple of seconds and starts a fresh one,
  forever, until :meth:`DeviceWatcher.stop` is called.
- :class:`Daemon` wires a :class:`~androsync.daemon.jobs.JobRunner`, an
  :class:`~androsync.daemon.server.ApiServer` and a :class:`DeviceWatcher`
  together: on connect it sets up ``adb reverse`` for the control channel,
  fires a desktop notification, and optionally kicks off an auto-sync; on
  disconnect it notifies and cancels whatever job is running. ``run_forever``
  is what ``androsync serve`` calls after ``start()``: it blocks until
  SIGTERM/SIGINT, then stops the server and the watcher in turn.
"""

from __future__ import annotations

import logging
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Protocol

from androsync.config import Config, ConfigError, secure_file
from androsync.daemon.auto import AutoSync
from androsync.run_conditions import parse_battery, parse_route
from androsync.daemon.jobs import JobBusy, JobRunner
from androsync.daemon.server import ApiServer
from androsync.model import Side
from androsync.service_state import remove_pid_file, write_pid_file
from androsync.transport.adb import Adb, AdbTransport, hardware_serial, phone_model, resolve_device, resolve_pair_device
from androsync.transport.agent import AgentBroker
from androsync.transport.local import LocalDirTransport

_LOG = logging.getLogger(__name__)

# Same shape adb uses for a Wi-Fi/`adb connect` serial ("host:port"), kept
# as a local copy so this module does not reach into transport/adb.py's
# private helper.
_TCP_SERIAL_RE = re.compile(r"^[^\s:]+:\d+$")

ConnectFn = Callable[[str], None]
LogFn = Callable[[str], None]


def _transport_kind(serial: str) -> str:
    return "tcp" if _TCP_SERIAL_RE.match(serial) else "usb"


# ---------------------------------------------------------------------------
# `adb track-devices` frame parsing
# ---------------------------------------------------------------------------


class _Readable(Protocol):
    def read(self, n: int) -> bytes: ...


def _read_exact(stream: _Readable, n: int) -> bytes | None:
    """Read exactly ``n`` bytes from ``stream``, or ``None`` on EOF partway through."""
    if n == 0:
        return b""
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def _read_frame(stream: _Readable) -> bytes | None:
    """Read one ``adb track-devices`` frame: a 4 hex digit length then that many bytes.

    Returns ``None`` at a clean EOF (the process exited).
    """
    header = _read_exact(stream, 4)
    if header is None:
        return None
    try:
        length = int(header, 16)
    except ValueError:
        return None
    return _read_exact(stream, length)


def _parse_device_list(text: str) -> dict[str, str]:
    """Parse a frame's payload (``serial\\tstate`` per line) into ``{serial: state}``."""
    devices: dict[str, str] = {}
    for line in text.splitlines():
        line = line.rstrip("\r")
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        serial, state = parts
        devices[serial] = state
    return devices


class DeviceWatcher(threading.Thread):
    """Watches ``adb track-devices`` and calls back on connect/disconnect.

    Only transitions into/out of state ``device`` (plugged in, authorized,
    ready) are reported - ``unauthorized``/``offline`` are tracked in
    :attr:`devices` but never trigger a callback on their own.
    """

    def __init__(
        self,
        on_connect: ConnectFn | None = None,
        on_disconnect: ConnectFn | None = None,
        *,
        adb_path: str = "adb",
        restart_delay: float = 2.0,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        log: LogFn | None = None,
    ) -> None:
        super().__init__(daemon=True, name="androsync-watcher")
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self._adb_path = adb_path
        self._restart_delay = restart_delay
        self._popen_factory = popen_factory
        self._log = log or _LOG.info
        self._stop_event = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._known_ready: set[str] = set()
        self._lock = threading.Lock()
        # Full last-seen serial -> state map, including non-"device" states.
        self.devices: dict[str, str] = {}

    def stop(self) -> None:
        """Ask the watcher to stop; safe to call from any thread, any number of times."""
        self._stop_event.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001 - best-effort shutdown
                pass

    def run(self) -> None:  # noqa: D102 - Thread.run override
        while not self._stop_event.is_set():
            try:
                proc = self._popen_factory([self._adb_path, "track-devices"], stdout=subprocess.PIPE)
            except Exception as exc:  # noqa: BLE001 - reported, then retried like a process exit
                self._log(f"androsync: could not start `adb track-devices`: {exc}")
                if self._stop_event.wait(self._restart_delay):
                    return
                continue

            self._proc = proc
            try:
                self._pump(proc.stdout)
            except Exception as exc:  # noqa: BLE001 - a stream error should not kill the watcher
                self._log(f"androsync: `adb track-devices` stream error: {exc}")
            finally:
                self._close(proc)

            if self._stop_event.is_set():
                return
            self._log("androsync: `adb track-devices` exited; restarting in 2s")
            if self._stop_event.wait(self._restart_delay):
                return

    @staticmethod
    def _close(proc: subprocess.Popen) -> None:
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:  # noqa: BLE001
            pass

    def _pump(self, stream: _Readable) -> None:
        while not self._stop_event.is_set():
            frame = _read_frame(stream)
            if frame is None:
                return  # adb exited
            self._handle_frame(frame.decode("utf-8", "replace"))

    def _handle_frame(self, text: str) -> None:
        current = _parse_device_list(text)
        with self._lock:
            self.devices = current

        ready = {serial for serial, state in current.items() if state == "device"}
        newly_connected = ready - self._known_ready
        vanished = self._known_ready - ready
        self._known_ready = ready

        for serial in sorted(newly_connected):
            self._invoke(self.on_connect, serial)
        for serial in sorted(vanished):
            self._invoke(self.on_disconnect, serial)

    def _invoke(self, callback: ConnectFn | None, serial: str) -> None:
        if callback is None:
            return
        try:
            callback(serial)
        except Exception:  # noqa: BLE001 - one bad callback must not take the watcher down
            _LOG.exception("androsync: device watcher callback failed for %s", serial)


class WifiReconnector(threading.Thread):
    """Periodically retries ``adb connect <target>`` for a ``wifi:IP:PORT`` device (task 25).

    ``adb track-devices`` (:class:`DeviceWatcher`) only reports devices adb
    already knows about - a dropped TCP connection needs an explicit
    reconnect attempt to come back, unlike a physically re-plugged USB
    device that adb notices on its own. Checks immediately on start, then
    every ``interval`` seconds.

    Logging (task 32c): the first disconnect and the reconnect that ends it
    are each a real state change, logged at INFO; every retry while still
    disconnected repeats at DEBUG - loud enough that a sync failure caused by
    "the phone has been unreachable for an hour" is visible in `daemon.log`
    without the journal filling up with an identical line every 30s.
    """

    def __init__(
        self,
        target: str,
        *,
        is_connected: Callable[[], bool],
        interval: float = 30.0,
        adb_path: str = "adb",
    ) -> None:
        super().__init__(daemon=True, name="androsync-wifi-reconnect")
        self._target = target
        self._is_connected = is_connected
        self._interval = interval
        self._adb_path = adb_path
        self._stop_event = threading.Event()
        self._was_disconnected = False

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # noqa: D102 - Thread.run override
        while not self._stop_event.is_set():
            connected = self._is_connected()
            if not connected:
                log = _LOG.debug if self._was_disconnected else _LOG.info
                log("androsync: %s not connected, attempting to reconnect", self._target)
                self._attempt_connect()
            elif self._was_disconnected:
                _LOG.info("androsync: %s reconnected", self._target)
            self._was_disconnected = not connected
            if self._stop_event.wait(self._interval):
                return

    def _attempt_connect(self) -> None:
        try:
            subprocess.run(
                [self._adb_path, "connect", self._target],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001 - retried on the next tick regardless
            _LOG.debug("androsync: adb connect %s failed: %s", self._target, exc)


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

ConfigLoader = Callable[[], Config]

# How long Daemon.start() keeps retrying a listen address that failed to
# bind (e.g. the tailnet is not up yet), and how often it retries.
_BIND_RETRY_TIMEOUT = 30.0
_BIND_RETRY_INTERVAL = 2.0

# How long on_disconnect waits before cancelling a job running over a TCP
# (Wi-Fi/Tailscale) device that just disconnected, in case it was a brief
# blip rather than a real drop (see on_disconnect for why).
_TCP_DISCONNECT_GRACE_S = 8.0


class Daemon:
    """Wires JobRunner + ApiServer + DeviceWatcher together for ``androsync serve``."""

    def __init__(self, config_loader: ConfigLoader, version: str, *, config_path: Path) -> None:
        self._config_loader = config_loader
        self._version = version
        self._config_path = config_path
        self._config: Config | None = None
        self._runner: JobRunner | None = None
        self._server: ApiServer | None = None
        self._watcher: DeviceWatcher | None = None
        self._wifi_reconnector: WifiReconnector | None = None
        self._extra_reconnectors: list[WifiReconnector] = []  # one per further phone with a wifi_target (task 33b)
        #: connection serial -> the phone's hardware serial, learned when it connects (kept out of the ping's way).
        self._hardware_serials: dict[str, str] = {}
        #: connection serial -> model name, the label for a phone the user has not named (Phase V2)
        self._models: dict[str, str] = {}

        # The mailbox the phone app polls in agent mode (task 37b); transports for agent pairs use this same one.
        self.agent_broker = AgentBroker()
        self._device_lock = threading.Lock()
        self._device_serial: str | None = None
        self._device_transport: str | None = None

        # Overridable by tests/callers without touching adb or the network:
        # a bound method by default, so `daemon.transport_factory = fake`
        # (or `daemon.watcher_factory = fake`) swaps it out cleanly.
        self.transport_factory: Callable[[Config], tuple[LocalDirTransport, AdbTransport]] = (
            self._default_transport_factory
        )
        self.watcher_factory: Callable[..., DeviceWatcher] = self._default_watcher_factory

    # -- construction helpers ------------------------------------------------

    @staticmethod
    def _default_transport_factory(config: Config) -> tuple[LocalDirTransport, AdbTransport]:
        serial = resolve_device(config.device, wifi_target=config.wifi_target)
        return LocalDirTransport(Side.LOCAL), AdbTransport(Adb(serial=serial))

    @staticmethod
    def _default_device_transport_factory(config: Config, device: str) -> tuple[LocalDirTransport, AdbTransport]:
        """The adb transport of the phone a pair is bound to (task 33a)."""
        serial = resolve_pair_device(device, config.device, wifi_target=config.wifi_target)
        return LocalDirTransport(Side.LOCAL), AdbTransport(Adb(serial=serial))

    def _auto_phone_available(self, pair) -> bool:  # noqa: ANN001 - PairConfig
        """Whether the phone a pair needs can be reached now; an absent phone is waited for, never counted as a failure."""
        if pair.transport == "agent":
            return self.agent_broker.connected()
        watcher = self._watcher
        ready = [s for s, st in (dict(watcher.devices).items() if watcher is not None else []) if st == "device"]
        if not ready:
            return False
        if pair.device in ("", "any"):
            return True
        return any(self._hardware_serials.get(s, s) == pair.device or s == pair.device for s in ready)

    def _auto_phone_state(self, pair) -> dict:  # noqa: ANN001 - PairConfig
        """How the pair's phone is connected and powered, for run conditions: ``{"net": ..., "charging": ...}``.

        The agent reports it itself; an adb phone is asked (``dumpsys battery`` and ``ip route get``). Unknown stays out
        of the dict, and run conditions never block on what is not known.
        """
        if pair.transport == "agent":
            return self.agent_broker.phone_state()
        watcher = self._watcher
        ready = [s for s, st in (dict(watcher.devices).items() if watcher is not None else []) if st == "device"]
        if not ready:
            return {}
        serial = ready[0]
        for candidate in ready:
            if pair.device in ("", "any") or self._hardware_serials.get(candidate, candidate) == pair.device:
                serial = candidate
                break
        state: dict = {}
        battery = self._adb_text(serial, "dumpsys battery")
        charging = parse_battery(battery) if battery else None
        if charging is not None:
            state["charging"] = charging
        route = self._adb_text(serial, "ip route get 1.1.1.1")
        net = parse_route(route) if route else None
        if net is not None:
            state["net"] = net
        return state

    @staticmethod
    def _adb_text(serial: str, command: str) -> str:
        try:
            result = subprocess.run(["adb", "-s", serial, "shell", command], capture_output=True, text=True, timeout=8)
        except Exception:  # noqa: BLE001 - a phone that does not answer simply has an unknown state
            return ""
        return result.stdout if result.returncode == 0 else ""

    def _default_watcher_factory(self, *, on_connect: ConnectFn, on_disconnect: ConnectFn) -> DeviceWatcher:
        return DeviceWatcher(on_connect=on_connect, on_disconnect=on_disconnect)

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Load the config, then start the job runner, HTTP API and device watcher.

        Raises :class:`~androsync.config.ConfigError` if ``service.token``
        is not set (``androsync service install`` generates one).
        """
        config = self._config_loader()
        if not config.service.token:
            raise ConfigError(
                "service.token is not set in the config - run `androsync service install` first"
            )
        self._config = config
        if secure_file(self._config_path):
            _LOG.info("androsync: made %s readable by you only (it holds the token)", self._config_path)

        self._runner = JobRunner(
            self._config_loader, self.transport_factory, self.agent_broker, self._default_device_transport_factory
        )
        # Pairs with `auto: true` sync by themselves when files change (Phase T2).
        self._auto = AutoSync(
            self._config_loader,
            self._runner,
            phone_available=self._auto_phone_available,
            notify=self.notify,
            phone_state=self._auto_phone_state,
        )
        self._server = ApiServer(
            self._runner,
            self._config_loader,
            token=config.service.token,
            listen=list(config.service.listen),
            port=config.service.port,
            version=self._version,
            device_info=self.device_info,
            config_path=self._config_path,
            agent=self.agent_broker,
            auto=self._auto,
        )
        self._server.start()
        self._auto.start()
        _LOG.info("androsync: HTTP API listening on %s", self._server.bound_addresses)
        write_pid_file()
        self._retry_missing_binds(config.service.listen, config.service.port)

        self._watcher = self.watcher_factory(on_connect=self.on_connect, on_disconnect=self.on_disconnect)
        self._watcher.start()
        _LOG.info("androsync: device watcher started")

        if config.device.startswith("wifi:"):
            target = config.device[len("wifi:") :]
            self._wifi_reconnector = WifiReconnector(target, is_connected=lambda: self._is_wifi_target_connected(target))
            self._wifi_reconnector.start()
            _LOG.info("androsync: wifi reconnect loop started for %s", target)

        # Every named phone with a Wi-Fi target gets its own reconnect loop (task 33b).
        started = {config.device[len("wifi:") :]} if config.device.startswith("wifi:") else set()
        for phone_id, phone in config.phones.items():
            target = phone.wifi_target
            if not target or target in started:
                continue
            started.add(target)
            reconnector = WifiReconnector(target, is_connected=lambda t=target: self._is_wifi_target_connected(t))
            reconnector.start()
            self._extra_reconnectors.append(reconnector)
            _LOG.info("androsync: wifi reconnect loop started for %s (%s)", target, phone.name or phone_id)

    def _is_wifi_target_connected(self, target: str) -> bool:
        watcher = self._watcher
        if watcher is None:
            return False
        return watcher.devices.get(target) == "device"

    def _retry_missing_binds(self, listen: list[str], port: int) -> None:
        """Retry binding any address ``ApiServer.start()`` could not, for up to 30s.

        Meant for a Tailscale IP that is not up yet right after boot: the
        tailnet usually comes up within a few seconds of ``network-online.target``.
        """
        assert self._server is not None
        deadline = time.monotonic() + _BIND_RETRY_TIMEOUT
        while True:
            bound_hosts = {addr for addr, _port in self._server.bound_addresses}
            missing = [addr for addr in listen if addr not in bound_hosts]
            if not missing:
                return
            if time.monotonic() >= deadline:
                for addr in missing:
                    _LOG.warning(
                        "androsync: still could not bind %s:%s after %.0fs, giving up",
                        addr,
                        port,
                        _BIND_RETRY_TIMEOUT,
                    )
                return
            time.sleep(_BIND_RETRY_INTERVAL)
            for addr in missing:
                self._server.bind_extra(addr)

    def run_forever(self) -> None:
        """Block until SIGTERM/SIGINT/SIGHUP, then stop the server and watcher.

        SIGHUP matters for the "run in a terminal, closing it stops the
        service" mode Phase J adds (task 31b's GUI "Run here", or a plain
        `androsync serve` in a terminal): closing the controlling terminal
        sends SIGHUP to the foreground process group, which would otherwise
        kill this process uncleanly instead of shutting down through `stop()`.
        """
        stop_event = threading.Event()

        def _handle_signal(signum, frame) -> None:  # noqa: ANN001 - stdlib signal handler signature
            _LOG.info("androsync: received signal %s, shutting down", signum)
            stop_event.set()

        old_term = signal.signal(signal.SIGTERM, _handle_signal)
        old_int = signal.signal(signal.SIGINT, _handle_signal)
        old_hup = signal.signal(signal.SIGHUP, _handle_signal)
        try:
            stop_event.wait()
        finally:
            signal.signal(signal.SIGTERM, old_term)
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGHUP, old_hup)
            self.stop()

    def stop(self) -> None:
        """Stop the watcher and the HTTP API. Safe to call more than once."""
        for reconnector in [r for r in (self._wifi_reconnector, *self._extra_reconnectors) if r is not None]:
            reconnector.stop()
            reconnector.join(timeout=5.0)
        self._wifi_reconnector = None
        self._extra_reconnectors = []
        auto = getattr(self, "_auto", None)
        if auto is not None:
            auto.stop()
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher.join(timeout=5.0)
            self._watcher = None
        if self._server is not None:
            self._server.stop()
            self._server = None
        remove_pid_file()

    # -- watcher callbacks ------------------------------------------------------

    def on_connect(self, serial: str) -> None:
        """Called by the watcher when ``serial`` enters state ``device``."""
        hardware = hardware_serial(serial)
        if hardware:
            self._hardware_serials[serial] = hardware
        model = phone_model(serial)
        if model:
            self._models[serial] = model
        with self._device_lock:
            self._device_serial = serial
            self._device_transport = _transport_kind(serial)

        if self._device_transport == "tcp":
            # A TCP device (task 25: `wifi:IP:PORT`) reaches the API directly
            # over Tailscale/LAN - `adb reverse` only matters for the USB
            # loopback path (phone -> 127.0.0.1 -> laptop).
            _LOG.info("androsync: %s connected over wifi (tcp); no adb reverse needed", serial)
        else:
            port = self._config.service.port if self._config is not None else 8765
            result = subprocess.run(
                ["adb", "-s", serial, "reverse", f"tcp:{port}", f"tcp:{port}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                _LOG.warning(
                    "androsync: `adb reverse` failed for %s: %s", serial, (result.stderr or "").strip()
                )
            else:
                _LOG.info("androsync: adb reverse tcp:%s tcp:%s set up for %s", port, port, serial)

            self._maybe_enable_tcpip(serial)

        self.notify("Phone connected", serial)

        if self._config is not None and self._config.service.auto_sync_on_connect and self._runner is not None:
            try:
                self._runner.submit(None)
            except JobBusy:
                _LOG.info("androsync: auto-sync skipped, a job is already running")

    def _maybe_enable_tcpip(self, serial: str) -> None:
        """``adb -s <serial> tcpip 5555`` (task 32b) so the phone keeps listening for
        the configured Wi-Fi/Tailscale target until its next reboot - the listener
        does not survive one, so this just re-primes it on every USB connect. Only
        worth doing when ``wifi_target`` actually names port 5555 (the port this
        command fixes); a differently-numbered target was paired some other way
        (e.g. Android 11+ Wireless debugging's own random port) and touching it
        here would be wrong.
        """
        if self._config is None or not self._config.service.enable_tcpip_on_usb:
            return
        target = self._config.wifi_target
        if not target or not target.endswith(":5555"):
            return
        result = subprocess.run(
            ["adb", "-s", serial, "tcpip", "5555"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            _LOG.warning("androsync: `adb tcpip 5555` failed for %s: %s", serial, (result.stderr or "").strip())
        else:
            _LOG.info("androsync: `adb tcpip 5555` set up for %s (wifi_target %s)", serial, target)

    def on_disconnect(self, serial: str) -> None:
        """Called by the watcher when ``serial`` leaves state ``device``."""
        with self._device_lock:
            was_current = self._device_serial == serial
            transport_at_disconnect = self._device_transport
            if was_current:
                self._device_serial = None
                self._device_transport = None

        _LOG.info("androsync: phone disconnected: %s", serial)
        self.notify("Phone disconnected", serial)

        if self._runner is None:
            return
        current = self._runner.current()
        if current is None:
            return

        if was_current and transport_at_disconnect == "tcp":
            # A Wi-Fi/Tailscale TCP connection can blip and reconnect within
            # well under a second (a brief re-negotiation `adb track-devices`
            # reports as a full disconnect+reconnect, not an actual lasting
            # drop - confirmed against a real device: reconnected 280ms
            # later) - unlike a USB unplug, which is unambiguous the instant
            # it happens. Cancelling immediately here used to kill an
            # in-progress job over a blip that had already self-healed by
            # the time the job would have noticed anything wrong, when just
            # continuing (the next adb call simply works again once the
            # same `-s <address>` target is reachable) would have finished
            # it with at most a couple of per-file errors during the gap.
            # Give it a few seconds before deciding it is really gone.
            timer = threading.Timer(
                _TCP_DISCONNECT_GRACE_S, self._cancel_if_still_disconnected, args=(serial, current.id)
            )
            timer.daemon = True
            timer.start()
        else:
            self._runner.cancel(current.id)

    def _cancel_if_still_disconnected(self, serial: str, job_id: str) -> None:
        with self._device_lock:
            reconnected = self._device_serial == serial
        if reconnected:
            return
        if self._runner is None:
            return
        current = self._runner.current()
        if current is not None and current.id == job_id:
            _LOG.info(
                "androsync: %s still disconnected %.0fs after the event, cancelling job %s",
                serial,
                _TCP_DISCONNECT_GRACE_S,
                job_id,
            )
            self._runner.cancel(job_id)

    # -- state exposed to the HTTP API ------------------------------------------

    def device_info(self) -> dict:
        """``{"transport", "device", "devices"}`` for ``GET /v1/ping``.

        ``devices`` lists every phone adb knows with its state (``device``, ``unauthorized`` or ``offline``), so the
        app's "Connect your phone" checklist can tell "not plugged in" from "waiting for you to tap Allow" (task 37f).
        """
        with self._device_lock:
            info = {"transport": self._device_transport, "device": self._device_serial}
        watcher = self._watcher
        seen = dict(watcher.devices) if watcher is not None else {}
        phones = self._config.phones if self._config is not None else {}
        rows = []
        for serial, state in sorted(seen.items()):
            phone_id = self._hardware_serials.get(serial, serial)
            phone = phones.get(phone_id) or phones.get(serial)
            rows.append(
                {
                    "serial": serial,
                    "state": state,
                    "transport": _transport_kind(serial),
                    "id": phone_id,
                    "name": ((phone.name or None) if phone is not None else None) or self._models.get(serial),
                }
            )
        info["devices"] = rows
        return info

    def notify(self, title: str, body: str) -> None:
        """Fire a desktop notification via ``notify-send``, if enabled and installed.

        Any failure (binary missing, no display, ...) is swallowed - a
        notification is a nicety, never a reason to disturb a sync.
        """
        if self._config is None or not self._config.service.notify:
            return
        if shutil.which("notify-send") is None:
            return
        try:
            subprocess.run(["notify-send", title, body], capture_output=True, timeout=5)
        except Exception:  # noqa: BLE001 - notifications must never break the daemon
            _LOG.debug("androsync: notify-send failed", exc_info=True)
