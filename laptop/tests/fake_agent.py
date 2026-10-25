"""A stand-in for the phone app in agent mode (task 37b, issue #14).

It speaks the real protocol over real HTTP to a real ``ApiServer`` - long-poll ``GET /v1/agent/next``, streamed
``GET``/``PUT /v1/agent/blob/<id>``, ``POST /v1/agent/result/<id>`` - and does the file work with a
``LocalDirTransport``, exactly as the Kotlin agent will do it with ``java.io.File``. Not named ``test_*`` so pytest
does not collect it.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

from androsync.ignore import IgnoreRules
from androsync.model import Side
from androsync.transport.local import LocalDirTransport


class FakeAgent:
    def __init__(self, base_url: str, token: str, *, poll_wait: float = 0.3) -> None:
        self.base_url = base_url
        self.token = token
        self.poll_wait = poll_wait
        self.local = LocalDirTransport(Side.REMOTE)
        self.log: list[str] = []  # operation names, in the order the agent ran them
        self.fail: dict[str, str] = {}  # op name -> error message to answer with
        self.hold = threading.Event()  # set = run normally; clear = take ops but sit on them
        self.hold.set()
        self.silent = threading.Event()  # set = stop polling entirely (simulates the app going away)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="fake-agent", daemon=True)

    # -- lifecycle ---------------------------------------------------------------------------------------------

    def start(self) -> "FakeAgent":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    # -- HTTP ----------------------------------------------------------------------------------------------------

    def _request(self, method: str, path: str, data=None, headers: dict[str, str] | None = None, timeout: float = 30.0):
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        return urllib.request.urlopen(req, timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.silent.is_set():
                self._stop.wait(0.05)
                continue
            try:
                with self._request("GET", f"/v1/agent/next?wait={self.poll_wait}") as resp:
                    if resp.status == 204:
                        continue
                    op = json.load(resp)
            except (urllib.error.URLError, OSError):
                self._stop.wait(0.05)
                continue
            self._run(op)

    def _post_result(self, op_id: str, ok: bool, result=None, error: str | None = None) -> None:
        body = json.dumps({"ok": ok, "result": result, "error": error}).encode()
        try:
            self._request("POST", f"/v1/agent/result/{op_id}", data=body, headers={"Content-Type": "application/json"}).close()
        except urllib.error.HTTPError:
            pass  # 404/410: the laptop gave up on this operation

    # -- operations ----------------------------------------------------------------------------------------------

    def _run(self, op: dict) -> None:
        name, args, op_id = op["op"], op["args"], op["id"]
        self.log.append(name)
        self.hold.wait()
        if name in self.fail:
            self._post_result(op_id, False, error=self.fail[name])
            return
        try:
            result = getattr(self, f"_op_{name}")(op_id, op, args)
        except Exception as exc:  # noqa: BLE001 - reported to the laptop like the real agent would
            self._post_result(op_id, False, error=f"{type(exc).__name__}: {exc}")
            return
        self._post_result(op_id, True, result)

    def _op_scan(self, op_id, op, args):
        root = args["root"]
        if not os.path.isdir(root):
            return {"exists": False, "files": []}
        snap = self.local.scan(root, IgnoreRules([]))
        return {"exists": True, "files": [[rel, e.size, e.mtime] for rel, e in snap.items()]}

    def _op_fetch(self, op_id, op, args):
        src = Path(args["root"]) / args["rel"]
        st = src.stat()
        with open(src, "rb") as handle:
            self._request(
                "PUT",
                f"/v1/agent/blob/{op_id}",
                data=handle,
                headers={"Content-Length": str(st.st_size), "Content-Type": "application/octet-stream"},
            ).close()
        return {"size": st.st_size, "mtime": st.st_mtime}

    def _op_store(self, op_id, op, args):
        with tempfile.TemporaryDirectory(prefix="fake-agent-") as tmp:
            part = Path(tmp) / "blob"
            with self._request("GET", f"/v1/agent/blob/{op_id}") as resp, open(part, "wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
            if part.stat().st_size != args["size"]:
                raise OSError(f"got {part.stat().st_size} bytes, expected {args['size']}")
            self.local.store(part, args["root"], args["rel"], args["mtime"])
        return {}

    def _op_hash_many(self, op_id, op, args):
        return {"hashes": self.local.hash_many(args["root"], args["rels"])}

    def _op_delete(self, op_id, op, args):
        self.local.delete(args["root"], args["rel"])
        return {}

    def _op_rename(self, op_id, op, args):
        self.local.rename(args["root"], args["from"], args["to"])
        return {}

    def _op_mkdirs(self, op_id, op, args):
        self.local.mkdirs(args["root"], args["rel_dir"])
        return {}

    def _op_exists(self, op_id, op, args):
        return {"exists": self.local.exists(args["root"])}

    def _op_stat(self, op_id, op, args):
        entry = self.local.stat(args["root"], args["rel"])
        if entry is None:
            return {"exists": False}
        return {"exists": True, "size": entry.size, "mtime": entry.mtime}

    def _op_ls(self, op_id, op, args):
        entries = []
        for child in sorted(Path(args["path"]).iterdir()):
            entries.append({"name": child.name, "is_dir": child.is_dir(), "size": child.stat().st_size if child.is_file() else 0})
        return {"entries": entries}
