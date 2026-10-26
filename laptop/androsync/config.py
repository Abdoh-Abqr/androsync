"""Load and validate the androsync YAML config.

Merges ``defaults`` into each pair (pair values win, ``ignore`` lists are
concatenated), expands ``~`` and environment variables in local paths, and
validates the result. Also provides the default config path and the state
directory helper used by ``state.py``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from androsync.safety import SAFETY_DEFAULTS, SafetyLimits

DIRECTIONS = ("two-way", "laptop-to-phone", "phone-to-laptop")
CONFLICT_POLICIES = ("newest-wins", "local-wins", "remote-wins", "ask")
VERSIONING_MODES = ("none", "git")
#: How a pair reaches the phone (task 37d, issue #16): "adb" (USB or Wi-Fi debugging, the default) or "agent"
#: (the phone app does the file work and calls the laptop; no debugging needed).
TRANSPORT_MODES = ("adb", "agent")
#: Where a kept conflict-loser copy goes when ``keep_conflict_copies`` is True
#: (Phase H, task 29b). "folder" (default): fetched to
#: ``<local>/.androsync/conflicts/<timestamp>/<rel>`` on the laptop only,
#: nothing renamed or pushed. "beside": the pre-Phase-H behaviour - renamed
#: in place with a ``.conflict-<side>-<timestamp>`` suffix and pushed to
#: both sides.
CONFLICT_COPIES_MODES = ("folder", "beside")

#: Defaults for the `undo` block (Phase I, task 30a) - `defaults.undo` merged
#: with a pair's own `undo:` dict, flattened onto PairConfig as
#: `undo_keep_runs`/`undo_max_total_mb`/`undo_max_file_mb`/`undo_enabled`.
UNDO_DEFAULTS = {"keep_runs": 10, "max_total_mb": 2048, "max_file_mb": 500, "enabled": True}

#: Defaults for the `transfer:` block (defaults.transfer, then pair.transfer): how many files / how many MB go in
#: one batched copy. Small batches mean frequent progress lines and a Stop that takes effect within seconds.
TRANSFER_DEFAULTS = {"batch_files": 50, "batch_mb": 64}


class ConfigError(Exception):
    """Raised when the config file is missing required fields or is invalid."""


def _default_browse_roots() -> list[str]:
    """``["~"]`` plus ``/media/<user>`` when that directory exists (removable media)."""
    roots = ["~"]
    user = os.environ.get("USER") or os.environ.get("USERNAME")
    if user:
        media_root = Path("/media") / user
        if media_root.is_dir():
            roots.append(str(media_root))
    return roots


@dataclass
class ServiceConfig:
    listen: list[str] = field(default_factory=lambda: ["127.0.0.1"])
    port: int = 8765
    token: str | None = None
    auto_sync_on_connect: bool = False
    notify: bool = True
    browse_roots: list[str] = field(default_factory=_default_browse_roots)
    #: On a USB connect, also run `adb -s <serial> tcpip 5555` so the phone starts
    #: listening for the later Wi-Fi/Tailscale sync (task 32b) - only when
    #: `wifi_target` ends with `:5555` (the port that command fixes); resets on
    #: the phone's next reboot, same as the port itself.
    enable_tcpip_on_usb: bool = True


@dataclass
class PhoneConfig:
    """A phone the user named (task 33b, issue #3 of the public repository). The id is the phone's hardware serial."""

    name: str = ""
    #: `host:port` for Wi-Fi/Tailscale debugging of this phone; the daemon keeps trying to reconnect it.
    wifi_target: str | None = None


@dataclass
class PairConfig:
    name: str
    local: Path
    remote: str
    direction: Literal["two-way", "laptop-to-phone", "phone-to-laptop"]
    conflict: Literal["newest-wins", "local-wins", "remote-wins", "ask"]
    keep_conflict_copies: bool
    propagate_deletes: bool
    mtime_tolerance_s: float
    ignore: list[str]
    versioning: Literal["none", "git"] = "none"
    conflict_copies: Literal["folder", "beside"] = "folder"
    #: "adb" (default) or "agent": see :data:`TRANSPORT_MODES`.
    transport: Literal["adb", "agent"] = "adb"
    #: Sync by itself when files change (Phase T2). Off by default; the first sync of a pair is always a manual one.
    auto: bool = False
    #: Seconds to wait after the last change before an automatic sync starts, so a burst of changes becomes one sync.
    auto_delay_s: int = 10
    #: How often a phone that cannot report changes (adb) is looked at, in seconds.
    auto_interval_s: int = 300
    #: Which phone this pair belongs to (task 33a): "any" (default) or a phone's hardware serial, as shown by
    #: `androsync devices`. A pair bound to a phone only syncs with that phone.
    device: str = "any"
    #: Off skips this pair in `status`/`run`/auto-sync (Phase I `--pause`). Does not affect `resolve`.
    enabled: bool = True
    undo_keep_runs: int = 10
    undo_max_total_mb: int = 2048
    undo_max_file_mb: int = 500
    undo_enabled: bool = True
    transfer_batch_files: int = 50
    transfer_batch_mb: int = 64
    safety_max_delete_count: int = 50
    safety_max_delete_percent: int = 20
    safety_big_first_run_files: int = 5000
    safety_big_first_run_gb: int = 5

    @property
    def safety_limits(self) -> SafetyLimits:
        """When a plan for this pair needs the user's confirmation (task 36d, issue #9)."""
        return SafetyLimits(
            max_delete_count=self.safety_max_delete_count,
            max_delete_percent=self.safety_max_delete_percent,
            big_first_run_files=self.safety_big_first_run_files,
            big_first_run_gb=self.safety_big_first_run_gb,
        )


@dataclass
class Config:
    service: ServiceConfig
    defaults: dict[str, Any]
    pairs: list[PairConfig]
    device: str = "auto"
    #: Wi-Fi fallback target for `device: auto` (task 32b): tried only when no
    #: USB device and no already-connected Wi-Fi device are found. A legacy
    #: `device: wifi:IP:PORT` value is equivalent to `device: auto` +
    #: this field set to `IP:PORT` - see `resolve_device`.
    wifi_target: str | None = None
    #: The phones the user named, by hardware serial (`androsync devices` shows it). Optional.
    phones: dict[str, PhoneConfig] = field(default_factory=dict)


def secure_file(path: Path) -> bool:
    """Make ``path`` readable by its owner only (mode 600); returns True if its mode had to change.

    The config holds the service token, which is the key to the whole API, so no other user of the computer may read it.
    """
    try:
        mode = path.stat().st_mode & 0o777
        if mode != 0o600:
            path.chmod(0o600)
            return True
    except OSError:
        pass
    return False


def default_config_path() -> Path:
    """Return ``$XDG_CONFIG_HOME/androsync/config.yaml`` or the ``~/.config`` fallback."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "androsync" / "config.yaml"


def state_dir() -> Path:
    """Return ``$XDG_DATA_HOME/androsync/state`` or the ``~/.local/share`` fallback."""
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "androsync" / "state"


def _expand_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value))))


def _as_listen_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return list(value)


def load_config(path: Path | None = None) -> Config:
    """Load, merge and validate the YAML config at ``path`` (default: the XDG path)."""
    cfg_path = path if path is not None else default_config_path()
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    device = raw.get("device", "auto")
    wifi_target = raw.get("wifi_target")

    service_raw = dict(raw.get("service") or {})
    if "listen" in service_raw:
        service_raw["listen"] = _as_listen_list(service_raw["listen"])
    service = ServiceConfig(**service_raw)

    if not (1 <= service.port <= 65535):
        raise ConfigError(f"service: port must be between 1 and 65535, got {service.port!r}")

    defaults = dict(raw.get("defaults") or {})
    default_ignore = list(defaults.get("ignore") or [])

    pairs_raw = raw.get("pairs") or []
    pairs: list[PairConfig] = []
    seen_names: set[str] = set()

    for pair_raw in pairs_raw:
        pair_raw = dict(pair_raw)
        name = pair_raw.get("name")
        if not name:
            raise ConfigError("pair <unnamed>: 'name' is required")

        if "@" in str(name):
            # "<pair>@<phone>" names a phone's history files (task 33a), so a pair name must not contain it.
            raise ConfigError(f"pair {name}: the name must not contain '@'")
        if name in seen_names:
            raise ConfigError(f"pair {name}: duplicate pair name")
        seen_names.add(name)

        merged: dict[str, Any] = dict(defaults)
        merged.pop("ignore", None)
        merged.update(pair_raw)

        pair_ignore = list(pair_raw.get("ignore") or [])
        merged["ignore"] = default_ignore + pair_ignore

        local = merged.get("local")
        if not local:
            raise ConfigError(f"pair {name}: 'local' is required")
        merged["local"] = _expand_path(local)

        remote = merged.get("remote")
        if not remote:
            raise ConfigError(f"pair {name}: 'remote' is required")
        if not str(remote).startswith("/"):
            raise ConfigError(f"pair {name}: 'remote' must be an absolute path starting with '/', got {remote!r}")

        direction = merged.get("direction")
        if direction not in DIRECTIONS:
            raise ConfigError(
                f"pair {name}: 'direction' must be one of {DIRECTIONS}, got {direction!r}"
            )

        conflict = merged.get("conflict", "newest-wins")
        if conflict not in CONFLICT_POLICIES:
            raise ConfigError(
                f"pair {name}: 'conflict' must be one of {CONFLICT_POLICIES}, got {conflict!r}"
            )

        keep_conflict_copies = bool(merged.get("keep_conflict_copies", True))
        propagate_deletes = bool(merged.get("propagate_deletes", True))
        mtime_tolerance_s = float(merged.get("mtime_tolerance_s", 2))

        versioning = merged.get("versioning", "none")
        if versioning not in VERSIONING_MODES:
            raise ConfigError(
                f"pair {name}: 'versioning' must be one of {VERSIONING_MODES}, got {versioning!r}"
            )

        conflict_copies = merged.get("conflict_copies", "folder")
        if conflict_copies not in CONFLICT_COPIES_MODES:
            raise ConfigError(
                f"pair {name}: 'conflict_copies' must be one of {CONFLICT_COPIES_MODES}, got {conflict_copies!r}"
            )

        transport = merged.get("transport", "adb")
        if transport not in TRANSPORT_MODES:
            raise ConfigError(f"pair {name}: 'transport' must be one of {TRANSPORT_MODES}, got {transport!r}")

        pair_device = str(merged.get("device", "any")).strip() or "any"

        auto = bool(merged.get("auto", False))
        try:
            auto_delay_s = int(merged.get("auto_delay_s", 10))
            auto_interval_s = int(merged.get("auto_interval_s", 300))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"pair {name}: 'auto_delay_s' and 'auto_interval_s' must be whole numbers") from exc
        if not 1 <= auto_delay_s <= 3600:
            raise ConfigError(f"pair {name}: 'auto_delay_s' must be between 1 and 3600 seconds")
        if not 60 <= auto_interval_s <= 86400:
            raise ConfigError(f"pair {name}: 'auto_interval_s' must be between 60 and 86400 seconds")

        enabled = bool(merged.get("enabled", True))

        undo_raw = dict(UNDO_DEFAULTS)
        undo_raw.update(defaults.get("undo") or {})
        undo_raw.update(pair_raw.get("undo") or {})
        try:
            undo_keep_runs = int(undo_raw["keep_runs"])
            undo_max_total_mb = int(undo_raw["max_total_mb"])
            undo_max_file_mb = int(undo_raw["max_file_mb"])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"pair {name}: 'undo' settings must be numbers: {exc}") from exc
        undo_enabled = bool(undo_raw["enabled"])

        transfer_raw = dict(TRANSFER_DEFAULTS)
        transfer_raw.update(defaults.get("transfer") or {})
        transfer_raw.update(pair_raw.get("transfer") or {})
        try:
            transfer_batch_files = int(transfer_raw["batch_files"])
            transfer_batch_mb = int(transfer_raw["batch_mb"])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"pair {name}: 'transfer' settings must be numbers: {exc}") from exc
        if transfer_batch_files < 1 or transfer_batch_mb < 1:
            raise ConfigError(f"pair {name}: 'transfer' batch_files and batch_mb must be at least 1")

        safety_raw = dict(SAFETY_DEFAULTS)
        safety_raw.update(defaults.get("safety") or {})
        safety_raw.update(pair_raw.get("safety") or {})
        try:
            safety_values = {key: int(safety_raw[key]) for key in SAFETY_DEFAULTS}
        except (TypeError, ValueError, KeyError) as exc:
            raise ConfigError(f"pair {name}: 'safety' settings must be whole numbers: {exc}") from exc
        if any(value < 0 for value in safety_values.values()):
            raise ConfigError(f"pair {name}: 'safety' settings cannot be negative (use 0 to switch a check off)")

        pairs.append(
            PairConfig(
                name=name,
                local=merged["local"],
                remote=remote,
                direction=direction,
                conflict=conflict,
                keep_conflict_copies=keep_conflict_copies,
                propagate_deletes=propagate_deletes,
                mtime_tolerance_s=mtime_tolerance_s,
                ignore=merged["ignore"],
                versioning=versioning,
                conflict_copies=conflict_copies,
                transport=transport,
                device=pair_device,
                auto=auto,
                auto_delay_s=auto_delay_s,
                auto_interval_s=auto_interval_s,
                enabled=enabled,
                undo_keep_runs=undo_keep_runs,
                undo_max_total_mb=undo_max_total_mb,
                undo_max_file_mb=undo_max_file_mb,
                undo_enabled=undo_enabled,
                transfer_batch_files=transfer_batch_files,
                transfer_batch_mb=transfer_batch_mb,
                safety_max_delete_count=safety_values["max_delete_count"],
                safety_max_delete_percent=safety_values["max_delete_percent"],
                safety_big_first_run_files=safety_values["big_first_run_files"],
                safety_big_first_run_gb=safety_values["big_first_run_gb"],
            )
        )

    phones: dict[str, PhoneConfig] = {}
    phones_raw = raw.get("phones") or {}
    if not isinstance(phones_raw, dict):
        raise ConfigError("'phones' must be a mapping of phone id to {name, wifi_target}")
    for phone_id, entry in phones_raw.items():
        entry = entry or {}
        if not isinstance(entry, dict) or set(entry) - {"name", "wifi_target"}:
            raise ConfigError(f"phone {phone_id}: only 'name' and 'wifi_target' are allowed")
        target = entry.get("wifi_target")
        if target is not None and not re.fullmatch(r"[^\s:]+:\d{1,5}", str(target)):
            raise ConfigError(f"phone {phone_id}: 'wifi_target' must look like host:port, got {target!r}")
        phones[str(phone_id)] = PhoneConfig(name=str(entry.get("name") or ""), wifi_target=str(target) if target else None)

    return Config(
        device=device, service=service, defaults=defaults, pairs=pairs, wifi_target=wifi_target, phones=phones
    )
