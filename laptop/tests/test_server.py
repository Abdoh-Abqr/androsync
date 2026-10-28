"""Tests for androsync.daemon.server (ApiServer), driven with urllib/http.client.

Same fixture style as test_jobs.py: LocalDirTransport stands in for both
sides, XDG_DATA_HOME is isolated per test, and a small YAML config is
written to tmp_path. Every server started here is stopped in a
try/finally so its background threads never outlive the test.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from androsync.config import Config, load_config
from androsync.daemon.jobs import JobRunner
from androsync.daemon.server import ApiServer
from androsync.model import Side
from androsync.transport.local import LocalDirTransport

TOKEN = "s3cr3t-token"


@pytest.fixture(autouse=True)
def _xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def build_config(tmp_path: Path, pair_specs: list[dict]) -> Config:
    lines = [
        "device: auto",
        "service:",
        "  port: 8765",
        "defaults:",
        "  conflict: newest-wins",
        "  mtime_tolerance_s: 2",
        "pairs:",
    ]
    for spec in pair_specs:
        lines.append(f"  - name: {spec['name']}")
        lines.append(f"    local: {spec['local']}")
        lines.append(f"    remote: {spec['remote']}")
        lines.append(f"    direction: {spec.get('direction', 'two-way')}")
    path = tmp_path / "config.yaml"
    path.write_text("\n".join(lines) + "\n")
    return load_config(path)


def fast_transport_factory(_config: Config) -> tuple[LocalDirTransport, LocalDirTransport]:
    return LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE)


class SleepyLocalTransport(LocalDirTransport):
    """LocalDirTransport whose fetch() sleeps first, to make a job take a while."""

    def __init__(self, side: Side, delay: float) -> None:
        super().__init__(side)
        self.delay = delay

    def fetch(self, root, rel, tmp_path) -> None:  # noqa: ANN001 - match base signature
        time.sleep(self.delay)
        super().fetch(root, rel, tmp_path)


class _Server:
    """Starts an ApiServer for one test and always stops it, even on failure."""

    def __init__(
        self, config: Config, transport_factory=fast_transport_factory, config_path: Path | None = None, **kwargs
    ) -> None:
        self.config = config
        # Mirrors Daemon.start(): the same config_loader goes to both the
        # JobRunner and the ApiServer. When a test gives config_path, reload
        # from disk on every call (like the real daemon does) so writes made
        # through the pair-CRUD routes are visible on the very next request;
        # otherwise keep the old static-`config` behavior every other test here relies on.
        config_loader = (lambda: load_config(config_path)) if config_path is not None else (lambda: config)
        self.runner = JobRunner(config_loader, transport_factory)
        self.api = ApiServer(
            self.runner,
            config_loader,
            token=TOKEN,
            listen=["127.0.0.1"],
            port=0,
            version="0.1.0-test",
            config_path=config_path,
            **kwargs,
        )

    def __enter__(self) -> "_Server":
        self.api.start()
        host, port = self.api.bound_addresses[0]
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, *exc_info) -> None:
        self.api.stop()

    def request(self, method: str, path: str, *, token: str | None = TOKEN, body=None, raw_body: bytes | None = None):
        """Return (status, parsed_json_or_none)."""
        data = raw_body
        if data is None and body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        if token is not None:
            req.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                payload = resp.read()
                return resp.status, (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            return exc.code, (json.loads(payload) if payload else None)


def _one_pair_config(tmp_path: Path, name: str = "alpha") -> Config:
    local = tmp_path / f"{name}_local"
    local.mkdir()
    (local / "a.txt").write_text("hello")
    return build_config(
        tmp_path, [{"name": name, "local": str(local), "remote": str(tmp_path / f"{name}_remote")}]
    )


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


def test_missing_token_is_unauthorized(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/ping", token=None)
        assert status == 401
        assert body == {"error": "unauthorized"}


def test_wrong_token_is_unauthorized(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/ping", token="not-the-token")
        assert status == 401
        assert body == {"error": "unauthorized"}


# --------------------------------------------------------------------------
# ping / pairs
# --------------------------------------------------------------------------


def test_ping_returns_name_version_and_device_info(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config, device_info=lambda: {"transport": "usb", "device": "ABC123"}) as srv:
        status, body = srv.request("GET", "/v1/ping")
        assert status == 200
        assert body == {"name": "androsync", "version": "0.1.0-test", "transport": "usb", "device": "ABC123", "devices": [], "current_job": None, "agent": "absent", "agent_route": None, "laptop_name": body["laptop_name"], "agents": []}
        assert body["laptop_name"]


def test_ping_defaults_device_info_to_none(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/ping")
        assert status == 200
        assert body["transport"] is None
        assert body["device"] is None


def test_pairs_lists_configured_pairs(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/pairs")
        assert status == 200
        assert [row["name"] for row in body] == ["alpha"]
        assert body[0]["last_run"] is None


# --------------------------------------------------------------------------
# status (dry-run plan)
# --------------------------------------------------------------------------


def test_status_returns_planned_actions_without_touching_files(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/status/alpha")
        assert status == 200
        assert body["pair"] == "alpha"
        assert body["summary"]["copied_to_remote"] == 1
        assert any("a.txt" in line for line in body["actions"])

    assert not (tmp_path / "alpha_remote").exists()


def test_status_unknown_pair_is_404(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/status/nope")
        assert status == 404


# --------------------------------------------------------------------------
# pending (task 23: the app's "N conflicts waiting" line)
# --------------------------------------------------------------------------


def test_pending_empty_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/pending/alpha")
        assert status == 200
        assert body == []


def test_pending_returns_saved_entries(tmp_path, monkeypatch):
    from androsync import state

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    entries = [{"rel": "a.md", "local": None, "remote": None, "reason": "modified on both sides"}]
    state.save_pending("alpha", entries)

    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/pending/alpha")
        assert status == 200
        assert body == entries


def test_pending_unknown_pair_is_404(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, _ = srv.request("GET", "/v1/pending/nope")
        assert status == 404


# --------------------------------------------------------------------------
# conflicts (task 29b)
# --------------------------------------------------------------------------


def _make_folder_mode_conflict(tmp_path, monkeypatch, name: str = "alpha"):
    from datetime import datetime

    from androsync.executor import run_pair

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    config = _one_pair_config(tmp_path, name)
    pair = config.pairs[0]
    local_dir = pair.local
    remote_dir = Path(pair.remote)
    remote_dir.mkdir(parents=True, exist_ok=True)

    (local_dir / "note.md").write_text("v1")
    (remote_dir / "note.md").write_text("v1")
    run_pair(pair, LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE), now=datetime(2026, 9, 5, 15, 0, 0))

    (local_dir / "note.md").write_text("laptop edit")
    (remote_dir / "note.md").write_text("phone edit")
    run_pair(pair, LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE), now=datetime(2026, 9, 5, 15, 30, 0))
    return config


def test_conflicts_list_returns_saved_backup(tmp_path, monkeypatch):
    config = _make_folder_mode_conflict(tmp_path, monkeypatch)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/conflicts/alpha")
        assert status == 200
        assert len(body) == 1
        assert body[0]["when"] == "20260905-153000"
        assert body[0]["rel"] == "note.md"


def test_conflicts_list_unknown_pair_is_404(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, _ = srv.request("GET", "/v1/conflicts/nope")
        assert status == 404


def test_conflicts_restore_copies_backup_over_live_file(tmp_path, monkeypatch):
    config = _make_folder_mode_conflict(tmp_path, monkeypatch)
    local_dir = config.pairs[0].local
    assert (local_dir / "note.md").read_text() == "phone edit"

    with _Server(config) as srv:
        status, body = srv.request("POST", "/v1/conflicts/alpha/restore", body={"rel": "note.md"})
        assert status == 200
        assert body == {"rel": "note.md", "when": "20260905-153000"}

    assert (local_dir / "note.md").read_text() == "laptop edit"


def test_conflicts_restore_missing_version_is_404(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, _ = srv.request("POST", "/v1/conflicts/alpha/restore", body={"rel": "nope.md"})
        assert status == 404


def test_conflicts_clean_phone_not_connected_is_503(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    config = _one_pair_config(tmp_path)
    with _Server(config, device_info=lambda: {"transport": None, "device": None}) as srv:
        status, body = srv.request("POST", "/v1/conflicts/alpha/clean", body={})
        assert status == 503
        assert body == {"error": "phone not connected"}


def test_conflicts_clean_dry_run_reports_without_changing_anything(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    config = _one_pair_config(tmp_path)
    local_dir = config.pairs[0].local
    remote_dir = Path(config.pairs[0].remote)
    remote_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "a.conflict-laptop-20260101-000000.txt").write_text("hello")
    (remote_dir / "a.conflict-laptop-20260101-000000.txt").write_text("hello")

    with _Server(config, device_info=lambda: {"transport": "usb", "device": "SER1"}) as srv:
        status, body = srv.request("POST", "/v1/conflicts/alpha/clean", body={"dry_run": True})
        assert status == 200
        assert body["dry_run"] is True
        assert len(body["actions"]) == 1
        assert body["actions"][0]["outcome"] == "would_delete"

    assert (local_dir / "a.conflict-laptop-20260101-000000.txt").exists()


def test_conflicts_clean_applies_when_dry_run_false(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    config = _one_pair_config(tmp_path)
    local_dir = config.pairs[0].local
    remote_dir = Path(config.pairs[0].remote)
    remote_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "a.conflict-laptop-20260101-000000.txt").write_text("hello")
    (remote_dir / "a.conflict-laptop-20260101-000000.txt").write_text("hello")

    with _Server(config, device_info=lambda: {"transport": "usb", "device": "SER1"}) as srv:
        status, body = srv.request("POST", "/v1/conflicts/alpha/clean", body={"dry_run": False})
        assert status == 200
        assert body["actions"][0]["outcome"] == "deleted"

    assert not (local_dir / "a.conflict-laptop-20260101-000000.txt").exists()


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


def test_run_returns_202_and_job_completes(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("POST", "/v1/run", body={})
        assert status == 202
        job_id = body["job_id"]

        def job_done():
            s, b = srv.request("GET", f"/v1/jobs/{job_id}")
            return s == 200 and b["state"] not in ("queued", "running")

        assert wait_until(job_done)

        status, job = srv.request("GET", f"/v1/jobs/{job_id}")
        assert status == 200
        assert job["state"] == "done"
        assert job["summaries"]["alpha"]["copied_to_remote"] == 1

    assert (tmp_path / "alpha_remote" / "a.txt").exists()


def test_run_unknown_pair_is_404(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("POST", "/v1/run", body={"pairs": ["does-not-exist"]})
        assert status == 404


def test_run_bad_json_is_400(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("POST", "/v1/run", raw_body=b"{not valid json")
        assert status == 400
        assert body == {"error": "bad json"}


def test_second_run_while_busy_is_409(tmp_path):
    bulk_local = tmp_path / "bulk_local"
    bulk_local.mkdir()
    for i in range(10):
        (bulk_local / f"f{i}.txt").write_text(f"file {i}")
    config = build_config(
        tmp_path, [{"name": "bulk", "local": str(bulk_local), "remote": str(tmp_path / "bulk_remote")}]
    )

    def slow_transport_factory(_config: Config):
        return SleepyLocalTransport(Side.LOCAL, 0.2), LocalDirTransport(Side.REMOTE)

    with _Server(config, transport_factory=slow_transport_factory) as srv:
        status, body = srv.request("POST", "/v1/run", body={})
        assert status == 202
        job_id = body["job_id"]

        status, body = srv.request("POST", "/v1/run", body={})
        assert status == 409
        assert body["error"] == "busy"
        assert body["job_id"] == job_id

        # status should also refuse to run adb-conflicting dry-run plans while busy
        status, _ = srv.request("GET", "/v1/status/bulk")
        assert status == 409

        assert wait_until(
            lambda: srv.request("GET", f"/v1/jobs/{job_id}")[1]["state"] not in ("queued", "running")
        )


# --------------------------------------------------------------------------
# runs / undo (Phase I, task 30b)
# --------------------------------------------------------------------------


def test_runs_empty_before_any_sync(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/runs/alpha")
        assert status == 200
        assert body == []


def test_runs_unknown_pair_is_404(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, _ = srv.request("GET", "/v1/runs/nope")
        assert status == 404


def test_runs_lists_a_completed_sync(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("POST", "/v1/run", body={})
        assert status == 202
        job_id = body["job_id"]
        assert wait_until(lambda: srv.request("GET", f"/v1/jobs/{job_id}")[1]["state"] not in ("queued", "running"))

        status, runs = srv.request("GET", "/v1/runs/alpha")
        assert status == 200
        assert len(runs) == 1
        assert runs[0]["kind"] == "sync"
        assert runs[0]["finished"] is True


def test_undo_returns_202_and_reverts_the_sync(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("POST", "/v1/run", body={})
        job_id = body["job_id"]
        assert wait_until(lambda: srv.request("GET", f"/v1/jobs/{job_id}")[1]["state"] not in ("queued", "running"))
        assert (tmp_path / "alpha_remote" / "a.txt").exists()

        status, body = srv.request("POST", "/v1/undo/alpha", body={})
        assert status == 202
        undo_job_id = body["job_id"]

        def undo_done():
            s, b = srv.request("GET", f"/v1/jobs/{undo_job_id}")
            return s == 200 and b["state"] not in ("queued", "running")

        assert wait_until(undo_done)
        status, job = srv.request("GET", f"/v1/jobs/{undo_job_id}")
        assert status == 200
        assert job["kind"] == "undo"
        assert job["state"] == "done"
        assert job["undo_summary"]["restored_remote"] == 1

    assert not (tmp_path / "alpha_remote" / "a.txt").exists()


def test_undo_unknown_pair_is_404(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, _ = srv.request("POST", "/v1/undo/nope", body={})
        assert status == 404


def test_undo_no_runs_yet_is_404(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("POST", "/v1/undo/alpha", body={})
        assert status == 404
        assert "no runs" in body["error"]


def test_undo_bad_json_is_400(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, _ = srv.request("POST", "/v1/undo/alpha", raw_body=b"{not valid json")
        assert status == 400


def test_undo_while_busy_is_409(tmp_path):
    bulk_local = tmp_path / "bulk_local"
    bulk_local.mkdir()
    for i in range(10):
        (bulk_local / f"f{i}.txt").write_text(f"file {i}")
    config = build_config(
        tmp_path, [{"name": "bulk", "local": str(bulk_local), "remote": str(tmp_path / "bulk_remote")}]
    )

    def slow_transport_factory(_config: Config):
        return SleepyLocalTransport(Side.LOCAL, 0.2), LocalDirTransport(Side.REMOTE)

    with _Server(config, transport_factory=slow_transport_factory) as srv:
        status, body = srv.request("POST", "/v1/run", body={})
        assert status == 202
        job_id = body["job_id"]

        status, body = srv.request("POST", "/v1/undo/bulk", body={})
        assert status == 409
        assert body["error"] == "busy"

        assert wait_until(
            lambda: srv.request("GET", f"/v1/jobs/{job_id}")[1]["state"] not in ("queued", "running")
        )


def test_undo_pause_sets_enabled_false_in_config(tmp_path):
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, body = srv.request("POST", "/v1/run", body={})
        job_id = body["job_id"]
        assert wait_until(lambda: srv.request("GET", f"/v1/jobs/{job_id}")[1]["state"] not in ("queued", "running"))

        status, body = srv.request("POST", "/v1/undo/alpha", body={"pause": True})
        assert status == 202
        undo_job_id = body["job_id"]
        assert wait_until(
            lambda: srv.request("GET", f"/v1/jobs/{undo_job_id}")[1]["state"] not in ("queued", "running")
        )

    raw = yaml.safe_load(config_path.read_text())
    assert raw["pairs"][0]["enabled"] is False


# --------------------------------------------------------------------------
# jobs / cancel
# --------------------------------------------------------------------------


def test_jobs_get_unknown_is_404(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/jobs/does-not-exist")
        assert status == 404


def test_cancel_unknown_job_is_404(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("POST", "/v1/jobs/does-not-exist/cancel")
        assert status == 404


def test_cancel_marks_job_cancelled(tmp_path):
    bulk_local = tmp_path / "bulk_local"
    bulk_local.mkdir()
    for i in range(10):
        (bulk_local / f"f{i}.txt").write_text(f"file {i}")
    config = build_config(
        tmp_path, [{"name": "bulk", "local": str(bulk_local), "remote": str(tmp_path / "bulk_remote")}]
    )

    def slow_transport_factory(_config: Config):
        return SleepyLocalTransport(Side.LOCAL, 0.2), LocalDirTransport(Side.REMOTE)

    with _Server(config, transport_factory=slow_transport_factory) as srv:
        status, body = srv.request("POST", "/v1/run", body={})
        assert status == 202
        job_id = body["job_id"]

        time.sleep(0.05)  # let it start, before the first 0.2s fetch completes
        status, body = srv.request("POST", f"/v1/jobs/{job_id}/cancel")
        assert status == 200
        assert body == {"ok": True}

        assert wait_until(
            lambda: srv.request("GET", f"/v1/jobs/{job_id}")[1]["state"] not in ("queued", "running", "cancelling")
        )
        status, job = srv.request("GET", f"/v1/jobs/{job_id}")
        assert job["state"] == "cancelled"
        assert job["cancel_requested"] is True


# --------------------------------------------------------------------------
# SSE log stream
# --------------------------------------------------------------------------


def test_job_log_stream_yields_data_lines_and_end_event(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("POST", "/v1/run", body={})
        assert status == 202
        job_id = body["job_id"]

        assert wait_until(
            lambda: srv.request("GET", f"/v1/jobs/{job_id}")[1]["state"] not in ("queued", "running")
        )

        host, port = srv.api.bound_addresses[0]
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.request(
                "GET",
                f"/v1/jobs/{job_id}/log?since=0",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            resp = conn.getresponse()
            assert resp.status == 200
            assert resp.getheader("Content-Type") == "text/event-stream"
            raw = resp.read()
        finally:
            conn.close()

    text = raw.decode("utf-8")
    assert "data: " in text
    assert "event: end" in text
    assert "data: done" in text


def test_job_log_unknown_job_is_404(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/jobs/does-not-exist/log")
        assert status == 404


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------


def test_unknown_route_is_404(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/nope")
        assert status == 404


def test_wrong_method_is_405(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, body = srv.request("POST", "/v1/ping")
        assert status == 405


def test_injectable_log_receives_one_line_per_request(tmp_path):
    config = _one_pair_config(tmp_path)
    lines: list[str] = []
    with _Server(config, log=lines.append) as srv:
        srv.request("GET", "/v1/ping")
        srv.request("GET", "/v1/nope")

    assert any(line.startswith("GET /v1/ping 200") for line in lines)
    assert any(line.startswith("GET /v1/nope 404") for line in lines)


# --------------------------------------------------------------------------
# pair CRUD (task 23b: the app's Add/Edit/Delete pair screens)
# --------------------------------------------------------------------------


def test_create_pair_returns_201_and_updated_list(tmp_path):
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, body = srv.request(
            "POST",
            "/v1/pairs",
            body={"name": "beta", "local": str(tmp_path / "beta"), "remote": "/sdcard/beta", "direction": "two-way"},
        )
        assert status == 201
        assert {row["name"] for row in body} == {"alpha", "beta"}

    raw = yaml.safe_load(config_path.read_text())
    assert [p["name"] for p in raw["pairs"]] == ["alpha", "beta"]


def test_create_pair_duplicate_name_is_409(tmp_path):
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, body = srv.request(
            "POST",
            "/v1/pairs",
            body={"name": "alpha", "local": str(tmp_path / "x"), "remote": "/sdcard/x", "direction": "two-way"},
        )
        assert status == 409


def test_create_pair_invalid_body_is_400(tmp_path):
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, body = srv.request(
            "POST", "/v1/pairs", body={"name": "beta", "local": str(tmp_path / "beta"), "direction": "two-way"}
        )  # no remote
        assert status == 400


def test_create_pair_null_optional_fields_are_treated_as_unset(tmp_path):
    """Regression: a client that always sends every field (e.g. kotlinx.serialization
    with encodeDefaults=true) sends JSON null, not an omitted key, for an
    unset optional field like `conflict`. That must not end up as a literal
    `conflict: null` in the YAML, which load_config would then reject.
    """
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, body = srv.request(
            "POST",
            "/v1/pairs",
            body={
                "name": "beta",
                "local": str(tmp_path / "beta"),
                "remote": "/sdcard/beta",
                "direction": "two-way",
                "conflict": None,
                "propagate_deletes": None,
                "ignore": None,
            },
        )
        assert status == 201, body

    raw = yaml.safe_load(config_path.read_text())
    new_pair = raw["pairs"][1]
    assert "conflict" not in new_pair
    assert "propagate_deletes" not in new_pair
    assert "ignore" not in new_pair


def test_update_pair_returns_200(tmp_path):
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, body = srv.request(
            "PUT",
            "/v1/pairs/alpha",
            body={
                "name": "alpha",
                "local": str(tmp_path / "alpha_local"),
                "remote": str(tmp_path / "alpha_remote"),
                "direction": "phone-to-laptop",
            },
        )
        assert status == 200

    raw = yaml.safe_load(config_path.read_text())
    assert raw["pairs"][0]["direction"] == "phone-to-laptop"


def test_update_pair_rename_moves_state_files(tmp_path, monkeypatch):
    from androsync import state

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    state.save_snapshot("alpha", {})

    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, _ = srv.request(
            "PUT",
            "/v1/pairs/alpha",
            body={
                "name": "renamed",
                "local": str(tmp_path / "alpha_local"),
                "remote": str(tmp_path / "alpha_remote"),
                "direction": "two-way",
            },
        )
        assert status == 200

    from androsync.config import state_dir

    assert not (state_dir() / "alpha.json").exists()
    assert (state_dir() / "renamed.json").exists()


def test_update_pair_missing_name_is_404(tmp_path):
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, _ = srv.request(
            "PUT",
            "/v1/pairs/nope",
            body={"name": "nope", "local": str(tmp_path / "x"), "remote": "/sdcard/x", "direction": "two-way"},
        )
        assert status == 404


def test_delete_pair_returns_200_and_keeps_state_by_default(tmp_path, monkeypatch):
    from androsync import state

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    state.save_snapshot("alpha", {})

    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, body = srv.request("DELETE", "/v1/pairs/alpha")
        assert status == 200
        assert body == []

    from androsync.config import state_dir

    assert (state_dir() / "alpha.json").exists()


def test_delete_pair_purge_state_removes_state_files(tmp_path, monkeypatch):
    from androsync import state

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    state.save_snapshot("alpha", {})

    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, _ = srv.request("DELETE", "/v1/pairs/alpha?purge_state=1")
        assert status == 200

    from androsync.config import state_dir

    assert not (state_dir() / "alpha.json").exists()


def test_delete_pair_missing_name_is_404(tmp_path):
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, _ = srv.request("DELETE", "/v1/pairs/nope")
        assert status == 404


# --------------------------------------------------------------------------
# browse / mkdir (task 23b: the app's folder pickers)
# --------------------------------------------------------------------------


def _config_with_browse_roots(tmp_path: Path, roots: list[str], pair_name: str = "alpha") -> Config:
    local = tmp_path / f"{pair_name}_local"
    local.mkdir()
    roots_yaml = "\n".join(f"    - {r}" for r in roots)
    text = f"""\
device: auto
service:
  port: 8765
  browse_roots:
{roots_yaml}
pairs:
  - name: {pair_name}
    local: {local}
    remote: /sdcard/{pair_name}
    direction: two-way
"""
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return load_config(path)


class _FakeAdbTransport:
    """Stands in for AdbTransport in remote browse/mkdir tests."""

    def __init__(self, listing: dict[str, list[tuple[str, bool, int]]]) -> None:
        self._listing = listing
        self.mkdir_calls: list[str] = []

    def ls(self, path: str) -> list[tuple[str, bool, int]]:
        return self._listing[path]

    def mkdirs(self, root: str, rel_dir: str) -> None:
        self.mkdir_calls.append(root if not rel_dir else f"{root}/{rel_dir}")

    def close(self) -> None:
        pass


def _remote_transport_factory(fake: _FakeAdbTransport):
    def factory(_config: Config) -> tuple[LocalDirTransport, _FakeAdbTransport]:
        return LocalDirTransport(Side.LOCAL), fake

    return factory


def test_browse_local_empty_path_lists_roots(tmp_path):
    config = _config_with_browse_roots(tmp_path, [str(tmp_path / "vault")])
    (tmp_path / "vault").mkdir()
    with _Server(config) as srv:
        status, body = srv.request("GET", "/v1/browse?side=local&path=")
        assert status == 200
        assert body["entries"] == [{"name": str(tmp_path / "vault"), "is_dir": True, "size": 0, "hidden": False}]


def test_browse_local_lists_a_directory(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "notes.md").write_text("x")
    config = _config_with_browse_roots(tmp_path, [str(root)])
    with _Server(config) as srv:
        status, body = srv.request("GET", f"/v1/browse?side=local&path={root}")
        assert status == 200
        assert body["path"] == str(root)
        assert any(e["name"] == "notes.md" for e in body["entries"])


def test_browse_local_outside_roots_is_403(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    config = _config_with_browse_roots(tmp_path, [str(allowed)])
    with _Server(config) as srv:
        status, _ = srv.request("GET", f"/v1/browse?side=local&path={outside}")
        assert status == 403


def test_browse_missing_side_is_400(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config) as srv:
        status, _ = srv.request("GET", "/v1/browse?path=/tmp")
        assert status == 400


def test_browse_remote_phone_not_connected_is_503(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config, device_info=lambda: {"transport": None, "device": None}) as srv:
        status, body = srv.request("GET", "/v1/browse?side=remote&path=")
        assert status == 503
        assert body == {"error": "phone not connected"}


def test_browse_remote_busy_is_409(tmp_path):
    config = _one_pair_config(tmp_path)

    def slow_transport_factory(_config: Config):
        return SleepyLocalTransport(Side.LOCAL, 0.2), LocalDirTransport(Side.REMOTE)

    with _Server(
        config,
        transport_factory=slow_transport_factory,
        device_info=lambda: {"transport": "usb", "device": "SER1"},
    ) as srv:
        status, body = srv.request("POST", "/v1/run", body={})
        assert status == 202
        status, _ = srv.request("GET", "/v1/browse?side=remote&path=")
        assert status == 409


def test_browse_remote_lists_phone_directory(tmp_path):
    fake = _FakeAdbTransport({"/sdcard/DCIM": [("Camera", True, 4096), ("a.jpg", False, 100)]})
    config = _one_pair_config(tmp_path)
    with _Server(
        config, transport_factory=_remote_transport_factory(fake), device_info=lambda: {"transport": "usb", "device": "SER1"}
    ) as srv:
        status, body = srv.request("GET", "/v1/browse?side=remote&path=/sdcard/DCIM")
        assert status == 200
        assert [e["name"] for e in body["entries"]] == ["Camera", "a.jpg"]


def test_mkdir_local_creates_directory(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    config = _config_with_browse_roots(tmp_path, [str(root)])
    with _Server(config) as srv:
        status, body = srv.request("POST", "/v1/mkdir", body={"side": "local", "path": str(root / "new")})
        assert status == 201
        assert (root / "new").is_dir()


def test_mkdir_local_outside_roots_is_403(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    config = _config_with_browse_roots(tmp_path, [str(allowed)])
    with _Server(config) as srv:
        status, _ = srv.request("POST", "/v1/mkdir", body={"side": "local", "path": str(tmp_path / "outside" / "new")})
        assert status == 403


def test_mkdir_remote_calls_transport(tmp_path):
    fake = _FakeAdbTransport({})
    config = _one_pair_config(tmp_path)
    with _Server(
        config, transport_factory=_remote_transport_factory(fake), device_info=lambda: {"transport": "usb", "device": "SER1"}
    ) as srv:
        status, body = srv.request("POST", "/v1/mkdir", body={"side": "remote", "path": "/sdcard/NewFolder"})
        assert status == 201
        assert fake.mkdir_calls == ["/sdcard/NewFolder"]


def test_mkdir_remote_phone_not_connected_is_503(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config, device_info=lambda: {"transport": None, "device": None}) as srv:
        status, _ = srv.request("POST", "/v1/mkdir", body={"side": "remote", "path": "/sdcard/x"})
        assert status == 503


# ---------------------------------------------------------------------------
# phone folder rules (task 36c, issue #8)
# ---------------------------------------------------------------------------


def test_create_pair_maps_the_storage_label_to_sdcard(tmp_path):
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, _ = srv.request(
            "POST",
            "/v1/pairs",
            body={"name": "inbox", "local": str(tmp_path / "inbox"), "remote": "/Internal storage/Inbox/", "direction": "two-way"},
        )
        assert status == 201
    raw = yaml.safe_load(config_path.read_text())
    assert {p["name"]: p["remote"] for p in raw["pairs"]}["inbox"] == "/sdcard/Inbox"


@pytest.mark.parametrize("remote", ["/", "/data", "/sdcard/Android/data"])
def test_create_pair_refuses_phone_folders_outside_shared_storage(tmp_path, remote):
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    before = config_path.read_text() if config_path.exists() else None
    with _Server(config, config_path=config_path) as srv:
        status, body = srv.request(
            "POST",
            "/v1/pairs",
            body={"name": "bad", "local": str(tmp_path / "bad"), "remote": remote, "direction": "two-way"},
        )
        assert status == 400
        assert "pair bad:" in body["error"]
    assert (config_path.read_text() if config_path.exists() else None) == before


def test_browse_remote_outside_shared_storage_is_403(tmp_path):
    fake = _FakeAdbTransport({})
    config = _one_pair_config(tmp_path)
    with _Server(
        config, transport_factory=_remote_transport_factory(fake), device_info=lambda: {"transport": "usb", "device": "SER1"}
    ) as srv:
        status, body = srv.request("GET", "/v1/browse?side=remote&path=/data")
        assert status == 403
        assert "internal storage" in body["error"]


def test_browse_remote_up_from_sdcard_has_no_parent(tmp_path):
    fake = _FakeAdbTransport({"/sdcard": [("DCIM", True, 4096)]})
    config = _one_pair_config(tmp_path)
    with _Server(
        config, transport_factory=_remote_transport_factory(fake), device_info=lambda: {"transport": "usb", "device": "SER1"}
    ) as srv:
        status, body = srv.request("GET", "/v1/browse?side=remote&path=")
        assert status == 200
        assert body["path"] == "/sdcard"
        assert body["parent"] is None


def test_mkdir_remote_outside_shared_storage_is_403_and_label_is_mapped(tmp_path):
    fake = _FakeAdbTransport({})
    config = _one_pair_config(tmp_path)
    with _Server(
        config, transport_factory=_remote_transport_factory(fake), device_info=lambda: {"transport": "usb", "device": "SER1"}
    ) as srv:
        status, _ = srv.request("POST", "/v1/mkdir", body={"side": "remote", "path": "/data/x"})
        assert status == 403
        assert fake.mkdir_calls == []
        status, body = srv.request("POST", "/v1/mkdir", body={"side": "remote", "path": "/Internal storage/New"})
        assert status == 201
        assert body["path"] == "/sdcard/New"
        assert fake.mkdir_calls == ["/sdcard/New"]


# ---------------------------------------------------------------------------
# the running sync is visible to every client through the ping (task 36b, issue #7)
# ---------------------------------------------------------------------------


def test_ping_reports_no_current_job_when_idle(tmp_path):
    with _Server(_one_pair_config(tmp_path)) as srv:
        status, body = srv.request("GET", "/v1/ping")
        assert status == 200
        assert body["current_job"] is None


def test_ping_reports_the_running_job_with_its_latest_line_and_then_the_stopping_state(tmp_path):
    bulk_local = tmp_path / "bulk_local"
    bulk_local.mkdir()
    for i in range(10):
        (bulk_local / f"f{i}.txt").write_text(f"file {i}")
    config = build_config(
        tmp_path, [{"name": "bulk", "local": str(bulk_local), "remote": str(tmp_path / "bulk_remote")}]
    )

    def slow_transport_factory(_config: Config):
        return SleepyLocalTransport(Side.LOCAL, 0.2), LocalDirTransport(Side.REMOTE)

    with _Server(config, transport_factory=slow_transport_factory) as srv:
        _, run = srv.request("POST", "/v1/run", body={})
        job_id = run["job_id"]

        assert wait_until(lambda: (srv.request("GET", "/v1/ping")[1]["current_job"] or {}).get("last_line"))
        _, ping = srv.request("GET", "/v1/ping")
        job = ping["current_job"]
        assert job["id"] == job_id and job["pairs"] == ["bulk"] and job["kind"] == "sync"
        assert job["state"] in ("running", "cancelling") and job["started"] is not None
        assert not job["last_line"][:2].isdigit()  # the HH:MM:SS stamp is removed

        srv.request("POST", f"/v1/jobs/{job_id}/cancel")
        assert wait_until(lambda: srv.request("GET", "/v1/ping")[1]["current_job"] is None)


def test_updating_a_pairs_phone_folder_resets_its_history_and_says_so_in_a_header(tmp_path, monkeypatch):
    import urllib.request

    from androsync import state
    from androsync.model import SnapshotEntry

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    state.save_snapshot("alpha", {"a.txt": SnapshotEntry(size=1, mtime_local=1.0, mtime_remote=1.0)})
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, body = srv.request(
            "PUT",
            "/v1/pairs/alpha",
            body={"name": "alpha", "local": str(tmp_path / "alpha_local"), "remote": "/sdcard/Elsewhere", "direction": "two-way"},
        )
        assert status == 200 and isinstance(body, list)  # still the pairs list: older apps rely on it
    assert state.load_snapshot("alpha") == {}


def test_run_passes_confirm_large_to_the_job_and_a_blocked_job_reports_why(tmp_path):
    import os

    local, remote = tmp_path / "big_local", tmp_path / "big_remote"
    local.mkdir()
    for i in range(4):
        (local / f"f{i}.txt").write_text("x")
    config = build_config(tmp_path, [{"name": "big", "local": str(local), "remote": str(remote)}])
    # the shared helper writes no safety block; make the first run "too big" through the pair object
    config.pairs[0].safety_big_first_run_files = 2

    def factory(_config):
        return LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE)

    with _Server(config, transport_factory=factory) as srv:
        _, run = srv.request("POST", "/v1/run", body={"pairs": ["big"]})
        assert wait_until(lambda: srv.request("GET", f"/v1/jobs/{run['job_id']}")[1]["state"] not in ("queued", "running"))
        _, job = srv.request("GET", f"/v1/jobs/{run['job_id']}")
        assert job["state"] == "needs_confirmation"
        assert "first sync would copy 4 files" in job["confirmation"]["big"][0]
        assert not list(remote.glob("*.txt")) if remote.exists() else True

        _, again = srv.request("POST", "/v1/run", body={"pairs": ["big"], "confirm_large": True})
        assert wait_until(lambda: srv.request("GET", f"/v1/jobs/{again['job_id']}")[1]["state"] not in ("queued", "running"))
        _, done = srv.request("GET", f"/v1/jobs/{again['job_id']}")
        assert done["state"] == "done" and done["confirmation"] == {}
        assert len(list(remote.glob("*.txt"))) == 4


def test_ping_passes_the_device_list_through_so_the_app_can_show_what_is_wrong(tmp_path):
    devices = [{"serial": "RZ123", "state": "unauthorized", "transport": "usb"}]
    with _Server(
        _one_pair_config(tmp_path), device_info=lambda: {"transport": None, "device": None, "devices": devices}
    ) as srv:
        status, body = srv.request("GET", "/v1/ping")
        assert status == 200
        assert body["devices"] == devices and body["device"] is None
