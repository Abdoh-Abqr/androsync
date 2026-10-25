"""Pairs that use the phone app's agent instead of adb (task 37d, issue #16): config, jobs, preview, undo, guards."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from test_agent_transport import Env, put

from androsync import browse
from androsync.config import ConfigError, load_config
from androsync.transport.agent import AgentTransport


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    # The tests' "phone" is a temp folder, not /sdcard, so skip the shared-storage rule on the agent transport.
    monkeypatch.setattr(AgentTransport, "_default_check", staticmethod(lambda root: root))


def config_text(tmp_path: Path, pairs: list[tuple[str, str]]) -> str:
    """``pairs`` is ``[(name, transport)]``; every pair syncs ``local/<name>`` with ``phone/<name>``."""
    lines = ["device: auto", "service:", "  port: 8765", "pairs:"]
    for name, transport in pairs:
        lines += [
            f"  - name: {name}",
            f"    local: {tmp_path / 'laptop' / name}",
            f"    remote: {tmp_path / 'env' / 'phone' / name}",
            "    direction: two-way",
            f"    transport: {transport}",
        ]
    return "\n".join(lines) + "\n"


def failing_adb(config):
    raise RuntimeError("no adb device (simulated)")


def make_env(tmp_path, pairs, factory=failing_adb) -> Env:
    for name, _ in pairs:
        (tmp_path / "laptop" / name).mkdir(parents=True, exist_ok=True)
    return Env(tmp_path / "env", config_text=config_text(tmp_path, pairs), adb_factory=factory)


def wait_job(env: Env, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, job = env.request("GET", f"/v1/jobs/{job_id}")
        if job["state"] not in ("queued", "running", "cancelling"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def run_job(env: Env, pairs: list[str] | None = None, **extra) -> dict:
    status, body = env.request("POST", "/v1/run", {"pairs": pairs, **extra})
    assert status == 202, body
    return wait_job(env, body["job_id"])


# --- config ------------------------------------------------------------------------------------------------


def test_transport_defaults_to_adb_and_accepts_agent(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(config_text(tmp_path, [("a", "agent")]) + "  - name: b\n    local: /x\n    remote: /sdcard/b\n    direction: two-way\n")
    config = load_config(path)
    assert {p.name: p.transport for p in config.pairs} == {"a": "agent", "b": "adb"}


def test_an_unknown_transport_is_refused_with_the_pair_name(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(config_text(tmp_path, [("odd", "carrier-pigeon")]))
    with pytest.raises(ConfigError, match="pair odd: 'transport' must be one of"):
        load_config(path)


# --- the daemon --------------------------------------------------------------------------------------------


def test_the_pairs_list_says_which_transport_each_pair_uses(tmp_path):
    env = make_env(tmp_path, [("a", "agent"), ("b", "adb")])
    try:
        _, pairs = env.request("GET", "/v1/pairs")
        assert {p["name"]: p["transport"] for p in pairs} == {"a": "agent", "b": "adb"}
    finally:
        env.close()


def test_an_agent_pair_syncs_without_any_adb_device(tmp_path):
    env = make_env(tmp_path, [("notes", "agent")])
    try:
        put(tmp_path / "laptop" / "notes", "from-laptop.txt", "L", 1_700_000_000)
        put(env.phone / "notes", "from-phone.txt", "P", 1_700_000_100)
        job = run_job(env)
        assert job["state"] == "done", job
        assert (env.phone / "notes/from-laptop.txt").read_text() == "L"
        assert (tmp_path / "laptop/notes/from-phone.txt").read_text() == "P"
    finally:
        env.close()


def test_a_mixed_job_syncs_the_agent_pair_and_fails_only_the_adb_pair(tmp_path):
    env = make_env(tmp_path, [("viaagent", "agent"), ("viaadb", "adb")])
    try:
        put(tmp_path / "laptop" / "viaagent", "x.txt", "x", 1_700_000_000)
        put(tmp_path / "laptop" / "viaadb", "y.txt", "y", 1_700_000_000)
        job = run_job(env)
        assert job["state"] == "failed"
        assert (env.phone / "viaagent/x.txt").read_text() == "x"
        assert not (env.phone / "viaadb/y.txt").exists()
        log = "\n".join(line for _, line in env.runner.get(job["id"]).log.tail(100))
        assert "cannot be reached over adb" in log and "ERROR viaadb" in log
    finally:
        env.close()


def test_an_adb_only_job_still_fails_as_before_when_there_is_no_phone(tmp_path):
    env = make_env(tmp_path, [("viaadb", "adb")])
    try:
        job = run_job(env)
        assert job["state"] == "failed" and "no adb device" in job["error"]
    finally:
        env.close()


def test_preview_of_an_agent_pair_lists_the_plan_and_changes_nothing(tmp_path):
    env = make_env(tmp_path, [("notes", "agent")])
    try:
        put(tmp_path / "laptop" / "notes", "new.txt", "n", 1_700_000_000)
        status, body = env.request("GET", "/v1/status/notes")
        assert status == 200, body
        assert any("new.txt" in line for line in body["actions"])
        assert not (env.phone / "notes/new.txt").exists()
    finally:
        env.close()


def test_undo_works_on_an_agent_pair(tmp_path):
    env = make_env(tmp_path, [("notes", "agent")])
    try:
        laptop = tmp_path / "laptop" / "notes"
        put(laptop, "doc.txt", "v1", 1_700_000_000)
        assert run_job(env)["state"] == "done"
        put(laptop, "doc.txt", "v2 edited", 1_700_000_500)
        assert run_job(env)["state"] == "done"
        assert (env.phone / "notes/doc.txt").read_text() == "v2 edited"
        status, body = env.request("POST", "/v1/undo/notes", {})
        assert status == 202, body
        assert wait_job(env, body["job_id"])["state"] == "done"
        assert (env.phone / "notes/doc.txt").read_text() == "v1"
        assert (laptop / "doc.txt").read_text() == "v2 edited"  # undo reverts what the sync changed, not your edit
    finally:
        env.close()


def test_the_safety_guard_stops_a_big_first_run_on_an_agent_pair(tmp_path):
    text = config_text(tmp_path, [("big", "agent")]) + "    safety:\n      big_first_run_files: 3\n"
    for name in ("big",):
        (tmp_path / "laptop" / name).mkdir(parents=True, exist_ok=True)
    env = Env(tmp_path / "env", config_text=text, adb_factory=failing_adb)
    try:
        for i in range(5):
            put(tmp_path / "laptop" / "big", f"f{i}.txt", "x", 1_700_000_000)
        job = run_job(env)
        assert job["state"] == "needs_confirmation", job
        assert not any((env.phone / "big").glob("*")) if (env.phone / "big").exists() else True
        confirmed = run_job(env, confirm_large=True)
        assert confirmed["state"] == "done"
        assert len(list((env.phone / "big").glob("*.txt"))) == 5
    finally:
        env.close()


def test_conflicts_clean_dry_run_uses_the_agent_for_an_agent_pair(tmp_path):
    env = make_env(tmp_path, [("notes", "agent")])
    try:
        status, body = env.request("POST", "/v1/conflicts/notes/clean", {"dry_run": True})
        assert status == 200, body
        assert body["actions"] == []
    finally:
        env.close()


def test_folder_browsing_goes_through_the_agent_when_no_adb_phone_is_attached(tmp_path, monkeypatch):
    monkeypatch.setattr(browse, "_checked_remote", lambda path: path)
    env = make_env(tmp_path, [("notes", "agent")])
    try:
        put(env.phone, "docs/a.txt", "a")
        status, body = env.request("GET", f"/v1/browse?side=remote&path={env.phone}")
        assert status == 200, body
        assert [e["name"] for e in body["entries"]] == ["docs"]
    finally:
        env.close()


def test_browsing_the_phone_says_not_connected_with_neither_adb_nor_agent(tmp_path):
    env = Env(tmp_path / "env", start_agent=False)
    try:
        status, body = env.request("GET", "/v1/browse?side=remote&path=/sdcard")
        assert status == 503 and "not connected" in body["error"]
    finally:
        env.close()


# --- a new token, generated through the API -------------------------------------------------------------------


def test_a_new_token_is_saved_and_only_the_new_one_works_afterwards(tmp_path):
    env = make_env(tmp_path, [("notes", "agent")])
    try:
        status, body = env.request("POST", "/v1/token/rotate", {})
        assert status == 200
        new = body["token"]
        assert new and new != "t0ken" and len(new) >= 32
        assert load_config(env.config_file).service.token == new  # written to the config file

        status, _ = env.request("GET", "/v1/ping")  # the helper still sends the old token
        assert status == 401
        import json as _json
        import urllib.request

        req = urllib.request.Request(env.base + "/v1/ping")
        req.add_header("Authorization", f"Bearer {new}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200 and _json.load(resp)["name"] == "androsync"
    finally:
        env.close()


def test_rotating_the_token_needs_the_current_token(tmp_path):
    env = make_env(tmp_path, [("notes", "agent")])
    try:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(env.base + "/v1/token/rotate", data=b"{}", method="POST")
        req.add_header("Authorization", "Bearer wrong")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 401
        assert load_config(env.config_file).service.token != "wrong"
    finally:
        env.close()
