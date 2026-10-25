"""Tests for androsync.cli (devices/ls covered indirectly; status/run/errors here).

``open_pair_transports`` is monkeypatched to return two ``LocalDirTransport``
instances over temp directories - a plain local directory stands in for the
phone just as well as it does for the laptop (see ``transport/local.py``),
so the whole CLI can be exercised without ``adb`` or a device attached.
"""

from __future__ import annotations

import argparse
import logging

import pytest
import yaml

from androsync import cli
from androsync.model import Side
from androsync.transport.local import LocalDirTransport


@pytest.fixture(autouse=True)
def _xdg_data_home(tmp_path, monkeypatch):
    # Isolate the JSON snapshot/meta store per test, same pattern as test_state.py.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    # Isolate ~/.local/state/androsync/last-run.log too (leftover L1).
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


@pytest.fixture()
def pair_dirs(tmp_path):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    return local_dir, remote_dir


def write_config(tmp_path, local_dir, remote_dir, *, name="pair1", direction="two-way", extra_pair=None):
    pairs = [
        {
            "name": name,
            "local": str(local_dir),
            "remote": str(remote_dir),
            "direction": direction,
        }
    ]
    if extra_pair is not None:
        pairs.append(extra_pair)
    config = {"device": "auto", "pairs": pairs}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


@pytest.fixture(autouse=True)
def _fake_transports(monkeypatch):
    """Replace real ADB device resolution with two local directories."""

    def fake_open_pair_transports(config, *, log=None):
        return LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE)

    monkeypatch.setattr(cli, "open_pair_transports", fake_open_pair_transports)


def test_status_prints_planned_copy_and_returns_0(tmp_path, pair_dirs, capsys):
    local_dir, remote_dir = pair_dirs
    (local_dir / "a.txt").write_text("hello")
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "status"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "a.txt" in out
    assert "nothing to do" not in out
    # status must not touch the filesystem
    assert not (remote_dir / "a.txt").exists()


def test_status_with_no_changes_prints_nothing_to_do(tmp_path, pair_dirs, capsys):
    local_dir, remote_dir = pair_dirs
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "status"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing to do" in out


def test_run_yes_copies_files_and_returns_0(tmp_path, pair_dirs, capsys):
    local_dir, remote_dir = pair_dirs
    (local_dir / "a.txt").write_text("hello")
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "run", "--yes"])

    out = capsys.readouterr().out
    assert rc == 0
    assert (remote_dir / "a.txt").read_text() == "hello"
    assert "copied_to_remote=1" in out


def test_run_writes_last_run_log(tmp_path, pair_dirs, capsys):
    from androsync.runlog import log_path

    local_dir, remote_dir = pair_dirs
    (local_dir / "a.txt").write_text("hello")
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "run", "--yes"])

    assert rc == 0
    log_text = log_path().read_text()
    assert "a.txt" in log_text


def test_run_last_run_log_is_truncated_each_run(tmp_path, pair_dirs, capsys):
    from androsync.runlog import log_path

    local_dir, remote_dir = pair_dirs
    (local_dir / "a.txt").write_text("hello")
    config_path = write_config(tmp_path, local_dir, remote_dir)
    cli.main(["--config", str(config_path), "run", "--yes"])
    first_log = log_path().read_text()
    assert "a.txt" in first_log

    (local_dir / "b.txt").write_text("world")
    cli.main(["--config", str(config_path), "run", "--yes"])
    second_log = log_path().read_text()

    assert "b.txt" in second_log
    assert "a.txt" not in second_log  # the previous run's log is gone, not appended to


def test_status_does_not_write_last_run_log(tmp_path, pair_dirs, capsys):
    from androsync.runlog import log_path

    local_dir, remote_dir = pair_dirs
    (local_dir / "a.txt").write_text("hello")
    config_path = write_config(tmp_path, local_dir, remote_dir)

    cli.main(["--config", str(config_path), "status"])

    assert not log_path().exists()


def test_run_dry_run_does_not_copy(tmp_path, pair_dirs, capsys):
    local_dir, remote_dir = pair_dirs
    (local_dir / "a.txt").write_text("hello")
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "run", "--dry-run"])

    capsys.readouterr()
    assert rc == 0
    assert not (remote_dir / "a.txt").exists()


def test_run_without_yes_prompts_and_declining_does_nothing(tmp_path, pair_dirs, capsys, monkeypatch):
    local_dir, remote_dir = pair_dirs
    (local_dir / "a.txt").write_text("hello")
    config_path = write_config(tmp_path, local_dir, remote_dir)

    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    rc = cli.main(["--config", str(config_path), "run"])

    out = capsys.readouterr().out
    assert rc == 0
    assert not (remote_dir / "a.txt").exists()
    assert "aborted" in out


def test_run_without_yes_accepting_copies(tmp_path, pair_dirs, capsys, monkeypatch):
    local_dir, remote_dir = pair_dirs
    (local_dir / "a.txt").write_text("hello")
    config_path = write_config(tmp_path, local_dir, remote_dir)

    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    rc = cli.main(["--config", str(config_path), "run"])

    capsys.readouterr()
    assert rc == 0
    assert (remote_dir / "a.txt").read_text() == "hello"


def test_run_selects_only_named_pair(tmp_path, pair_dirs, capsys):
    local_dir, remote_dir = pair_dirs
    (local_dir / "a.txt").write_text("hello")

    other_local = tmp_path / "local2"
    other_remote = tmp_path / "remote2"
    other_local.mkdir()
    other_remote.mkdir()
    (other_local / "b.txt").write_text("world")

    config_path = write_config(
        tmp_path,
        local_dir,
        remote_dir,
        extra_pair={
            "name": "pair2",
            "local": str(other_local),
            "remote": str(other_remote),
            "direction": "two-way",
        },
    )

    rc = cli.main(["--config", str(config_path), "run", "pair1", "--yes"])

    capsys.readouterr()
    assert rc == 0
    assert (remote_dir / "a.txt").exists()
    assert not (other_remote / "b.txt").exists()


def test_run_unknown_pair_name_exits_2(tmp_path, pair_dirs, capsys):
    local_dir, remote_dir = pair_dirs
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "run", "does-not-exist", "--yes"])

    err = capsys.readouterr().err
    assert rc == 2
    assert "androsync:" in err


def test_missing_config_exits_2_with_message(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.yaml"

    rc = cli.main(["--config", str(missing), "status"])

    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("androsync:")


def test_no_command_prints_help_and_returns_0(capsys):
    rc = cli.main([])

    out = capsys.readouterr().out
    assert rc == 0
    assert "usage" in out.lower()


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0


# ---------------------------------------------------------------------------
# serve: double-start refusal + logging setup (task 31a)
# ---------------------------------------------------------------------------


def test_serve_refuses_to_double_start_when_port_already_answers(tmp_path, monkeypatch, pair_dirs, capsys):
    local_dir, remote_dir = pair_dirs
    config_path = write_config(tmp_path, local_dir, remote_dir)
    raw = yaml.safe_load(config_path.read_text())
    raw["service"] = {"token": "tok", "port": 8765}
    config_path.write_text(yaml.safe_dump(raw))

    monkeypatch.setattr(
        cli.service_state,
        "ping",
        lambda port, token, timeout=2.0: {"device": "SERIAL1", "transport": "usb"},
    )

    rc = cli.cmd_serve(argparse.Namespace(config=config_path, verbose=False))

    assert rc == 3
    assert "already answers" in capsys.readouterr().err


def test_setup_logging_with_file_writes_a_rotating_daemon_log(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    try:
        cli.setup_logging(verbose=False, file=True)
        logging.getLogger("androsync.test_setup_logging").info("hello from the test")
        for handler in root.handlers:
            handler.flush()

        log_path = cli.service_state.daemon_log_path()
        assert log_path.exists()
        assert "hello from the test" in log_path.read_text()
    finally:
        for handler in list(root.handlers):
            if handler not in handlers_before:
                root.removeHandler(handler)
                handler.close()


# ---------------------------------------------------------------------------
# large changes need explicit confirmation (task 36d, issue #9)
# ---------------------------------------------------------------------------


def _blocked_setup(tmp_path, monkeypatch):
    """A synced pair of 8 files with 6 then deleted on the laptop, and a guard that trips at 2 deletions."""
    import os

    local, remote = tmp_path / "l", tmp_path / "r"
    local.mkdir()
    remote.mkdir()
    for i in range(8):
        (local / f"f{i}.txt").write_text(str(i))
        os.utime(local / f"f{i}.txt", (1_700_000_000, 1_700_000_000))
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"pairs:\n  - name: p\n    local: {local}\n    remote: {remote}\n    direction: two-way\n    safety: {{max_delete_count: 2}}\n"
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setattr(
        cli,
        "open_pair_transports",
        lambda config, log=None: (LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE)),
    )
    assert cli.main(["--config", str(cfg), "run", "--yes"]) == 0  # first sync copies everything
    for i in range(6):
        (local / f"f{i}.txt").unlink()
    return cfg, remote


def test_run_yes_alone_refuses_a_large_deletion_and_changes_nothing(tmp_path, monkeypatch, capsys):
    cfg, remote = _blocked_setup(tmp_path, monkeypatch)
    code = cli.main(["--config", str(cfg), "run", "--yes"])
    out = capsys.readouterr()
    assert code == 1
    assert "needs your explicit confirmation" in out.out and "delete 6 files" in out.out
    assert "--confirm-large" in out.err
    assert len(list(remote.iterdir())) == 8


def test_run_confirm_large_goes_ahead(tmp_path, monkeypatch, capsys):
    cfg, remote = _blocked_setup(tmp_path, monkeypatch)
    assert cli.main(["--config", str(cfg), "run", "--yes", "--confirm-large"]) == 0
    assert len(list(remote.iterdir())) == 2


def test_run_without_yes_asks_to_type_yes_for_a_large_change(tmp_path, monkeypatch, capsys):
    cfg, remote = _blocked_setup(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")  # a plain y is not enough
    assert cli.main(["--config", str(cfg), "run"]) == 0
    assert "aborted" in capsys.readouterr().out
    assert len(list(remote.iterdir())) == 8

    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    assert cli.main(["--config", str(cfg), "run"]) == 0
    assert len(list(remote.iterdir())) == 2


def test_status_shows_the_warning_for_a_large_change(tmp_path, monkeypatch, capsys):
    cfg, _remote = _blocked_setup(tmp_path, monkeypatch)
    assert cli.main(["--config", str(cfg), "status"]) in (0, 1)
    assert "needs your confirmation: It would delete 6 files" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# `androsync devices` tells the user what to do about a phone that is attached but unusable (task 37f, issue #10)
# ---------------------------------------------------------------------------


def _devices_cli(monkeypatch, tmp_path, devices):
    from androsync.transport.adb import AdbError, DeviceInfo

    cfg = tmp_path / "config.yaml"
    cfg.write_text("pairs: []\n")
    monkeypatch.setattr(cli, "list_devices", lambda: [DeviceInfo(serial=s, state=st, model=None, transport="usb") for s, st in devices])

    def no_pick(*args, **kwargs):
        raise AdbError("no device is ready")

    monkeypatch.setattr(cli, "resolve_device", no_pick)
    return cli.main(["--config", str(cfg), "devices"])


def test_devices_says_what_to_do_for_an_unauthorized_phone(tmp_path, monkeypatch, capsys):
    assert _devices_cli(monkeypatch, tmp_path, [("ABC123", "unauthorized")]) == 0
    out = capsys.readouterr().out
    assert "ABC123 is unauthorized:" in out
    assert "tick 'Always allow from this computer'" in out


def test_devices_says_what_to_do_for_an_offline_phone(tmp_path, monkeypatch, capsys):
    assert _devices_cli(monkeypatch, tmp_path, [("ABC123", "offline")]) == 0
    assert "ABC123 is offline: Unplug and replug the cable" in capsys.readouterr().out


def test_devices_points_to_the_app_checklist_when_no_phone_is_found(tmp_path, monkeypatch, capsys):
    assert _devices_cli(monkeypatch, tmp_path, []) == 0
    out = capsys.readouterr().out
    assert "No devices found" in out and "Connect your phone" in out


def test_devices_prints_no_hint_for_a_ready_phone(tmp_path, monkeypatch, capsys):
    _devices_cli(monkeypatch, tmp_path, [("ABC123", "device")])
    assert " is device:" not in capsys.readouterr().out
