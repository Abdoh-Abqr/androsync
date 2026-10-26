"""The wrong-connection sentence in front of the adb hint (Phase V3)."""

import pytest

from androsync.daemon.problems import AGENT_OPTION, classify_route, explain_failure, is_no_phone_error

ADB_MSG = "no device found - check the USB cable and that USB debugging is enabled (`adb devices -l` reported none)"


@pytest.mark.parametrize(
    "ip, route",
    [
        ("127.0.0.1", "usb"),
        ("::1", "usb"),
        ("100.64.0.9", "tailscale"),
        ("100.127.255.1", "tailscale"),
        ("100.128.0.1", "other"),
        ("192.168.1.20", "lan"),
        ("10.1.2.3", "lan"),
        ("8.8.8.8", "other"),
        ("not-an-ip", "other"),
        (None, "other"),
    ],
)
def test_classify_route(ip, route):
    assert classify_route(ip) == route


def test_sentence_comes_first_and_hint_stays():
    text = explain_failure(ADB_MSG, pair_transport="adb", agent_connected=True, agent_route="tailscale")
    assert text.startswith(
        "This pair works over USB or Wi-Fi debugging only, but the phone is connected over Tailscale right now. "
    )
    assert ADB_MSG in text
    assert text.endswith(AGENT_OPTION)


def test_route_words():
    lan = explain_failure(ADB_MSG, pair_transport="adb", agent_connected=True, agent_route="lan")
    assert "connected over the local network right now" in lan
    app = explain_failure(ADB_MSG, pair_transport="adb", agent_connected=True, agent_route=None)
    assert "connected through the phone app right now" in app


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(pair_transport="agent", agent_connected=True, agent_route="tailscale"),
        dict(pair_transport="adb", agent_connected=False, agent_route=None),
    ],
)
def test_unchanged_when_not_a_wrong_connection(kwargs):
    assert explain_failure(ADB_MSG, **kwargs) == ADB_MSG


def test_other_errors_are_not_touched():
    msg = "more than one phone is attached: A, B"
    assert not is_no_phone_error(msg)
    assert explain_failure(msg, pair_transport="adb", agent_connected=True, agent_route="tailscale") == msg
