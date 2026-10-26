"""Support: a redacted log bundle a person can attach to a problem report (Phase S1).

``androsync support-bundle`` and the window's Help, "Report a problem" collect what a developer needs (versions, the
service state, the config, the last log lines, ``adb devices``, the latest jobs) into one zip. Everything goes through
:class:`Redactor` first: the token, addresses, phone serials, the user's name and folders, and (by default) pair names
and folders are replaced by placeholders. Nothing is ever sent anywhere; the person reads the bundle and attaches it
themselves.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from androsync import __version__

REPO_URL = "https://github.com/Abdoh-Abqr/androsync"
#: Opens the bug report form (``.github/ISSUE_TEMPLATE/bug_report.yml``).
ISSUE_URL = f"{REPO_URL}/issues/new?template=bug_report.yml"
#: A project address for private reports. Empty until the owner has made an alias; the email button stays hidden then.
SUPPORT_EMAIL = ""

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9_\-]{16,}")
_TOKEN_FIELD = re.compile(r"""(["']?token["']?\s*[:=]\s*["']?)[A-Za-z0-9_\-]{16,}""", re.IGNORECASE)


def classify_ip(text: str) -> str | None:
    """The placeholder for an IPv4 address in ``text``, or None to leave it (loopback) or when it is not an address."""
    try:
        ip = ipaddress.IPv4Address(text)
    except ValueError:
        return None
    if ip.is_loopback or ip.is_unspecified:
        return None
    first, second = ip.packed[0], ip.packed[1]
    if first == 100 and 64 <= second <= 127:  # the block Tailscale hands out
        return "<tailscale-ip>"
    if ip.is_private or ip.is_link_local:
        return "<lan-ip>"
    return "<ip>"


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:6]


@dataclass
class Redactor:
    """Replaces what identifies a person, a phone or a laptop. Applied to every line that goes into a report."""

    secrets: list[str] = field(default_factory=list)
    serials: list[str] = field(default_factory=list)
    home: str = ""
    user: str = ""
    hostname: str = ""
    #: exact text -> replacement, for pair names and folders; longest first so a folder wins over the name inside it
    names: dict[str, str] = field(default_factory=dict)

    def __call__(self, text: str) -> str:
        for original in sorted(self.names, key=len, reverse=True):
            if original:
                text = text.replace(original, self.names[original])
        if self.home and len(self.home) > 1:
            text = text.replace(self.home, "~")
        for secret in sorted(set(self.secrets), key=len, reverse=True):
            if secret:
                text = text.replace(secret, "<token>")
        text = _BEARER.sub(r"\1<token>", text)
        text = _TOKEN_FIELD.sub(r"\1<token>", text)
        for serial in sorted(set(self.serials), key=len, reverse=True):
            if len(serial) >= 4:
                text = text.replace(serial, f"phone-{short_hash(serial)}")
        for word, placeholder in ((self.hostname, "<host>"), (self.user, "<user>")):
            if word and len(word) >= 3:
                text = re.sub(rf"\b{re.escape(word)}\b", placeholder, text)

        def swap(match: re.Match) -> str:
            return classify_ip(match.group(0)) or match.group(0)

        return _IPV4.sub(swap, text)


def make_redactor(
    *,
    token: str | None,
    serials: list[str],
    pair_names: list[str],
    folders: list[str],
    hide_names: bool,
    home: str | None = None,
    user: str | None = None,
    hostname: str | None = None,
) -> Redactor:
    names: dict[str, str] = {}
    if hide_names:
        for i, folder in enumerate(sorted(set(folders), key=len, reverse=True), 1):
            names[folder] = f"<folder-{i}>"
        for i, name in enumerate(sorted(set(pair_names)), 1):
            if len(name) >= 3:
                names[name] = f"pair-{i}"
    return Redactor(
        secrets=[token] if token else [],
        serials=serials,
        home=home if home is not None else str(Path.home()),
        user=user if user is not None else (os.environ.get("USER") or os.environ.get("USERNAME") or ""),
        hostname=hostname if hostname is not None else platform.node(),
        names=names,
    )


def _tail(path: Path, lines: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readlines()[-lines:])
    except OSError:
        return f"(could not read {path.name})\n"


def _run(argv: list[str], timeout: float = 10) -> str:
    if shutil.which(argv[0]) is None:
        return f"({argv[0]} is not installed)\n"
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(could not run {argv[0]}: {exc})\n"
    return (result.stdout or "") + (result.stderr or "")


def parse_adb_serials(adb_devices_output: str) -> list[str]:
    """The serials in ``adb devices -l`` output (every line after the header with a state column)."""
    serials = []
    for line in adb_devices_output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("device", "unauthorized", "offline"):
            serials.append(parts[0])
    return serials


def _jobs_text(config, redact: Redactor) -> str:
    """Recent jobs from the running service, or a note when none is running."""
    from androsync.apiclient import ApiClient, ApiError

    if not config.service.token:
        return "(no token configured)\n"
    client = ApiClient(f"http://127.0.0.1:{config.service.port}", config.service.token, timeout=5)
    try:
        client.ping()
        jobs = client._request("GET", "/v1/jobs")
    except (ApiError, OSError) as exc:
        return f"(the service did not answer: {exc})\n"
    out = []
    for job in list(jobs)[:3]:
        out.append(f"== job {job.get('id')} state={job.get('state')} kind={job.get('kind')} pairs={job.get('pairs')}")
        try:
            detail = client.job(job["id"])
            for line in (detail.get("log_tail") or [])[-60:]:
                out.append(f"  {line}")
        except (ApiError, OSError, KeyError):
            out.append("  (log not available)")
    return redact("\n".join(out) + "\n")


def collect(config_path: Path, *, hide_names: bool = True) -> dict[str, str]:
    """Build the bundle: file name -> redacted text. Never raises for a missing piece; notes it instead."""
    from androsync import service_state
    from androsync.config import load_config

    files: dict[str, str] = {}
    adb_out = _run(["adb", "devices", "-l"])
    token = None
    pair_names: list[str] = []
    folders: list[str] = []
    config = None
    config_error = ""
    try:
        config = load_config(config_path)
        token = config.service.token
        pair_names = [p.name for p in config.pairs]
        folders = [str(p.local) for p in config.pairs] + [p.remote for p in config.pairs]
    except Exception as exc:  # noqa: BLE001 - a broken config is exactly what a report may be about
        config_error = str(exc)
    redact = make_redactor(
        token=token,
        serials=parse_adb_serials(adb_out),
        pair_names=pair_names,
        folders=folders,
        hide_names=hide_names,
    )

    try:
        mode = service_state.detect_state().mode
    except Exception as exc:  # noqa: BLE001
        mode = f"unknown ({exc})"
    files["report.txt"] = redact(
        "\n".join(
            [
                f"androsync {__version__}",
                f"python {sys.version.split()[0]} on {platform.platform()}",
                f"service mode: {mode}",
                f"made: {datetime.now().isoformat(timespec='seconds')}",
                f"pairs: {len(pair_names)}",
                f"names and folders hidden: {hide_names}",
                f"config problem: {config_error}" if config_error else "config: loads fine",
                "",
            ]
        )
    )
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        service = dict(raw.get("service") or {})
        if "token" in service:
            service["token"] = "<token>"
        raw["service"] = service
        files["config.yaml.txt"] = redact(yaml.safe_dump(raw, sort_keys=False))
    except Exception as exc:  # noqa: BLE001
        files["config.yaml.txt"] = f"(could not read the config: {exc})\n"

    state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "androsync"
    files["daemon.log.txt"] = redact(_tail(state_home / "daemon.log.1", 100) + _tail(state_home / "daemon.log", 300))
    files["last-run.log.txt"] = redact(_tail(state_home / "last-run.log", 200))
    files["adb-devices.txt"] = redact(adb_out)
    files["service-status.txt"] = redact(_run(["systemctl", "--user", "status", "androsync", "--no-pager", "-n", "30"]))
    files["recent-jobs.txt"] = _jobs_text(config, redact) if config is not None else "(no config)\n"
    return files


def write_bundle(files: dict[str, str], out: Path) -> Path:
    """Write ``files`` as a zip at ``out`` (owner-only) and return the path."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    out.chmod(0o600)
    return out


def default_bundle_path() -> Path:
    return Path.home() / f"androsync-support-{datetime.now():%Y%m%d-%H%M%S}.zip"


def issue_url_with(version: str | None = None) -> str:
    """The new-issue URL; the form itself asks for the rest. Only the version is prefilled (no personal data)."""
    return ISSUE_URL + (f"&version={version}" if version else "")


def describe(files: dict[str, str]) -> str:
    lines = [f"  {name:<22}{len(text.encode('utf-8')):>9} bytes" for name, text in files.items()]
    return "\n".join(lines)


__all__ = [
    "Redactor",
    "classify_ip",
    "collect",
    "default_bundle_path",
    "describe",
    "issue_url_with",
    "make_redactor",
    "parse_adb_serials",
    "write_bundle",
]
