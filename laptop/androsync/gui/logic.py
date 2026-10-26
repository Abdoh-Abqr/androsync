"""Pure helpers behind the Tk GUI (task 31b) - no ``tkinter`` import here.

Kept separate from the widget modules so the state -> button-set mapping,
pair row formatting, and the close-window decision table are all plain
functions with plain unit tests, not something that needs a display to
exercise. See ``gui/app.py``/``service_panel.py``/``pairs_panel.py`` for the
widgets that call these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from androsync.service_state import Mode, ServiceState

ButtonId = Literal["run_here", "start_background", "stop", "autostart", "show_qr"]


@dataclass(frozen=True)
class ServicePanelView:
    """What the Service panel should show for a given :class:`ServiceState`."""

    status_text: str
    detail_text: str
    buttons: frozenset[ButtonId] = field(default_factory=frozenset)
    autostart_checked: bool = False


def _ping_detail(state: ServiceState) -> str:
    if not state.ping:
        return ""
    device = state.ping.get("device")
    transport = state.ping.get("transport")
    return f"device: {device} ({transport})" if device else "no device connected"


def service_panel_view(state: ServiceState) -> ServicePanelView:
    """Map a detected run state to the Service panel's status text and button set."""
    if state.mode == "none":
        return ServicePanelView(
            status_text="Not running",
            detail_text="",
            buttons=frozenset({"run_here", "start_background", "autostart"}),
            autostart_checked=state.autostart,
        )
    if state.mode == "inprocess":
        return ServicePanelView(
            status_text="Running here (this window)",
            detail_text=_ping_detail(state),
            buttons=frozenset({"stop", "autostart", "show_qr"}),
            autostart_checked=state.autostart,
        )
    if state.mode == "systemd":
        autostart = "on" if state.autostart else "off"
        return ServicePanelView(
            status_text=f"Running in background, autostart {autostart}",
            detail_text=_ping_detail(state),
            buttons=frozenset({"stop", "autostart", "show_qr"}),
            autostart_checked=state.autostart,
        )
    # "other": some separately-started process (a plain `androsync serve`,
    # or another instance of the GUI) - nothing here started it, so there is
    # nothing safe to offer but autostart and the QR code.
    return ServicePanelView(
        status_text=f"Running in another process (pid {state.pid})",
        detail_text=_ping_detail(state),
        buttons=frozenset({"autostart", "show_qr"}),
        autostart_checked=state.autostart,
    )


PairRow = tuple[str, str, str, str, str]


def format_pair_row(pair: dict) -> PairRow:
    """``(name, direction, last run, last result, enabled)`` for the Pairs table.

    ``pair`` is one entry of ``daemon.jobs.pairs_status()`` - the same shape
    whether it came from calling that function directly or from
    ``GET /v1/pairs``.
    """
    name = pair["name"]
    direction = pair.get("direction", "")
    last_run = pair.get("last_run") or "never"
    last_result = pair.get("last_result") or ""
    enabled = "yes" if pair.get("enabled", True) else "no"
    return (name, direction, last_run, last_result, enabled)


def suggest_transport(config_default: str | None, ping: dict | None) -> str:
    """The way to reach the phone to preselect for a NEW pair (task 37d follow-up).

    The user's saved default wins. Otherwise: an adb phone that is attached means ``adb``; no adb phone but the
    phone app's agent polling means ``agent``; nothing known means ``adb`` (the long-standing default).
    """
    if config_default in ("adb", "agent"):
        return config_default
    info = ping or {}
    if info.get("device") or any(d.get("state") == "device" for d in info.get("devices") or []):
        return "adb"
    if info.get("agent") == "connected":
        return "agent"
    return "adb"


def auto_label(pair: dict) -> str:
    """The Pairs table's "Auto" cell (Phase T2): empty when off, else what automatic sync is doing."""
    if not pair.get("auto"):
        return "off"
    info = pair.get("auto_state") or {}
    state = info.get("state", "watching")
    return "paused" if state == "paused" else ("waiting" if state == "waiting" else "on")


def transport_label(pair: dict) -> str:
    """How the pair reaches the phone, for the Pairs table (task 37d): ``adb`` or ``phone app``."""
    return "phone app" if pair.get("transport") == "agent" else "adb"


CloseDecision = Literal["just_close", "ask_stop_or_background"]


def close_window_decision(mode: Mode) -> CloseDecision:
    """Window close = ``[q]`` (PLAN.md): only ask when the daemon runs in this process.

    Any other mode (systemd, another process, or not running) leaves
    whatever is or isn't running untouched - closing the window is just
    closing the window.
    """
    return "ask_stop_or_background" if mode == "inprocess" else "just_close"


ShutdownStep = Literal["start_background", "stop_daemon", "destroy"]


def shutdown_steps(mode: Mode, answer: bool | None) -> list[ShutdownStep]:
    """What ``App._on_close`` should do, in order, for a given mode/answer (task 32a).

    ``answer`` is the result of the "stop it now?" confirmation dialog
    (``close_window_decision`` says to ask one at all only when
    ``mode == "inprocess"``): ``True`` = stop it, ``False`` = hand it over
    to the background service instead, ``None`` = cancel.

    Every other mode never asks, so it is always just ``["destroy"]`` -
    nothing here started or owns whatever is (or isn't) running, so closing
    the window must not touch it either.
    """
    if mode != "inprocess":
        return ["destroy"]
    if answer is None:
        return []  # cancelled: the window stays open, nothing happens
    if answer is False:
        # Hand over first - starting the background copy before stopping this
        # one means the port is never briefly held by neither, and never by
        # both at once past the point `stop_daemon` runs.
        return ["start_background", "stop_daemon", "destroy"]
    return ["stop_daemon", "destroy"]


@dataclass(frozen=True)
class SyncBarView:
    """What the strip above the log shows: the running sync (whoever started it) and its Stop button."""

    text: str
    can_stop: bool
    stop_label: str = "Stop"
    job_id: str | None = None


def _pretty_pairs(pairs: list[str]) -> str:
    if not pairs:
        return "all pairs"
    return ", ".join(pairs)


def sync_bar_view(current_job: dict | None, running_directly: bool) -> SyncBarView:
    """Map the daemon's ``current_job`` (from ``GET /v1/ping``) or a run in this process to the bar (task 36b)."""
    if current_job is not None:
        kind = "Undoing" if current_job.get("kind") == "undo" else "Syncing"
        what = f"{kind} {_pretty_pairs(list(current_job.get('pairs') or []))}"
        last = current_job.get("last_line")
        text = f"{what}: {last}" if last else what
        if current_job.get("state") == "cancelling":
            return SyncBarView(text=text, can_stop=False, stop_label="Stopping...", job_id=current_job.get("id"))
        return SyncBarView(text=text, can_stop=True, job_id=current_job.get("id"))
    if running_directly:
        return SyncBarView(text="Syncing in this window", can_stop=True)
    return SyncBarView(text="No sync running", can_stop=False)


def should_attach(current_job: dict | None, following_job_id: str | None, backend_busy: bool) -> str | None:
    """Job id the window should start following, or ``None``.

    A sync started somewhere else (the phone, a script) appears in the ping; the window then shows its full
    log. Nothing is attached while the window is already busy, or already follows this very job.
    """
    if current_job is None or backend_busy:
        return None
    job_id = current_job.get("id")
    if not job_id or job_id == following_job_id:
        return None
    return job_id


def folder_change_prompt(pair_name: str) -> tuple[str, str]:
    """Title and text of the question the window asks BEFORE saving a pair whose laptop or phone folder changed.

    Saving resets the pair's history (task 36d, issue #9). It used to be reported only in the log after saving, so a
    user could not know beforehand; now they can cancel.
    """
    return (
        "Folders changed",
        f"You changed the laptop or phone folder of '{pair_name}'.\n\n"
        "Saving forgets what this pair knew about its files, so the next sync only adds files and deletes "
        "nothing. No file is removed from either side because of the change.\n\n"
        "Save anyway?",
    )
