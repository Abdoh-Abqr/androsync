# Agent protocol (laptop <-> phone app)

How the phone app does the file work itself, so no USB or Wireless debugging is needed (tasks 37b to 37d, issues #14
to #16). The laptop cannot call into the phone, so the app calls the laptop. **Status:** the laptop side is built
(`laptop/androsync/transport/agent.py`, routes in `daemon/server.py`, tested against `laptop/tests/fake_agent.py`).
The user guide is [agent-mode.md](agent-mode.md). The Android side is built (`android/app/src/full/.../agent/`, issue #15) and follows this page; pairs choose it with `transport: agent` (issue #16). The spike in task 37a showed that the app can set
file times, rename, hash and scan fast enough.

All requests carry `Authorization: Bearer <token>`, like every other route.

## The loop the app runs

(`GET /v1/agent/next` may carry `&agent=<id>`, `&net=wifi|mobile|none` and `&battery=charging|discharging`. The laptop remembers the
last two for automatic sync's run conditions and refuses a second agent id while another agent is connected.)

1. `GET /v1/agent/next` (optional `?wait=<seconds>`, at most 25). The laptop holds the request until work exists.
   - `204`, no body: nothing to do. Ask again at once.
   - `200`, JSON: one operation, see below.
2. Do the operation (and move the file body if it has one).
3. `POST /v1/agent/result/<id>` with `{"ok": true, "result": {...}}` or `{"ok": false, "error": "text"}`.
4. Go to 1. Keep one request in flight at all times: the laptop calls the agent **connected** (`/v1/ping` says
   `"agent": "connected"`) when it heard from it in the last 45 seconds, or in the last 5 minutes while an operation is
   out with it. One agent per laptop; one operation at a time.

If any call answers `404` (operation unknown or finished) or `410` (the user pressed Stop), drop the operation and
poll again. Do not retry it.

## An operation

```json
{"id": "9f2c...", "op": "store", "args": {"root": "/sdcard/DCIM", "rel": "a/b.jpg", "mtime": 1700000000.5, "size": 123456},
 "upload": true, "download": false}
```

- `upload: true`: the laptop has a body for you. `GET /v1/agent/blob/<id>` returns it (with `Content-Length`).
- `download: true`: you must send a body. `PUT /v1/agent/blob/<id>` with `Content-Length` set (no chunked encoding) and
  the raw bytes. The answer is `200 {"ok": true, "size": N}`; a short body answers `400`.

`root` is the pair's phone folder, already checked on the laptop (`/sdcard/...` or `/storage/<id>/...`). `rel` uses
`/` and never contains `..`. All times are seconds since the epoch as a number with decimals (the spike showed
millisecond times stick).

| `op` | `args` | `result` |
|---|---|---|
| `scan` | `root` | `{"exists": bool, "files": [[rel, size, mtime], ...]}`. Regular files only, no symlinks, everything (including `.androsync`): the laptop applies the ignore rules. |
| `stat` | `root`, `rel` | `{"exists": false}` or `{"exists": true, "size": N, "mtime": T}` |
| `hash_many` | `root`, `rels` (up to 200) | `{"hashes": {rel: sha256 hex}}` |
| `fetch` | `root`, `rel` (`download: true`) | `{"size": N, "mtime": T}`; PUT the bytes first |
| `store` | `root`, `rel`, `mtime`, `size` (`upload: true`) | `{}`. GET the bytes, write them to `<root>/.androsync/incoming/<token>/`, check the size, set the time, then move into place (create parent folders). A Stop must never leave half a file in the synced folder. |
| `delete` | `root`, `rel` | `{}`. Missing file is fine. Afterwards remove empty parent folders up to, not including, `root`. |
| `rename` | `root`, `from`, `to` | `{}`. Create parents of `to`; replace an existing file. |
| `mkdirs` | `root`, `rel_dir` | `{}`. Empty `rel_dir` means `root` itself. |
| `exists` | `root` | `{"exists": bool}` (a folder) |
| `ls` | `path` | `{"entries": [{"name", "is_dir", "size"}]}` (children only) |

## Telling the laptop about changes (optional)

`POST /v1/agent/event` with `{"root": "/sdcard/DCIM"}` says files changed under that folder. The laptop marks the agent pairs
whose phone folder covers it, and automatic sync ([auto-sync.md](auto-sync.md)) syncs them once the changes settle. Answer is
`200 {"ok": true}`. An agent that never sends it still works: automatic sync checks the phone every `auto_interval_s`.

## Errors

Answer `{"ok": false, "error": "..."}`; the laptop shows `"<op> failed on the phone: <error>"`. Put the real cause
in the text (permission denied, no space left, ...).

## What the laptop does by itself

- Refuses a bad `rel` or a phone folder outside shared storage before anything is sent.
- Gives up with "not connected" at once when no agent polled recently, with "did not pick up" after 60 seconds, and with
  "went silent" when the agent stops talking mid-operation.
- Stop: cancels the queued and running operations; the next answer from the app gets `410`.
