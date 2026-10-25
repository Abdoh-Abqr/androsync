"""Tests for androsync.cli's `pair-wifi` command (task 25).

`adb pair`/`adb connect` are mocked via `cli.subprocess.run` - no real adb
or device is touched.
"""

from __future__ import annotations

import json
import subprocess

import pytest
import yaml

from androsync import cli


def _cp(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def write_config(tmp_path, raw: dict):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


def test_pair_wifi_non_interactive_happy_path_writes_config(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"device": "auto", "pairs": []})

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["adb", "pair"]:
            return _cp(argv, 0, "Successfully paired to 100.64.0.20:41163", "")
        if argv[:2] == ["adb", "connect"]:
            return _cp(argv, 0, "connected to 100.64.0.20:5555", "")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = cli.main(
        [
            "--config",
            str(config_path),
            "pair-wifi",
            "--pairing-address",
            "100.64.0.20:37831",
            "--pairing-code",
            "123456",
            "--connect-address",
            "100.64.0.20:5555",
            "--yes",
        ]
    )

    assert rc == 0
    assert calls[0] == ["adb", "pair", "100.64.0.20:37831", "123456"]
    assert calls[1] == ["adb", "connect", "100.64.0.20:5555"]
    raw = yaml.safe_load(config_path.read_text())
    assert raw["device"] == "auto"
    assert raw["wifi_target"] == "100.64.0.20:5555"


def test_pair_wifi_interactive_prompts_and_default_connect_address_from_tailscale(tmp_path, monkeypatch):
    """task 33e: the suggested default comes from `tailscale status --json`, not a built-in address."""
    config_path = write_config(tmp_path, {"device": "auto", "pairs": []})

    tailscale_status = json.dumps(
        {
            "Peer": {
                "nodekey:1": {"OS": "linux", "TailscaleIPs": ["100.64.0.1"]},
                "nodekey:2": {"OS": "android", "TailscaleIPs": ["100.64.0.5", "fd7a:115c:a1e0::1"]},
            }
        }
    )

    def fake_run(argv, **kwargs):
        if argv[:2] == ["adb", "pair"]:
            return _cp(argv, 0, "Successfully paired", "")
        if argv[:2] == ["adb", "connect"]:
            return _cp(argv, 0, "connected", "")
        if argv[:2] == ["tailscale", "status"]:
            return _cp(argv, 0, tailscale_status, "")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/tailscale" if name == "tailscale" else None)

    answers = iter(
        [
            "192.0.2.10:37831",  # pairing address
            "123456",  # pairing code
            "",  # connect address: accept the suggested default
            "y",  # write to config
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = cli.main(["--config", str(config_path), "pair-wifi"])

    assert rc == 0
    raw = yaml.safe_load(config_path.read_text())
    assert raw["device"] == "auto"
    assert raw["wifi_target"] == "100.64.0.5:5555"


def test_pair_wifi_interactive_prompt_has_no_default_without_tailscale(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"device": "auto", "pairs": []})

    def fake_run(argv, **kwargs):
        if argv[:2] == ["adb", "pair"]:
            return _cp(argv, 0, "Successfully paired", "")
        if argv[:2] == ["adb", "connect"]:
            return _cp(argv, 0, "connected", "")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)  # tailscale not installed

    answers = iter(
        [
            "192.0.2.10:37831",  # pairing address
            "123456",  # pairing code
            "192.168.1.42:5555",  # connect address: typed by hand, no default offered
            "y",  # write to config
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = cli.main(["--config", str(config_path), "pair-wifi"])

    assert rc == 0
    raw = yaml.safe_load(config_path.read_text())
    assert raw["wifi_target"] == "192.168.1.42:5555"


def test_pair_wifi_pair_failure_does_not_touch_config(tmp_path, monkeypatch, capsys):
    config_path = write_config(tmp_path, {"device": "auto", "pairs": []})

    def fake_run(argv, **kwargs):
        if argv[:2] == ["adb", "pair"]:
            return _cp(argv, 1, "", "Failed: wrong pairing code")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = cli.main(
        [
            "--config",
            str(config_path),
            "pair-wifi",
            "--pairing-address",
            "100.64.0.20:37831",
            "--pairing-code",
            "000000",
            "--yes",
        ]
    )

    assert rc == 2
    assert "adb pair failed" in capsys.readouterr().out
    raw = yaml.safe_load(config_path.read_text())
    assert raw["device"] == "auto"


def test_pair_wifi_connect_failure_does_not_touch_config(tmp_path, monkeypatch, capsys):
    config_path = write_config(tmp_path, {"device": "auto", "pairs": []})

    def fake_run(argv, **kwargs):
        if argv[:2] == ["adb", "pair"]:
            return _cp(argv, 0, "Successfully paired", "")
        if argv[:2] == ["adb", "connect"]:
            return _cp(argv, 1, "", "unable to connect to 100.64.0.20:5555")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = cli.main(
        [
            "--config",
            str(config_path),
            "pair-wifi",
            "--pairing-address",
            "100.64.0.20:37831",
            "--pairing-code",
            "123456",
            "--connect-address",
            "100.64.0.20:5555",
            "--yes",
        ]
    )

    assert rc == 2
    assert "adb connect" in capsys.readouterr().out
    raw = yaml.safe_load(config_path.read_text())
    assert raw["device"] == "auto"


def test_pair_wifi_connect_exit_0_but_failure_text_is_still_detected(tmp_path, monkeypatch):
    """`adb connect` can exit 0 while printing a failure message - same
    quirk `resolve_device`'s wifi handling already works around.
    """
    config_path = write_config(tmp_path, {"device": "auto", "pairs": []})

    def fake_run(argv, **kwargs):
        if argv[:2] == ["adb", "pair"]:
            return _cp(argv, 0, "Successfully paired", "")
        if argv[:2] == ["adb", "connect"]:
            return _cp(argv, 0, "unable to connect to 100.64.0.20:5555: Connection refused", "")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = cli.main(
        [
            "--config",
            str(config_path),
            "pair-wifi",
            "--pairing-address",
            "100.64.0.20:37831",
            "--pairing-code",
            "123456",
            "--connect-address",
            "100.64.0.20:5555",
            "--yes",
        ]
    )

    assert rc == 2


def test_pair_wifi_declining_the_write_prompt_leaves_config_untouched(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"device": "auto", "pairs": []})

    def fake_run(argv, **kwargs):
        if argv[:2] == ["adb", "pair"]:
            return _cp(argv, 0, "Successfully paired", "")
        if argv[:2] == ["adb", "connect"]:
            return _cp(argv, 0, "connected", "")
        raise AssertionError(f"unexpected call: {argv}")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    rc = cli.main(
        [
            "--config",
            str(config_path),
            "pair-wifi",
            "--pairing-address",
            "100.64.0.20:37831",
            "--pairing-code",
            "123456",
            "--connect-address",
            "100.64.0.20:5555",
        ]
    )

    assert rc == 0
    raw = yaml.safe_load(config_path.read_text())
    assert raw["device"] == "auto"


def test_pair_wifi_missing_pairing_address_exits_2(tmp_path, monkeypatch, capsys):
    config_path = write_config(tmp_path, {"device": "auto", "pairs": []})
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    rc = cli.main(["--config", str(config_path), "pair-wifi", "--pairing-code", "123456"])

    assert rc == 2
    assert "required" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _detect_android_tailscale_ip (task 33e)
# ---------------------------------------------------------------------------


def test_detect_android_tailscale_ip_returns_none_without_tailscale(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli._detect_android_tailscale_ip() is None


def test_detect_android_tailscale_ip_returns_none_with_no_android_peer(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/tailscale")
    payload = json.dumps({"Peer": {"nodekey:1": {"OS": "linux", "TailscaleIPs": ["100.64.0.1"]}}})
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: _cp(argv, 0, payload, ""))
    assert cli._detect_android_tailscale_ip() is None


def test_detect_android_tailscale_ip_finds_the_android_peers_ipv4(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/tailscale")
    payload = json.dumps(
        {"Peer": {"nodekey:1": {"OS": "android", "TailscaleIPs": ["100.64.0.5", "fd7a:115c:a1e0::1"]}}}
    )
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: _cp(argv, 0, payload, ""))
    assert cli._detect_android_tailscale_ip() == "100.64.0.5"


def test_detect_android_tailscale_ip_returns_none_on_bad_json(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/tailscale")
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: _cp(argv, 0, "not json", ""))
    assert cli._detect_android_tailscale_ip() is None


def test_detect_android_tailscale_ip_returns_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/tailscale")
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: _cp(argv, 1, "", "not logged in"))
    assert cli._detect_android_tailscale_ip() is None
