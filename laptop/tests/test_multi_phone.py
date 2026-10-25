"""One history per phone, pairs bound to a phone, and picking the right phone (task 33a, issue #2 of the public repo)."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from androsync import state
from androsync.config import ConfigError, PairConfig, load_config
from androsync.daemon.jobs import JobRunner
from androsync.executor import run_pair
from androsync.model import Side
from androsync.transport import adb as adb_module
from androsync.transport.adb import Adb, AdbError, AdbTransport, resolve_pair_device
from androsync.transport.local import LocalDirTransport
from androsync.undo import UndoError, list_runs_all, undo_run

BASE = 1_700_000_000


class Phone(LocalDirTransport):
    """A fake phone: a local folder that also says which phone it is."""

    def __init__(self, key: str | None) -> None:
        super().__init__(Side.REMOTE)
        self.device_key = key


@pytest.fixture(autouse=True)
def _xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))


def pair(local: Path, remote: Path, **kw) -> PairConfig:
    fields = dict(
        name="notes", local=local, remote=str(remote), direction="two-way", conflict="newest-wins",
        keep_conflict_copies=True, propagate_deletes=True, mtime_tolerance_s=2, ignore=[],
    )
    fields.update(kw)
    return PairConfig(**fields)


def write(root: Path, rel: str, text: str, mtime: float = BASE) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))
    return path


def sync(p: PairConfig, phone: Phone, when: float):
    return run_pair(p, LocalDirTransport(Side.LOCAL), phone, now=datetime.fromtimestamp(when))


# --- keys ---------------------------------------------------------------------------------------------------


def test_state_key_is_the_name_alone_without_a_phone_and_file_safe_with_one():
    assert state.state_key("notes", None) == "notes"
    assert state.state_key("notes", "") == "notes"
    assert state.state_key("notes", "ABC123XYZ") == "notes@ABC123XYZ"
    assert state.state_key("notes", "192.168.0.5:5555") == "notes@192.168.0.5_5555"


def test_pair_names_may_not_contain_an_at_sign(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("device: auto\npairs:\n  - name: a@b\n    local: /x\n    remote: /sdcard/a\n    direction: two-way\n")
    with pytest.raises(ConfigError, match="must not contain '@'"):
        load_config(path)


def test_the_pair_device_defaults_to_any_and_is_read_from_the_config(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "device: auto\npairs:\n"
        "  - {name: a, local: /x, remote: /sdcard/a, direction: two-way}\n"
        "  - {name: b, local: /y, remote: /sdcard/b, direction: two-way, device: ABC123}\n"
    )
    config = load_config(path)
    assert config.device == "auto"  # the config-level device is untouched
    assert {p.name: p.device for p in config.pairs} == {"a": "any", "b": "ABC123"}


# --- two phones, one pair name -----------------------------------------------------------------------------


def test_a_second_phone_cannot_make_the_laptop_delete_what_the_first_phone_brought(tmp_path):
    laptop, phone_a, phone_b = tmp_path / "laptop", tmp_path / "a", tmp_path / "b"
    for d in (laptop, phone_a, phone_b):
        d.mkdir()
    p = pair(laptop, phone_a)
    write(phone_a, "from-a.txt", "A's file")
    assert sync(p, Phone("PHONE_A"), BASE + 100).errors == 0
    assert (laptop / "from-a.txt").read_text() == "A's file"

    # The same pair name against phone B, whose folder is empty. With one shared history B would look like
    # "everything was deleted on the phone" and the laptop copy would be removed.
    p_b = pair(laptop, phone_b)
    summary = sync(p_b, Phone("PHONE_B"), BASE + 200)
    assert summary.errors == 0
    assert (laptop / "from-a.txt").read_text() == "A's file"
    assert (phone_b / "from-a.txt").read_text() == "A's file"  # B simply receives it

    names = {p.name for p in state.state_dir().iterdir()}
    assert {"notes@PHONE_A.json", "notes@PHONE_B.json"} <= names


def test_histories_stay_separate_after_more_runs(tmp_path):
    laptop, phone_a, phone_b = tmp_path / "laptop", tmp_path / "a", tmp_path / "b"
    for d in (laptop, phone_a, phone_b):
        d.mkdir()
    write(laptop, "shared.txt", "v1")
    sync(pair(laptop, phone_a), Phone("PHONE_A"), BASE + 100)
    sync(pair(laptop, phone_b), Phone("PHONE_B"), BASE + 200)
    (phone_a / "shared.txt").unlink()  # A deletes it, and that deletion is A's alone
    sync(pair(laptop, phone_a), Phone("PHONE_A"), BASE + 300)
    assert not (laptop / "shared.txt").exists()  # propagated from A to the laptop
    # B still has its own copy and its own history; its next run brings the laptop to B's view or keeps files safe,
    # but it never raises or silently loses B's data.
    assert (phone_b / "shared.txt").exists()


# --- migration of the old, phone-less history -------------------------------------------------------------


def test_the_first_phone_takes_over_the_old_history_and_a_second_phone_does_not(tmp_path):
    state.save_snapshot("notes", {})
    state.save_pair_meta("notes", {"last_run": "2026-01-01T00:00:00", "last_result": "old"})
    (state.state_dir() / "notes.json").write_text("{}")
    assert state.key_for("notes", Phone("PHONE_A")) == "notes@PHONE_A"
    assert (state.state_dir() / "notes@PHONE_A.meta.json").exists()
    assert not (state.state_dir() / "notes.meta.json").exists()
    assert (state.state_dir() / "notes.claimed").read_text() == "notes@PHONE_A"

    state.save_pair_meta("notes", {"last_run": "later", "last_result": "created by a phone-less transport"})
    assert state.key_for("notes", Phone("PHONE_B")) == "notes@PHONE_B"
    assert state.load_pair_meta("notes@PHONE_B") == {}  # B did not take the later phone-less file
    assert (state.state_dir() / "notes.meta.json").exists()


def test_a_transport_without_a_phone_identity_keeps_using_the_plain_name():
    assert state.key_for("notes", LocalDirTransport(Side.REMOTE)) == "notes"
    assert not (state.state_dir() / "notes.claimed").exists()


def test_rename_purge_and_reset_cover_every_phone(tmp_path):
    for key in ("notes", "notes@A", "notes@B"):
        state.save_snapshot(key, {})
        state.save_pair_meta(key, {"last_run": key})
        (state.undo_dir(key) / "run1").mkdir(parents=True)
    (state.state_dir() / "notes.claimed").write_text("notes@A")

    state.rename_pair_state("notes", "memos")
    assert state.keys_for("memos") == ["memos", "memos@A", "memos@B"]
    assert state.keys_for("notes") == []
    assert (state.state_dir() / "memos.claimed").exists()

    state.reset_pair_state("memos")
    assert not (state.state_dir() / "memos@A.json").exists()
    reset = next(state.state_dir().glob("memos.reset-*"))
    assert (reset / "memos@B.json").exists() and (reset / "undo@B").is_dir() and (reset / "undo").is_dir()

    state.purge_pair_state("memos")
    assert state.keys_for("memos") == []
    assert not (state.state_dir() / "memos.claimed").exists()


def test_the_pair_list_shows_the_newest_result_of_any_phone():
    state.save_pair_meta("notes@A", {"last_run": "A", "last_result": "a"})
    time.sleep(0.02)
    state.save_pair_meta("notes@B", {"last_run": "B", "last_result": "b"})
    assert state.latest_meta("notes")["last_run"] == "B"


# --- undo -----------------------------------------------------------------------------------------------------


def test_undo_only_works_with_the_phone_that_made_the_run(tmp_path):
    laptop, phone = tmp_path / "laptop", tmp_path / "phone"
    laptop.mkdir()
    phone.mkdir()
    write(laptop, "a.txt", "a")
    p = pair(laptop, phone)
    summary = sync(p, Phone("PHONE_A"), BASE + 100)
    runs = list_runs_all("notes")
    assert [r["device"] for r in runs] == ["PHONE_A"]
    with pytest.raises(UndoError, match="another phone"):
        undo_run(p, summary.run_id, LocalDirTransport(Side.LOCAL), Phone("PHONE_B"))
    undone = undo_run(p, summary.run_id, LocalDirTransport(Side.LOCAL), Phone("PHONE_A"))
    assert undone.errors == 0 and not (phone / "a.txt").exists()


# --- picking the phone ----------------------------------------------------------------------------------------


class _Done:
    def __init__(self, out: str, code: int = 0):
        self.stdout, self.stderr, self.returncode = out, "", code


def _fake_adb(monkeypatch, devices: dict[str, str]):
    """``devices`` maps connection serial -> hardware serial."""

    listing = "List of devices attached\n" + "".join(f"{s}\tdevice usb:1-1 product:x model:y\n" for s in devices)

    def run(argv, **kwargs):
        if argv[1:3] == ["devices", "-l"]:
            return _Done(listing)
        if "getprop ro.serialno" in argv:
            return _Done(devices[argv[argv.index("-s") + 1]] + "\n")
        raise AssertionError(f"unexpected adb call {argv}")

    monkeypatch.setattr(adb_module, "_run_process", run)


def test_a_pair_bound_to_a_phone_finds_it_by_its_hardware_serial(monkeypatch):
    _fake_adb(monkeypatch, {"192.168.0.5:5555": "HW_A", "USB_B": "HW_B"})
    assert resolve_pair_device("HW_B", "auto") == "USB_B"
    assert resolve_pair_device("HW_A", "auto") == "192.168.0.5:5555"  # the same phone over Wi-Fi
    assert resolve_pair_device("USB_B", "auto") == "USB_B"  # a connection serial works too


def test_a_pair_bound_to_a_missing_phone_says_which_phones_are_ready(monkeypatch):
    _fake_adb(monkeypatch, {"USB_B": "HW_B"})
    with pytest.raises(AdbError, match=r"HW_X.*not connected.*USB_B"):
        resolve_pair_device("HW_X", "auto")


def test_two_phones_attached_and_no_phone_chosen_stops_with_a_clear_message(monkeypatch):
    _fake_adb(monkeypatch, {"USB_A": "HW_A", "USB_B": "HW_B"})
    with pytest.raises(AdbError, match="more than one phone is attached.*Nothing was synced.*'device'"):
        resolve_pair_device("any", "auto")


def test_the_adb_transport_names_the_phone_by_its_hardware_serial(monkeypatch):
    _fake_adb(monkeypatch, {"192.168.0.5:5555": "HW_A"})
    transport = AdbTransport(Adb(serial="192.168.0.5:5555"))
    assert transport.device_key == "HW_A"
    assert transport.device_key == "HW_A"  # asked once, then remembered


def test_a_phone_that_does_not_report_a_serial_falls_back_to_the_connection_serial(monkeypatch):
    _fake_adb(monkeypatch, {"USB_X": "unknown"})
    assert AdbTransport(Adb(serial="USB_X")).device_key == "USB_X"


# --- the job runner opens the right phone per pair ------------------------------------------------------------


def _config(tmp_path: Path, pairs: list[tuple[str, str]]):
    lines = ["device: auto", "service:", "  port: 8765", "pairs:"]
    for name, device in pairs:
        lines += [f"  - name: {name}", f"    local: {tmp_path / 'l' / name}", f"    remote: {tmp_path / 'p' / name}",
                  "    direction: two-way", f"    device: {device}"]
    path = tmp_path / "config.yaml"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_each_bound_pair_gets_the_transport_of_its_own_phone(tmp_path):
    path = _config(tmp_path, [("x", "any"), ("y", "HW_B")])
    asked: list[str] = []

    def any_factory(config):
        asked.append("any")
        return LocalDirTransport(Side.LOCAL), Phone("ANY")

    def device_factory(config, device):
        asked.append(device)
        return LocalDirTransport(Side.LOCAL), Phone(device)

    runner = JobRunner(lambda: load_config(path), any_factory, None, device_factory)
    config = load_config(path)
    opened = runner.open_transports(config, list(config.pairs))
    assert sorted(asked) == ["HW_B", "any"]
    assert opened.remote_for(config.pairs[0]).device_key == "ANY"
    assert opened.remote_for(config.pairs[1]).device_key == "HW_B"


def test_a_missing_phone_fails_only_the_pairs_bound_to_it(tmp_path):
    path = _config(tmp_path, [("x", "any"), ("y", "HW_GONE")])

    def device_factory(config, device):
        raise AdbError(f"the phone {device!r} is not connected")

    runner = JobRunner(
        lambda: load_config(path), lambda c: (LocalDirTransport(Side.LOCAL), Phone("ANY")), None, device_factory
    )
    config = load_config(path)
    opened = runner.open_transports(config, list(config.pairs))
    assert opened.remote_for(config.pairs[0]).device_key == "ANY"
    with pytest.raises(AdbError, match="HW_GONE"):
        opened.remote_for(config.pairs[1])


def test_a_job_with_only_pairs_for_a_missing_phone_fails_as_a_whole(tmp_path):
    path = _config(tmp_path, [("y", "HW_GONE")])

    def device_factory(config, device):
        raise AdbError("not connected")

    runner = JobRunner(lambda: load_config(path), lambda c: None, None, device_factory)
    config = load_config(path)
    with pytest.raises(AdbError):
        runner.open_transports(config, list(config.pairs))
