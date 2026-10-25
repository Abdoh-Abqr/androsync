"""Tests for androsync.cli's ``runs``/``undo`` commands and the `--pause` /
``enabled: false`` skip behaviour of ``status``/``run`` (Phase I, task 30b).

Same pattern as test_cli_pairs.py: ``cli.open_pair_transports`` is
monkeypatched to two ``LocalDirTransport`` instances over temp directories.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from androsync import cli
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


def write_config(tmp_path, local_dir, remote_dir, *, name="p1", extra: dict | None = None) -> Path:
    pair = {
        "name": name,
        "local": str(local_dir),
        "remote": str(remote_dir),
        "direction": "two-way",
    }
    if extra:
        pair.update(extra)
    raw = {"device": "auto", "pairs": [pair]}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


def write(root: Path, rel: str, text: str, mtime: float) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))
    return path


def tree(root: Path) -> dict[str, str]:
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            out[rel] = full.read_text()
    return out


def _run_sync(tmp_path, config_path):
    rc = cli.main(["--config", str(config_path), "run", "--yes"])
    assert rc == 0


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


def test_runs_empty_before_any_sync(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "runs", "p1"])

    assert rc == 0
    assert "no runs recorded yet" in capsys.readouterr().out


def test_runs_lists_a_completed_sync(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    write(local_dir, "a.txt", "hello", BASE)
    config_path = write_config(tmp_path, local_dir, remote_dir)

    _run_sync(tmp_path, config_path)
    capsys.readouterr()

    rc = cli.main(["--config", str(config_path), "runs", "p1"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "sync" in out
    assert "done" in out


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------


def test_undo_most_recent_run_restores_tree(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    write(local_dir, "a.txt", "hello", BASE)
    config_path = write_config(tmp_path, local_dir, remote_dir)
    _run_sync(tmp_path, config_path)
    assert "a.txt" in tree(remote_dir)
    capsys.readouterr()

    rc = cli.main(["--config", str(config_path), "undo", "p1"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Restored 0 file(s) on laptop, 1 on phone." in out
    assert "a.txt" not in tree(remote_dir)
    assert "a.txt" in tree(local_dir)  # the laptop original is untouched


def test_undo_dry_run_changes_nothing(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    write(local_dir, "a.txt", "hello", BASE)
    config_path = write_config(tmp_path, local_dir, remote_dir)
    _run_sync(tmp_path, config_path)
    capsys.readouterr()

    rc = cli.main(["--config", str(config_path), "undo", "p1", "--dry-run"])

    assert rc == 0
    assert "a.txt" in tree(remote_dir)  # untouched


def test_undo_unknown_run_id_exits_2(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    write(local_dir, "a.txt", "hello", BASE)
    config_path = write_config(tmp_path, local_dir, remote_dir)
    _run_sync(tmp_path, config_path)

    rc = cli.main(["--config", str(config_path), "undo", "p1", "--run", "20260101-000000-0000"])

    assert rc == 2
    assert "no run" in capsys.readouterr().err.lower()


def test_undo_no_runs_yet_exits_2(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "undo", "p1"])

    assert rc == 2
    assert "no runs recorded" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# --pause / enabled: false skip behaviour
# ---------------------------------------------------------------------------


def test_undo_pause_disables_the_pair_and_run_skips_it(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    write(local_dir, "a.txt", "hello", BASE)
    config_path = write_config(tmp_path, local_dir, remote_dir)
    _run_sync(tmp_path, config_path)
    capsys.readouterr()

    rc = cli.main(["--config", str(config_path), "undo", "p1", "--pause"])
    assert rc == 0
    assert "paused" in capsys.readouterr().out

    raw = yaml.safe_load(config_path.read_text())
    assert raw["pairs"][0]["enabled"] is False

    rc = cli.main(["--config", str(config_path), "run", "--yes"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped" in out
    assert "nothing to do" in out


def test_run_with_no_names_skips_disabled_pair_with_a_note(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    write(local_dir, "a.txt", "hello", BASE)
    config_path = write_config(tmp_path, local_dir, remote_dir, extra={"enabled": False})

    rc = cli.main(["--config", str(config_path), "run", "--yes"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped" in out
    assert "paused" in out
    assert "a.txt" not in tree(remote_dir)


def test_run_with_explicit_name_still_syncs_a_disabled_pair(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    write(local_dir, "a.txt", "hello", BASE)
    config_path = write_config(tmp_path, local_dir, remote_dir, extra={"enabled": False})

    rc = cli.main(["--config", str(config_path), "run", "p1", "--yes"])

    assert rc == 0
    assert "a.txt" in tree(remote_dir)  # explicit name overrides the pause


def test_status_with_no_names_skips_disabled_pair(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    write(local_dir, "a.txt", "hello", BASE)
    config_path = write_config(tmp_path, local_dir, remote_dir, extra={"enabled": False})

    rc = cli.main(["--config", str(config_path), "status"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped" in out
    assert "nothing to do" in out


def test_edit_pair_enabled_reenables_a_paused_pair(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = write_config(tmp_path, local_dir, remote_dir, extra={"enabled": False})

    rc = cli.main(["--config", str(config_path), "edit-pair", "p1", "--enabled"])

    assert rc == 0
    raw = yaml.safe_load(config_path.read_text())
    assert raw["pairs"][0]["enabled"] is True


def test_undo_of_undo_via_cli_is_redo(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    write(local_dir, "note.txt", "v1", BASE)
    config_path = write_config(tmp_path, local_dir, remote_dir)
    _run_sync(tmp_path, config_path)

    write(local_dir, "note.txt", "v2", BASE + 200)
    _run_sync(tmp_path, config_path)
    assert tree(remote_dir)["note.txt"] == "v2"
    capsys.readouterr()

    cli.main(["--config", str(config_path), "undo", "p1"])
    assert tree(remote_dir)["note.txt"] == "v1"
    capsys.readouterr()

    cli.main(["--config", str(config_path), "undo", "p1"])
    assert tree(remote_dir)["note.txt"] == "v2"
