"""AgentTransport and the agent endpoints, against a fake phone app over real HTTP (task 37b, issue #14)."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fake_agent import FakeAgent

from androsync.config import load_config
from androsync.daemon.jobs import JobRunner
from androsync.daemon.server import ApiServer
from androsync.ignore import IgnoreRules
from androsync.model import Side
from androsync.transport import agent as agent_module
from androsync.transport.agent import AgentBroker, AgentTransport
from androsync.transport.base import TransportCancelled, TransportError, hash_file

TOKEN = "t0ken"


class Env:
    """An ApiServer with a broker, a fake agent polling it, and an AgentTransport using the same broker."""

    def __init__(self, tmp_path: Path, *, start_agent: bool = True, config_text: str | None = None, adb_factory=None) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.broker = AgentBroker()
        self.config_file = tmp_path / "config.yaml"
        self.config_file.write_text(config_text or "device: auto\nservice:\n  port: 8765\npairs: []\n")
        config_file = self.config_file
        config = load_config(config_file)
        self.runner = JobRunner(lambda: load_config(config_file), adb_factory or (lambda c: None), self.broker)
        self.api = ApiServer(
            self.runner,
            lambda: load_config(config_file),
            token=TOKEN,
            listen=["127.0.0.1"],
            port=0,
            version="test",
            agent=self.broker,
            config_path=self.config_file,
        )
        self.api.start()
        self.base = f"http://127.0.0.1:{self.api.bound_addresses[0][1]}"
        self.phone = tmp_path / "phone"
        self.phone.mkdir()
        self.agent = FakeAgent(self.base, TOKEN)
        if start_agent:
            self.agent.start()
            self.wait_connected()
        # Tests use absolute temp paths as the "phone" folder, so skip the /sdcard rule.
        self.transport = AgentTransport(self.broker, Side.REMOTE, check_root=lambda r: r)

    def wait_connected(self) -> None:
        deadline = time.monotonic() + 5
        while not self.broker.connected():
            assert time.monotonic() < deadline, "the fake agent never connected"
            time.sleep(0.02)

    def get(self, path: str, token: str | None = TOKEN):
        req = urllib.request.Request(self.base + path)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        return urllib.request.urlopen(req, timeout=5)

    def request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {TOKEN}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")

    def close(self) -> None:
        self.agent.stop()
        self.api.stop()


@pytest.fixture()
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.close()


def put(root: Path, rel: str, text: str, mtime: float = 1_700_000_000) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))
    return path


# --- ping ---------------------------------------------------------------------------------------------------


def test_ping_reports_agent_connected_while_the_app_polls(env):
    assert json.load(env.get("/v1/ping"))["agent"] == "connected"


def test_ping_reports_agent_absent_without_an_app(tmp_path):
    e = Env(tmp_path, start_agent=False)
    try:
        assert json.load(e.get("/v1/ping"))["agent"] == "absent"
    finally:
        e.close()


def test_agent_endpoints_need_the_token(env):
    with pytest.raises(urllib.error.HTTPError) as err:
        env.get("/v1/agent/next?wait=0", token=None)
    assert err.value.code == 401


# --- the transport, operation by operation ----------------------------------------------------------------------


def test_scan_lists_regular_files_with_size_and_mtime(env):
    put(env.phone, "a.txt", "hello", 1_700_000_100)
    put(env.phone, "sub/b.txt", "world!", 1_700_000_200)
    snap = env.transport.scan(str(env.phone), IgnoreRules([]))
    assert {rel: (e.size, e.mtime) for rel, e in snap.items()} == {
        "a.txt": (5, 1_700_000_100),
        "sub/b.txt": (6, 1_700_000_200),
    }


def test_scan_applies_ignore_rules_on_the_laptop_including_whole_folders(env):
    put(env.phone, "keep.txt", "k")
    put(env.phone, "skip.tmp", "s")
    put(env.phone, ".git/config", "g")
    put(env.phone, ".androsync/incoming/x/y", "i")
    snap = env.transport.scan(str(env.phone), IgnoreRules(["*.tmp", ".git/**", ".androsync/**"]))
    assert sorted(snap) == ["keep.txt"]


def test_scan_of_a_missing_folder_is_empty(env):
    assert env.transport.scan(str(env.phone / "nope"), IgnoreRules([])) == {}


def test_fetch_streams_the_file_and_keeps_its_mtime(env, tmp_path):
    put(env.phone, "pic.bin", "x" * 3_000_000, 1_650_000_000)
    dest = tmp_path / "got.bin"
    env.transport.fetch(str(env.phone), "pic.bin", dest)
    assert dest.read_text() == "x" * 3_000_000
    assert dest.stat().st_mtime == 1_650_000_000


def test_store_streams_the_file_and_sets_the_mtime(env, tmp_path):
    src = tmp_path / "up.bin"
    src.write_text("y" * 2_500_000)
    env.transport.store(src, str(env.phone), "deep/dir/up.bin", 1_640_000_000)
    stored = env.phone / "deep/dir/up.bin"
    assert stored.read_text() == "y" * 2_500_000
    assert stored.stat().st_mtime == 1_640_000_000


def test_hash_many_matches_a_local_hash_and_batches(env, monkeypatch):
    monkeypatch.setattr(agent_module, "HASH_BATCH", 2)
    for name in "abcde":
        put(env.phone, f"{name}.txt", name * 10)
    hashes = env.transport.hash_many(str(env.phone), [f"{n}.txt" for n in "abcde"])
    assert hashes == {f"{n}.txt": hash_file(env.phone / f"{n}.txt") for n in "abcde"}
    assert env.agent.log.count("hash_many") == 3  # 2 + 2 + 1


def test_delete_rename_mkdirs_exists_stat(env):
    root = str(env.phone)
    put(env.phone, "gone.txt", "g")
    put(env.phone, "old.txt", "o", 1_600_000_000)
    env.transport.delete(root, "gone.txt")
    assert not (env.phone / "gone.txt").exists()
    env.transport.rename(root, "old.txt", "new/name.txt")
    assert (env.phone / "new/name.txt").read_text() == "o"
    env.transport.mkdirs(root, "made/here")
    assert (env.phone / "made/here").is_dir()
    assert env.transport.exists(root) is True
    assert env.transport.exists(str(env.phone / "absent")) is False
    entry = env.transport.stat(root, "new/name.txt")
    assert (entry.size, entry.mtime) == (1, 1_600_000_000)
    assert env.transport.stat(root, "nothing.txt") is None


def test_ls_lists_children(env):
    put(env.phone, "file.txt", "abc")
    (env.phone / "folder").mkdir()
    assert sorted(env.transport.ls(str(env.phone))) == [("file.txt", False, 3), ("folder", True, 0)]


def test_unsafe_relative_paths_are_refused_before_anything_is_sent(env):
    with pytest.raises(TransportError):
        env.transport.delete(str(env.phone), "../escape")
    assert env.agent.log == []


def test_default_root_check_refuses_folders_outside_shared_storage():
    transport = AgentTransport(AgentBroker(), Side.REMOTE)
    with pytest.raises(TransportError):
        transport.exists("/data")


# --- failure and stop -------------------------------------------------------------------------------------------


def test_an_error_from_the_app_becomes_a_transport_error(env):
    env.agent.fail["delete"] = "permission denied"
    with pytest.raises(TransportError, match="permission denied"):
        env.transport.delete(str(env.phone), "x.txt")


def test_no_agent_connected_fails_at_once_with_a_clear_message(tmp_path):
    e = Env(tmp_path, start_agent=False)
    try:
        started = time.monotonic()
        with pytest.raises(TransportError, match="not connected"):
            e.transport.exists(str(e.phone))
        assert time.monotonic() - started < 1
    finally:
        e.close()


def test_abort_cuts_a_waiting_operation_short(env):
    env.agent.hold.clear()  # the app takes the operation but never answers
    errors: list[BaseException] = []

    def run():
        try:
            env.transport.scan(str(env.phone), IgnoreRules([]))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.6)
    started = time.monotonic()
    env.transport.abort()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert time.monotonic() - started < 2
    assert len(errors) == 1 and isinstance(errors[0], TransportCancelled)
    env.agent.hold.set()


def test_after_abort_every_later_call_is_cancelled(env):
    env.transport.abort()
    with pytest.raises(TransportCancelled):
        env.transport.exists(str(env.phone))


def test_an_answer_for_a_cancelled_operation_gets_410(env):
    env.agent.hold.clear()
    thread = threading.Thread(target=lambda: _swallow(lambda: env.transport.scan(str(env.phone), IgnoreRules([]))))
    thread.start()
    time.sleep(0.6)
    op_id = next(iter(env.broker._ops))
    env.transport.abort()
    thread.join(timeout=3)
    req = urllib.request.Request(
        env.base + f"/v1/agent/result/{op_id}",
        data=b'{"ok": true, "result": {}}',
        method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)
    assert err.value.code in (404, 410)
    env.agent.hold.set()


def _swallow(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass


def test_an_app_that_goes_silent_mid_operation_fails_the_call(monkeypatch, env):
    monkeypatch.setattr(agent_module, "CONNECTED_GRACE", 0.3)
    monkeypatch.setattr(agent_module, "BUSY_GRACE", 0.5)
    env.agent.hold.clear()
    env.agent.silent.set()
    with pytest.raises(TransportError, match="silent|not connected"):
        env.transport.scan(str(env.phone), IgnoreRules([]))
    env.agent.hold.set()


# --- several phones' apps at the same time (Phase W2) -------------------------------------------------------


def test_two_apps_can_be_connected_and_each_gets_only_its_own_work():
    from androsync.transport.agent import AgentBroker

    now = [0.0]
    broker = AgentBroker(clock=lambda: now[0])
    assert broker.next_op(0, "phone-a") is None
    assert broker.next_op(0, "phone-b") is None  # no 409 any more
    assert broker.connected("phone-a") and broker.connected("phone-b")
    assert sorted(broker.connected_ids()) == ["phone-a", "phone-b"]

    results = {}

    def ask(agent_id):
        results[agent_id] = broker.call("exists", {"root": "/x"}, agent_id=agent_id)

    import threading

    threads = [threading.Thread(target=ask, args=(a,)) for a in ("phone-a", "phone-b")]
    for t in threads:
        t.start()
    for agent_id in ("phone-a", "phone-b"):
        op = broker.next_op(5, agent_id)
        assert op is not None and op["op"] == "exists"
        broker.complete(op["id"], True, {"exists": agent_id == "phone-a"})
    for t in threads:
        t.join(5)
    assert results == {"phone-a": {"exists": True}, "phone-b": {"exists": False}}


def test_without_a_choice_the_only_connected_app_is_used_and_two_are_refused_by_name():
    from androsync.transport.agent import AgentBroker, NOT_CONNECTED
    from androsync.transport.base import TransportError

    now = [0.0]
    broker = AgentBroker(clock=lambda: now[0])
    with pytest.raises(TransportError, match="not connected"):
        broker.resolve(None)
    broker.next_op(0, "phone-a")
    assert broker.resolve(None) == "phone-a"
    broker.report_identity("phone-a", "S24 FE", "SM-S721B")
    broker.report_identity("phone-b", None, "Tab S9")
    broker.next_op(0, "phone-b")
    with pytest.raises(TransportError, match=r"more than one phone app is connected \(S24 FE, Tab S9\)"):
        broker.resolve(None)
    assert broker.resolve("phone-b") == "phone-b"


def test_each_app_has_its_own_history_key_and_the_choice_is_kept_for_the_whole_sync():
    from androsync.transport.agent import AgentBroker, AgentTransport, agent_device, agent_id_from_device

    now = [0.0]
    broker = AgentBroker(clock=lambda: now[0])
    broker.next_op(0, "phone-a")
    sole = AgentTransport(broker, check_root=lambda r: r)
    assert sole.device_key == agent_device("phone-a") == "agent-phone-a"
    broker.next_op(0, "phone-b")  # a second phone appears in the middle: the first choice stands
    assert sole.device_key == "agent-phone-a"
    assert AgentTransport(broker, agent_id="phone-b").device_key == "agent-phone-b"
    assert agent_id_from_device("agent-phone-b") == "phone-b"
    assert agent_id_from_device("any") is None and agent_id_from_device("RZ123") is None


def test_ping_lists_every_app_with_its_name_and_route():
    from androsync.transport.agent import AgentBroker

    now = [0.0]
    broker = AgentBroker(clock=lambda: now[0])
    broker.next_op(0, "phone-a")
    broker.report_identity("phone-a", "S24 FE", "SM-S721B")
    broker.report_route("tailscale", "phone-a")
    broker.next_op(0, "phone-b")
    now[0] += 100  # b is the only one that stays connected? both are stale now
    broker.next_op(0, "phone-b")
    rows = {r["id"]: r for r in broker.agent_infos()}
    assert rows["phone-a"]["name"] == "S24 FE" and rows["phone-a"]["route"] == "tailscale"
    assert rows["phone-a"]["connected"] is False and rows["phone-b"]["connected"] is True
    assert rows["phone-a"]["device"] == "agent-phone-a"


def test_the_http_poll_is_never_refused_and_records_the_name(env):
    status = env.get("/v1/agent/next?wait=0&agent=some-other-phone&name=Tablet&model=SM-X").status
    assert status in (200, 204)  # no 409
    _, ping = env.request("GET", "/v1/ping")
    rows = {r["id"]: r for r in ping["agents"]}
    assert rows["some-other-phone"]["name"] == "Tablet"
    assert ping["agent"] == "connected"


def test_an_agent_that_does_not_identify_itself_is_not_blocked(env):
    # Older apps send no id; they keep working (one phone at a time was always the rule in practice).
    assert env.get("/v1/agent/next?wait=0").status in (200, 204)
