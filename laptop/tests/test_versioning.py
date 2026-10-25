"""Tests for androsync.versioning (task 26: `versioning: git`).

Uses real `git` against real temp directories - no mocking, since this
module's whole job is shelling out to git correctly.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from androsync.config import PairConfig
from androsync.model import Summary
from androsync.versioning import VersioningError, commit_if_configured, history

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

NOW = datetime(2026, 9, 7, 12, 0, 0)


def make_pair(local: Path, **overrides) -> PairConfig:
    fields = dict(
        name="p",
        local=local,
        remote="/sdcard/x",
        direction="two-way",
        conflict="newest-wins",
        keep_conflict_copies=True,
        propagate_deletes=True,
        mtime_tolerance_s=2,
        ignore=[],
        versioning="git",
    )
    fields.update(overrides)
    return PairConfig(**fields)


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture(autouse=True)
def _git_identity(tmp_path, monkeypatch):
    # A commit needs a configured author; keep it local to this test's HOME
    # so it never touches (or depends on) the real user's git config.
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir()
    monkeypatch.setenv("GIT_AUTHOR_NAME", "androsync-test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "androsync-test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "androsync-test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "androsync-test@example.com")


def test_versioning_none_is_a_no_op(tmp_path):
    local = tmp_path / "vault"
    local.mkdir()
    (local / "a.md").write_text("hello")
    pair = make_pair(local, versioning="none")

    committed = commit_if_configured(pair, Summary(), NOW)

    assert committed is False
    assert not (local / ".git").exists()


def test_first_commit_initializes_repo_and_gitignore(tmp_path):
    local = tmp_path / "vault"
    local.mkdir()
    (local / "a.md").write_text("hello")
    pair = make_pair(local)

    committed = commit_if_configured(pair, Summary(copied_to_remote=1), NOW)

    assert committed is True
    assert (local / ".git").is_dir()
    gitignore = (local / ".gitignore").read_text()
    assert ".obsidian/workspace*" in gitignore
    assert ".trash/" in gitignore
    log = _git(["log", "--oneline"], local)
    assert "androsync 2026-09-07T12:00:00" in log


def test_second_run_with_a_change_makes_a_second_commit(tmp_path):
    local = tmp_path / "vault"
    local.mkdir()
    (local / "a.md").write_text("hello")
    pair = make_pair(local)

    commit_if_configured(pair, Summary(), NOW)
    (local / "b.md").write_text("world")
    commit_if_configured(pair, Summary(), NOW)

    log = _git(["log", "--oneline"], local)
    assert len(log.strip().splitlines()) == 2


def test_no_change_run_makes_no_commit(tmp_path):
    local = tmp_path / "vault"
    local.mkdir()
    (local / "a.md").write_text("hello")
    pair = make_pair(local)

    commit_if_configured(pair, Summary(), NOW)
    committed_again = commit_if_configured(pair, Summary(), NOW)

    assert committed_again is False
    log = _git(["log", "--oneline"], local)
    assert len(log.strip().splitlines()) == 1


def test_commit_message_includes_the_summary(tmp_path):
    local = tmp_path / "vault"
    local.mkdir()
    (local / "a.md").write_text("hello")
    pair = make_pair(local)

    commit_if_configured(pair, Summary(copied_to_local=3, copied_to_remote=1), NOW)

    log = _git(["log", "-1", "--format=%s"], local)
    assert "copied_to_local=3" in log
    assert "copied_to_remote=1" in log


def test_existing_repo_is_reused_not_reinitialized(tmp_path):
    local = tmp_path / "vault"
    local.mkdir()
    _git(["init", "-q"], local)
    (local / "a.md").write_text("hello")
    pair = make_pair(local)

    commit_if_configured(pair, Summary(), NOW)

    # No .gitignore was pre-created before init in this test, but
    # commit_if_configured only writes one when it does the init itself.
    log = _git(["log", "--oneline"], local)
    assert len(log.strip().splitlines()) == 1


def test_history_returns_log_output(tmp_path):
    local = tmp_path / "vault"
    local.mkdir()
    (local / "a.md").write_text("hello")
    pair = make_pair(local)
    commit_if_configured(pair, Summary(), NOW)

    output = history(local)

    assert "androsync 2026-09-07T12:00:00" in output


def test_history_empty_string_when_no_repo(tmp_path):
    local = tmp_path / "vault"
    local.mkdir()

    assert history(local) == ""


def test_history_respects_limit(tmp_path):
    local = tmp_path / "vault"
    local.mkdir()
    pair = make_pair(local)
    for i in range(5):
        (local / f"f{i}.txt").write_text(str(i))
        commit_if_configured(pair, Summary(), NOW)

    output = history(local, limit=2)

    assert len(output.strip().splitlines()) == 2


def test_git_failure_raises_versioning_error(tmp_path):
    local = tmp_path / "vault"
    local.mkdir()
    # A `.git` that exists but isn't a valid repo pointer: passes the
    # `history()`/`commit_if_configured()` existence check, then makes the
    # actual `git` invocation itself fail.
    (local / ".git").write_text("not a real git dir or gitfile pointer\n")

    with pytest.raises(VersioningError):
        history(local)
