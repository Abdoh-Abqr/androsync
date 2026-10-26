"""Run conditions for automatic syncs (Phase T3, public issue #11): Wi-Fi only, charging only, a time window.

They apply only to syncs that start by themselves; a sync you start is always allowed. When the phone's state is not
known (an older app, a failed query) the condition that needs it does not block: the point is to avoid surprises on mobile
data and battery, not to stop syncing because something could not be read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time

from androsync.config import ConfigError

_SCHEDULE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class RunConditions:
    wifi_only: bool = False
    charging_only: bool = False
    #: (start, end) local times; may cross midnight (22:00-07:00).
    schedule: tuple[time, time] | None = None

    @property
    def any(self) -> bool:
        return self.wifi_only or self.charging_only or self.schedule is not None


def parse(raw: object, pair_name: str) -> RunConditions:
    """Read a pair's ``run_conditions`` mapping; ``ConfigError`` names the pair on any mistake."""
    if raw in (None, {}):
        return RunConditions()
    if not isinstance(raw, dict) or set(raw) - {"wifi_only", "charging_only", "schedule"}:
        raise ConfigError(f"pair {pair_name}: 'run_conditions' may only have wifi_only, charging_only and schedule")
    schedule = None
    text = raw.get("schedule")
    if text is not None:
        match = _SCHEDULE.match(str(text).strip())
        if not match:
            raise ConfigError(f"pair {pair_name}: 'schedule' must look like 22:00-07:00, got {text!r}")
        h1, m1, h2, m2 = (int(g) for g in match.groups())
        if not (0 <= h1 <= 23 and 0 <= h2 <= 23 and 0 <= m1 <= 59 and 0 <= m2 <= 59):
            raise ConfigError(f"pair {pair_name}: 'schedule' times must be real clock times, got {text!r}")
        schedule = (time(h1, m1), time(h2, m2))
    return RunConditions(
        wifi_only=bool(raw.get("wifi_only", False)),
        charging_only=bool(raw.get("charging_only", False)),
        schedule=schedule,
    )


def in_schedule(window: tuple[time, time], now: time) -> bool:
    start, end = window
    if start <= end:
        return start <= now < end
    return now >= start or now < end  # crosses midnight


def allowed(cond: RunConditions, now: datetime, phone: dict) -> tuple[bool, str]:
    """``(True, "")`` when an automatic sync may start now, else ``(False, why)``.

    ``phone`` is ``{"net": "wifi"|"mobile"|"none"|None, "charging": bool|None}``; ``None`` means not known.
    """
    if cond.schedule is not None and not in_schedule(cond.schedule, now.time()):
        start, end = cond.schedule
        return False, f"outside the allowed hours {start:%H:%M}-{end:%H:%M}"
    if cond.wifi_only and phone.get("net") in ("mobile", "none"):
        return False, "waiting for Wi-Fi"
    if cond.charging_only and phone.get("charging") is False:
        return False, "waiting for the phone to charge"
    return True, ""


def parse_battery(dumpsys_battery: str) -> bool | None:
    """``True`` when the phone is on a charger, from ``adb shell dumpsys battery``; ``None`` if it cannot be told."""
    powered = re.findall(r"(?:AC|USB|Wireless|Dock) powered:\s*(true|false)", dumpsys_battery, re.IGNORECASE)
    if not powered:
        return None
    return any(p.lower() == "true" for p in powered)


def parse_route(ip_route_get: str) -> str | None:
    """``wifi`` or ``mobile`` from ``adb shell ip route get 1.1.1.1`` (the interface that carries traffic), else ``None``."""
    match = re.search(r"\bdev\s+(\S+)", ip_route_get)
    if not match:
        return None
    device = match.group(1).lower()
    if device.startswith(("wlan", "wifi", "swlan")):
        return "wifi"
    if device.startswith(("rmnet", "ccmni", "v4-rmnet", "pdp", "ppp", "wwan")):
        return "mobile"
    return None
