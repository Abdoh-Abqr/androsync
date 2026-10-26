"""HTTP API for the laptop daemon (`androsync serve`).

``ApiServer`` wraps a :class:`~androsync.daemon.jobs.JobRunner` in a small
JSON/SSE API using only the standard library
(``http.server.ThreadingHTTPServer``), so the phone app and the CLI's
``qr``/``service`` commands have nothing extra to install. See the "HTTP
API" section of ``PLAN.md`` for the endpoint list and the bearer-token
auth model.

Every request must carry ``Authorization: Bearer <token>`` - even on
``127.0.0.1`` - compared with :func:`hmac.compare_digest` to avoid a
timing side-channel. Route handlers never let an exception escape as a
bare traceback: the dispatcher turns any of them into a JSON 500.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlsplit

from androsync import config_edit, state
from androsync.browse import BrowseError, BrowseForbidden, browse_local, browse_remote, mkdir_local, mkdir_remote
from androsync.config import Config, ConfigError
from androsync.conflicts import ConflictsError, clean, list_conflicts, restore
from androsync.daemon.problems import classify_route, explain_failure
from androsync.daemon.jobs import JobBusy, JobRunner, current_job_dict, job_to_dict, pairs_status
from androsync.executor import run_pair
from androsync.transport.agent import POLL_SECONDS, AgentBroker, AgentBusy, AgentCancelled, AgentTransport
from androsync.model import Side
from androsync.throttle import Throttle
from androsync.transport.local import LocalDirTransport
from androsync.undo import UndoError, list_runs_all

_LOG = logging.getLogger(__name__)

# Mirrors jobs._ACTIVE_STATES; kept as a local copy so this module does not
# reach into JobRunner's private state.
_ACTIVE_STATES = ("queued", "running")

# How often the SSE stream sends a keep-alive comment while otherwise idle.
_SSE_PING_INTERVAL = 15.0
# Poll granularity for LogBuffer.wait() while streaming - short enough that
# a finished job's "event: end" is not delayed noticeably.
_SSE_WAIT_SLICE = 0.5

DeviceInfo = Callable[[], dict]


class _ClientGone(Exception):
    """Raised internally when writing to a client fails (they disconnected)."""


def _drop_null_fields(body: dict) -> dict:
    """Strip JSON ``null`` values from a pair-CRUD request body.

    A client that always sends every field (kotlinx.serialization with
    ``encodeDefaults = true``, for one) sends JSON ``null`` for an unset
    optional field like ``conflict``. Passed straight through to
    ``config_edit``, that would write a literal ``conflict: null`` into the
    YAML - which is not "unset", it is an explicit invalid value, and
    ``load_config`` would reject it. Treat ``null`` the same as "omitted".
    """
    return {k: v for k, v in body.items() if v is not None}


_ROUTES: list[tuple[str, re.Pattern, str]] = [
    ("GET", re.compile(r"^/v1/ping$"), "_h_ping"),
    ("GET", re.compile(r"^/v1/pairs$"), "_h_pairs"),
    ("POST", re.compile(r"^/v1/pairs$"), "_h_pairs_create"),
    ("PUT", re.compile(r"^/v1/pairs/(?P<name>[^/]+)$"), "_h_pairs_update"),
    ("DELETE", re.compile(r"^/v1/pairs/(?P<name>[^/]+)$"), "_h_pairs_delete"),
    ("GET", re.compile(r"^/v1/status/(?P<pair>[^/]+)$"), "_h_status"),
    ("GET", re.compile(r"^/v1/pending/(?P<pair>[^/]+)$"), "_h_pending"),
    ("POST", re.compile(r"^/v1/run$"), "_h_run"),
    ("GET", re.compile(r"^/v1/jobs$"), "_h_jobs_list"),
    ("GET", re.compile(r"^/v1/jobs/(?P<id>[^/]+)$"), "_h_job_get"),
    ("GET", re.compile(r"^/v1/jobs/(?P<id>[^/]+)/log$"), "_h_job_log"),
    ("POST", re.compile(r"^/v1/jobs/(?P<id>[^/]+)/cancel$"), "_h_job_cancel"),
    ("GET", re.compile(r"^/v1/browse$"), "_h_browse"),
    ("POST", re.compile(r"^/v1/mkdir$"), "_h_mkdir"),
    ("GET", re.compile(r"^/v1/conflicts/(?P<pair>[^/]+)$"), "_h_conflicts_list"),
    ("POST", re.compile(r"^/v1/conflicts/(?P<pair>[^/]+)/restore$"), "_h_conflicts_restore"),
    ("POST", re.compile(r"^/v1/conflicts/(?P<pair>[^/]+)/clean$"), "_h_conflicts_clean"),
    ("GET", re.compile(r"^/v1/runs/(?P<pair>[^/]+)$"), "_h_runs_list"),
    ("POST", re.compile(r"^/v1/undo/(?P<pair>[^/]+)$"), "_h_undo"),
    ("POST", re.compile(r"^/v1/token/rotate$"), "_h_token_rotate"),
    ("POST", re.compile(r"^/v1/agent/event$"), "_h_agent_event"),
    # The phone app as an agent (task 37b, issue #14); the wire format is in docs/agent-protocol.md.
    ("GET", re.compile(r"^/v1/agent/next$"), "_h_agent_next"),
    ("POST", re.compile(r"^/v1/agent/result/(?P<id>[^/]+)$"), "_h_agent_result"),
    ("GET", re.compile(r"^/v1/agent/blob/(?P<id>[^/]+)$"), "_h_agent_blob_get"),
    ("PUT", re.compile(r"^/v1/agent/blob/(?P<id>[^/]+)$"), "_h_agent_blob_put"),
]

# Body streaming chunk for agent blobs.
_BLOB_CHUNK = 1 << 20


class _Handler(BaseHTTPRequestHandler):
    """Routes one request to an ``ApiServer`` method. Never raises out."""

    server: "_BoundHTTPServer"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # default access logging is suppressed; ApiServer.log is used instead

    @property
    def api(self) -> "ApiServer":
        return self.server.api

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802 - stdlib naming
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib naming
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        status = None
        try:
            if not self._authorized():
                status = self._send_json(401, {"error": "unauthorized"})
                return
            status = self._route(method, path, parsed.query)
        except _ClientGone:
            status = None
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the client
            _LOG.exception("request failed: %s %s", method, path)
            try:
                status = self._send_json(500, {"error": str(exc)})
            except Exception:  # noqa: BLE001 - client may already be gone
                status = None
        finally:
            if status is not None:
                self.api._log_request(method, path, status)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        supplied = header[len(prefix) :]
        return hmac.compare_digest(supplied, self.api._token)

    def _route(self, method: str, path: str, query: str):
        matched_path = False
        for route_method, pattern, handler_name in _ROUTES:
            match = pattern.match(path)
            if match is None:
                continue
            matched_path = True
            if route_method != method:
                continue
            return getattr(self, handler_name)(match, query)
        if matched_path:
            return self._send_json(405, {"error": "method not allowed"})
        return self._send_json(404, {"error": "not found"})

    # -- response helpers --------------------------------------------------

    def _send_json(self, status: int, body, headers: dict[str, str] | None = None) -> int:
        payload = json.dumps(body).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise _ClientGone from exc
        return status

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    # -- route handlers ------------------------------------------------------

    def _h_ping(self, match: re.Match, query: str) -> int:
        info = self.api._device_info() if self.api._device_info is not None else {}
        body = {
            "name": "androsync",
            "version": self.api._version,
            "transport": info.get("transport"),
            "device": info.get("device"),
            "devices": info.get("devices", []),
            "current_job": current_job_dict(self.api._runner.current()),
            "agent": "connected" if self.api.agent.connected() else "absent",
            "agent_route": self.api.agent.route() if self.api.agent.connected() else None,
        }
        return self._send_json(200, body)

    def _pairs_json(self, config: Config) -> list[dict]:
        return pairs_status(config, self.api.auto.status if self.api.auto is not None else None)

    def _h_pairs(self, match: re.Match, query: str) -> int:
        config = self.api._config_loader()
        return self._send_json(200, self._pairs_json(config))

    def _h_agent_event(self, match: re.Match, query: str) -> int:
        """The phone app saw files change under ``root``: tell automatic sync (Phase T2)."""
        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send_json(400, {"error": "bad json"})
        root = body.get("root") if isinstance(body, dict) else None
        if not isinstance(root, str) or not root:
            return self._send_json(400, {"error": "body must be {root}"})
        if self.api.auto is not None:
            self.api.auto.mark_root_dirty(root)
        return self._send_json(200, {"ok": True})

    def _h_token_rotate(self, match: re.Match, query: str) -> int:
        """Make a new token (the caller proved it has the old one) and start accepting only the new one.

        Everything else that uses the old token (other phones, other tools) stops working until it is given the new
        one, for example by scanning the QR code again. The caller gets the new token in the answer.
        """
        config_path = self.api._config_path
        if config_path is None:
            return self._send_json(503, {"error": "this daemon was started without a config file, so it cannot save a new token"})
        new_token = secrets.token_urlsafe(32)
        try:
            config_edit.save_raw(config_path, config_edit.set_service_token(config_edit.load_raw(config_path), new_token))
        except ConfigError as exc:
            return self._send_json(500, {"error": str(exc)})
        self.api._token = new_token
        _LOG.info("a new token was generated through the API")
        return self._send_json(200, {"token": new_token})

    # -- agent mode: the phone app does the file work (task 37b) ------------------------------------------------

    def _send_empty(self, status: int) -> int:
        try:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise _ClientGone from exc
        return status

    def _agent_op(self, match: re.Match):
        """The live operation for this request, or ``(None, status)`` after sending the error answer."""
        try:
            return self.api.agent.find(unquote(match.group("id"))), None
        except KeyError:
            return None, self._send_json(404, {"error": "unknown operation"})
        except AgentCancelled:
            return None, self._send_json(410, {"error": "operation was cancelled"})

    def _h_agent_next(self, match: re.Match, query: str) -> int:
        try:
            wait = float((parse_qs(query).get("wait") or [POLL_SECONDS])[0])
        except ValueError:
            return self._send_json(400, {"error": "wait must be a number"})
        params = parse_qs(query)
        agent_id = (params.get("agent") or [None])[0]
        self.api.agent.report_state((params.get("net") or [None])[0], (params.get("battery") or [None])[0])
        self.api.agent.report_route(classify_route(self.client_address[0]))
        try:
            op = self.api.agent.next_op(max(0.0, min(wait, POLL_SECONDS)), agent_id)
        except AgentBusy:
            return self._send_json(
                409, {"error": "another phone's agent is already connected to this laptop; only one at a time"}
            )
        if op is None:
            return self._send_empty(204)
        return self._send_json(200, op)

    def _h_agent_result(self, match: re.Match, query: str) -> int:
        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send_json(400, {"error": "bad json"})
        if not isinstance(body, dict) or "ok" not in body:
            return self._send_json(400, {"error": "body must be {ok, result?, error?}"})
        try:
            self.api.agent.complete(unquote(match.group("id")), body["ok"], body.get("result"), body.get("error"))
        except KeyError:
            return self._send_json(404, {"error": "unknown operation"})
        except AgentCancelled:
            return self._send_json(410, {"error": "operation was cancelled"})
        return self._send_json(200, {"ok": True})

    def _throttle(self) -> Throttle:
        """The speed limit for file bodies, from ``service.bandwidth_kb_s`` in the config (0 = none)."""
        try:
            kb_s = self.api._config_loader().service.bandwidth_kb_s
        except Exception:  # noqa: BLE001 - a config that cannot be read must not stop a transfer
            kb_s = 0
        return Throttle(kb_s * 1024)

    def _h_agent_blob_get(self, match: re.Match, query: str) -> int:
        op, error = self._agent_op(match)
        if op is None:
            return error
        if op.upload is None:
            return self._send_json(400, {"error": "this operation has no body to download"})
        try:
            size = op.upload.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            throttle = self._throttle()
            with open(op.upload, "rb") as handle:
                while True:
                    chunk = handle.read(throttle.chunk_size())
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.api.agent.touch()
                    throttle.wait(len(chunk))
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise _ClientGone from exc
        return 200

    def _h_agent_blob_put(self, match: re.Match, query: str) -> int:
        op, error = self._agent_op(match)
        if op is None:
            return error
        if op.download is None:
            return self._send_json(400, {"error": "this operation expects no body"})
        header = self.headers.get("Content-Length")
        if header is None or not header.isdigit():
            return self._send_json(411, {"error": "Content-Length is required"})
        expected = int(header)
        received = 0
        try:
            throttle = self._throttle()
            with open(op.download, "wb") as handle:
                while received < expected:
                    chunk = self.rfile.read(min(throttle.chunk_size(), expected - received))
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    self.api.agent.touch()
                    throttle.wait(len(chunk))
        except OSError as exc:
            return self._send_json(500, {"error": f"could not write the file: {exc}"})
        if received != expected:
            op.download.unlink(missing_ok=True)
            return self._send_json(400, {"error": f"body ended after {received} of {expected} bytes"})
        return self._send_json(200, {"ok": True, "size": received})

    # -- pair CRUD (task 23b: the app's Add/Edit/Delete pair screens) -------

    def _config_edit_error_status(self, message: str) -> int:
        if "already exists" in message:
            return 409
        if "no pair named" in message:
            return 404
        return 400

    def _h_pairs_create(self, match: re.Match, query: str) -> int:
        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send_json(400, {"error": "bad json"})
        if not isinstance(body, dict) or not body.get("name"):
            return self._send_json(400, {"error": "bad json"})
        body = _drop_null_fields(body)

        config_path = self.api._config_path
        raw = config_edit.load_raw(config_path)
        try:
            new_raw = config_edit.add_pair(raw, body)
            config_edit.save_raw(config_path, new_raw)
        except ConfigError as exc:
            return self._send_json(self._config_edit_error_status(str(exc)), {"error": str(exc)})

        config = self.api._config_loader()
        return self._send_json(201, self._pairs_json(config))

    def _h_pairs_update(self, match: re.Match, query: str) -> int:
        name = unquote(match.group("name"))
        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send_json(400, {"error": "bad json"})
        if not isinstance(body, dict):
            return self._send_json(400, {"error": "bad json"})
        body = _drop_null_fields(body)

        try:
            state_reset = config_edit.update_pair_and_state(self.api._config_path, name, body)
        except ConfigError as exc:
            return self._send_json(self._config_edit_error_status(str(exc)), {"error": str(exc)})

        config = self.api._config_loader()
        # The body stays the pairs list (older apps expect that); a header says the history was reset.
        return self._send_json(200, self._pairs_json(config), headers={"X-Androsync-State-Reset": "1"} if state_reset else None)

    def _h_pairs_delete(self, match: re.Match, query: str) -> int:
        name = unquote(match.group("name"))
        purge = parse_qs(query).get("purge_state", ["0"])[0] == "1"

        config_path = self.api._config_path
        raw = config_edit.load_raw(config_path)
        try:
            new_raw = config_edit.remove_pair(raw, name)
            config_edit.save_raw(config_path, new_raw)
        except ConfigError as exc:
            return self._send_json(404, {"error": str(exc)})

        if purge:
            state.purge_pair_state(name)

        config = self.api._config_loader()
        return self._send_json(200, self._pairs_json(config))

    # -- folder browsing (task 23b: the app's folder pickers) ---------------

    def _explain_no_phone(self, message: str, pair) -> str:
        """``message``, with the wrong-connection sentence first when the phone app is connected instead (Phase V3)."""
        agent = self.api.agent
        return explain_failure(
            message,
            pair_transport=pair.transport if pair is not None else "adb",
            agent_connected=agent.connected(),
            agent_route=agent.route(),
        )

    def _remote_transport_or_error(self, pair=None):
        """The 503/409/502 gating shared by the remote browse and mkdir handlers.

        Returns ``((local, remote), None)`` on success, or ``(None, (status, body))``.
        """
        has_device = self.api._device_info is not None and self.api._device_info().get("device") is not None
        agent_pair = pair is not None and pair.transport == "agent"
        if agent_pair:
            if not self.api.agent.connected():
                return None, (503, {"error": "the phone app's agent is not connected"})
        elif not has_device and (pair is not None or not self.api.agent.connected()):
            return None, (503, {"error": self._explain_no_phone("phone not connected", pair)})
        if self.api._runner.current() is not None:
            return None, (409, {"error": "busy"})
        if agent_pair or not has_device:  # the phone app's agent does the work (task 37d)
            return (LocalDirTransport(Side.LOCAL), AgentTransport(self.api.agent)), None
        config = self.api._config_loader()
        try:
            transports = self.api._runner._transport_factory(config)
        except Exception as exc:  # noqa: BLE001 - reported to the caller, not raised
            return None, (502, {"error": str(exc)})
        return transports, None

    @staticmethod
    def _browse_result_json(result) -> dict:
        return {
            "path": result.path,
            "parent": result.parent,
            "entries": [asdict(e) for e in result.entries],
            "writable": result.writable,
        }

    def _h_browse(self, match: re.Match, query: str) -> int:
        params = parse_qs(query)
        side = (params.get("side") or [""])[0]
        path = (params.get("path") or [""])[0]
        if side not in ("local", "remote"):
            return self._send_json(400, {"error": "side must be 'local' or 'remote'"})

        if side == "local":
            config = self.api._config_loader()
            try:
                result = browse_local(path, config.service.browse_roots)
            except BrowseForbidden as exc:
                return self._send_json(403, {"error": str(exc)})
            except BrowseError as exc:
                return self._send_json(400, {"error": str(exc)})
            return self._send_json(200, self._browse_result_json(result))

        transports, error = self._remote_transport_or_error()
        if error is not None:
            return self._send_json(*error)
        local, remote = transports
        try:
            result = browse_remote(remote, path)
        except BrowseForbidden as exc:
            return self._send_json(403, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - reported to the caller, not raised
            return self._send_json(502, {"error": str(exc)})
        finally:
            for transport in (local, remote):
                try:
                    transport.close()
                except Exception:  # noqa: BLE001 - closing must never mask a result
                    pass
        return self._send_json(200, self._browse_result_json(result))

    def _h_mkdir(self, match: re.Match, query: str) -> int:
        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send_json(400, {"error": "bad json"})
        side = body.get("side") if isinstance(body, dict) else None
        path = body.get("path") if isinstance(body, dict) else None
        if side not in ("local", "remote") or not path:
            return self._send_json(400, {"error": "bad json"})

        if side == "local":
            config = self.api._config_loader()
            try:
                mkdir_local(path, config.service.browse_roots)
            except BrowseForbidden as exc:
                return self._send_json(403, {"error": str(exc)})
            except OSError as exc:
                return self._send_json(400, {"error": str(exc)})
            return self._send_json(201, {"path": path})

        transports, error = self._remote_transport_or_error()
        if error is not None:
            return self._send_json(*error)
        local, remote = transports
        try:
            path = mkdir_remote(remote, path)
        except BrowseForbidden as exc:
            return self._send_json(403, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - reported to the caller, not raised
            return self._send_json(502, {"error": str(exc)})
        finally:
            for transport in (local, remote):
                try:
                    transport.close()
                except Exception:  # noqa: BLE001 - closing must never mask a result
                    pass
        return self._send_json(201, {"path": path})

    # -- conflict-copy backups (task 29b) ------------------------------------

    def _pair_or_404(self, config: Config, name: str):
        pair = next((p for p in config.pairs if p.name == name), None)
        if pair is None:
            return None, self._send_json(404, {"error": f"unknown pair: {name}"})
        return pair, None

    def _h_conflicts_list(self, match: re.Match, query: str) -> int:
        config = self.api._config_loader()
        pair, error = self._pair_or_404(config, unquote(match.group("pair")))
        if error is not None:
            return error

        versions = list_conflicts(pair)
        return self._send_json(200, [asdict(v) for v in versions])

    def _h_conflicts_restore(self, match: re.Match, query: str) -> int:
        config = self.api._config_loader()
        pair, error = self._pair_or_404(config, unquote(match.group("pair")))
        if error is not None:
            return error

        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send_json(400, {"error": "bad json"})
        rel = body.get("rel") if isinstance(body, dict) else None
        if not rel:
            return self._send_json(400, {"error": "bad json"})
        when = body.get("when")

        try:
            restored_when = restore(pair, LocalDirTransport(), rel, when)
        except ConflictsError as exc:
            return self._send_json(404, {"error": str(exc)})

        return self._send_json(200, {"rel": rel, "when": restored_when})

    def _h_conflicts_clean(self, match: re.Match, query: str) -> int:
        config = self.api._config_loader()
        pair, error = self._pair_or_404(config, unquote(match.group("pair")))
        if error is not None:
            return error

        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send_json(400, {"error": "bad json"})
        dry_run = bool(body.get("dry_run", True)) if isinstance(body, dict) else True

        transports, error_pair = self._remote_transport_or_error(pair)
        if error_pair is not None:
            return self._send_json(*error_pair)
        local, remote = transports
        try:
            result = clean(pair, local, remote, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - reported to the caller, not raised
            return self._send_json(502, {"error": str(exc)})
        finally:
            for transport in (local, remote):
                try:
                    transport.close()
                except Exception:  # noqa: BLE001 - closing must never mask a result
                    pass

        return self._send_json(200, {"dry_run": dry_run, "actions": [asdict(a) for a in result.actions]})

    def _h_pending(self, match: re.Match, query: str) -> int:
        name = unquote(match.group("pair"))
        config: Config = self.api._config_loader()
        if not any(p.name == name for p in config.pairs):
            return self._send_json(404, {"error": f"unknown pair: {name}"})
        return self._send_json(200, state.load_pending_all(name))

    def _h_status(self, match: re.Match, query: str) -> int:
        name = unquote(match.group("pair"))
        config: Config = self.api._config_loader()
        pair = next((p for p in config.pairs if p.name == name), None)
        if pair is None:
            return self._send_json(404, {"error": f"unknown pair: {name}"})

        if self.api._runner.current() is not None:
            return self._send_json(409, {"error": "busy"})

        try:
            opened = self.api._runner.open_transports(config, [pair])
        except Exception as exc:  # noqa: BLE001 - reported to the caller, not raised
            return self._send_json(502, {"error": str(exc)})

        lines: list[str] = []
        try:
            summary = run_pair(pair, opened.local, opened.remote_for(pair), dry_run=True, log=lines.append)
        except Exception as exc:  # noqa: BLE001 - reported to the caller, not raised
            return self._send_json(502, {"error": str(exc)})
        finally:
            for transport in opened.all():
                try:
                    transport.close()
                except Exception:  # noqa: BLE001 - closing must never mask a result
                    pass

        return self._send_json(200, {"pair": name, "actions": lines, "summary": asdict(summary)})

    def _h_run(self, match: re.Match, query: str) -> int:
        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send_json(400, {"error": "bad json"})
        if not isinstance(body, dict):
            return self._send_json(400, {"error": "bad json"})

        pairs = body.get("pairs")
        if pairs is not None and (
            not isinstance(pairs, list) or not all(isinstance(p, str) for p in pairs)
        ):
            return self._send_json(400, {"error": "bad json"})
        dry_run = bool(body.get("dry_run", False))
        confirm_large = bool(body.get("confirm_large", False))

        try:
            job = self.api._runner.submit(pairs=pairs, dry_run=dry_run, confirm_large=confirm_large)
        except JobBusy:
            current = self.api._runner.current()
            return self._send_json(409, {"error": "busy", "job_id": current.id if current else None})
        except KeyError as exc:
            return self._send_json(404, {"error": f"unknown pair: {exc.args[0]}"})

        return self._send_json(202, {"job_id": job.id})

    def _h_runs_list(self, match: re.Match, query: str) -> int:
        name = unquote(match.group("pair"))
        config = self.api._config_loader()
        if not any(p.name == name for p in config.pairs):
            return self._send_json(404, {"error": f"unknown pair: {name}"})
        return self._send_json(200, list_runs_all(name))

    def _h_undo(self, match: re.Match, query: str) -> int:
        name = unquote(match.group("pair"))
        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send_json(400, {"error": "bad json"})
        if not isinstance(body, dict):
            return self._send_json(400, {"error": "bad json"})

        run_id = body.get("run_id")
        if run_id is not None and not isinstance(run_id, str):
            return self._send_json(400, {"error": "bad json"})
        dry_run = bool(body.get("dry_run", False))
        force = bool(body.get("force", False))
        pause = bool(body.get("pause", False))

        try:
            job = self.api._runner.submit_undo(name, run_id, force=force, dry_run=dry_run)
        except JobBusy:
            current = self.api._runner.current()
            return self._send_json(409, {"error": "busy", "job_id": current.id if current else None})
        except KeyError:
            return self._send_json(404, {"error": f"unknown pair: {name}"})
        except UndoError as exc:
            return self._send_json(404, {"error": str(exc)})

        if pause and not dry_run:
            config_path = self.api._config_path
            raw = config_edit.load_raw(config_path)
            current = next((p for p in raw.get("pairs") or [] if p.get("name") == name), None)
            pair_dict = dict(current) if current is not None else {"name": name}
            pair_dict["enabled"] = False
            try:
                new_raw = config_edit.update_pair(raw, name, pair_dict)
                config_edit.save_raw(config_path, new_raw)
            except ConfigError as exc:
                return self._send_json(400, {"error": str(exc)})

        return self._send_json(202, {"job_id": job.id})

    def _h_jobs_list(self, match: re.Match, query: str) -> int:
        jobs = [job_to_dict(job) for job in self.api._runner.list()]
        return self._send_json(200, jobs)

    def _h_job_get(self, match: re.Match, query: str) -> int:
        job = self.api._runner.get(match.group("id"))
        if job is None:
            return self._send_json(404, {"error": "not found"})
        return self._send_json(200, job_to_dict(job))

    def _h_job_cancel(self, match: re.Match, query: str) -> int:
        job = self.api._runner.get(match.group("id"))
        if job is None:
            return self._send_json(404, {"error": "not found"})
        self.api._runner.cancel(job.id)
        return self._send_json(200, {"ok": True})

    def _h_job_log(self, match: re.Match, query: str) -> int:
        job = self.api._runner.get(match.group("id"))
        if job is None:
            return self._send_json(404, {"error": "not found"})

        try:
            since = int(parse_qs(query).get("since", ["0"])[0])
        except ValueError:
            since = 0

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            next_seq = since
            for seq, line in job.log.since(since):
                self._sse_write(f"id: {seq}\ndata: {line}\n\n")
                next_seq = seq + 1

            last_ping = time.monotonic()
            while True:
                pending = job.log.wait(next_seq, timeout=_SSE_WAIT_SLICE)
                if pending:
                    for seq, line in pending:
                        self._sse_write(f"id: {seq}\ndata: {line}\n\n")
                        next_seq = seq + 1
                    last_ping = time.monotonic()
                    continue
                if job.state not in _ACTIVE_STATES:
                    break
                if time.monotonic() - last_ping >= _SSE_PING_INTERVAL:
                    self._sse_write(": ping\n\n")
                    last_ping = time.monotonic()

            self._sse_write(f"event: end\ndata: {job.state}\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        return 200

    def _sse_write(self, chunk: str) -> None:
        try:
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise _ClientGone from exc


class _BoundHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], api: "ApiServer") -> None:
        super().__init__(address, _Handler)
        self.api = api


class ApiServer:
    """Binds :class:`_Handler` to one ``ThreadingHTTPServer`` per ``listen`` address."""

    def __init__(
        self,
        runner: JobRunner,
        config_loader: Callable[[], Config],
        *,
        token: str,
        listen: list[str],
        port: int,
        version: str,
        device_info: DeviceInfo | None = None,
        log: Callable[[str], None] | None = None,
        config_path: Path | None = None,
        agent: AgentBroker | None = None,
        auto=None,  # noqa: ANN001 - AutoSync; optional so tests can leave it out
    ) -> None:
        self._runner = runner
        self._config_loader = config_loader
        self._token = token
        self._listen = list(listen)
        self._port = port
        self._version = version
        self._device_info = device_info
        self._log = log
        self._config_path = config_path
        # The mailbox for agent mode (task 37b); the daemon passes the one its transports use.
        self.agent: AgentBroker = agent if agent is not None else AgentBroker()
        self.auto = auto
        self._servers: list[_BoundHTTPServer] = []
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        """Bind every address in ``listen``; skip (with a warning) any that fail.

        Raises ``OSError`` if none of them could be bound.
        """
        if self._servers:
            raise RuntimeError("ApiServer already started")

        servers: list[_BoundHTTPServer] = []
        for address in self._listen:
            try:
                httpd = _BoundHTTPServer((address, self._port), self)
            except OSError as exc:
                _LOG.warning("androsync: could not bind %s:%s (%s)", address, self._port, exc)
                continue
            servers.append(httpd)

        if not servers:
            raise OSError(f"could not bind any of {self._listen} on port {self._port}")

        self._servers = servers
        for httpd in servers:
            thread = threading.Thread(
                target=httpd.serve_forever,
                kwargs={"poll_interval": 0.05},
                name=f"androsync-api-{httpd.server_address[0]}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

    def bind_extra(self, address: str) -> bool:
        """Try to bind one more ``listen`` address after :meth:`start`.

        Used by ``Daemon.start()`` to retry an address (typically a
        Tailscale IP) that was not up yet when :meth:`start` first ran.
        Returns True if it bound, False (after logging a warning) if not.
        """
        if not self._servers:
            raise RuntimeError("ApiServer.start() must run before bind_extra()")
        try:
            httpd = _BoundHTTPServer((address, self._port), self)
        except OSError as exc:
            _LOG.warning("androsync: could not bind %s:%s (%s)", address, self._port, exc)
            return False

        self._servers.append(httpd)
        thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.05},
            name=f"androsync-api-{httpd.server_address[0]}",
            daemon=True,
        )
        self._threads.append(thread)
        thread.start()
        return True

    def stop(self) -> None:
        """Shut down every bound server and join its serving thread."""
        for httpd in self._servers:
            httpd.shutdown()
        for httpd in self._servers:
            httpd.server_close()
        for thread in self._threads:
            thread.join(timeout=5.0)
        self._servers = []
        self._threads = []

    @property
    def bound_addresses(self) -> list[tuple[str, int]]:
        """The actual ``(host, port)`` of every server that bound successfully."""
        return [httpd.server_address[:2] for httpd in self._servers]

    def _log_request(self, method: str, path: str, status: int) -> None:
        line = f"{method} {path} {status}"
        if self._log is not None:
            self._log(line)
        else:
            _LOG.info(line)
