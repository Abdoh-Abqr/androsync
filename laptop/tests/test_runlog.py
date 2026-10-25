"""Tests for androsync.runlog (leftover L1: ~/.local/state/androsync/last-run.log)."""

from __future__ import annotations

import pytest

from androsync.runlog import RunLog, log_path, tee


@pytest.fixture(autouse=True)
def _xdg_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    return tmp_path


def test_log_path_under_xdg_state_home(tmp_path):
    assert log_path() == tmp_path / "xdg-state" / "androsync" / "last-run.log"


def test_run_log_writes_lines():
    run_log = RunLog()
    run_log.write("first line")
    run_log.write("second line")
    run_log.close()

    assert log_path().read_text() == "first line\nsecond line\n"


def test_run_log_truncates_on_each_new_instance():
    first = RunLog()
    first.write("from the first run")
    first.close()

    second = RunLog()
    second.write("from the second run")
    second.close()

    assert log_path().read_text() == "from the second run\n"


def test_run_log_creates_parent_directory(tmp_path):
    assert not (tmp_path / "xdg-state").exists()
    RunLog().close()
    assert log_path().exists()


def test_tee_writes_to_both_run_log_and_primary():
    run_log = RunLog()
    seen: list[str] = []

    combined = tee(seen.append, run_log)
    combined("hello")
    run_log.close()

    assert seen == ["hello"]
    assert log_path().read_text() == "hello\n"


def test_tee_with_no_primary_only_writes_run_log():
    run_log = RunLog()
    combined = tee(None, run_log)
    combined("only in the file")
    run_log.close()

    assert log_path().read_text() == "only in the file\n"
