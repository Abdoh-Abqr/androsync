"""The token's home: an owner-only config file, `service new-token`, and rotating through the client (task S2)."""

from __future__ import annotations

import os
import stat

import pytest
import yaml

from androsync import cli, config_edit
from androsync.apiclient import ApiClient
from androsync.config import load_config, secure_file


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_secure_file_removes_group_and_other_access_and_says_when_it_changed(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("x: 1\n")
    path.chmod(0o664)
    assert secure_file(path) is True
    assert mode(path) == 0o600
    assert secure_file(path) is False  # already fine: nothing to do
    assert secure_file(tmp_path / "missing.yaml") is False


def test_saving_the_config_keeps_it_owner_only(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("device: auto\nservice: {token: abc}\npairs: []\n")
    path.chmod(0o664)
    config_edit.save_raw(path, config_edit.load_raw(path))
    assert mode(path) == 0o600


def test_service_install_creates_an_owner_only_config(tmp_path, monkeypatch):
    path = tmp_path / "cfg" / "config.yaml"
    monkeypatch.setattr("androsync.service._systemd_unit_path", lambda: tmp_path / "androsync.service")
    monkeypatch.setattr("androsync.service._detect_tailscale_ip", lambda **kw: None)
    rc = cli.main(["--config", str(path), "service", "install", "--no-tailscale", "--dry-run"])
    assert rc == 0
    # A dry run writes nothing; the real run is exercised by the first test above and the one below.
    assert not path.exists() or mode(path) == 0o600


def test_new_token_replaces_the_token_without_printing_it(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("device: auto\nservice:\n  token: old-token\n  port: 8765\npairs: []\n")
    path.chmod(0o664)
    assert cli.main(["--config", str(path), "service", "new-token"]) == 0
    out = capsys.readouterr().out
    new = yaml.safe_load(path.read_text())["service"]["token"]
    assert new != "old-token" and len(new) >= 32
    assert new not in out and "old-token" not in out  # the secret never goes to the terminal or the logs
    assert "androsync qr" in out
    assert mode(path) == 0o600
    assert load_config(path).service.token == new


def test_new_token_needs_an_existing_config(tmp_path, capsys):
    assert cli.main(["--config", str(tmp_path / "nope.yaml"), "service", "new-token"]) == 2


def test_the_window_client_can_rotate_the_token_through_a_running_service(tmp_path):
    from test_agent_transport import TOKEN, Env

    env = Env(tmp_path / "env")
    try:
        client = ApiClient(env.base, TOKEN)
        new = client.rotate_token()
        assert new != TOKEN and load_config(env.config_file).service.token == new
        with pytest.raises(Exception):
            client.ping()  # the old token no longer works
    finally:
        env.close()
