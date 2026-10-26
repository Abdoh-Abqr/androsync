"""AdbTransport: Transport implementation backed by ``adb`` (the phone side).

This module is a thin wrapper around the ``adb`` command-line tool:

- :class:`Adb` runs ``adb`` subprocesses (optionally pinned to one serial
  with ``-s``), raising :class:`AdbError` with the command's stderr (plus a
  human hint for the common failure modes: unauthorized, no device, more
  than one device) on a non-zero exit.
- :func:`list_devices` parses ``adb devices -l``.
- :func:`resolve_device` turns the config's ``device:`` value (``"auto"``,
  ``"wifi:IP:PORT"``, or a literal serial) into a concrete serial/host:port
  to pass to :class:`Adb`.
- :class:`AdbTransport` implements the :class:`~androsync.transport.base.Transport`
  interface for the phone side: scan via ``find -printf`` (NUL-separated),
  falling back to ``find -exec stat`` (newline-separated) on older/toybox
  builds that lack ``-printf``; fetch/store via ``adb pull -a``/``adb push``
  plus a lazy mtime-preservation self-test that decides whether an explicit
  ``touch`` is needed after every push.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from androsync.ignore import IgnoreRules
from androsync.model import FileEntry, Side, Snapshot
from androsync.phone_path import PhonePathError, check_remote
from androsync.transport.base import Transport, TransportCancelled, TransportError, check_rel

LogFn = Callable[[str], None]

_TCP_SERIAL_RE = re.compile(r"^[^\s:]+:\d+$")
_SELFTEST_PATH = "/sdcard/.androsync-selftest"


class AdbError(TransportError):
    """Raised for any failure running ``adb`` or resolving a device."""


#: What to tell the user for a phone that is attached but not usable (shown by `androsync devices`; the app's
#: "Connect your phone" checklist says the same in its own words, task 37f / issue #10).
DEVICE_STATE_HINTS = {
    "unauthorized": (
        "Look at the phone: tap Allow on 'Allow USB debugging?' and tick 'Always allow from this computer'."
    ),
    "offline": "Unplug and replug the cable. If it stays offline, switch USB debugging off and on again on the phone.",
}

NO_DEVICE_HINT = (
    "No phone found. Check the cable, switch on USB debugging (Settings > Developer options), "
    "or open the app and follow 'Connect your phone'."
)


class AdbCancelled(AdbError, TransportCancelled):
    """An adb call was killed because the user pressed Stop (see :meth:`Adb.abort`)."""


@dataclass(frozen=True)
class DeviceInfo:
    """One line of ``adb devices -l`` output."""

    serial: str
    state: str
    model: str | None
    transport: str  # "usb" | "tcp"


def _looks_like_tcp(serial: str) -> bool:
    """True if ``serial`` looks like ``host:port`` (a Wi-Fi/``adb connect`` device)."""
    return bool(_TCP_SERIAL_RE.match(serial))


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return data


def _format_adb_error(args: list[str], returncode: int, stderr: bytes | str | None) -> str:
    """Build an ``AdbError`` message: the command, exit code, stderr, and a hint if recognised."""
    stderr_text = _decode(stderr).strip()
    message = f"`{' '.join(args)}` failed (exit {returncode}): {stderr_text}"
    lower = stderr_text.lower()
    if "unauthorized" in lower:
        message += (
            "\nHint: the phone has not authorized this computer yet - accept the "
            "'Allow USB debugging' prompt on the phone screen."
        )
    elif "no devices/emulators found" in lower or "no devices found" in lower:
        message += (
            "\nHint: no device is connected - check the USB cable and that USB "
            "debugging is enabled in Developer options."
        )
    elif "more than one device" in lower or "more than one emulator" in lower:
        message += (
            "\nHint: multiple devices are connected - set 'device' in the config "
            "to a specific serial instead of 'auto'."
        )
    return message


def _run_process(
    argv: list[str],
    *,
    capture_output: bool = True,
    text: bool = False,
    timeout: float | None = None,
    on_start: Callable[[subprocess.Popen], None] | None = None,
) -> subprocess.CompletedProcess:
    """``subprocess.run`` that tells the caller which process it started, so another thread can kill it.

    ``on_start`` is called with the ``Popen`` right after the process exists. Raises
    ``subprocess.TimeoutExpired`` (after killing the process) and ``FileNotFoundError`` like ``subprocess.run``.
    """
    stream = subprocess.PIPE if capture_output else None
    proc = subprocess.Popen(argv, stdout=stream, stderr=stream, text=text)
    if on_start is not None:
        on_start(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


class Adb:
    """Runs ``adb`` subprocesses, optionally pinned to one device serial."""

    def __init__(self, serial: str | None = None, adb_path: str = "adb", log: LogFn | None = None) -> None:
        self.serial = serial
        self.adb_path = adb_path
        self.log = log
        self._abort_lock = threading.Lock()
        self._aborted = False
        self._proc: subprocess.Popen | None = None

    def _argv(self, args: list[str]) -> list[str]:
        argv = [self.adb_path]
        if self.serial:
            argv += ["-s", self.serial]
        argv += list(args)
        return argv

    def run(
        self,
        args: list[str],
        *,
        check: bool = True,
        binary: bool = False,
        timeout: float = 600,
    ) -> subprocess.CompletedProcess:
        """Run ``adb [-s serial] <args>``, returning the ``CompletedProcess``.

        Raises :class:`AdbError` on a non-zero exit when ``check`` is True
        (the default), or if ``adb`` itself cannot be found or times out.
        """
        argv = self._argv(args)
        if self._aborted:
            raise AdbCancelled("cancelled")
        if self.log is not None:
            self.log(" ".join(shlex.quote(a) for a in argv))
        try:
            result = _run_process(
                argv,
                capture_output=True,
                text=not binary,
                timeout=timeout,
                on_start=self._remember_process,
            )
        except FileNotFoundError as exc:
            raise AdbError(f"adb executable not found ({self.adb_path!r}): {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"`{' '.join(argv)}` timed out after {timeout}s") from exc
        finally:
            with self._abort_lock:
                self._proc = None

        if self._aborted:
            raise AdbCancelled("cancelled")
        if check and result.returncode != 0:
            raise AdbError(_format_adb_error(argv, result.returncode, result.stderr))
        return result

    def _remember_process(self, proc: subprocess.Popen) -> None:
        with self._abort_lock:
            self._proc = proc
            kill_now = self._aborted
        if kill_now:
            proc.kill()

    def abort(self) -> None:
        """Kill the adb process running right now and make every later call raise :class:`AdbCancelled`.

        Safe to call from any thread, any number of times. The caller that was waiting on the killed process
        raises :class:`AdbCancelled` instead of returning a result.
        """
        with self._abort_lock:
            self._aborted = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                return
            threading.Timer(2.0, lambda: proc.poll() is None and proc.kill()).start()

    def shell(self, cmd: str) -> str:
        """Run ``adb shell <cmd>`` and return its stdout as text."""
        result = self.run(["shell", cmd])
        return result.stdout if isinstance(result.stdout, str) else _decode(result.stdout)

    def exec_out(self, cmd: str) -> bytes:
        """Run ``adb exec-out <cmd>`` and return its stdout as raw bytes (binary-safe)."""
        result = self.run(["exec-out", cmd], binary=True)
        return result.stdout


def list_devices(adb_path: str = "adb") -> list[DeviceInfo]:
    """Parse ``adb devices -l`` into a list of :class:`DeviceInfo`."""
    argv = [adb_path, "devices", "-l"]
    try:
        result = _run_process(argv, capture_output=True, text=True, timeout=30)
    except FileNotFoundError as exc:
        raise AdbError(f"adb executable not found ({adb_path!r}): {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"`{' '.join(argv)}` timed out") from exc

    if result.returncode != 0:
        raise AdbError(_format_adb_error(argv, result.returncode, result.stderr))

    devices: list[DeviceInfo] = []
    for line in result.stdout.splitlines()[1:]:  # skip "List of devices attached"
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        serial = parts[0]
        state = parts[1] if len(parts) > 1 else "unknown"
        model = None
        for tok in parts[2:]:
            if tok.startswith("model:"):
                model = tok.split(":", 1)[1]
                break
        devices.append(
            DeviceInfo(
                serial=serial,
                state=state,
                model=model,
                transport="tcp" if _looks_like_tcp(serial) else "usb",
            )
        )
    return devices


def _adb_connect(target: str, adb_path: str) -> str:
    """Run ``adb connect <target>``, raising :class:`AdbError` on any failure."""
    argv = [adb_path, "connect", target]
    try:
        result = _run_process(argv, capture_output=True, text=True, timeout=30)
    except FileNotFoundError as exc:
        raise AdbError(f"adb executable not found ({adb_path!r}): {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"`{' '.join(argv)}` timed out") from exc
    output = f"{result.stdout or ''}{result.stderr or ''}"
    if result.returncode != 0 or "unable to connect" in output.lower() or "failed to connect" in output.lower():
        raise AdbError(f"adb connect {target} failed: {output.strip()}")
    return target


def _ambiguous_error(ready: list[DeviceInfo]) -> AdbError:
    details = ", ".join(f"{d.serial} ({d.state})" for d in ready)
    return AdbError(
        f"more than one phone is attached: {details}. Nothing was synced. Unplug one, or set the pair's "
        "'device' to the phone it belongs to (see `androsync devices`)"
    )


def hardware_serial(connection_serial: str, adb_path: str = "adb") -> str | None:
    """The phone's own serial number (``ro.serialno``): the same over USB and over Wi-Fi, unlike the connection serial.

    ``None`` when the phone does not tell (some report ``unknown``).
    """
    try:
        result = _run_process(
            [adb_path, "-s", connection_serial, "shell", "getprop ro.serialno"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (result.stdout or "").strip() if isinstance(result.stdout, str) else ""
    return value if result.returncode == 0 and value and value.lower() != "unknown" else None


def phone_model(connection_serial: str, adb_path: str = "adb") -> str | None:
    """The phone's model name (``ro.product.model``, for example "SM-S721B"), used until the user names the phone."""
    try:
        result = _run_process(
            [adb_path, "-s", connection_serial, "shell", "getprop ro.product.model"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (result.stdout or "").strip() if isinstance(result.stdout, str) else ""
    return value if result.returncode == 0 and value else None


def resolve_pair_device(
    pair_device: str | None, config_device: str, *, wifi_target: str | None = None, adb_path: str = "adb"
) -> str:
    """The connection serial to use for a pair bound to the phone ``pair_device`` (task 33a).

    ``"any"``/empty means the usual :func:`resolve_device`. Otherwise the phone is looked up among the ready devices
    by its hardware serial or its connection serial, so a phone bound by its hardware serial is found over USB and
    over Wi-Fi alike. Raises :class:`AdbError` naming what is attached when it is not there.
    """
    if not pair_device or pair_device == "any":
        return resolve_device(config_device, wifi_target=wifi_target, adb_path=adb_path)
    ready = [d for d in list_devices(adb_path) if d.state == "device"]
    for dev in ready:
        if dev.serial == pair_device or hardware_serial(dev.serial, adb_path) == pair_device:
            return dev.serial
    attached = ", ".join(d.serial for d in ready) or "none"
    raise AdbError(f"the phone {pair_device!r} is not connected (ready phones: {attached})")


def resolve_device(config_device: str, *, wifi_target: str | None = None, adb_path: str = "adb") -> str:
    """Turn the config's ``device:``/``wifi_target:`` values into a concrete serial/host:port.

    - ``"auto"`` (task 32b): prefers 1) a USB device already in state
      ``"device"``, 2) an already-connected Wi-Fi/TCP device, 3) ``adb
      connect <wifi_target>`` if one is configured - in that order, so a
      phone sitting right there on the USB cable is always picked over a
      stale or unreachable Wi-Fi target instead of the whole sync failing
      outright. Raises :class:`AdbError` (listing what ``adb devices -l``
      showed and what was tried) if none of those work.
    - ``"wifi:IP:PORT"``: equivalent to ``"auto"`` with that ``IP:PORT`` as
      ``wifi_target`` - previously this always ran ``adb connect`` even with
      the phone already on the USB cable, which failed outright whenever the
      phone's Wireless-debugging TCP listener had reset (e.g. after a
      reboot - it does not survive one).
    - anything else: treated as a literal serial, verified present and
      authorized in ``adb devices -l``.
    """
    if config_device.startswith("wifi:") and wifi_target is None:
        wifi_target = config_device[len("wifi:") :]
        config_device = "auto"

    if config_device == "auto":
        devices = list_devices(adb_path)

        usb_ready = [d for d in devices if d.transport == "usb" and d.state == "device"]
        if len(usb_ready) == 1:
            return usb_ready[0].serial
        if len(usb_ready) > 1:
            raise _ambiguous_error(usb_ready)

        tcp_ready = [d for d in devices if d.transport == "tcp" and d.state == "device"]
        if len(tcp_ready) == 1:
            return tcp_ready[0].serial
        if len(tcp_ready) > 1:
            raise _ambiguous_error(tcp_ready)

        if wifi_target:
            return _adb_connect(wifi_target, adb_path)

        if not devices:
            raise AdbError(
                "no device found - check the USB cable and that USB debugging is "
                "enabled (`adb devices -l` reported none)"
            )
        details = ", ".join(f"{d.serial} ({d.state})" for d in devices)
        message = f"no ready device found: {details}"
        if any(d.state == "unauthorized" for d in devices):
            message += " - accept the 'Allow USB debugging' prompt on the phone"
        raise AdbError(message)

    devices = list_devices(adb_path)
    match = next((d for d in devices if d.serial == config_device), None)
    if match is None:
        raise AdbError(f"device {config_device!r} not found (`adb devices -l` did not list it)")
    if match.state != "device":
        message = f"device {config_device!r} is not ready (state: {match.state})"
        if match.state == "unauthorized":
            message += " - accept the 'Allow USB debugging' prompt on the phone"
        raise AdbError(message)
    return config_device


def _pruned_by_ancestor_dir(rel: str, ignore: IgnoreRules) -> bool:
    """True if any ancestor directory of ``rel`` would be pruned by ``ignore.matches_dir``."""
    parts = rel.split("/")[:-1]
    acc = ""
    for part in parts:
        acc = part if not acc else f"{acc}/{part}"
        if ignore.matches_dir(acc):
            return True
    return False


def _join(root: str, rel: str) -> str:
    if not rel:
        return root
    return f"{root.rstrip('/')}/{rel}"


_MEDIA_ROOTS = tuple(f"/sdcard/{name}/" for name in ("DCIM", "Pictures", "Movies", "Music", "Download"))


def _is_media_path(full_path: str) -> bool:
    """True for a path under one of the standard media directories (task 24).

    Android's ``MediaScanner`` only indexes those on its own schedule;
    broadcasting a scan for a freshly pushed file there is what makes it
    show up in the Gallery/Photos/Files apps immediately instead of after
    the next full scan (which can be a long time on some devices).
    """
    return full_path.startswith(_MEDIA_ROOTS)


def _media_scan_broadcast(full_path: str) -> str:
    return f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://{shlex.quote(full_path)}"


def _incoming_dir(root: str) -> str:
    """Hidden folder inside a pair's phone folder where pushes land before being moved into place.

    ``.androsync/**`` is always ignored by the scanner, so anything left here (after a Stop or a pulled
    cable) is never synced.
    """
    return f"{root.rstrip('/')}/.androsync/incoming"


def _find_root(path: str) -> str:
    """Normalize a path for use as a ``find`` search root.

    Samsung's toybox ``find`` silently returns zero matches when the root is
    given without a trailing slash and happens to be a mount point (e.g.
    ``/sdcard``, which is bind-mounted to ``/storage/emulated/0``); the same
    command with a trailing slash works. Adding one unconditionally is a
    no-op for ordinary directories, so it is always safe.
    """
    return path.rstrip("/") + "/"


class AdbTransport(Transport):
    """Transport backed by ``adb``, for the phone side of a pair."""

    # A destination path colliding with an existing directory reliably
    # makes `adb push` fail loudly ("remote couldn't create file: Is a
    # directory", nonzero exit - confirmed against a real device), which
    # store_many already raises on via Adb.run's exit-code check. See
    # executor._do_copy_batch for the local-transport counter-example this
    # flag exists to distinguish from.
    store_many_is_self_verifying = True

    def __init__(self, adb: Adb) -> None:
        super().__init__(Side.REMOTE)
        self.adb = adb
        self._find_printf_ok: bool | None = None
        self._mtime_preserved: bool | None = None
        self._sha256sum_ok: bool | None = None
        self._device_key: str | None = None
        self._device_key_known = False

    @property
    def device_key(self) -> str | None:
        """What names this phone's sync history (task 33a): its hardware serial, else the connection serial."""
        if not self._device_key_known:
            serial = self.adb.serial
            found = hardware_serial(serial, self.adb.adb_path) if serial else None
            self._device_key = found or serial
            self._device_key_known = True
        return self._device_key

    # -- capability probes --------------------------------------------------

    def _supports_find_printf(self) -> bool:
        """Cache (on the instance) whether ``find -printf`` works on this device."""
        if self._find_printf_ok is None:
            result = self.adb.run(
                ["exec-out", "find /sdcard -maxdepth 0 -printf '%p\\0'"],
                check=False,
                binary=True,
            )
            self._find_printf_ok = result.returncode == 0 and bool((result.stdout or b"").strip())
        return self._find_printf_ok

    def check_mtime_preserved(self) -> bool:
        """Cache (on the instance) whether ``adb push`` preserves the source mtime.

        Pushes a tiny temp file with a known mtime to
        ``/sdcard/.androsync-selftest``, stats it back, and deletes it.
        """
        if self._mtime_preserved is None:
            self._mtime_preserved = self._probe_mtime_preserved()
        return self._mtime_preserved

    def _probe_mtime_preserved(self) -> bool:
        test_mtime = 1_000_000_000  # fixed, unambiguous, far from "now"
        fd, local_tmp = tempfile.mkstemp(prefix="androsync-selftest-")
        try:
            os.close(fd)
            Path(local_tmp).write_bytes(b"androsync selftest\n")
            os.utime(local_tmp, (test_mtime, test_mtime))
            push = self.adb.run(["push", local_tmp, _SELFTEST_PATH], check=False)
            if push.returncode != 0:
                return False
            entry = self._stat_path(_SELFTEST_PATH)
            if entry is None:
                return False
            return abs(entry.mtime - test_mtime) <= 2
        finally:
            os.unlink(local_tmp)
            self.adb.run(["shell", f"rm -f {shlex.quote(_SELFTEST_PATH)}"], check=False)

    def _supports_sha256sum(self) -> bool:
        """Cache (on the instance) whether ``sha256sum`` exists on this device."""
        if self._sha256sum_ok is None:
            result = self.adb.run(["shell", "command -v sha256sum"], check=False)
            self._sha256sum_ok = result.returncode == 0 and bool((result.stdout or "").strip())
        return self._sha256sum_ok

    # -- Transport interface --------------------------------------------------

    def scan(self, root: str, ignore: IgnoreRules) -> Snapshot:
        snapshot: Snapshot = {}
        if not self.exists(root):
            return snapshot

        if self._supports_find_printf():
            cmd = f"find {shlex.quote(_find_root(root))} -type f -printf '%s\\t%T@\\t%P\\0'"
            raw = self.adb.exec_out(cmd)
            for chunk in raw.split(b"\0"):
                if not chunk:
                    continue
                text = chunk.decode("utf-8", "replace")
                try:
                    size_s, mtime_s, rel = text.split("\t", 2)
                except ValueError:
                    continue
                self._record(snapshot, rel, size_s, mtime_s, ignore)
        else:
            cmd = f"find {shlex.quote(_find_root(root))} -type f -exec stat -c '%s %Y %n' {{}} +"
            out = self.adb.shell(cmd)
            prefix = root.rstrip("/") + "/"
            for line in out.splitlines():
                line = line.rstrip("\r")
                if not line:
                    continue
                try:
                    size_s, mtime_s, name = line.split(" ", 2)
                except ValueError:
                    continue
                rel = name[len(prefix) :] if name.startswith(prefix) else name
                self._record(snapshot, rel, size_s, mtime_s, ignore)

        return snapshot

    @staticmethod
    def _record(snapshot: Snapshot, rel: str, size_s: str, mtime_s: str, ignore: IgnoreRules) -> None:
        if not rel:
            return
        if ignore.matches(rel):
            return
        if _pruned_by_ancestor_dir(rel, ignore):
            return
        try:
            snapshot[rel] = FileEntry(size=int(size_s), mtime=float(mtime_s))
        except ValueError:
            return

    def ls(self, path: str) -> list[tuple[str, bool, int]]:
        """List the immediate children of ``path`` as ``(name, is_dir, size)``.

        Uses ``%M`` (the ``ls``-style mode string, e.g. ``drwxr-xr-x``) rather
        than GNU find's ``%y`` file-type char: Samsung's toybox ``find``
        rejects ``%y`` outright ("bad -printf %y").
        """
        if self._supports_find_printf():
            cmd = f"find {shlex.quote(_find_root(path))} -mindepth 1 -maxdepth 1 -printf '%M\\t%s\\t%f\\0'"
            raw = self.adb.exec_out(cmd)
            entries: list[tuple[str, bool, int]] = []
            for chunk in raw.split(b"\0"):
                if not chunk:
                    continue
                text = chunk.decode("utf-8", "replace")
                try:
                    mode_s, size_s, name = text.split("\t", 2)
                except ValueError:
                    continue
                try:
                    size = int(size_s)
                except ValueError:
                    size = -1
                entries.append((name, mode_s.startswith("d"), size))
            return entries

        out = self.adb.shell(f"ls -p {shlex.quote(path)}")
        entries = []
        for line in out.splitlines():
            name = line.strip()
            if not name or name in (".", "..", "./", "../"):
                continue
            is_dir = name.endswith("/")
            if is_dir:
                name = name[:-1]
            entries.append((name, is_dir, -1))
        return entries

    def fetch(self, root: str, rel: str, tmp_path: Path) -> None:
        check_rel(rel)
        full = _join(root, rel)
        self.adb.run(["pull", "-a", full, str(tmp_path)])

    def store(self, tmp_path: Path, root: str, rel: str, mtime: float) -> None:
        root = self._checked(root)
        check_rel(rel)
        full = _join(root, rel)
        parent = full.rsplit("/", 1)[0] if "/" in full else root
        # Push to a hidden folder (never synced) and move into place when complete, so Stop or a pulled cable
        # can never leave a truncated file where the next run would take it for a real change (task 36a).
        incoming = _incoming_dir(root)
        partial = f"{incoming}/{uuid.uuid4().hex[:8]}"
        self.adb.run(["shell", f"mkdir -p {shlex.quote(parent)} {shlex.quote(incoming)}"])
        self.adb.run(["push", str(tmp_path), partial])
        self.adb.run(["shell", f"mv -f {shlex.quote(partial)} {shlex.quote(full)}"])
        if not self.check_mtime_preserved():
            self._touch_mtime(full, mtime)
        if _is_media_path(full):
            self.adb.run(["shell", _media_scan_broadcast(full)], check=False)

    def store_many(self, items: list[tuple[Path, str, str, float]]) -> None:
        """Batched :meth:`store` (task 24): one ``adb push`` for the whole batch.

        Stages every ``tmp_path`` into a temp tree mirroring its ``rel``,
        pushes that tree with a single ``adb push <staging>/. <root>`` (which
        creates any missing destination directories, root included), then
        runs one multi-line ``adb shell`` script - only if the mtime
        self-test says push does not preserve it - touching every file's
        mtime, plus (for any file under ``DCIM``/``Pictures``/``Movies``/
        ``Music``/``Download``) a media-scanner broadcast so it shows up in
        the phone's gallery/media apps without waiting for their next scan,
        instead of one ``adb shell`` call per file per concern. Items for
        different ``root``s (not something the executor's batching ever
        actually produces, since one pair syncs one root per side, but the
        interface allows it) are grouped and pushed separately.
        """
        if not items:
            return
        by_root: dict[str, list[tuple[Path, str, float]]] = {}
        for tmp_path, root, rel, mtime in items:
            check_rel(rel)
            by_root.setdefault(self._checked(root), []).append((tmp_path, rel, mtime))
        for root, entries in by_root.items():
            self._store_many_one_root(root, entries)

    def _store_many_one_root(self, root: str, entries: list[tuple[Path, str, float]]) -> None:
        with tempfile.TemporaryDirectory(prefix="androsync-push-") as staging_name:
            staging = Path(staging_name)
            for tmp_path, rel, _mtime in entries:
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(tmp_path, dest)
            # Push into a hidden folder first (never synced), then move each file into place: a Stop or a pulled
            # cable mid-push leaves only junk in there, never a truncated file in the synced folder (task 36a).
            incoming = f"{_incoming_dir(root)}/{uuid.uuid4().hex[:8]}"
            self.adb.run(["shell", f"rm -rf {shlex.quote(_incoming_dir(root))}; mkdir -p {shlex.quote(incoming)}"])
            self.adb.run(["push", f"{staging}/.", incoming])

        moves: list[str] = []
        made_dirs: set[str] = set()
        for _tmp, rel, _mtime in entries:
            full = _join(root, rel)
            parent = full.rsplit("/", 1)[0]
            if parent not in made_dirs:
                moves.append(f"mkdir -p {shlex.quote(parent)}")
                made_dirs.add(parent)
            moves.append(f"mv -f {shlex.quote(_join(incoming, rel))} {shlex.quote(full)}")
        moves.append(f"rm -rf {shlex.quote(incoming)}")
        self.adb.run(["shell", "\n".join(moves)])

        touch_needed = not self.check_mtime_preserved()
        lines: list[str] = []
        if touch_needed:
            lines.extend(f"touch -m -d @{int(mtime)} {shlex.quote(_join(root, rel))}" for _tmp, rel, mtime in entries)
        lines.extend(
            _media_scan_broadcast(full)
            for _tmp, rel, _mtime in entries
            if _is_media_path(full := _join(root, rel))
        )
        if not lines:
            return

        result = self.adb.run(["shell", "\n".join(lines)], check=False)
        if result.returncode == 0 or not touch_needed:
            return
        # Same per-file fallback _touch_mtime uses for a device whose toybox
        # rejects `-d @epoch` (needs `-t` instead) - one file at a time here
        # since the batched script already failed as a whole. The media
        # scan broadcast, if any ran, is best-effort and not retried.
        for _tmp, rel, mtime in entries:
            self._touch_mtime(_join(root, rel), mtime)

    def fetch_many(self, items: list[tuple[str, str, Path]]) -> None:
        """Batched :meth:`fetch` (task 24): pull a whole directory in one
        ``adb pull -a`` when most of a batch's files share one, instead of
        pulling each individually.

        Groups ``items`` by ``(root, immediate parent directory of rel)``.
        Pulling several *individually named* remote paths in one ``adb
        pull`` was tried first and rejected: adb flattens every pulled path
        into the single local destination by basename, so two files with
        the same name from different directories silently clobber each
        other. Pulling one directory *recursively* (a single source
        argument) does not have that problem - it mirrors the remote
        subtree - so a group is only pulled this way when a cheap remote
        file count (``find -maxdepth 1``) shows at least 80% of that
        directory's files are wanted; otherwise (or for a lone file) it
        falls back to :meth:`fetch` per file.
        """
        if not items:
            return
        groups: dict[tuple[str, str], list[tuple[str, Path]]] = {}
        for root, rel, tmp_path in items:
            check_rel(rel)
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            groups.setdefault((root, parent), []).append((rel, tmp_path))

        for (root, parent), entries in groups.items():
            if len(entries) < 2 or not self._directory_pull_worthwhile(root, parent, len(entries)):
                for rel, tmp_path in entries:
                    self.fetch(root, rel, tmp_path)
                continue
            self._fetch_directory(root, parent, entries)

    def _directory_pull_worthwhile(self, root: str, parent: str, wanted_count: int) -> bool:
        full_dir = _join(root, parent) if parent else root
        cmd = f"find {shlex.quote(_find_root(full_dir))} -maxdepth 1 -type f | wc -l"
        result = self.adb.run(["shell", cmd], check=False)
        if result.returncode != 0:
            return False
        try:
            total = int(_decode(result.stdout).strip())
        except ValueError:
            return False
        return total > 0 and wanted_count / total >= 0.8

    def _fetch_directory(self, root: str, parent: str, entries: list[tuple[str, Path]]) -> None:
        full_dir = _join(root, parent) if parent else root
        with tempfile.TemporaryDirectory(prefix="androsync-pull-") as staging_name:
            staging = Path(staging_name)
            result = self.adb.run(["pull", "-a", full_dir, str(staging)], check=False)
            if result.returncode != 0:
                for rel, tmp_path in entries:
                    self.fetch(root, rel, tmp_path)
                return
            # `adb pull -a <dir> <staging>` nests the pulled tree one level
            # deeper, under the source directory's own basename.
            pulled_root = staging / full_dir.rstrip("/").rsplit("/", 1)[-1]
            for rel, tmp_path in entries:
                basename = rel.rsplit("/", 1)[-1]
                pulled_file = pulled_root / basename
                if pulled_file.exists():
                    shutil.copy2(pulled_file, tmp_path)
                else:
                    # Vanished between scan and pull, or an edge case in how
                    # adb laid out the tree - fall back for this one file.
                    self.fetch(root, rel, tmp_path)

    _HASH_BATCH_SIZE = 200

    def hash_many(self, root: str, rels: list[str]) -> dict[str, str]:
        """Hash ``rels`` under ``root`` on-device (task 29a): ``sha256sum``, or

        ``md5sum`` (stored as ``"md5:<hex>"``) on a device that lacks it.
        Runs in batches of up to 200 paths per ``exec_out`` call. Output
        lines are parsed by the known digest length rather than splitting on
        whitespace, since a filename can itself contain spaces.
        """
        if not rels:
            return {}
        for rel in rels:
            check_rel(rel)
        use_sha256 = self._supports_sha256sum()
        algo = "sha256sum" if use_sha256 else "md5sum"
        digest_len = 64 if use_sha256 else 32
        prefix = "" if use_sha256 else "md5:"

        result: dict[str, str] = {}
        for i in range(0, len(rels), self._HASH_BATCH_SIZE):
            batch = rels[i : i + self._HASH_BATCH_SIZE]
            quoted = " ".join(shlex.quote(rel) for rel in batch)
            cmd = f"cd {shlex.quote(root)} && {algo} -- {quoted}"
            raw = self.adb.exec_out(cmd)
            text = raw.decode("utf-8", "replace")
            for line in text.splitlines():
                line = line.rstrip("\r")
                if len(line) <= digest_len + 1:
                    continue
                digest = line[:digest_len]
                name = line[digest_len:].lstrip(" *")
                result[name] = prefix + digest
        return result

    def _touch_mtime(self, full_path: str, mtime: float) -> None:
        epoch = int(mtime)
        quoted = shlex.quote(full_path)
        result = self.adb.run(["shell", f"touch -m -d @{epoch} {quoted}"], check=False)
        if result.returncode == 0:
            return
        stamp = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y%m%d%H%M.%S")
        result2 = self.adb.run(["shell", f"TZ=UTC touch -m -t {stamp} {quoted}"], check=False)
        if result2.returncode != 0:
            raise AdbError(
                f"touch failed for {full_path!r}: {_decode(result.stderr).strip()} / "
                f"{_decode(result2.stderr).strip()}"
            )

    def delete(self, root: str, rel: str) -> None:
        root = self._checked(root)
        check_rel(rel)
        full = _join(root, rel)
        self.adb.run(["shell", f"rm -f {shlex.quote(full)}"])
        self._prune_empty_parents(root, full)

    def _prune_empty_parents(self, root: str, deleted_full: str) -> None:
        root_norm = root.rstrip("/")
        parent = deleted_full.rsplit("/", 1)[0] if "/" in deleted_full else root_norm
        while parent != root_norm and parent.startswith(root_norm + "/"):
            result = self.adb.run(["shell", f"rmdir {shlex.quote(parent)}"], check=False)
            if result.returncode != 0:
                break
            parent = parent.rsplit("/", 1)[0] if "/" in parent else root_norm

    def rename(self, root: str, rel_from: str, rel_to: str) -> None:
        root = self._checked(root)
        check_rel(rel_from)
        check_rel(rel_to)
        full_from = _join(root, rel_from)
        full_to = _join(root, rel_to)
        parent_to = full_to.rsplit("/", 1)[0] if "/" in full_to else root
        self.adb.run(
            [
                "shell",
                f"mkdir -p {shlex.quote(parent_to)} && mv {shlex.quote(full_from)} {shlex.quote(full_to)}",
            ]
        )

    def mkdirs(self, root: str, rel_dir: str) -> None:
        root = self._checked(root)
        if rel_dir:
            check_rel(rel_dir)
        full = _join(root, rel_dir) if rel_dir else root
        if not rel_dir:
            self._require_existing_ancestor(root)
        self.adb.run(["shell", f"mkdir -p {shlex.quote(full)}"])

    def _require_existing_ancestor(self, root: str, max_levels: int = 2) -> None:
        """Creating a pair's phone folder may add at most ``max_levels`` new levels under a folder that exists.

        Stops a typo such as ``/Internal storage/Inbox`` from making new top-level folders on the phone
        (or failing deep inside the file system) by accident.
        """
        path = root.rstrip("/")
        for _ in range(max_levels):
            path = path.rsplit("/", 1)[0]
            if not path:
                break
            if self._dir_exists(path):
                return
        raise AdbError(
            f"Phone folder {root} does not exist and no folder above it (up to {max_levels} levels) does either. "
            "Create the parent folder on the phone first, or choose an existing folder."
        )

    def abort(self) -> None:
        self.adb.abort()

    def _checked(self, root: str) -> str:
        """Normalize a pair's phone folder and refuse anything outside shared storage (task 36c).

        Runs again at sync time, so a hand-edited config cannot point a sync at ``/`` or ``/data``.
        """
        try:
            return check_remote(root)
        except PhonePathError as exc:
            raise AdbError(str(exc)) from exc

    def _dir_exists(self, path: str) -> bool:
        result = self.adb.run(["shell", f"test -d {shlex.quote(path)}"], check=False)
        return result.returncode == 0

    def exists(self, root: str) -> bool:
        return self._dir_exists(self._checked(root))

    def stat(self, root: str, rel: str) -> FileEntry | None:
        check_rel(rel)
        return self._stat_path(_join(root, rel))

    def _stat_path(self, full: str) -> FileEntry | None:
        result = self.adb.run(["shell", f"stat -c '%s %Y' {shlex.quote(full)} 2>/dev/null"], check=False)
        out = (result.stdout or "").strip() if isinstance(result.stdout, str) else _decode(result.stdout).strip()
        if result.returncode != 0 or not out:
            return None
        try:
            size_s, mtime_s = out.split()
            return FileEntry(size=int(size_s), mtime=float(mtime_s))
        except ValueError:
            return None
