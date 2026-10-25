"""The window's backend: following any daemon job, Stop, and the failed-job bug (task 36b, issues #7 and #27)."""

from __future__ import annotations

import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from androsync.apiclient import ApiClient, ApiError
from androsync.gui.backend import TERMINAL_JOB_STATES, Backend


def drain(backend: Backend) -> list[tuple]:
    out = []
    while True:
        try:
            out.append(backend.events.get_nowait())
        except queue.Empty:
            return out


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class FakeClient:
    """Stands in for ApiClient: scripted log lines, then a final job dict."""

    def __init__(self, lines, final_job, stream_error: Exception | None = None):
        self.lines = lines
        self.final_job = final_job
        self.stream_error = stream_error
        self.cancelled: list[str] = []
        self.jobs_served = 0

    def stream_job_log(self, job_id, since=0):
        if self.stream_error is not None:
            raise self.stream_error
        for line in self.lines:
            yield ("line", line)
        yield ("end", self.final_job["state"])

    def job(self, job_id):
        self.jobs_served += 1
        return dict(self.final_job, log_tail=self.lines)

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        return {"ok": True}


def test_terminal_states_include_failed():
    assert "failed" in TERMINAL_JOB_STATES  # issue #27: the window used to wait forever on a failed job


def test_follow_shows_the_full_log_of_a_job_started_elsewhere_and_finishes():
    backend = Backend()
    client = FakeClient(["scanning", "batch 1", "done copying"], {"id": "j1", "state": "done", "error": None})

    backend.follow(client, "j1")
    assert wait_until(lambda: not backend.busy)

    events = drain(backend)
    assert [e[1] for e in events if e[0] == "log"] == ["scanning", "batch 1", "done copying"]
    done = [e for e in events if e[0] == "done"]
    assert done == [("done", True, "done")]
    assert backend.following_job_id is None


def test_a_failed_job_ends_the_follow_and_frees_the_window_issue_27():
    backend = Backend()
    client = FakeClient(["ERROR vault: no device"], {"id": "j2", "state": "failed", "error": "no device found"})

    backend.follow(client, "j2")
    assert wait_until(lambda: not backend.busy, timeout=5)  # before the fix this never ended

    done = [e for e in drain(backend) if e[0] == "done"]
    assert done == [("done", False, "no device found")]


def test_a_cancelled_job_is_reported_as_cancelled():
    backend = Backend()
    client = FakeClient(["cancelled"], {"id": "j3", "state": "cancelled", "error": None})
    backend.follow(client, "j3")
    assert wait_until(lambda: not backend.busy)
    assert [e for e in drain(backend) if e[0] == "done"] == [("done", False, "cancelled")]


def test_if_the_stream_breaks_the_backend_falls_back_to_polling():
    backend = Backend()
    client = FakeClient(["a", "b"], {"id": "j4", "state": "done", "error": None}, stream_error=OSError("reset"))
    backend.follow(client, "j4")
    assert wait_until(lambda: not backend.busy)
    assert [e[1] for e in drain(backend) if e[0] == "log"] == ["a", "b"]


def test_follow_does_nothing_while_another_action_runs():
    backend = Backend()
    gate = threading.Event()
    backend._start(gate.wait)  # an action in flight
    client = FakeClient(["x"], {"id": "j5", "state": "done"})
    backend.follow(client, "j5")
    assert client.jobs_served == 0
    gate.set()


def test_stop_asks_the_daemon_to_cancel_the_job_without_blocking():
    backend = Backend()
    client = FakeClient([], {"id": "j6", "state": "running"})
    backend.stop(client, "j6")
    assert wait_until(lambda: client.cancelled == ["j6"])
    assert ("log", "stopping...") in drain(backend)


def test_stop_without_a_client_or_job_does_nothing():
    backend = Backend()
    backend.stop(None, None)
    backend.stop(FakeClient([], {"state": "running"}), None)
    assert drain(backend) == []


def test_stop_ends_a_sync_running_in_this_process_and_aborts_its_transports(monkeypatch, tmp_path):
    """No daemon: the window runs the engine itself, and Stop must reach it (flag plus abort)."""
    import androsync.gui.backend as backend_module
    from androsync.config import load_config
    from androsync.model import Side
    from androsync.transport.base import TransportCancelled
    from androsync.transport.local import LocalDirTransport

    local_dir = tmp_path / "l"
    local_dir.mkdir()
    (local_dir / "a.txt").write_text("a")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"pairs:\n  - {{name: p, local: {local_dir}, remote: {tmp_path / 'r'}, direction: two-way}}\n")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    class Blocking(LocalDirTransport):
        def __init__(self, side):
            super().__init__(side)
            self.aborted = threading.Event()
            self.in_fetch = threading.Event()

        def abort(self):
            self.aborted.set()

        def fetch(self, root, rel, tmp_path):
            self.in_fetch.set()
            if self.aborted.wait(10):
                raise TransportCancelled("cancelled")
            super().fetch(root, rel, tmp_path)

    blocking = Blocking(Side.LOCAL)
    monkeypatch.setattr(backend_module, "open_pair_transports", lambda config, log=None: (blocking, LocalDirTransport(Side.REMOTE)))

    backend = Backend()
    backend._start(lambda: backend._run_direct(load_config(cfg), None, False))
    assert blocking.in_fetch.wait(5)
    assert backend.running_directly is True

    backend.stop(None, None)
    assert wait_until(lambda: not backend.busy, timeout=5)
    events = drain(backend)
    assert ("log", "stopping...") in events
    assert ("done", False, "cancelled") in events


# ---- the SSE client against a real local HTTP server ---------------------------------------------------------


class _SseHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer tok":
            self.send_response(401)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        body = (
            "id: 0\ndata: 12:00:01 scanning\n\n"
            ": ping\n\n"
            "id: 1\ndata: 12:00:02 batch 1: 2 files, 1.0 MB\n\n"
            "event: end\ndata: cancelled\n\n"
        )
        self.wfile.write(body.encode())


@pytest.fixture()
def sse_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_stream_job_log_yields_lines_skips_keepalives_and_ends_with_the_state(sse_server):
    events = list(ApiClient(sse_server, "tok").stream_job_log("j1"))
    assert events == [
        ("line", "12:00:01 scanning"),
        ("line", "12:00:02 batch 1: 2 files, 1.0 MB"),
        ("end", "cancelled"),
    ]


def test_stream_job_log_raises_api_error_for_a_bad_token(sse_server):
    with pytest.raises(ApiError) as info:
        list(ApiClient(sse_server, "wrong").stream_job_log("j1"))
    assert info.value.status == 401


# ---- a run stopped for confirmation (task 36d, issue #9) ----------------------------------------------------


def test_a_job_that_needs_confirmation_asks_the_window_to_offer_go_ahead():
    backend = Backend()
    job = {
        "id": "j7",
        "state": "needs_confirmation",
        "error": None,
        "confirmation": {"vault": ["It would delete 785 files on the laptop (100% of the 785 files this pair knows)."]},
    }
    client = FakeClient(["STOPPED, nothing was changed."], job)

    backend.follow(client, "j7")
    assert wait_until(lambda: not backend.busy)

    events = drain(backend)
    confirm = [e for e in events if e[0] == "confirm"]
    assert confirm == [("confirm", ["vault"], ["vault: It would delete 785 files on the laptop (100% of the 785 files this pair knows)."])]
    assert [e for e in events if e[0] == "done"] == [("done", False, "needs_confirmation")]


def test_needs_confirmation_is_a_final_state_for_the_window():
    assert "needs_confirmation" in TERMINAL_JOB_STATES


def test_a_direct_run_that_is_stopped_offers_go_ahead_and_confirming_runs_it(monkeypatch, tmp_path):
    import os

    import androsync.gui.backend as backend_module
    from androsync.config import load_config
    from androsync.model import Side
    from androsync.transport.local import LocalDirTransport

    local, remote = tmp_path / "l", tmp_path / "r"
    local.mkdir()
    for i in range(4):
        (local / f"f{i}.txt").write_text("x")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"pairs:\n  - {{name: p, local: {local}, remote: {remote}, direction: two-way, safety: {{big_first_run_files: 2}}}}\n"
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setattr(
        backend_module, "open_pair_transports", lambda config, log=None: (LocalDirTransport(Side.LOCAL), LocalDirTransport(Side.REMOTE))
    )
    config = load_config(cfg)

    backend = Backend()
    backend._start(lambda: backend._run_direct(config, None, False))
    assert wait_until(lambda: not backend.busy)
    events = drain(backend)
    assert [e for e in events if e[0] == "confirm"][0][1] == ["p"]
    assert not remote.exists() or not list(remote.glob("*.txt"))  # nothing was copied

    backend._start(lambda: backend._run_direct(config, None, False, True, ["p"]))
    assert wait_until(lambda: not backend.busy)
    assert len(list(remote.glob("*.txt"))) == 4
    assert not [e for e in drain(backend) if e[0] == "confirm"]
