"""Tests for androsync.config_edit (raw-YAML load/save + pair add/update/remove)."""

from __future__ import annotations

import pytest
import yaml

from androsync import config_edit
from androsync.config import ConfigError


def test_load_raw_missing_file_returns_empty_dict(tmp_path):
    assert config_edit.load_raw(tmp_path / "nope.yaml") == {}


def test_load_raw_round_trips_existing_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"device": "auto", "pairs": []}))
    assert config_edit.load_raw(path) == {"device": "auto", "pairs": []}


def test_save_raw_writes_valid_config(tmp_path):
    path = tmp_path / "config.yaml"
    raw = {
        "device": "auto",
        "pairs": [{"name": "p1", "local": str(tmp_path / "p1"), "remote": "/sdcard/p1", "direction": "two-way"}],
    }
    config_edit.save_raw(path, raw)
    assert yaml.safe_load(path.read_text()) == raw


def test_save_raw_rejects_invalid_config_and_leaves_file_untouched(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"device": "auto", "pairs": []}))

    bad_raw = {"device": "auto", "pairs": [{"name": "p1", "local": "~/x", "direction": "two-way"}]}  # no remote
    with pytest.raises(ConfigError):
        config_edit.save_raw(path, bad_raw)

    assert yaml.safe_load(path.read_text()) == {"device": "auto", "pairs": []}
    assert not path.with_suffix(".yaml.tmp").exists()


def test_save_raw_preserves_other_keys(tmp_path):
    path = tmp_path / "config.yaml"
    raw = {
        "device": "auto",
        "service": {"port": 9999},
        "defaults": {"conflict": "newest-wins"},
        "pairs": [],
    }
    config_edit.save_raw(path, raw)
    assert yaml.safe_load(path.read_text())["service"] == {"port": 9999}


def test_add_pair_appends_and_preserves_other_keys():
    raw = {"device": "auto", "pairs": [{"name": "p1", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"}]}
    new_pair = {"name": "p2", "local": "~/y", "remote": "/sdcard/y", "direction": "two-way"}

    result = config_edit.add_pair(raw, new_pair)

    assert [p["name"] for p in result["pairs"]] == ["p1", "p2"]
    assert raw["pairs"] == [{"name": "p1", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"}]  # untouched


def test_add_pair_rejects_duplicate_name():
    raw = {"pairs": [{"name": "p1", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"}]}
    with pytest.raises(ConfigError, match="already exists"):
        config_edit.add_pair(raw, {"name": "p1", "local": "~/y", "remote": "/sdcard/y", "direction": "two-way"})


def test_update_pair_replaces_fields():
    raw = {"pairs": [{"name": "p1", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"}]}
    updated = {"name": "p1", "local": "~/x", "remote": "/sdcard/x", "direction": "phone-to-laptop"}

    result = config_edit.update_pair(raw, "p1", updated)

    assert result["pairs"][0]["direction"] == "phone-to-laptop"


def test_update_pair_allows_rename():
    raw = {"pairs": [{"name": "p1", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"}]}
    renamed = {"name": "p1-renamed", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"}

    result = config_edit.update_pair(raw, "p1", renamed)

    assert [p["name"] for p in result["pairs"]] == ["p1-renamed"]


def test_update_pair_rename_collision_raises():
    raw = {
        "pairs": [
            {"name": "p1", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"},
            {"name": "p2", "local": "~/y", "remote": "/sdcard/y", "direction": "two-way"},
        ]
    }
    with pytest.raises(ConfigError, match="already exists"):
        config_edit.update_pair(raw, "p1", {"name": "p2", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"})


def test_update_pair_missing_name_raises():
    raw = {"pairs": []}
    with pytest.raises(ConfigError, match="no pair named"):
        config_edit.update_pair(raw, "nope", {"name": "nope", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"})


def test_remove_pair_removes_and_preserves_others():
    raw = {
        "pairs": [
            {"name": "p1", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"},
            {"name": "p2", "local": "~/y", "remote": "/sdcard/y", "direction": "two-way"},
        ]
    }
    result = config_edit.remove_pair(raw, "p1")
    assert [p["name"] for p in result["pairs"]] == ["p2"]


def test_remove_pair_missing_name_raises():
    raw = {"pairs": []}
    with pytest.raises(ConfigError, match="no pair named"):
        config_edit.remove_pair(raw, "nope")


# ---------------------------------------------------------------------------
# phone folder rules (task 36c, issue #8)
# ---------------------------------------------------------------------------


def _pair(remote, **extra):
    return {"name": "inbox", "local": "/tmp/inbox", "remote": remote, "direction": "two-way", **extra}


def test_add_pair_maps_the_storage_label_to_sdcard():
    raw = config_edit.add_pair({}, _pair("/Internal storage/Inbox/"))
    assert raw["pairs"][0]["remote"] == "/sdcard/Inbox"


def test_add_pair_refuses_the_filesystem_root_with_a_hint():
    with pytest.raises(ConfigError, match="pair inbox: .*Pick a folder under /sdcard"):
        config_edit.add_pair({}, _pair("/"))


def test_update_pair_checks_a_changed_phone_folder():
    raw = {"pairs": [_pair("/sdcard/Inbox")]}
    with pytest.raises(ConfigError, match="Did you mean /sdcard/Documents/Notes"):
        config_edit.update_pair(raw, "inbox", _pair("/Documents/Notes"))
    fixed = config_edit.update_pair(raw, "inbox", _pair("/Internal storage/Notes"))
    assert fixed["pairs"][0]["remote"] == "/sdcard/Notes"


def test_update_pair_leaves_an_unchanged_old_folder_alone_so_the_pair_can_still_be_paused():
    raw = {"pairs": [_pair("/old/fake/folder")]}
    paused = config_edit.update_pair(raw, "inbox", _pair("/old/fake/folder", enabled=False))
    assert paused["pairs"][0]["enabled"] is False
    assert paused["pairs"][0]["remote"] == "/old/fake/folder"


# ---------------------------------------------------------------------------
# changing a pair's folders resets its history; renaming keeps it (task 36d, issue #9)
# ---------------------------------------------------------------------------


def test_folders_changed_ignores_spellings_of_the_same_folder():
    old = {"name": "p", "local": "/home/me/vault", "remote": "/sdcard/Notes"}
    assert not config_edit.folders_changed(old, {"local": "/home/me/vault/", "remote": "/Internal storage/Notes/"})
    assert not config_edit.folders_changed(old, {"name": "renamed"})  # fields missing = unchanged
    assert config_edit.folders_changed(old, {"local": "/home/me/other"})
    assert config_edit.folders_changed(old, {"remote": "/sdcard/Other"})


def _write_config(tmp_path, local, remote):
    path = tmp_path / "config.yaml"
    path.write_text(f"pairs:\n  - {{name: p, local: {local}, remote: {remote}, direction: two-way}}\n")
    return path


def _remembers_files():
    from androsync import state
    from androsync.model import SnapshotEntry

    state.save_snapshot("p", {"a.md": SnapshotEntry(size=1, mtime_local=1.0, mtime_remote=1.0)})


def test_editing_the_phone_folder_resets_the_history_so_the_next_run_cannot_delete(tmp_path, monkeypatch):
    from androsync import state

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    path = _write_config(tmp_path, "/tmp/inbox", "/sdcard/Inbox")
    _remembers_files()

    reset = config_edit.update_pair_and_state(
        path, "p", {"name": "p", "local": "/tmp/inbox", "remote": "/sdcard/Inbox2", "direction": "two-way"}
    )

    assert reset is True
    assert state.load_snapshot("p") == {}


def test_editing_the_laptop_folder_also_resets(tmp_path, monkeypatch):
    from androsync import state

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    path = _write_config(tmp_path, "/tmp/inbox", "/sdcard/Inbox")
    _remembers_files()
    assert config_edit.update_pair_and_state(
        path, "p", {"name": "p", "local": "/tmp/inbox-new", "remote": "/sdcard/Inbox", "direction": "two-way"}
    )
    assert state.load_snapshot("p") == {}


def test_a_rename_alone_keeps_the_history_and_moves_it_to_the_new_name(tmp_path, monkeypatch):
    from androsync import state

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    path = _write_config(tmp_path, "/tmp/inbox", "/sdcard/Inbox")
    _remembers_files()

    reset = config_edit.update_pair_and_state(
        path, "p", {"name": "renamed", "local": "/tmp/inbox", "remote": "/sdcard/Inbox", "direction": "two-way"}
    )

    assert reset is False
    assert set(state.load_snapshot("renamed")) == {"a.md"}


def test_editing_something_else_keeps_the_history(tmp_path, monkeypatch):
    from androsync import state

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    path = _write_config(tmp_path, "/tmp/inbox", "/sdcard/Inbox")
    _remembers_files()
    assert config_edit.update_pair_and_state(path, "p", {"name": "p", "direction": "laptop-to-phone", "local": "/tmp/inbox", "remote": "/sdcard/Inbox"}) is False
    assert set(state.load_snapshot("p")) == {"a.md"}


def test_a_rejected_edit_changes_neither_the_config_nor_the_history(tmp_path, monkeypatch):
    from androsync import state

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    path = _write_config(tmp_path, "/tmp/inbox", "/sdcard/Inbox")
    before = path.read_text()
    _remembers_files()
    with pytest.raises(ConfigError):
        config_edit.update_pair_and_state(path, "p", {"name": "p", "local": "/tmp/x", "remote": "/data/app", "direction": "two-way"})
    assert path.read_text() == before
    assert set(state.load_snapshot("p")) == {"a.md"}


def test_set_default_transport_writes_defaults_and_pairs_inherit_it(tmp_path):
    from androsync import config_edit
    from androsync.config import load_config

    path = tmp_path / "c.yaml"
    path.write_text("device: auto\npairs:\n  - name: a\n    local: /x\n    remote: /sdcard/a\n    direction: two-way\n")
    raw = config_edit.set_default_transport(config_edit.load_raw(path), "agent")
    config_edit.save_raw(path, raw)
    config = load_config(path)
    assert config.defaults["transport"] == "agent"
    assert config.pairs[0].transport == "agent"  # no transport of its own: it inherits the default


def test_set_default_transport_rejects_anything_else(tmp_path):
    import pytest

    from androsync import config_edit
    from androsync.config import ConfigError

    with pytest.raises(ConfigError):
        config_edit.set_default_transport({"pairs": []}, "carrier-pigeon")
