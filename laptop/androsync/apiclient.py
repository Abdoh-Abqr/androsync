"""Tiny urllib-based client for the daemon's own HTTP API (task 31b).

The GUI's Pairs panel uses this instead of `run_pair`/`undo_run` directly
whenever a daemon is confirmed reachable (`service_state.ping` answered) -
so a sync started from the GUI shows up as a job the phone app can see too,
same as one started from the app itself. When nothing answers, the panel
falls back to calling the sync engine in-process (see `gui/backend.py`).
Deliberately not the Android app's Retrofit/OkHttp setup: a handful of
JSON calls over the stdlib is all a desktop client needs.
"""

from __future__ import annotations

import json
from typing import Iterator
import urllib.error
import urllib.request
from urllib.parse import urlencode


class ApiError(Exception):
    """An HTTP error response, with the daemon's own ``{"error": ...}`` message when present."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class ApiClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _request(
        self, method: str, path: str, *, body: dict | None = None, query: dict | None = None
    ) -> dict | list:
        url = f"{self._base_url}{path}"
        if query:
            filtered = {k: v for k, v in query.items() if v is not None}
            if filtered:
                url += "?" + urlencode(filtered)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            message = raw.decode("utf-8", "replace")
            try:
                message = json.loads(message).get("error", message)
            except (ValueError, AttributeError):
                pass
            raise ApiError(exc.code, message) from exc

    def ping(self) -> dict:
        return self._request("GET", "/v1/ping")

    def rotate_token(self) -> str:
        """Ask the running service for a new token (it saves it and stops accepting the old one)."""
        return self._request("POST", "/v1/token/rotate", body={})["token"]

    def pairs(self) -> list[dict]:
        return self._request("GET", "/v1/pairs")

    def status(self, pair: str) -> dict:
        return self._request("GET", f"/v1/status/{pair}")

    def run(
        self,
        pair: str | None = None,
        *,
        dry_run: bool = False,
        confirm_large: bool = False,
        pairs: list[str] | None = None,
    ) -> dict:
        """``pair=None`` and ``pairs=None`` run every (non-paused) configured pair.

        ``confirm_large=True`` lets through a plan the daemon stopped for confirmation (``needs_confirmation``).
        """
        body: dict = {"dry_run": dry_run}
        if confirm_large:
            body["confirm_large"] = True
        if pairs is not None:
            body["pairs"] = list(pairs)
        elif pair is not None:
            body["pairs"] = [pair]
        return self._request("POST", "/v1/run", body=body)

    def job(self, job_id: str) -> dict:
        return self._request("GET", f"/v1/jobs/{job_id}")

    def stream_job_log(self, job_id: str, since: int = 0) -> Iterator[tuple[str, str]]:
        """Follow ``GET /v1/jobs/<id>/log`` (server-sent events) until the job ends.

        Yields ``("line", text)`` for every log line from sequence number ``since`` on (the full log of a job that
        is already running), then ``("end", state)``. The server sends a keep-alive comment every 15 s, so a read
        timeout of 40 s means the connection is dead, not just quiet.
        """
        url = f"{self._base_url}/v1/jobs/{job_id}/log?since={since}"
        request = urllib.request.Request(url)
        request.add_header("Authorization", f"Bearer {self._token}")
        try:
            response = urllib.request.urlopen(request, timeout=40)
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, exc.reason or "log stream failed") from exc
        event = "message"
        data: list[str] = []
        with response:
            for raw in response:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line == "":
                    if data:
                        payload = "\n".join(data)
                        if event == "end":
                            yield ("end", payload)
                            return
                        yield ("line", payload)
                    event, data = "message", []
                elif line.startswith(":"):
                    continue  # keep-alive comment
                elif line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip(" ") if line[5:6] == " " else line[5:])

    def cancel(self, job_id: str) -> dict:
        return self._request("POST", f"/v1/jobs/{job_id}/cancel")

    def runs(self, pair: str) -> list[dict]:
        return self._request("GET", f"/v1/runs/{pair}")

    def undo(
        self,
        pair: str,
        *,
        run_id: str | None = None,
        pause: bool = False,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict:
        body: dict = {"pause": pause, "force": force, "dry_run": dry_run}
        if run_id is not None:
            body["run_id"] = run_id
        return self._request("POST", f"/v1/undo/{pair}", body=body)

    def conflicts(self, pair: str) -> list[dict]:
        return self._request("GET", f"/v1/conflicts/{pair}")
