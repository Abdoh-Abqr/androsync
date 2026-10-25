"""Named phones: the config registry and `androsync phone` (task 33b, issue #3 of the public repository)."""

from __future__ import annotations

import pytest
import yaml

from androsync import cli
from androsync.config import ConfigError, load_config


def write(tmp_path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


BASE = "device: auto\npairs: []\n"


def test_phones_are_read_with_name_and_wifi_target(tmp_path):
    path = write(tmp_path, BASE + "phones:\n  HW_A: {name: S24 FE, wifi_target: '100.64.0.9:5555'}\n  HW_B: {name: Realme}\n")
    config = load_config(path)
    assert config.phones["HW_A"].name == "S24 FE" and config.phones["HW_A"].wifi_target == "100.64.0.9:5555"
    assert config.phones["HW_B"].wifi_target is None


def test_no_phones_section_means_an_empty_registry(tmp_path):
    assert load_config(write(tmp_path, BASE)).phones == {}


@pytest.mark.parametrize(
    "extra, message",
    [
        ("phones: [a, b]\n", "must be a mapping"),
        ("phones:\n  A: {colour: red}\n", "only 'name' and 'wifi_target'"),
        ("phones:\n  A: {wifi_target: no-port}\n", "host:port"),
    ],
)
def test_bad_phone_entries_are_refused(tmp_path, extra, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, BASE + extra))


def test_phone_set_list_and_remove(tmp_path, capsys):
    path = write(tmp_path, BASE)
    common = ["--config", str(path)]
    assert cli.main(common + ["phone", "set", "HW_A", "--name", "S24 FE", "--wifi", "100.64.0.9:5555"]) == 0
    assert cli.main(common + ["phone", "set", "HW_A", "--name", "Galaxy"]) == 0  # the Wi-Fi target stays
    raw = yaml.safe_load(path.read_text())
    assert raw["phones"]["HW_A"] == {"name": "Galaxy", "wifi_target": "100.64.0.9:5555"}

    capsys.readouterr()
    assert cli.main(common + ["phone", "list"]) == 0
    out = capsys.readouterr().out
    assert "HW_A" in out and "Galaxy" in out and "100.64.0.9:5555" in out

    assert cli.main(common + ["phone", "remove", "HW_A"]) == 0
    assert "phones" not in yaml.safe_load(path.read_text())
    assert cli.main(common + ["phone", "remove", "HW_A"]) == 2  # no longer there


def test_phone_set_needs_something_to_set_and_a_valid_wifi_target(tmp_path, capsys):
    path = write(tmp_path, BASE)
    assert cli.main(["--config", str(path), "phone", "set", "HW_A"]) == 2
    assert cli.main(["--config", str(path), "phone", "set", "HW_A", "--wifi", "nonsense"]) == 2
    assert "host:port" in capsys.readouterr().err
    assert "phones" not in yaml.safe_load(path.read_text())  # nothing invalid was written


def test_the_devices_command_shows_each_phone_with_its_id_and_name(tmp_path, capsys, monkeypatch):
    from androsync.transport import adb as adb_module

    class Done:
        def __init__(self, out):
            self.stdout, self.stderr, self.returncode = out, "", 0

    def run(argv, **kw):
        if argv[1:3] == ["devices", "-l"]:
            return Done("List of devices attached\nUSB_A\tdevice usb:1-1 product:x model:S24 device:y\n")
        return Done("HW_A\n")

    monkeypatch.setattr(adb_module, "_run_process", run)
    monkeypatch.setattr(cli, "hardware_serial", lambda serial, *a, **k: "HW_A")
    path = write(tmp_path, BASE + "phones:\n  HW_A: {name: S24 FE}\n")
    assert cli.main(["--config", str(path), "devices"]) == 0
    out = capsys.readouterr().out
    assert "ID" in out and "NAME" in out
    line = next(line for line in out.splitlines() if line.startswith("*") or "USB_A" in line)
    assert "HW_A" in line and "S24 FE" in line
