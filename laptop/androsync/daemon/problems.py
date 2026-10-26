"""Say what is actually wrong when a pair cannot reach its phone (Phase V3).

A pair with ``transport: adb`` needs USB or Wi-Fi debugging. When the phone is only connected through the app (agent
mode over Tailscale or the local network), adb finds no device and the raw adb hint ("check the USB cable...") hides the
real cause. :func:`explain_failure` puts the cause first and keeps the original hint after it.
"""

from __future__ import annotations

import ipaddress

#: Parts of the adb "no phone" errors raised in ``transport/adb.py``. "more than one phone" is a different problem.
_NO_PHONE_MARKERS = (
    "no device found",
    "no ready device",
    "no devices/emulators",
    "no connection to the phone",
    "phone not connected",
    "is not connected (ready phones",
    "not found (`adb devices",
    "is not ready (state",
)

AGENT_OPTION = 'Or set this pair to "This app (no debugging)".'


def classify_route(ip: str | None) -> str:
    """How a request reached the computer: ``usb`` (this machine, e.g. ``adb reverse``), ``tailscale``, ``lan`` or ``other``."""
    try:
        addr = ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return "other"
    if addr.is_loopback:
        return "usb"
    if isinstance(addr, ipaddress.IPv4Address) and addr.packed[0] == 100 and 64 <= addr.packed[1] <= 127:  # the block Tailscale hands out
        return "tailscale"
    if addr.is_private or addr.is_link_local:
        return "lan"
    return "other"


def _via(route: str | None) -> str:
    return {
        "tailscale": "over Tailscale",
        "lan": "over the local network",
    }.get(route or "", "through the phone app")


def is_no_phone_error(message: str) -> bool:
    return any(marker in message for marker in _NO_PHONE_MARKERS)


def explain_failure(
    message: str,
    *,
    pair_transport: str,
    agent_connected: bool,
    agent_route: str | None,
) -> str:
    """``message`` unchanged, or with the wrong-connection sentence in front.

    The sentence is added only for an adb pair whose phone is not found over adb while the phone app is connected.
    Without the app connected the plain adb hint is the right text.
    """
    if pair_transport != "adb" or not agent_connected or not is_no_phone_error(message):
        return message
    return (
        f"This pair works over USB or Wi-Fi debugging only, but the phone is connected {_via(agent_route)} right now. "
        f"{message} {AGENT_OPTION}"
    )
