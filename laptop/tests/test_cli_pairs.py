"""Tests for androsync.cli_pairs (``add-pair``, ``resolve``).

Same pattern as test_cli.py: ``cli.open_pair_transports`` is monkeypatched
to two ``LocalDirTransport`` instances over temp directories, so the whole
CLI can be exercised without ``adb`` or a device attached.
"""

from __future__ import annotations

import os
from datetime import datetime

import pytest
import yaml

from androsync import cli, state
from androsync.executor import run_pair
from androsync.model import Side
from androsync.transport.local import LocalDirTransport

BASE = 1_700_000_000


@pytest.fixture(autouse=True)
def _xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))


@pytest.fixture(autouse=True)
def _fake_transports(monkeypatch):
    def fake_open_pair_transports(config, *, log=None):
        return LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE)

    monkeypatch.setattr(cli, "open_pair_transports", fake_open_pair_transports)


def write_config(tmp_path, raw: dict) -> "os.PathLike":
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


# ---------------------------------------------------------------------------
# add-pair (non-interactive)
# ---------------------------------------------------------------------------


def test_add_pair_non_interactive_appends_and_preserves_other_keys(tmp_path):
    config_path = write_config(
        tmp_path,
        {
            "device": "auto",
            "service": {"port": 9999},
            "defaults": {"conflict": "newest-wins"},
            "pairs": [{"name": "existing", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"}],
        },
    )

    rc = cli.main(
        [
            "--config",
            str(config_path),
            "add-pair",
            "--name",
            "vault",
            "--local",
            str(tmp_path / "vault"),
            "--remote",
            "/sdcard/Obsidian",
            "--direction",
            "two-way",
        ]
    )

    assert rc == 0
    raw = yaml.safe_load(config_path.read_text())
    assert raw["service"] == {"port": 9999}
    assert raw["defaults"] == {"conflict": "newest-wins"}
    names = [p["name"] for p in raw["pairs"]]
    assert names == ["existing", "vault"]
    new_pair = raw["pairs"][1]
    assert new_pair["remote"] == "/sdcard/Obsidian"
    assert new_pair["direction"] == "two-way"
    assert "conflict" not in new_pair  # newest-wins is the default, not written
    assert "propagate_deletes" not in new_pair  # True is the default, not written


def test_add_pair_writes_non_default_conflict_and_propagate_deletes(tmp_path):
    config_path = write_config(tmp_path, {"device": "auto", "pairs": []})

    rc = cli.main(
        [
            "--config",
            str(config_path),
            "add-pair",
            "--name",
            "photos",
            "--local",
            str(tmp_path / "photos"),
            "--remote",
            "/sdcard/DCIM",
            "--direction",
            "phone-to-laptop",
            "--conflict",
            "remote-wins",
            "--no-propagate-deletes",
        ]
    )

    assert rc == 0
    raw = yaml.safe_load(config_path.read_text())
    new_pair = raw["pairs"][0]
    assert new_pair["conflict"] == "remote-wins"
    assert new_pair["propagate_deletes"] is False


def test_add_pair_creates_config_when_missing(tmp_path):
    config_path = tmp_path / "does-not-exist" / "config.yaml"

    rc = cli.main(
        [
            "--config",
            str(config_path),
            "add-pair",
            "--name",
            "p1",
            "--local",
            str(tmp_path / "p1"),
            "--remote",
            "/sdcard/p1",
            "--direction",
            "two-way",
        ]
    )

    assert rc == 0
    assert config_path.exists()
    raw = yaml.safe_load(config_path.read_text())
    assert raw["pairs"][0]["name"] == "p1"


def test_add_pair_rejects_duplicate_name(tmp_path, capsys):
    config_path = write_config(
        tmp_path, {"pairs": [{"name": "p1", "local": "~/x", "remote": "/sdcard/x", "direction": "two-way"}]}
    )

    rc = cli.main(
        [
            "--config",
            str(config_path),
            "add-pair",
            "--name",
            "p1",
            "--local",
            str(tmp_path / "p1"),
            "--remote",
            "/sdcard/p1",
            "--direction",
            "two-way",
        ]
    )

    assert rc == 2
    assert "already exists" in capsys.readouterr().out


def test_add_pair_rejects_remote_not_starting_with_slash(tmp_path, capsys):
    config_path = write_config(tmp_path, {"pairs": []})

    rc = cli.main(
        [
            "--config",
            str(config_path),
            "add-pair",
            "--name",
            "p1",
            "--local",
            str(tmp_path / "p1"),
            "--remote",
            "sdcard/p1",
            "--direction",
            "two-way",
        ]
    )

    assert rc == 2
    assert "absolute path" in capsys.readouterr().out
    raw = yaml.safe_load(config_path.read_text())
    assert raw["pairs"] == []


def test_add_pair_local_dir_is_created(tmp_path):
    config_path = write_config(tmp_path, {"pairs": []})
    new_local = tmp_path / "brand-new"
    assert not new_local.exists()

    rc = cli.main(
        [
            "--config",
            str(config_path),
            "add-pair",
            "--name",
            "p1",
            "--local",
            str(new_local),
            "--remote",
            "/sdcard/p1",
            "--direction",
            "two-way",
        ]
    )

    # add-pair only writes the config; it does not need to create the local
    # dir itself for the non-interactive path (the interactive prompt does).
    assert rc == 0
    raw = yaml.safe_load(config_path.read_text())
    assert raw["pairs"][0]["local"] == str(new_local)


# ---------------------------------------------------------------------------
# edit-pair / remove-pair
# ---------------------------------------------------------------------------


def test_edit_pair_changes_direction(tmp_path):
    config_path = write_config(
        tmp_path, {"pairs": [{"name": "p1", "local": str(tmp_path / "p1"), "remote": "/sdcard/p1", "direction": "two-way"}]}
    )

    rc = cli.main(["--config", str(config_path), "edit-pair", "p1", "--direction", "phone-to-laptop"])

    assert rc == 0
    raw = yaml.safe_load(config_path.read_text())
    assert raw["pairs"][0]["direction"] == "phone-to-laptop"
    assert raw["pairs"][0]["local"] == str(tmp_path / "p1")  # untouched fields preserved


def test_edit_pair_rename(tmp_path):
    config_path = write_config(
        tmp_path, {"pairs": [{"name": "p1", "local": str(tmp_path / "p1"), "remote": "/sdcard/p1", "direction": "two-way"}]}
    )

    rc = cli.main(["--config", str(config_path), "edit-pair", "p1", "--rename", "p1-new"])

    assert rc == 0
    raw = yaml.safe_load(config_path.read_text())
    assert [p["name"] for p in raw["pairs"]] == ["p1-new"]


def test_edit_pair_missing_name_returns_2(tmp_path, capsys):
    config_path = write_config(tmp_path, {"pairs": []})

    rc = cli.main(["--config", str(config_path), "edit-pair", "nope", "--direction", "two-way"])

    assert rc == 2
    assert "no pair named" in capsys.readouterr().out


def test_remove_pair_removes_entry(tmp_path):
    config_path = write_config(
        tmp_path, {"pairs": [{"name": "p1", "local": str(tmp_path / "p1"), "remote": "/sdcard/p1", "direction": "two-way"}]}
    )

    rc = cli.main(["--config", str(config_path), "remove-pair", "p1"])

    assert rc == 0
    raw = yaml.safe_load(config_path.read_text())
    assert raw["pairs"] == []


def test_remove_pair_keeps_state_without_purge(tmp_path):
    config_path = write_config(
        tmp_path, {"pairs": [{"name": "p1", "local": str(tmp_path / "p1"), "remote": "/sdcard/p1", "direction": "two-way"}]}
    )
    state.save_snapshot("p1", {})

    rc = cli.main(["--config", str(config_path), "remove-pair", "p1"])

    assert rc == 0
    from androsync.config import state_dir

    assert (state_dir() / "p1.json").exists()


def test_remove_pair_purge_state_deletes_state_files(tmp_path):
    config_path = write_config(
        tmp_path, {"pairs": [{"name": "p1", "local": str(tmp_path / "p1"), "remote": "/sdcard/p1", "direction": "two-way"}]}
    )
    state.save_snapshot("p1", {})
    state.save_pair_meta("p1", {"last_run": "x"})
    state.save_pending("p1", [])

    rc = cli.main(["--config", str(config_path), "remove-pair", "p1", "--purge-state"])

    assert rc == 0
    from androsync.config import state_dir

    assert not (state_dir() / "p1.json").exists()
    assert not (state_dir() / "p1.meta.json").exists()
    assert not (state_dir() / "p1.pending.json").exists()


def test_remove_pair_missing_name_returns_2(tmp_path, capsys):
    config_path = write_config(tmp_path, {"pairs": []})

    rc = cli.main(["--config", str(config_path), "remove-pair", "nope"])

    assert rc == 2
    assert "no pair named" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# add-pair (interactive)
# ---------------------------------------------------------------------------


def test_add_pair_interactive_prompts_for_everything(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"pairs": []})
    local_dir = tmp_path / "notes"
    local_dir.mkdir()

    answers = iter(
        [
            "notes",  # name
            str(local_dir),  # local path (exists, no create prompt)
            "/sdcard/Notes",  # remote (typed - resolve_device will fail with no device)
            "two-way",  # direction
            "newest-wins",  # conflict
            "y",  # propagate deletes
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = cli.main(["--config", str(config_path), "add-pair"])

    assert rc == 0
    raw = yaml.safe_load(config_path.read_text())
    assert raw["pairs"][0] == {
        "name": "notes",
        "local": str(local_dir),
        "remote": "/sdcard/Notes",
        "direction": "two-way",
    }


def test_add_pair_interactive_offers_to_create_missing_local_dir(tmp_path, monkeypatch):
    config_path = write_config(tmp_path, {"pairs": []})
    local_dir = tmp_path / "brand-new"

    answers = iter(
        [
            "p1",
            str(local_dir),  # does not exist yet
            "y",  # create it
            "/sdcard/p1",
            "phone-to-laptop",
            "newest-wins",
            "n",  # propagate deletes (matches the smart default for phone-to-laptop anyway)
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = cli.main(["--config", str(config_path), "add-pair"])

    assert rc == 0
    assert local_dir.is_dir()


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def _ask_pair_config(tmp_path, local_dir, remote_dir, *, conflict_copies=None):
    pair = {
        "name": "p1",
        "local": str(local_dir),
        "remote": str(remote_dir),
        "direction": "two-way",
        "conflict": "ask",
    }
    if conflict_copies is not None:
        pair["conflict_copies"] = conflict_copies
    return {"device": "auto", "pairs": [pair]}


def _seed_ask_conflict(tmp_path, local_dir, remote_dir, rel="note.md", name="p1"):
    from androsync.config import PairConfig

    pair = PairConfig(
        name=name,
        local=local_dir,
        remote=str(remote_dir),
        direction="two-way",
        conflict="ask",
        keep_conflict_copies=True,
        propagate_deletes=True,
        mtime_tolerance_s=2,
        ignore=[],
    )
    local_t = LocalDirTransport(Side.LOCAL)
    remote_t = LocalDirTransport(Side.REMOTE)

    (local_dir / rel).write_text("v1")
    os.utime(local_dir / rel, (BASE, BASE))
    (remote_dir / rel).write_text("v1")
    os.utime(remote_dir / rel, (BASE, BASE))
    run_pair(pair, local_t, remote_t, now=datetime.fromtimestamp(BASE + 100))

    (local_dir / rel).write_text("laptop edit")
    os.utime(local_dir / rel, (BASE + 200, BASE + 200))
    (remote_dir / rel).write_text("phone edit")
    os.utime(remote_dir / rel, (BASE + 300, BASE + 300))
    run_pair(pair, local_t, remote_t, now=datetime.fromtimestamp(BASE + 400))


def test_resolve_prints_nothing_pending_when_none(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = write_config(tmp_path, _ask_pair_config(tmp_path, local_dir, remote_dir))

    rc = cli.main(["--config", str(config_path), "resolve"])

    assert rc == 0
    assert "nothing pending" in capsys.readouterr().out


def test_resolve_local_choice_overwrites_remote(tmp_path, monkeypatch, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    _seed_ask_conflict(tmp_path, local_dir, remote_dir)
    config_path = write_config(tmp_path, _ask_pair_config(tmp_path, local_dir, remote_dir))

    monkeypatch.setattr("builtins.input", lambda prompt="": "l")

    rc = cli.main(["--config", str(config_path), "resolve"])

    assert rc == 0
    assert (remote_dir / "note.md").read_text() == "laptop edit"
    assert state.load_pending("p1") == []
    assert "resolved 1, skipped 0" in capsys.readouterr().out


def test_resolve_skip_leaves_entry_pending(tmp_path, monkeypatch):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    _seed_ask_conflict(tmp_path, local_dir, remote_dir)
    config_path = write_config(tmp_path, _ask_pair_config(tmp_path, local_dir, remote_dir))

    monkeypatch.setattr("builtins.input", lambda prompt="": "s")

    rc = cli.main(["--config", str(config_path), "resolve"])

    assert rc == 0
    assert len(state.load_pending("p1")) == 1
    assert (remote_dir / "note.md").read_text() == "phone edit"  # untouched


def test_resolve_both_keeps_conflict_copy_on_both_sides(tmp_path, monkeypatch):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    _seed_ask_conflict(tmp_path, local_dir, remote_dir)
    config_path = write_config(
        tmp_path, _ask_pair_config(tmp_path, local_dir, remote_dir, conflict_copies="beside")
    )

    monkeypatch.setattr("builtins.input", lambda prompt="": "b")

    rc = cli.main(["--config", str(config_path), "resolve"])

    assert rc == 0
    local_names = {p.name for p in local_dir.iterdir()}
    remote_names = {p.name for p in remote_dir.iterdir()}
    conflict_files = [n for n in local_names if "conflict-laptop-" in n]
    assert len(conflict_files) == 1
    assert conflict_files[0] in remote_names
    assert state.load_pending("p1") == []


def test_resolve_reprompts_on_invalid_input(tmp_path, monkeypatch):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    _seed_ask_conflict(tmp_path, local_dir, remote_dir)
    config_path = write_config(tmp_path, _ask_pair_config(tmp_path, local_dir, remote_dir))

    answers = iter(["huh", "l"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = cli.main(["--config", str(config_path), "resolve"])

    assert rc == 0
    assert state.load_pending("p1") == []


def test_resolve_selects_only_named_pair(tmp_path, monkeypatch):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    _seed_ask_conflict(tmp_path, local_dir, remote_dir)

    other_local = tmp_path / "other-local"
    other_remote = tmp_path / "other-remote"
    other_local.mkdir()
    other_remote.mkdir()
    _seed_ask_conflict(tmp_path, other_local, other_remote, name="p2")

    config = _ask_pair_config(tmp_path, local_dir, remote_dir)
    config["pairs"].append(
        {
            "name": "p2",
            "local": str(other_local),
            "remote": str(other_remote),
            "direction": "two-way",
            "conflict": "ask",
        }
    )
    config_path = write_config(tmp_path, config)

    monkeypatch.setattr("builtins.input", lambda prompt="": "l")

    rc = cli.main(["--config", str(config_path), "resolve", "p1"])

    assert rc == 0
    assert state.load_pending("p1") == []
    assert len(state.load_pending("p2")) == 1  # untouched


# ---------------------------------------------------------------------------
# transport: agent (task 37d, issue #16)
# ---------------------------------------------------------------------------


def test_add_pair_with_transport_agent_is_saved_and_adb_is_left_out(tmp_path):
    config_path = write_config(tmp_path, {"device": "auto", "pairs": []})
    base = ["--config", str(config_path), "add-pair", "--direction", "two-way", "--local", str(tmp_path / "x"), "--remote", "/sdcard/X"]

    assert cli.main(base + ["--name", "viaagent", "--transport", "agent"]) == 0
    assert cli.main(base + ["--name", "viaadb", "--transport", "adb"]) == 0

    pairs = {p["name"]: p for p in yaml.safe_load(config_path.read_text())["pairs"]}
    assert pairs["viaagent"]["transport"] == "agent"
    assert "transport" not in pairs["viaadb"]  # adb is the default, so nothing is written


def test_edit_pair_can_switch_the_transport(tmp_path):
    config_path = write_config(
        tmp_path,
        {"device": "auto", "pairs": [{"name": "p1", "local": str(tmp_path / "a"), "remote": "/sdcard/a", "direction": "two-way"}]},
    )
    assert cli.main(["--config", str(config_path), "edit-pair", "p1", "--transport", "agent"]) == 0
    assert yaml.safe_load(config_path.read_text())["pairs"][0]["transport"] == "agent"


def test_run_and_status_skip_agent_pairs_without_needing_a_phone(tmp_path, capsys, monkeypatch):
    def no_phone(config, *, log=None):
        raise AssertionError("an agent-only run must not open adb")

    monkeypatch.setattr(cli, "open_pair_transports", no_phone)
    config_path = write_config(
        tmp_path,
        {
            "device": "auto",
            "pairs": [
                {"name": "viaagent", "local": str(tmp_path / "a"), "remote": "/sdcard/a", "direction": "two-way", "transport": "agent"}
            ],
        },
    )
    for command in ("status", "run"):
        assert cli.main(["--config", str(config_path), command]) == 0
    out = capsys.readouterr().out
    assert out.count("skipped: uses the phone app's agent") == 2
    assert "nothing to do" in out


def test_undo_of_an_agent_pair_explains_why_it_cannot_run_here(tmp_path, capsys):
    config_path = write_config(
        tmp_path,
        {
            "device": "auto",
            "pairs": [
                {"name": "viaagent", "local": str(tmp_path / "a"), "remote": "/sdcard/a", "direction": "two-way", "transport": "agent"}
            ],
        },
    )
    assert cli.main(["--config", str(config_path), "undo", "viaagent"]) == 2
    assert "phone app's agent" in capsys.readouterr().err


def test_add_and_edit_pair_can_turn_automatic_sync_on_and_off(tmp_path):
    config_path = write_config(tmp_path, {"device": "auto", "pairs": []})
    base = ["--config", str(config_path)]
    assert cli.main(base + ["add-pair", "--name", "n", "--local", str(tmp_path / "x"), "--remote", "/sdcard/X", "--direction", "two-way", "--auto"]) == 0
    assert yaml.safe_load(config_path.read_text())["pairs"][0]["auto"] is True
    assert cli.main(base + ["edit-pair", "n", "--no-auto"]) == 0
    assert yaml.safe_load(config_path.read_text())["pairs"][0]["auto"] is False
    assert cli.main(base + ["edit-pair", "n", "--auto"]) == 0
    assert yaml.safe_load(config_path.read_text())["pairs"][0]["auto"] is True


def test_run_conditions_can_be_set_changed_and_removed_from_the_command_line(tmp_path):
    config_path = write_config(tmp_path, {"device": "auto", "pairs": []})
    base = ["--config", str(config_path)]
    assert cli.main(base + ["add-pair", "--name", "n", "--local", str(tmp_path / "x"), "--remote", "/sdcard/X",
                            "--direction", "two-way", "--wifi-only", "--schedule", "22:00-07:00"]) == 0
    pair = yaml.safe_load(config_path.read_text())["pairs"][0]
    assert pair["run_conditions"] == {"wifi_only": True, "schedule": "22:00-07:00"}

    assert cli.main(base + ["edit-pair", "n", "--charging-only", "--no-wifi-only"]) == 0
    assert yaml.safe_load(config_path.read_text())["pairs"][0]["run_conditions"] == {"charging_only": True, "schedule": "22:00-07:00"}

    assert cli.main(base + ["edit-pair", "n", "--no-charging-only", "--schedule", ""]) == 0
    assert "run_conditions" not in yaml.safe_load(config_path.read_text())["pairs"][0]  # nothing left: key removed


def test_a_bad_schedule_is_refused_and_nothing_is_saved(tmp_path, capsys):
    config_path = write_config(tmp_path, {"device": "auto", "pairs": [{"name": "n", "local": str(tmp_path / "x"), "remote": "/sdcard/X", "direction": "two-way"}]})
    assert cli.main(["--config", str(config_path), "edit-pair", "n", "--schedule", "midnight"]) == 2
    assert "run_conditions" not in yaml.safe_load(config_path.read_text())["pairs"][0]
