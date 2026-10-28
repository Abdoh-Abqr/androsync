"""The end-to-end sync scenarios of ``test_e2e_local`` again, with the phone side reached through the agent protocol.

Task 37b (issue #14): "an AgentTransport passes the engine test suite against a fake agent". The scenarios are the
very same functions; only ``sync`` is swapped so that the remote side is an ``AgentTransport`` talking real HTTP to a
``FakeAgent`` that does the file work in the "phone" folder. Then a few scenarios the local suite does not cover:
undo and the large-batch path.
"""

from __future__ import annotations

from datetime import datetime

import pytest
import test_e2e_local as base
from fake_agent import FakeAgent
from test_agent_transport import TOKEN, Env
from test_e2e_local import (  # noqa: F401 - imported so pytest collects them here, with the swapped `sync`
    _xdg_data_home,
    make_pair,
    remove,
    roots,
    test_conflict_newest_wins_keeps_loser_as_conflict_copy_on_both_sides,
    test_delete_on_phone_not_propagated_when_disabled,
    test_delete_on_phone_propagates_to_laptop_when_enabled,
    test_edit_on_laptop_propagates_to_phone,
    test_edit_on_phone_propagates_to_laptop,
    test_first_sync_merges_disjoint_trees,
    test_ignored_paths_are_never_copied,
    test_one_way_phone_to_laptop,
    test_rename_on_laptop_is_delete_plus_create,
    test_three_consecutive_runs_are_stable,
    tree,
    write,
)

from androsync.executor import run_pair
from androsync.model import Side
from androsync.transport.agent import AgentTransport
from androsync.transport.local import LocalDirTransport
from androsync.undo import list_runs, undo_run


@pytest.fixture(autouse=True)
def _agent_remote(tmp_path, monkeypatch):
    """Make ``test_e2e_local.sync`` use the agent for the phone side."""
    env = Env(tmp_path / "agent-env")

    def agent_sync(pair, when):
        remote = AgentTransport(env.broker, Side.REMOTE, check_root=lambda r: r)
        return run_pair(pair, LocalDirTransport(Side.LOCAL), remote, now=datetime.fromtimestamp(when))

    monkeypatch.setattr(base, "sync", agent_sync)
    yield env
    env.close()


def test_the_scenarios_really_went_through_the_agent(roots, _agent_remote):
    local, remote = roots
    write(local, "a.txt", "from laptop", base.BASE)
    base.sync(make_pair(local, remote), base.BASE + 100)
    assert (remote / "a.txt").read_text() == "from laptop"
    assert "store" in _agent_remote.agent.log and "scan" in _agent_remote.agent.log


def test_many_files_in_one_run(roots):
    local, remote = roots
    for i in range(60):
        write(local, f"d{i % 6}/f{i}.txt", f"content {i}", base.BASE + i)
    summary = base.sync(make_pair(local, remote), base.BASE + 1000)
    assert summary.errors == 0
    assert tree(remote) == tree(local)
    assert len(tree(remote)) == 60


def test_undo_restores_both_sides_through_the_agent(roots, _agent_remote):
    local, remote = roots
    write(local, "keep.txt", "v1", base.BASE)
    write(remote, "other.txt", "phone only", base.BASE)
    base.sync(make_pair(local, remote), base.BASE + 100)
    write(local, "keep.txt", "v2 changed", base.BASE + 200)
    remove(remote, "other.txt")
    pair = make_pair(local, remote)
    base.sync(pair, base.BASE + 300)  # copies v2 to the phone and deletes other.txt on the laptop
    assert (remote / "keep.txt").read_text() == "v2 changed"
    assert not (local / "other.txt").exists()

    remote_transport = AgentTransport(_agent_remote.broker, Side.REMOTE, check_root=lambda r: r)
    # Each phone app keeps its own history (Phase W1), so the runs are under "<pair>@agent-<id>".
    run = list_runs(f"{pair.name}@{remote_transport.device_key}")[0]
    undo_run(pair, run["run_id"], LocalDirTransport(Side.LOCAL), remote_transport)
    assert (remote / "keep.txt").read_text() == "v1"
    assert (local / "other.txt").read_text() == "phone only"
