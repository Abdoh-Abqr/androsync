"""Each phone app keeps its own history, so a second phone's empty folder is never read as "deleted" (Phase W1)."""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from androsync import state
from androsync.config import PairConfig
from androsync.executor import run_pair
from androsync.model import Side
from androsync.transport.agent import AgentBroker, AgentTransport
from androsync.transport.local import LocalDirTransport


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setattr(AgentTransport, "_default_check", staticmethod(lambda root: root))


class _InstantAgent:
    """A fake phone app that answers a broker's operations from a local folder (its "phone")."""

    def __init__(self, broker: AgentBroker, agent_id: str, phone: Path) -> None:
        import threading

        self.broker, self.agent_id, self.phone = broker, agent_id, phone
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        local = LocalDirTransport(Side.REMOTE)
        from androsync.ignore import IgnoreRules

        while not self._stop.is_set():
            op = self.broker.next_op(0.1, self.agent_id)
            if op is None:
                continue
            args = op["args"]
            try:
                if op["op"] == "scan":
                    snap = local.scan(str(self.phone), IgnoreRules([])) if self.phone.is_dir() else {}
                    files = [[rel, e.size, e.mtime] for rel, e in snap.items()]
                    self.broker.complete(op["id"], True, {"exists": self.phone.is_dir(), "files": files})
                elif op["op"] == "exists":
                    self.broker.complete(op["id"], True, {"exists": self.phone.is_dir()})
                elif op["op"] == "mkdirs":
                    (self.phone / args["rel_dir"]).mkdir(parents=True, exist_ok=True)
                    self.broker.complete(op["id"], True, {})
                else:
                    self.broker.complete(op["id"], False, error=f"{op['op']} is not faked")
            except Exception as exc:  # noqa: BLE001
                self.broker.complete(op["id"], False, error=str(exc))

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2)


def _pair(local: Path, remote: str) -> PairConfig:
    return PairConfig(
        name="Obsidian",
        local=local,
        remote=remote,
        direction="two-way",
        conflict="newest-wins",
        keep_conflict_copies=True,
        propagate_deletes=True,
        mtime_tolerance_s=2,
        ignore=[],
        transport="agent",
    )


def test_a_second_phone_with_an_empty_folder_makes_a_first_run_with_no_deletions(tmp_path):
    local = tmp_path / "laptop"
    local.mkdir()
    for i in range(5):
        (local / f"n{i}.md").write_text(f"note {i}")
        os.utime(local / f"n{i}.md", (1_700_000_000, 1_700_000_000))
    s24, tablet = tmp_path / "s24", tmp_path / "tablet"
    s24.mkdir()
    for p in local.iterdir():
        (s24 / p.name).write_bytes(p.read_bytes())
        os.utime(s24 / p.name, (1_700_000_000, 1_700_000_000))

    broker = AgentBroker()
    a = _InstantAgent(broker, "s24", s24)
    b = _InstantAgent(broker, "tablet", tablet)  # no Obsidian folder at all
    try:
        time.sleep(0.3)
        pair = _pair(local, str(s24))
        when = datetime.fromtimestamp(1_700_000_100)
        first = run_pair(pair, LocalDirTransport(Side.LOCAL), AgentTransport(broker, agent_id="s24"), now=when)
        assert first.errors == 0
        # The tablet answers for the very same pair. Its history is its own (a first run), so its empty folder means
        # "nothing there yet", never "everything was deleted": the plan only copies, and nothing is deleted.
        t_pair = _pair(local, str(tablet))
        lines: list[str] = []
        second = run_pair(
            t_pair, LocalDirTransport(Side.LOCAL), AgentTransport(broker, agent_id="tablet"), now=when,
            dry_run=True, log=lines.append,
        )
        assert second.deleted_local == 0 and second.errors == 0
        assert not any("delete" in line.lower() for line in lines), lines
        assert sum("new on laptop" in line for line in lines) == 5
        assert sorted(p.name for p in local.iterdir()) == [f"n{i}.md" for i in range(5)]
        # Two phones, two separate histories: the plain, shared "Obsidian" history is not used by either.
        keys = set(state.keys_for("Obsidian"))
        assert "Obsidian@agent-s24" in keys and "Obsidian" not in keys
    finally:
        a.stop()
        b.stop()


def test_the_old_shared_history_goes_to_an_app_only_while_it_is_the_only_one_known(tmp_path):
    broker = AgentBroker()
    broker.next_op(0, "s24")
    only = AgentTransport(broker, agent_id="s24")
    assert only.claims_legacy_history is True
    broker.next_op(0, "tablet")
    assert only.claims_legacy_history is False
    assert state.key_for("Obsidian", only) == "Obsidian@agent-s24"
    assert not (state.state_dir() / "Obsidian.claimed").exists()  # nothing was claimed with two phones around
