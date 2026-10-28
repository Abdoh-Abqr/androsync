"""Install the AndroSync phone app from the computer (Phase W4).

The release wheel carries the signed ``full`` APK (``assets/apk/androsync-remote-full.apk`` and ``apk.json`` with its
version and SHA-256). ``androsync app install`` puts it on every phone adb sees, ``androsync app status`` says which
phone has which version, and ``androsync app share`` makes a one-time download link for a phone without adb.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from androsync.transport.adb import AdbError, _run_process, list_devices

PACKAGE = "com.androsync.remote"
APK_NAME = "androsync-remote-full.apk"
ASSET_DIR = Path(__file__).resolve().parent / "assets" / "apk"
DOWNLOAD_URL = "https://github.com/Abdoh-Abqr/androsync/releases/latest"
LINK_SECONDS = 600
#: More wrong guesses than this within a minute and the download route answers 429.
MAX_FAILURES_PER_MINUTE = 10


class AppInstallError(Exception):
    """A problem with the bundled APK or with putting it on a phone; the message says what to do."""


@dataclass(frozen=True)
class BundledApp:
    path: Path
    version_name: str
    version_code: int
    sha256: str


@dataclass(frozen=True)
class InstalledApp:
    version_name: str
    version_code: int
    updated: str


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundled_app(asset_dir: Path | None = None) -> BundledApp | None:
    """The APK that came with this install, or ``None`` (a source checkout has none). Raises if the file was altered."""
    folder = asset_dir or ASSET_DIR
    apk, meta_path = folder / APK_NAME, folder / "apk.json"
    if not apk.is_file() or not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        app = BundledApp(apk, str(meta["versionName"]), int(meta["versionCode"]), str(meta["sha256"]).lower())
    except (OSError, ValueError, KeyError) as exc:
        raise AppInstallError(f"the bundled app description is unreadable ({exc})") from exc
    if sha256_of(apk) != app.sha256:
        raise AppInstallError("the bundled APK does not match its checksum; reinstall androsync")
    return app


def missing_text() -> str:
    return (
        "this install has no app file (a source checkout does not bundle it). Download the APK from "
        f"{DOWNLOAD_URL} and run `androsync app install --apk <file>`."
    )


_VERSION_NAME = re.compile(r"versionName=(\S+)")
_VERSION_CODE = re.compile(r"versionCode=(\d+)")
_UPDATED = re.compile(r"lastUpdateTime=(.+)")


def parse_package_dump(text: str) -> InstalledApp | None:
    """The version lines of ``dumpsys package com.androsync.remote``, or ``None`` when the package is not installed."""
    name, code = _VERSION_NAME.search(text), _VERSION_CODE.search(text)
    if name is None or code is None:
        return None
    updated = _UPDATED.search(text)
    return InstalledApp(name.group(1), int(code.group(1)), updated.group(1).strip() if updated else "")


def installed_app(serial: str, adb_path: str = "adb") -> InstalledApp | None:
    try:
        result = _run_process(
            [adb_path, "-s", serial, "shell", f"dumpsys package {PACKAGE}"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppInstallError(f"could not ask {serial}: {exc}") from exc
    return parse_package_dump(result.stdout or "") if result.returncode == 0 else None


def explain_install_error(output: str) -> str:
    """What the person should do about an ``adb install`` failure."""
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in output or "signatures do not match" in output:
        return (
            "the app on the phone is signed with a different key (for example from another store). Uninstall it on the "
            "phone first; the app keeps only the laptop address and token, so you scan the pairing QR again afterwards."
        )
    if "INSTALL_FAILED_VERSION_DOWNGRADE" in output:
        return "the phone has a newer version of the app than this install carries."
    if "INSTALL_FAILED_USER_RESTRICTED" in output or "INSTALL_FAILED_ABORTED" in output:
        return "the phone refused the install. On some phones switch on 'Install via USB' in Developer options and accept the prompt."
    if "unauthorized" in output:
        return "the phone has not allowed this computer yet: accept the 'Allow USB debugging' prompt on its screen."
    return output.strip() or "adb install failed"


def install_apk(serial: str, apk: Path, adb_path: str = "adb") -> None:
    """``adb -s <serial> install -r <apk>``; raises :class:`AppInstallError` with advice when it fails."""
    try:
        result = _run_process([adb_path, "-s", serial, "install", "-r", str(apk)], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppInstallError(f"could not run adb ({exc})") from exc
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "Success" not in output:
        raise AppInstallError(explain_install_error(output))


def ready_phones(adb_path: str = "adb") -> list[str]:
    """The serials of the phones adb can talk to now."""
    try:
        return [d.serial for d in list_devices(adb_path) if d.state == "device"]
    except AdbError as exc:
        raise AppInstallError(str(exc)) from exc


def needs_install(app: BundledApp, installed: InstalledApp | None) -> bool:
    return installed is None or installed.version_code < app.version_code


class ShareLinks:
    """One-time, short-lived download links for the bundled APK (a phone without adb). The random path is the secret."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._links: dict[str, float] = {}
        self._failures: deque[float] = deque()

    def create(self, ttl: float = LINK_SECONDS) -> str:
        token = secrets.token_urlsafe(16)
        with self._lock:
            self._purge()
            self._links[token] = self._clock() + ttl
        return token

    def _purge(self) -> None:
        now = self._clock()
        for token in [t for t, expires in self._links.items() if expires <= now]:
            del self._links[token]

    def blocked(self) -> bool:
        with self._lock:
            now = self._clock()
            while self._failures and now - self._failures[0] > 60:
                self._failures.popleft()
            return len(self._failures) >= MAX_FAILURES_PER_MINUTE

    def consume(self, token: str) -> bool:
        """True once for a valid, unexpired token; every wrong or reused one counts towards the rate limit."""
        with self._lock:
            self._purge()
            if self._links.pop(token, None) is not None:
                return True
            self._failures.append(self._clock())
            return False
