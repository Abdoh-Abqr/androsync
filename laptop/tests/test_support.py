"""The support bundle never leaks the token, addresses, serials, names or folders (Phase S1)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from androsync import cli, support
from androsync.support import Redactor, classify_ip, make_redactor, parse_adb_serials

TOKEN = "Zk3pQ9vL2mT7xR1sW8yB4nC6dF0gH5jK-aE_uI"
SERIAL = "HWSERIAL12345"


def test_classify_ip_names_each_kind_of_address():
    assert classify_ip("100.64.0.9") == "<tailscale-ip>"
    assert classify_ip("100.127.255.1") == "<tailscale-ip>"
    assert classify_ip("192.168.0.5") == "<lan-ip>"
    assert classify_ip("10.1.2.3") == "<lan-ip>"
    assert classify_ip("172.16.0.1") == "<lan-ip>"
    assert classify_ip("8.8.8.8") == "<ip>"
    assert classify_ip("100.128.0.1") == "<ip>"  # just outside the Tailscale block
    assert classify_ip("127.0.0.1") is None  # loopback is harmless and useful to a developer
    assert classify_ip("not an ip") is None
    assert classify_ip("999.1.1.1") is None


def test_the_redactor_removes_every_kind_of_identifier():
    redact = make_redactor(
        token=TOKEN,
        serials=[SERIAL],
        pair_names=["family photos"],
        folders=["/home/alex/Pictures/Family", "/sdcard/DCIM/Family"],
        hide_names=True,
        home="/home/alex",
        user="alex",
        hostname="alex-laptop",
    )
    text = (
        f"token: {TOKEN}\nAuthorization: Bearer {TOKEN}\nlisten 100.64.0.9 and 192.168.0.5 and 127.0.0.1\n"
        f"phone {SERIAL} pair family photos at /home/alex/Pictures/Family -> /sdcard/DCIM/Family\n"
        "host alex-laptop user alex file /home/alex/.config/androsync/config.yaml public 8.8.8.8\n"
    )
    out = redact(text)
    for secret in (TOKEN, SERIAL, "alex", "family photos", "Pictures/Family", "DCIM/Family", "100.64.0.9", "192.168.0.5", "8.8.8.8"):
        assert secret not in out, secret
    assert "127.0.0.1" in out  # kept on purpose
    assert "<tailscale-ip>" in out and "<lan-ip>" in out and "~/.config/androsync/config.yaml" in out
    assert f"phone-{support.short_hash(SERIAL)}" in out


def test_names_and_folders_stay_when_the_person_chooses_to_show_them():
    redact = make_redactor(
        token=None, serials=[], pair_names=["vault"], folders=["/sdcard/Notes"], hide_names=False,
        home="/home/alex", user="alex", hostname="box",
    )
    assert "vault" in redact("pair vault at /sdcard/Notes")


def test_a_token_that_is_not_known_is_still_caught_by_its_shape():
    out = Redactor()(f'{{"token": "{TOKEN}"}}  Authorization: Bearer {TOKEN}')
    assert TOKEN not in out and "<token>" in out


def test_adb_serials_are_read_from_the_device_list():
    listing = (
        "List of devices attached\n"
        f"{SERIAL}   device usb:1-1 product:x model:S24 device:y transport_id:1\n"
        "BADSER99   unauthorized usb:1-2 transport_id:2\n"
        "\n"
    )
    assert parse_adb_serials(listing) == [SERIAL, "BADSER99"]


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A fake home with a config, a daemon log and a last-run log that all contain things to hide."""
    h = tmp_path / "alex-home"
    (h / ".config" / "androsync").mkdir(parents=True)
    state_dir = h / ".local" / "state" / "androsync"
    state_dir.mkdir(parents=True)
    config = h / ".config" / "androsync" / "config.yaml"
    config.write_text(
        "device: auto\nservice:\n  port: 8765\n"
        f"  token: {TOKEN}\n  listen: ['127.0.0.1', '100.64.0.9']\n"
        "pairs:\n  - name: family photos\n"
        f"    local: {h}/Pictures/Family\n    remote: /sdcard/DCIM/Family\n    direction: two-way\n"
    )
    (state_dir / "daemon.log").write_text(
        f"2026-10-26 INFO listening on 100.64.0.9:8765\n2026-10-26 INFO phone {SERIAL} connected\n"
        f"2026-10-26 WARNING header Authorization: Bearer {TOKEN}\n2026-10-26 INFO sync of family photos in {h}/Pictures/Family\n"
    )
    (state_dir / "last-run.log").write_text("copied 3 files to 192.168.0.5\n")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USER", "alex")
    monkeypatch.setenv("XDG_STATE_HOME", str(h / ".local" / "state"))
    monkeypatch.setattr(support.platform, "node", lambda: "alex-laptop")
    monkeypatch.setattr(
        support, "_run",
        lambda argv, timeout=10: (
            f"List of devices attached\n{SERIAL}\tdevice usb:1-1 model:S24\n" if argv[0] == "adb" else "service ok on 192.168.0.5\n"
        ),
    )
    return h, config


def test_the_bundle_contains_nothing_that_identifies_the_person(home):
    h, config = home
    files = support.collect(config, hide_names=True)
    assert {"report.txt", "config.yaml.txt", "daemon.log.txt", "last-run.log.txt", "adb-devices.txt"} <= set(files)
    everything = "\n".join(files.values())
    for secret in (TOKEN, SERIAL, "alex", "family photos", "Pictures/Family", "DCIM/Family", "100.64.0.9", "192.168.0.5"):
        assert secret not in everything, secret
    assert "listening on <tailscale-ip>:8765" in files["daemon.log.txt"]  # still useful to a developer
    assert "token: <token>" in files["config.yaml.txt"]
    assert "androsync " in files["report.txt"]


def test_a_broken_config_still_gives_a_bundle_that_says_so(home):
    h, config = home
    config.write_text("pairs: [this is: not valid")
    files = support.collect(config)
    assert "config problem" in files["report.txt"]
    assert TOKEN not in "\n".join(files.values())


def test_the_cli_writes_an_owner_only_zip_and_prints_where_to_report(home, capsys, tmp_path):
    h, config = home
    out = tmp_path / "bundle.zip"
    assert cli.main(["--config", str(config), "support-bundle", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert str(out) in printed and "issues/new" in printed and TOKEN not in printed
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    with zipfile.ZipFile(out) as archive:
        text = "\n".join(archive.read(n).decode() for n in archive.namelist())
    assert TOKEN not in text and SERIAL not in text and "family photos" not in text
    assert Path(out).exists()


def test_the_email_line_appears_only_when_an_address_is_set(home, capsys, tmp_path, monkeypatch):
    h, config = home
    cli.main(["--config", str(config), "support-bundle", "--out", str(tmp_path / "a.zip")])
    assert "email it" not in capsys.readouterr().out.lower()
    monkeypatch.setattr(support, "SUPPORT_EMAIL", "help@example.org")
    cli.main(["--config", str(config), "support-bundle", "--out", str(tmp_path / "b.zip")])
    assert "help@example.org" in capsys.readouterr().out
