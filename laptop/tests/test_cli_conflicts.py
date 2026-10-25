"""Tests for androsync.cli_conflicts (``conflicts list|restore|clean``).

Same pattern as test_cli_pairs.py: ``cli.open_pair_transports`` is
monkeypatched to two ``LocalDirTransport`` instances over temp directories,
so the whole CLI can be exercised without ``adb`` or a device attached.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from androsync import cli
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


def write_config(tmp_path, local_dir, remote_dir) -> Path:
    raw = {
        "device": "auto",
        "pairs": [
            {
                "name": "p1",
                "local": str(local_dir),
                "remote": str(remote_dir),
                "direction": "two-way",
            }
        ],
    }
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


def _make_folder_mode_conflict(tmp_path, local_dir, remote_dir):
    """A real conflict via cli's own run_pair path, saved as a folder-mode backup."""
    from androsync.config import load_config

    write(local_dir, "note.md", "v1", BASE)
    write(remote_dir, "note.md", "v1", BASE)
    config_path = write_config(tmp_path, local_dir, remote_dir)
    pair = load_config(config_path).pairs[0]
    run_pair(pair, LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE), now=datetime.fromtimestamp(BASE + 100))

    write(local_dir, "note.md", "laptop edit", BASE + 200)
    write(remote_dir, "note.md", "phone edit", BASE + 300)  # newer -> phone wins, laptop is the loser
    run_pair(
        pair,
        LocalDirTransport(Side.LOCAL),
        LocalDirTransport(Side.REMOTE),
        now=datetime(2026, 9, 5, 15, 30, 0),
    )
    return config_path


# ---------------------------------------------------------------------------
# conflicts list
# ---------------------------------------------------------------------------


def test_conflicts_list_empty(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "conflicts", "list", "p1"])

    assert rc == 0
    assert "no saved conflict versions" in capsys.readouterr().out


def test_conflicts_list_shows_saved_backup(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = _make_folder_mode_conflict(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "conflicts", "list", "p1"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "20260905-153000" in out
    assert "note.md" in out


def test_conflicts_list_unknown_pair(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "conflicts", "list", "nope"])

    assert rc == 2
    assert "unknown pair" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# conflicts restore
# ---------------------------------------------------------------------------


def test_conflicts_restore_copies_backup_back_over_live_file(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = _make_folder_mode_conflict(tmp_path, local_dir, remote_dir)
    assert tree(local_dir)["note.md"] == "phone edit"

    rc = cli.main(["--config", str(config_path), "conflicts", "restore", "p1", "note.md"])

    assert rc == 0
    assert tree(local_dir)["note.md"] == "laptop edit"
    assert "restored" in capsys.readouterr().out


def test_conflicts_restore_missing_version_exits_2(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "conflicts", "restore", "p1", "nope.md"])

    assert rc == 2
    assert "no saved conflict version" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# conflicts clean
# ---------------------------------------------------------------------------


def test_conflicts_clean_dry_run_by_default(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    write(local_dir, "a.txt", "original", BASE)
    write(remote_dir, "a.txt", "original", BASE)
    write(local_dir, "a.conflict-laptop-20260101-000000.txt", "original", BASE)
    write(remote_dir, "a.conflict-laptop-20260101-000000.txt", "original", BASE)
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "conflicts", "clean", "p1"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "would delete" in out
    assert "re-run with --yes" in out
    assert "a.conflict-laptop-20260101-000000.txt" in tree(local_dir)  # untouched


def test_conflicts_clean_yes_applies_and_deletes(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    write(local_dir, "a.txt", "original", BASE)
    write(remote_dir, "a.txt", "original", BASE)
    write(local_dir, "a.conflict-laptop-20260101-000000.txt", "original", BASE)
    write(remote_dir, "a.conflict-laptop-20260101-000000.txt", "original", BASE)
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "conflicts", "clean", "p1", "--yes"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "cleaned up" in out
    assert "a.conflict-laptop-20260101-000000.txt" not in tree(local_dir)
    assert "a.conflict-laptop-20260101-000000.txt" not in tree(remote_dir)
