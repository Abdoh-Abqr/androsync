"""Tests for androsync.cli's `history` command (task 26)."""

from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml

from androsync import cli

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture(autouse=True)
def _git_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "androsync-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "androsync-test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "androsync-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "androsync-test@example.com")


def write_config(tmp_path, local_dir, remote_dir, *, versioning="git"):
    config = {
        "device": "auto",
        "pairs": [
            {
                "name": "vault",
                "local": str(local_dir),
                "remote": str(remote_dir),
                "direction": "two-way",
                "versioning": versioning,
            }
        ],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def test_history_no_repo_yet(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "history", "vault"])

    assert rc == 0
    assert "run a sync first" in capsys.readouterr().out


def test_history_prints_git_log(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = write_config(tmp_path, local_dir, remote_dir)

    subprocess.run(["git", "init", "-q"], cwd=local_dir, check=True)
    (local_dir / "a.txt").write_text("hi")
    subprocess.run(["git", "add", "-A"], cwd=local_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "androsync test commit"], cwd=local_dir, check=True)

    rc = cli.main(["--config", str(config_path), "history", "vault"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "androsync test commit" in out


def test_history_respects_n_flag(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = write_config(tmp_path, local_dir, remote_dir)

    subprocess.run(["git", "init", "-q"], cwd=local_dir, check=True)
    for i in range(5):
        (local_dir / f"f{i}.txt").write_text(str(i))
        subprocess.run(["git", "add", "-A"], cwd=local_dir, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"commit {i}"], cwd=local_dir, check=True)

    rc = cli.main(["--config", str(config_path), "history", "vault", "-n", "2"])

    out = capsys.readouterr().out
    assert rc == 0
    assert len(out.strip().splitlines()) == 2


def test_history_rejects_pair_without_versioning(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = write_config(tmp_path, local_dir, remote_dir, versioning="none")

    rc = cli.main(["--config", str(config_path), "history", "vault"])

    assert rc == 2
    assert "does not have versioning: git" in capsys.readouterr().err


def test_history_unknown_pair_exits_2(tmp_path, capsys):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    local_dir.mkdir()
    remote_dir.mkdir()
    config_path = write_config(tmp_path, local_dir, remote_dir)

    rc = cli.main(["--config", str(config_path), "history", "nope"])

    assert rc == 2
