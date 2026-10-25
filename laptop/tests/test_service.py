"""Tests for androsync.service (`service install/start/.../uninstall`, `qr`).

Every test here monkeypatches ``service.subprocess.run`` (recording the
argv of each call) and ``service.shutil.which`` (to control whether
``tailscale`` "is installed"), so nothing ever shells out to a real
``systemctl``, ``loginctl``, ``sudo ufw`` or ``tailscale`` binary.
``XDG_CONFIG_HOME`` is redirected to a temp dir so the systemd unit file
never touches the real ``~/.config``.
"""

from __future__ import annotations

import argparse
import getpass
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from androsync import service
from androsync.config import ConfigError

UNIT_NAME = service.UNIT_NAME


@pytest.fixture(autouse=True)
def _xdg_config_home(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return xdg


def unit_path(xdg_config_home: Path) -> Path:
    return xdg_config_home / "systemd" / "user" / UNIT_NAME


def make_fake_run(calls: list[list[str]], tailscale_ip: str | None = "100.64.0.10"):
    """A stand-in for subprocess.run that records argv and fakes `tailscale ip -4`."""

    def fake_run(argv, *, check=True, capture_output=False, text=True, **kwargs):  # noqa: ANN001
        calls.append(list(argv))
        if argv[:2] == ["tailscale", "ip"]:
            stdout = f"{tailscale_ip}\n" if tailscale_ip else ""
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    return fake_run


def make_install_args(config_path, *, dry_run=False, tailscale=None, listen=None, ufw=False, autostart=False):
    return argparse.Namespace(
        config=config_path,
        dry_run=dry_run,
        tailscale=tailscale,
        listen=list(listen or []),
        ufw=ufw,
        autostart=autostart,
        verbose=False,
    )


def write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def no_tailscale_which(name: str) -> str | None:
    return None


def tailscale_present_which(name: str) -> str | None:
    return "/usr/bin/tailscale" if name == "tailscale" else None


# ---------------------------------------------------------------------------
# unit file rendering
# ---------------------------------------------------------------------------


def test_render_unit_contains_expected_fields():
    content = service.render_unit("/usr/local/bin/androsync")
    assert "ExecStart=/usr/local/bin/androsync serve" in content
    assert "Restart=on-failure" in content
    assert "RestartSec=5" in content
    assert "After=default.target network-online.target" in content
    assert "WantedBy=default.target" in content
    assert "Environment=PYTHONUNBUFFERED=1" in content


# ---------------------------------------------------------------------------
# install: token generation + preserving the rest of the config
# ---------------------------------------------------------------------------


def test_install_generates_token_and_preserves_other_keys(tmp_path, monkeypatch):
    config_path = write_config(
        tmp_path,
        {
            "device": "auto",
            "service": {"listen": ["127.0.0.1"], "port": 9999, "notify": False},
            "defaults": {"conflict": "newest-wins"},
            "pairs": [{"name": "p1", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"}],
        },
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))
    monkeypatch.setattr(service.shutil, "which", no_tailscale_which)

    rc = service.cmd_service_install(make_install_args(config_path))
    assert rc == 0

    raw = yaml.safe_load(config_path.read_text())
    assert raw["device"] == "auto"
    assert raw["defaults"] == {"conflict": "newest-wins"}
    assert raw["pairs"] == [{"name": "p1", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"}]
    assert raw["service"]["port"] == 9999
    assert raw["service"]["notify"] is False
    token = raw["service"]["token"]
    assert isinstance(token, str) and len(token) >= 20


def test_install_keeps_existing_token(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"service": {"token": "already-set-token"}})
    monkeypatch.setattr(service.subprocess, "run", make_fake_run([]))
    monkeypatch.setattr(service.shutil, "which", no_tailscale_which)

    service.cmd_service_install(make_install_args(config_path))

    raw = yaml.safe_load(config_path.read_text())
    assert raw["service"]["token"] == "already-set-token"


def test_install_regenerates_the_example_config_placeholder_token(tmp_path, monkeypatch):
    r"""Regression: copying config.example.yaml verbatim leaves
    ``token: <generated on first \`service install\`>`` - a value published in
    the repo. Treating that non-empty placeholder as "already set" would
    deploy a publicly-known bearer token.
    """
    config_path = write_config(
        tmp_path,
        {"service": {"token": "<generated on first `service install`>"}},
    )
    monkeypatch.setattr(service.subprocess, "run", make_fake_run([]))
    monkeypatch.setattr(service.shutil, "which", no_tailscale_which)

    service.cmd_service_install(make_install_args(config_path))

    raw = yaml.safe_load(config_path.read_text())
    token = raw["service"]["token"]
    assert token != "<generated on first `service install`>"
    assert isinstance(token, str) and len(token) >= 20


def test_install_creates_minimal_config_when_missing(tmp_path, monkeypatch):
    config_path = tmp_path / "does-not-exist" / "config.yaml"
    monkeypatch.setattr(service.subprocess, "run", make_fake_run([]))
    monkeypatch.setattr(service.shutil, "which", no_tailscale_which)

    rc = service.cmd_service_install(make_install_args(config_path))
    assert rc == 0
    assert config_path.exists()
    raw = yaml.safe_load(config_path.read_text())
    assert "token" in raw["service"]


def test_install_dry_run_writes_nothing(tmp_path, monkeypatch, _xdg_config_home):
    config_path = tmp_path / "config.yaml"  # does not exist yet
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))
    monkeypatch.setattr(service.shutil, "which", no_tailscale_which)

    rc = service.cmd_service_install(make_install_args(config_path, dry_run=True))
    assert rc == 0
    assert not config_path.exists()
    assert not unit_path(_xdg_config_home).exists()
    assert calls == []  # no systemctl/loginctl/ufw actually run


# ---------------------------------------------------------------------------
# install: Tailscale IP handling
# ---------------------------------------------------------------------------


def test_install_adds_tailscale_ip_when_flag_given(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"service": {}})
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls, tailscale_ip="100.64.0.10"))
    monkeypatch.setattr(service.shutil, "which", tailscale_present_which)

    service.cmd_service_install(make_install_args(config_path, tailscale=True))

    raw = yaml.safe_load(config_path.read_text())
    assert set(raw["service"]["listen"]) == {"127.0.0.1", "100.64.0.10"}
    assert ["tailscale", "ip", "-4"] in calls


class _NonTtyStdin:
    def isatty(self) -> bool:
        return False


def test_install_does_not_add_tailscale_ip_by_default_when_not_a_tty(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"service": {}})
    monkeypatch.setattr(service.subprocess, "run", make_fake_run([], tailscale_ip="100.64.0.10"))
    monkeypatch.setattr(service.shutil, "which", tailscale_present_which)
    monkeypatch.setattr(service.sys, "stdin", _NonTtyStdin())

    service.cmd_service_install(make_install_args(config_path, tailscale=None))

    raw = yaml.safe_load(config_path.read_text())
    assert raw["service"]["listen"] == ["127.0.0.1"]


def test_install_no_tailscale_flag_skips_ip(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"service": {}})
    monkeypatch.setattr(service.subprocess, "run", make_fake_run([], tailscale_ip="100.64.0.10"))
    monkeypatch.setattr(service.shutil, "which", tailscale_present_which)

    service.cmd_service_install(make_install_args(config_path, tailscale=False))

    raw = yaml.safe_load(config_path.read_text())
    assert raw["service"]["listen"] == ["127.0.0.1"]


def test_install_refuses_0_0_0_0_via_listen(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"service": {}})
    monkeypatch.setattr(service.subprocess, "run", make_fake_run([]))
    monkeypatch.setattr(service.shutil, "which", no_tailscale_which)

    with pytest.raises(service.ServiceError):
        service.cmd_service_install(make_install_args(config_path, listen=["0.0.0.0"]))

    # Nothing should have been written - the refusal happens before any I/O.
    raw = yaml.safe_load(config_path.read_text())
    assert "token" not in raw["service"]


# ---------------------------------------------------------------------------
# install: systemctl/loginctl order + ufw gating
# ---------------------------------------------------------------------------


def test_install_without_autostart_only_reloads_daemon(tmp_path, monkeypatch):
    """Task 31a: install writes the unit but starts/enables nothing by default."""
    config_path = write_config(tmp_path, {"service": {}})
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))
    monkeypatch.setattr(service.shutil, "which", no_tailscale_which)
    monkeypatch.setattr(service, "_androsync_executable", lambda: "/usr/local/bin/androsync")

    service.cmd_service_install(make_install_args(config_path))

    assert calls == [["systemctl", "--user", "daemon-reload"]]


def test_install_with_autostart_runs_systemctl_and_loginctl_in_order(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"service": {}})
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))
    monkeypatch.setattr(service.shutil, "which", no_tailscale_which)
    monkeypatch.setattr(service, "_androsync_executable", lambda: "/usr/local/bin/androsync")

    service.cmd_service_install(make_install_args(config_path, autostart=True))

    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", UNIT_NAME],
        ["loginctl", "enable-linger", getpass.getuser()],
    ]


def test_install_writes_unit_file_with_resolved_exec_start(tmp_path, monkeypatch, _xdg_config_home):
    config_path = write_config(tmp_path, {"service": {}})
    monkeypatch.setattr(service.subprocess, "run", make_fake_run([]))
    monkeypatch.setattr(service.shutil, "which", no_tailscale_which)
    monkeypatch.setattr(service, "_androsync_executable", lambda: "/opt/venv/bin/androsync")

    service.cmd_service_install(make_install_args(config_path))

    content = unit_path(_xdg_config_home).read_text()
    assert "ExecStart=/opt/venv/bin/androsync serve" in content


def test_install_ufw_only_runs_with_flag(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"service": {}})
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))
    monkeypatch.setattr(service.shutil, "which", tailscale_present_which)

    service.cmd_service_install(make_install_args(config_path, tailscale=True, ufw=False))
    assert not any(c[:2] == ["sudo", "ufw"] for c in calls)

    calls.clear()
    service.cmd_service_install(make_install_args(config_path, tailscale=True, ufw=True))
    assert any(c[:3] == ["sudo", "ufw", "allow"] for c in calls)
    ufw_call = next(c for c in calls if c[:2] == ["sudo", "ufw"])
    assert ufw_call == [
        "sudo", "ufw", "allow", "in", "on", "tailscale0",
        "to", "any", "port", "8765", "proto", "tcp", "comment", "androsync",
    ]


def test_install_warns_when_ufw_command_fails(tmp_path, monkeypatch, capsys):
    """Regression: a non-interactive sudo (no TTY for the password prompt)
    used to fail silently - `_run(..., check=False)` swallowed the nonzero
    exit with no message, leaving the user thinking the rule was applied.
    """
    config_path = write_config(tmp_path, {"service": {}})

    def fake_run(argv, *, check=True, capture_output=False, text=True):  # noqa: ANN001
        if argv[:2] == ["tailscale", "ip"]:
            return subprocess.CompletedProcess(argv, 0, stdout="100.64.0.10\n", stderr="")
        if argv[:2] == ["sudo", "ufw"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="sudo: a password is required")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    monkeypatch.setattr(service.shutil, "which", tailscale_present_which)

    service.cmd_service_install(make_install_args(config_path, tailscale=True, ufw=True))

    out = capsys.readouterr().out
    assert "WARNING" in out and "ufw" in out


def test_install_no_firewall_rule_needed_without_tailscale(tmp_path, monkeypatch, capsys):
    config_path = write_config(tmp_path, {"service": {}})
    monkeypatch.setattr(service.subprocess, "run", make_fake_run([]))
    monkeypatch.setattr(service.shutil, "which", no_tailscale_which)

    service.cmd_service_install(make_install_args(config_path))
    out = capsys.readouterr().out
    assert "no firewall rule needed (localhost/USB only)" in out


# ---------------------------------------------------------------------------
# start/stop/restart/status/logs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "func, action",
    [
        (service.cmd_service_start, "start"),
        (service.cmd_service_stop, "stop"),
        (service.cmd_service_restart, "restart"),
    ],
)
def test_systemctl_wrappers_run_expected_command(func, action, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))
    rc = func(argparse.Namespace(dry_run=False))
    assert rc == 0
    assert calls == [["systemctl", "--user", action, UNIT_NAME]]


# ---------------------------------------------------------------------------
# status (task 31a: detect_state-backed, not a raw systemctl proxy)
# ---------------------------------------------------------------------------


def test_status_reports_not_running_with_exit_3(monkeypatch, tmp_path):
    from androsync import service_state

    monkeypatch.setattr(service_state, "_systemd_active", lambda: False)
    monkeypatch.setattr(service_state, "_read_pid_file", lambda: None)
    monkeypatch.setattr(service_state, "is_autostart_enabled", lambda: False)

    rc = service.cmd_service_status(argparse.Namespace(config=tmp_path / "missing.yaml"))
    assert rc == 3


def test_status_reports_systemd_running_with_exit_0(monkeypatch, tmp_path, capsys):
    from androsync import service_state

    monkeypatch.setattr(service_state, "_systemd_active", lambda: True)
    monkeypatch.setattr(service_state, "is_autostart_enabled", lambda: True)
    monkeypatch.setattr(service_state, "_systemd_since", lambda: "Sun 2026-09-13 08:00:11 CEST")

    rc = service.cmd_service_status(argparse.Namespace(config=tmp_path / "missing.yaml"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "background service" in out
    assert "autostart on" in out


# ---------------------------------------------------------------------------
# autostart on|off
# ---------------------------------------------------------------------------


def test_autostart_on_enables_and_lingers(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))

    rc = service.cmd_service_autostart(argparse.Namespace(autostart_state="on", dry_run=False))
    assert rc == 0
    assert calls == [
        ["systemctl", "--user", "enable", UNIT_NAME],
        ["loginctl", "enable-linger", getpass.getuser()],
    ]


def test_autostart_off_disables_and_unlingers(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))

    rc = service.cmd_service_autostart(argparse.Namespace(autostart_state="off", dry_run=False))
    assert rc == 0
    assert calls == [
        ["systemctl", "--user", "disable", UNIT_NAME],
        ["loginctl", "disable-linger", getpass.getuser()],
    ]


def test_autostart_dry_run_does_not_run_anything(monkeypatch, capsys):
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))

    rc = service.cmd_service_autostart(argparse.Namespace(autostart_state="on", dry_run=True))
    assert rc == 0
    assert calls == []
    assert "systemctl --user enable" in capsys.readouterr().out


def test_dry_run_wrapper_prints_without_running(monkeypatch, capsys):
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))
    rc = service.cmd_service_start(argparse.Namespace(dry_run=True))
    assert rc == 0
    assert calls == []
    assert "systemctl --user start" in capsys.readouterr().out


def test_logs_runs_journalctl(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, check=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    rc = service.cmd_service_logs(argparse.Namespace(dry_run=False))
    assert rc == 0
    assert calls == [["journalctl", "--user", "-u", UNIT_NAME, "-f"]]


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def test_uninstall_disables_and_removes_unit_file(monkeypatch, _xdg_config_home):
    up = unit_path(_xdg_config_home)
    up.parent.mkdir(parents=True)
    up.write_text("dummy unit")

    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))

    rc = service.cmd_service_uninstall(argparse.Namespace(dry_run=False, ufw=False, config=None))
    assert rc == 0
    assert not up.exists()
    assert calls == [["systemctl", "--user", "disable", "--now", UNIT_NAME]]


def test_uninstall_with_ufw_removes_the_rule(tmp_path, monkeypatch, _xdg_config_home):
    config_path = write_config(tmp_path, {"service": {"port": 9999}})
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))

    rc = service.cmd_service_uninstall(argparse.Namespace(dry_run=False, ufw=True, config=config_path))
    assert rc == 0
    ufw_call = next(c for c in calls if c[:3] == ["sudo", "ufw", "delete"])
    assert ufw_call == [
        "sudo", "ufw", "delete", "allow", "in", "on", "tailscale0",
        "to", "any", "port", "9999", "proto", "tcp", "comment", "androsync",
    ]


def test_uninstall_dry_run_does_not_remove_unit_file(monkeypatch, _xdg_config_home):
    up = unit_path(_xdg_config_home)
    up.parent.mkdir(parents=True)
    up.write_text("dummy unit")
    monkeypatch.setattr(service.subprocess, "run", make_fake_run([]))

    service.cmd_service_uninstall(argparse.Namespace(dry_run=True, ufw=False, config=None))
    assert up.exists()


# ---------------------------------------------------------------------------
# qr
# ---------------------------------------------------------------------------


def test_qr_prints_token_and_urls_falls_back_to_json_without_qrcode(tmp_path, monkeypatch, capsys):
    config_path = write_config(
        tmp_path, {"service": {"token": "tok123", "listen": ["127.0.0.1", "100.64.0.10"], "port": 8765}}
    )
    monkeypatch.setitem(sys.modules, "qrcode", None)  # `import qrcode` raises ImportError

    rc = service.cmd_qr(argparse.Namespace(config=config_path, verbose=False))
    out = capsys.readouterr().out

    assert rc == 0
    assert "tok123" in out
    assert "http://127.0.0.1:8765" in out
    assert "http://100.64.0.10:8765" in out
    assert '"token": "tok123"' in out
    assert "androsync[qr]" in out


def test_qr_renders_ascii_matrix_when_qrcode_installed(tmp_path, capsys):
    pytest.importorskip("qrcode")
    config_path = write_config(tmp_path, {"service": {"token": "tok123", "port": 8765}})

    rc = service.cmd_qr(argparse.Namespace(config=config_path, verbose=False))
    out = capsys.readouterr().out

    assert rc == 0
    assert "tok123" in out
    assert "#" in out


def test_qr_without_token_raises_config_error(tmp_path):
    config_path = write_config(tmp_path, {"service": {}})
    with pytest.raises(ConfigError):
        service.cmd_qr(argparse.Namespace(config=config_path, verbose=False))


def test_qr_missing_config_file_raises_config_error(tmp_path):
    config_path = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError):
        service.cmd_qr(argparse.Namespace(config=config_path, verbose=False))


# ---------------------------------------------------------------------------
# CLI wiring (androsync serve/service/qr all parse)
# ---------------------------------------------------------------------------


def test_cli_registers_serve_service_and_qr_commands():
    from androsync.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["service", "install", "--dry-run"])
    assert args.func is service.cmd_service_install
    assert args.dry_run is True

    args = parser.parse_args(["qr"])
    assert args.func is service.cmd_qr

    args = parser.parse_args(["serve"])
    from androsync.cli import cmd_serve

    assert args.func is cmd_serve

    args = parser.parse_args(["service", "install-launcher"])
    assert args.func is service.cmd_service_install_launcher

    args = parser.parse_args(["service", "uninstall-launcher"])
    assert args.func is service.cmd_service_uninstall_launcher

    args = parser.parse_args(["gui"])
    from androsync.cli import cmd_gui

    assert args.func is cmd_gui


# ---------------------------------------------------------------------------
# install-launcher / uninstall-launcher (task 31c)
# ---------------------------------------------------------------------------


@pytest.fixture()
def xdg_data_home(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    return xdg


def test_install_launcher_writes_desktop_file_and_icon(tmp_path, monkeypatch, xdg_data_home):
    monkeypatch.setattr(service, "_androsync_executable", lambda: "/usr/local/bin/androsync")
    monkeypatch.setattr(service.shutil, "which", lambda name: None)

    rc = service.cmd_service_install_launcher(argparse.Namespace(dry_run=False))
    assert rc == 0

    desktop_path = xdg_data_home / "applications" / "androsync.desktop"
    content = desktop_path.read_text()
    assert "Name=AndroSync" in content
    assert "Exec=/usr/local/bin/androsync gui" in content
    assert "Icon=androsync" in content
    assert "Terminal=false" in content
    assert "Categories=Utility;" in content

    icon_path = xdg_data_home / "icons" / "hicolor" / "scalable" / "apps" / "androsync.svg"
    assert icon_path.exists()
    assert "<svg" in icon_path.read_text()


def test_install_launcher_runs_update_desktop_database_when_present(tmp_path, monkeypatch, xdg_data_home):
    calls: list[list[str]] = []
    monkeypatch.setattr(service, "_androsync_executable", lambda: "/usr/local/bin/androsync")
    monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/update-desktop-database")
    monkeypatch.setattr(service.subprocess, "run", make_fake_run(calls))

    service.cmd_service_install_launcher(argparse.Namespace(dry_run=False))

    assert any(call[0] == "update-desktop-database" for call in calls)


def test_install_launcher_dry_run_writes_nothing(tmp_path, monkeypatch, xdg_data_home):
    monkeypatch.setattr(service, "_androsync_executable", lambda: "/usr/local/bin/androsync")
    monkeypatch.setattr(service.shutil, "which", lambda name: None)

    service.cmd_service_install_launcher(argparse.Namespace(dry_run=True))

    assert not (xdg_data_home / "applications" / "androsync.desktop").exists()
    assert not (xdg_data_home / "icons").exists()


def test_uninstall_launcher_removes_both_files(tmp_path, monkeypatch, xdg_data_home):
    monkeypatch.setattr(service, "_androsync_executable", lambda: "/usr/local/bin/androsync")
    monkeypatch.setattr(service.shutil, "which", lambda name: None)
    service.cmd_service_install_launcher(argparse.Namespace(dry_run=False))

    desktop_path = xdg_data_home / "applications" / "androsync.desktop"
    icon_path = xdg_data_home / "icons" / "hicolor" / "scalable" / "apps" / "androsync.svg"
    assert desktop_path.exists() and icon_path.exists()

    rc = service.cmd_service_uninstall_launcher(argparse.Namespace(dry_run=False))
    assert rc == 0
    assert not desktop_path.exists()
    assert not icon_path.exists()


def test_uninstall_launcher_is_safe_when_nothing_was_installed(xdg_data_home):
    rc = service.cmd_service_uninstall_launcher(argparse.Namespace(dry_run=False))
    assert rc == 0


def test_svg_asset_is_packaged_and_readable():
    import importlib.resources

    content = importlib.resources.files("androsync").joinpath("assets", "androsync.svg").read_text(
        encoding="utf-8"
    )
    assert "<svg" in content


def test_qr_payload_carries_the_laptop_name_the_urls_and_the_token(monkeypatch):
    import json

    from androsync import service

    monkeypatch.setattr("socket.gethostname", lambda: "work-laptop")
    payload = json.loads(service.qr_payload(["http://127.0.0.1:8765", "http://100.64.0.1:8765"], "tok"))
    assert payload == {"name": "work-laptop", "urls": ["http://127.0.0.1:8765", "http://100.64.0.1:8765"], "token": "tok"}


def test_url_label_names_each_kind_of_address():
    from androsync import service

    assert service.url_label("http://127.0.0.1:8765") == "USB cable"
    assert service.url_label("http://100.64.0.7:8765") == "Tailscale"
    assert service.url_label("http://100.127.255.1:8765") == "Tailscale"
    assert service.url_label("http://box.tail1.ts.net:8765") == "Tailscale"
    assert service.url_label("http://192.168.0.5:8765") == "LAN / Wi-Fi"
    assert service.url_label("http://100.128.0.1:8765") == "LAN / Wi-Fi"  # just outside the Tailscale range
