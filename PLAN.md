# androsync — USB/Wi‑Fi folder sync between Ubuntu laptop and Android phone

> **Issue numbers.** Numbers like "issue #10" in this file and in `PLAN.md` refer to the project's first, private repository
> (`androsync-private`, kept as an archive). The work still open was recreated in this repository as issues #1 to #8.


## Context

Goal: keep chosen folder pairs in sync between the laptop and the phone, first over the USB cable,
later over Wi‑Fi. Examples: phone `DCIM` → laptop pictures folder; laptop Obsidian vault ↔ phone
`Documents/Obsidian`. Two‑way, decided by "what changed since last sync", configurable conflict rule.

Two pieces:
1. **Laptop side (Python)** — does all the file work through ADB and runs as a background service
   that starts with the PC (systemd user service). CLI for setup and manual runs.
2. **Phone side (Android app, Kotlin)** — a remote control: shows pairs, "Sync now" button,
   progress/log, settings. It talks to the laptop service over HTTP. It never touches files itself,
   so it needs no storage permission. You can start a sync from the couch while the phone is on the cable.

Decisions already made (with user):
- **Transport: ADB** (USB debugging on). Sees all shared storage (`/sdcard/...`), keeps timestamps,
  `adb connect` gives Wi‑Fi later with the same code. App‑private folders (`/data/data/...`) are out of reach.
- **First version: Python CLI + YAML config.** Desktop GUI later.
- **Default conflict rule: newest wins, loser kept as `name.conflict-<side>-<date>.ext`.**
- **Phone ↔ laptop control channel: HTTP.** Over USB via `adb reverse tcp:8765 tcp:8765`
  (phone reaches the laptop as `http://127.0.0.1:8765`), over Wi‑Fi via the laptop LAN IP. Same API.

Environment: Python 3.12.3, adb 1.0.41, rsync, gio. Project folder is empty. Android app built
with Android Studio / Gradle (Kotlin, Jetpack Compose, minSdk 26).

## Architecture

```
Android_synconizer/
  laptop/                       # Python package, deps: pyyaml (+pytest dev)
    pyproject.toml              # console script: androsync = androsync.cli:main
    config.example.yaml
    systemd/androsync.service   # template installed by `androsync service install`
    androsync/
      cli.py                    # argparse subcommands
      config.py                 # load + validate YAML, expand ~, defaults
      model.py                  # FileEntry, Snapshot, ChangeKind, Action
      ignore.py                 # glob ignore rules
      state.py                  # snapshot JSON per pair (~/.local/share/androsync/state/)
      planner.py                # PURE: (scan_a, scan_b, last_snapshot, settings) -> [Action]
      executor.py               # applies actions through transports, saves snapshot per action
      conflict.py               # conflict-copy naming, policies
      transport/base.py         # Transport ABC
      transport/local.py        # LocalDirTransport (laptop side; also fakes a phone in tests)
      transport/adb.py          # AdbTransport (subprocess around `adb`)
      daemon/server.py          # HTTP API (stdlib http.server, threaded), token auth
      daemon/watcher.py         # `adb track-devices` loop: on connect -> adb reverse, optional auto-sync
      daemon/jobs.py            # one sync job at a time, live log buffer, status
    tests/
  android/                      # Android Studio project "AndroSync Remote"
    app/src/main/java/.../      # Compose UI, Retrofit client, DataStore settings
  README.md
```

### Transport interface (`transport/base.py`)
```python
class Transport(ABC):
    name: str                                     # "laptop" | "phone"
    def scan(self, root, ignore) -> dict[str, FileEntry]   # relpath -> (size, mtime)
    def fetch(self, root, rel, tmp_path)          # side -> laptop temp file
    def store(self, tmp_path, root, rel, mtime)   # temp file -> side, sets mtime
    def delete(self, root, rel); def rename(self, root, a, b); def mkdirs(self, root, d); def exists(self, root)
```
Both sides are Transports; copy A→B = `A.fetch` → `B.store`. Engine is fully testable with two local dirs.

### Sync algorithm (state-based, like unison)
1. Load last snapshot `{relpath: (size, mtime)}` for the pair.
2. Scan both sides. 3. Classify each side vs snapshot: `unchanged | created | modified | deleted`
   (modified = size differs or |Δmtime| > `mtime_tolerance_s`, default 2 s).
4. Decide: one side changed → propagate (respecting `direction`, `propagate_deletes`);
   both changed → conflict policy (`newest-wins` + conflict copy | `local-wins` | `remote-wins` | `ask`);
   deleted vs modified → keep modified. First run: equal size+mtime → synced, unequal → policy,
   one side only → copy, **never delete**.
5. Execute; after each action update + atomically write the snapshot. 6. Summary.

### Config (`~/.config/androsync/config.yaml`)
```yaml
device: auto                 # serial from `adb devices`, or "wifi:192.168.1.20:5555"
service:
  listen: 127.0.0.1          # add "0.0.0.0" later for Wi‑Fi; USB needs only localhost
  port: 8765
  token: <generated on first `service install`>
  auto_sync_on_connect: false   # true = run all pairs when the phone is plugged in
  notify: true                  # desktop notification via notify-send
defaults:
  conflict: newest-wins
  keep_conflict_copies: true
  mtime_tolerance_s: 2
  ignore: [".git/**", ".trash/**", "*.tmp"]
pairs:
  - name: photos
    local: ~/Pictures/Phone/DCIM
    remote: /sdcard/DCIM
    direction: phone-to-laptop
    propagate_deletes: false
  - name: vault
    local: ~/Documents/Obsidian/MyVault
    remote: /sdcard/Documents/Obsidian/MyVault
    direction: two-way
    ignore: [".obsidian/workspace*.json"]
```

### CLI
```
androsync devices | ls [/sdcard/path] | add-pair | status [pair] | run [pair] [--dry-run] [--yes] | resolve [pair]
androsync serve                       # foreground daemon (what systemd runs)
androsync service install|start|stop|status|uninstall|logs
androsync qr                          # prints token + URLs as QR for the phone app
```

### HTTP API (daemon ↔ phone app), header `Authorization: Bearer <token>`
```
GET  /v1/ping            -> {name, version, transport: "usb"|"wifi", device: serial|null}
GET  /v1/pairs           -> [{name, local, remote, direction, last_run, last_result}]
GET  /v1/status/<pair>   -> dry-run plan {copies_to_phone, copies_to_laptop, deletes, conflicts}
POST /v1/run             -> {pairs:[...], dry_run:false} -> {job_id}   (409 if a job is running)
GET  /v1/jobs/<id>       -> {state: queued|running|done|failed, summary, log_tail}
GET  /v1/jobs/<id>/log   -> text/event-stream (live log)
POST /v1/jobs/<id>/cancel
```

### Service start/stop hints (goes verbatim into README and `androsync service --help`)
```
androsync service install      # writes ~/.config/systemd/user/androsync.service, enables it,
                               # runs `loginctl enable-linger $USER` so it starts at boot, not only at login
androsync service status       # = systemctl --user status androsync
androsync service stop         # stop now (starts again at next boot)
androsync service start
androsync service uninstall    # = systemctl --user disable --now androsync; removes the unit file
androsync service logs         # = journalctl --user -u androsync -f
```
Switch off permanently but keep it installed: `systemctl --user disable androsync`.
Pause auto-sync only: set `service.auto_sync_on_connect: false`, then `androsync service restart`.

### Network, Tailscale and firewall (same pattern as another self-hosted app on this laptop)
Facts found on this laptop: ufw is **enabled**; Tailscale is up, laptop = `<laptop-ts-ip>`
(`<laptop-hostname>`), phone = `<phone-ts-ip>` (`<phone-hostname>`), tablet = `<tablet-ts-ip>`.
Another locally-installed service on this laptop already binds each port to `127.0.0.1` **and**
the tailnet IP only, never `0.0.0.0`, with a matching ufw rule on `tailscale0`.
androsync copies that pattern:
- `service.listen` accepts a list; default `["127.0.0.1"]`; the install command offers to add the
  Tailscale IP (detected with `tailscale ip -4`). Never bind `0.0.0.0`.
- Firewall: `androsync service install` prints (and with `--ufw` runs via sudo):
  `sudo ufw allow in on tailscale0 to any port 8765 proto tcp comment 'androsync'`.
  `uninstall --ufw` deletes that rule. USB path (`adb reverse`) needs no firewall rule.
- Phone app presets: **USB** `http://127.0.0.1:8765`, **Tailscale** `http://<laptop-ts-ip>:8765`
  (works from anywhere the phone has internet), **LAN** `http://<lan-ip>:8765`.
- Remote file sync (phone not on the cable): `device: wifi:<phone-ts-ip>:5555` → `adb connect`
  over the tailnet. Needs Wireless debugging enabled on the phone. Tailscale traffic is already
  encrypted, so plain HTTP with the bearer token is acceptable; HTTPS with a locally-trusted CA
  (the same `gen-certs.sh` pattern that other app already uses) is an optional later task for the
  LAN preset.
- Token stays required on every interface, including localhost.

### ADB details (`transport/adb.py`, `daemon/watcher.py`)
- Selection: `adb -s <serial>`; `auto` = the single device, error on 0 or >1.
- Scan: `adb exec-out "find '<root>' -type f -printf '%s\t%T@\t%P\0'"` (toybox ≥0.8, Android 11+);
  fallback `find ... -exec stat -c '%s %Y %n' {} +`. `shlex.quote` every path.
- Fetch `adb pull -a`; store `adb push` + `touch -m -d @<epoch>` if mtime not kept (self-test at startup).
- Watcher: `adb track-devices` stream; on `device` state → `adb reverse tcp:8765 tcp:8765`,
  desktop notification, optional auto-sync. On disconnect → mark job failed if running.
- Media scan broadcast after writing into `/sdcard/DCIM|Pictures|Movies|Music`. `/sdcard` is
  case-insensitive → warn on case collisions.

### Android app "AndroSync Remote" (Kotlin, Compose, minSdk 26)
Screens: **Setup** (enter laptop URL + token or scan QR; USB preset = `http://127.0.0.1:8765`),
**Pairs** (list with last result, per-pair "Sync" and "Preview", "Sync all"),
**Job** (live log via SSE, summary, cancel), **Settings** (USB/Wi‑Fi address, token, auto-refresh).
Extras: Quick Settings tile "Sync now", home‑screen widget, foreground notification during a job.
Connection badge: "USB", "Wi‑Fi", or "Laptop not reachable" (ping every 5 s while app is open).
The app has no file access; all file work stays on the laptop.

### Later
- **Wi‑Fi**: `device: wifi:<ip>:5555` → `adb connect`; `service.listen: 0.0.0.0`; app uses LAN URL.
- **Version control**: pair option `versioning: git` → commit local side after a successful run.
- **Desktop GUI (Tk)** editing the YAML and calling the same functions as `cli.py`.
- **MTP fallback transport** only if ADB is ever unavailable.

---

## Task list for Sonnet

> Status 2026-10-15: next up is Phase O (36a–36d) then Phase P (37-doc, 37f, 37g, optional agent mode), see NEXT_TASKS.md.
> Next: the rest of Phase L (33a–33d), then Phase N (35a–35f) - both in NEXT_TASKS.md.

One bounded change per task, each with tests or a checkable result, ≈ 20–60 min.
Milestone 1 (manual USB sync works) = tasks 1–12. Milestone 2 (service + phone app) = 13–24.

### Phase A — skeleton and pure logic (no phone)
1. **Skeleton.** `laptop/pyproject.toml` (deps `pyyaml`, dev `pytest`, script `androsync`), `cli.py` with `--version`, README stub, `.gitignore`, `git init` at repo root. Check: `pip install -e laptop && androsync --version`.
2. **Models + ignore rules.** `model.py` dataclasses/enums; `ignore.py` `IgnoreRules.matches(rel)` with `*`, `**`, `dir/**`. Tests.
3. **Config loader.** `config.py`: YAML → validated objects, defaults merged into pairs, `~` expanded, errors with pair name. Tests good/bad.
4. **Snapshot store.** `state.py` load/save JSON, atomic write, `$XDG_DATA_HOME` respected. Tests.
5. **Planner core.** `plan()` for `two-way`, first run and later runs, tolerance. Table-driven tests per decision row.
6. **Planner directions + policies.** One‑way directions, `propagate_deletes: false`, `local-wins`/`remote-wins`/`ask`, `conflict.py` naming. Tests.

### Phase B — local transport and executor (no phone)
7. **Transport ABC + LocalDirTransport.** `os.walk`, `copy2`, `utime`, skip symlinks. Tests.
8. **Executor.** Temp-dir copies, conflict copy = rename loser then copy winner, snapshot saved per action, `Summary`, `dry_run`. Tests: two runs → second is no-op.
9. **E2E suite** with two temp dirs: first sync, edit, delete, rename, conflict, no‑delete mode, ignores.

### Phase C — ADB transport (phone plugged in)
10. **AdbTransport scan + `devices`/`ls` commands.** `find -printf` with `stat` fallback, NUL parsing, quoting. Manual: `androsync ls /sdcard/DCIM`.
11. **AdbTransport transfer ops** + mtime self-test. Test skipped when no device.
12. **`status` / `run` commands.** Wire everything; `y/N` prompt, `--yes`, `--dry-run`, exit codes 0/1/2. Manual check on vault + DCIM. **Milestone 1.**

### Phase D — laptop service
13. **Job runner.** `daemon/jobs.py`: one job at a time (lock), thread, ring-buffer log with sequence numbers, cancel flag checked between actions, `last_result` per pair persisted in state dir. Tests with LocalDirTransport.
14. **HTTP API.** `daemon/server.py` on `http.server.ThreadingHTTPServer`: endpoints above, bearer token, JSON errors, SSE log stream. Tests with `urllib` against a server on a random port using local transports.
15. **Device watcher.** `daemon/watcher.py`: parse `adb track-devices`, on connect run `adb reverse`, `notify-send`, `auto_sync_on_connect`; reconnect loop if adb server dies. `androsync serve` starts watcher + server. Manual: plug/unplug shows notifications; `curl -H "Authorization: Bearer $T" http://127.0.0.1:8765/v1/ping`.
16. **`service` + `qr` commands.** Unit file template (`Restart=on-failure`, `After=default.target network-online.target`, `ExecStart=%h/.local/bin/androsync serve`), `install` generates token if missing, enables unit, `loginctl enable-linger`; `start/stop/status/logs/uninstall/restart`; `qr` prints token + all URLs (USB, Tailscale, LAN) with `qrcode` lib (ASCII). README section "Start / stop the service" copied from above. Manual: reboot → `androsync service status` = active.
16b. **Tailscale bind + ufw rule.** `service.listen` list; `install` detects `tailscale ip -4` and asks to add it; `--ufw` runs the `ufw allow in on tailscale0 ... 8765` command via sudo and `uninstall --ufw` removes it; `serve` re-resolves the tailnet IP at start and retries bind for 30 s if `tailscale0` is not up yet. README "Remote access over Tailscale" section. Manual: from the phone on mobile data, `curl http://<laptop-ts-ip>:8765/v1/ping` with token works; from a non-tailnet host the port is closed.

### Phase E — Android app
17. **Project + API client.** Android Studio project under `android/`, Compose, Retrofit + OkHttp + kotlinx‑serialization, `DataStore` for URL/token, `ApiClient` with the endpoints above. Unit test parsing `/v1/pairs` JSON with MockWebServer.
18. **Setup screen + connection badge.** URL/token form, presets "USB (127.0.0.1:8765)" / "Tailscale (<laptop-ts-ip>:8765)" / "LAN", QR scan via ML Kit barcode, ping loop → badge showing USB / Tailscale / LAN / not reachable. The app tries USB first, then Tailscale, then LAN automatically. Manual: phone on cable shows "USB"; unplugged on mobile data shows "Tailscale".
19. **Pairs screen.** List from `/v1/pairs`, per‑pair Preview (shows `/v1/status`) and Sync, "Sync all"; pull-to-refresh; error toasts for 401/409/offline.
20. **Job screen.** Start job → navigate; SSE log view (OkHttp `EventSource`), progress summary, Cancel; foreground service + notification while a job runs so it survives screen off.
21. **Quick Settings tile + widget.** Tile "Sync now" runs all pairs; Glance widget with last result + button. Manual check.
22. **Settings + polish.** Auto-refresh interval, theme, "test connection" button, about screen with laptop version; app icon; release build signing notes in README.

### Phase F — extensions (independent, after Milestone 2)
23. **`add-pair` and `resolve` CLI commands** (interactive folder browser; per‑file conflict resolution for `ask` mode; the app shows pending conflicts read‑only).
24. **Batched push + media scan + case-collision warning.** Stage tree → single `adb push`; broadcast scan; planner warning. Timing before/after in the PR.
25. **Remote ADB over Wi‑Fi / Tailscale**: `device: wifi:<ip>:5555` → `adb connect` with retry, `pair-wifi` helper (`adb pair`), daemon watcher also treats a connected TCP device as "online". Default remote target = phone tailnet IP `<phone-ts-ip>`. Manual: phone off the cable, sync `vault` from the app over Tailscale.
25b. **Optional HTTPS for LAN preset** reusing the same `gen-certs.sh` local‑CA pattern another self-hosted app on this laptop already uses (`ssl` block in `service:`; app trusts the installed CA). Skip if Tailscale covers all remote use.
26. **Version control**: `versioning: git` commits local side after each successful run. Tests with temp repo.
27. **Desktop Tk GUI** (optional) and **MTP fallback** (only if needed).

### Phase G — manage folder pairs from the phone app (requested 2026-09-07)

Context: today pairs exist only in the laptop YAML; the app lists them read-only. The user wants an
"Add" button in the app, then pick both locations. Design: the laptop daemon gets pair CRUD + a folder
browser API; the app gets an Add/Edit pair screen with two folder pickers. Phone folder is picked
natively with the Android folder picker (SAF, no cable needed); laptop folder is browsed through the API.

28a. **Laptop API: pair CRUD + browse.** Files: new `laptop/androsync/config_edit.py` (move
  `_load_raw_config`/`safe_dump(sort_keys=False)` writing out of `service.py` into `load_raw(path)`,
  `save_raw(path, raw)`, `add_pair(raw, pair_dict)`, `update_pair(raw, name, pair_dict)`,
  `remove_pair(raw, name)`; validate by building a `Config` through `config.py` before saving so bad
  input never reaches disk), `laptop/androsync/daemon/server.py` (add to `_ROUTES`):
  - `POST /v1/pairs` body `{name, local, remote, direction, conflict?, propagate_deletes?, ignore?}` → 201 + pairs list;
    400 with the `ConfigError` message; 409 if the name exists.
  - `PUT /v1/pairs/<name>` same body (rename allowed: state snapshot file is renamed too) → 200.
  - `DELETE /v1/pairs/<name>?purge_state=1` → 200; deletes `<pair>.json`/`.meta.json` only with `purge_state`.
  - `GET /v1/browse?side=local|remote&path=<abs>` → `{path, parent, entries:[{name, is_dir, size}], writable}`.
    Local side: `os.scandir`, dirs first, hidden entries included but flagged `hidden`, restricted to
    `service.browse_roots` (default `[~]`, plus `/media/<user>` if it exists) → 403 outside; `path` empty
    → list the roots. Remote side: `AdbTransport.ls` via the runner's transport factory, 409 while a job runs,
    503 `{"error":"phone not connected"}` when `device_info().device` is None.
  - `POST /v1/mkdir` body `{side, path}` → creates the folder (local: `os.makedirs`; remote: `mkdirs`).
  The daemon already reloads the config per job, so no restart is needed after edits.
  Tests in `laptop/tests/test_server.py` + new `test_config_edit.py`: add/update/delete round trip keeps
  other YAML keys and comments-free structure; invalid direction → 400; duplicate → 409; browse local lists
  dirs first and refuses `/etc`; browse remote returns 503 without a device (mock `device_info`).

28b. **App: Add/Edit pair screen + folder pickers.** Files: `data/api/{Dto,AndroSyncApi,ApiClient}.kt`
  (`BrowseDto`, `PairRequest`, `createPair/updatePair/deletePair/browse/mkdir`), new `ui/pairs/PairEditorScreen.kt`
  + `PairEditorViewModel.kt`, new `ui/common/FolderBrowserSheet.kt`, new `data/SafPath.kt`, `ui/Routes.kt`
  (`pair/new`, `pair/{name}`), `ui/pairs/PairsScreen.kt`.
  - Pairs screen: a `FloatingActionButton` "+" (bottom right) → `pair/new`; each card gets an overflow
    menu with **Edit** and **Delete** (confirm dialog; checkbox "also delete sync history" → `purge_state`).
  - Editor form: name; **Laptop folder** field with a "Browse" button; **Phone folder** field with
    "Pick on phone" (SAF) and "Browse via laptop" buttons; direction as three segmented buttons
    (`laptop → phone`, `↔`, `←`); conflict rule dropdown (newest wins / laptop wins / phone wins / ask);
    "Propagate deletes" switch (off by default when direction is phone→laptop, matching the DCIM backup
    case); ignore patterns as a comma-separated text field. Save → POST/PUT → back to Pairs with a
    snackbar; API 400 shows the message under the field.
  - `FolderBrowserSheet(side)`: modal bottom sheet with breadcrumb, "Up", folder list from `/v1/browse`,
    "New folder" (→ `/v1/mkdir`), and "Choose this folder". Remote side shows "Connect the phone by USB
    first" on 503.
  - `SafPath.treeUriToPath(uri)`: `content://com.android.externalstorage.documents/tree/primary%3ADocuments%2FObsidian`
    → `/sdcard/Documents/Obsidian`; other volume ids → `/storage/<ID>/...`; anything else (Downloads
    provider, cloud) → null and the UI says "Pick a folder on internal storage or SD card". Launch with
    `ActivityResultContracts.OpenDocumentTree`; take no persistable permission (the app never reads files).
  - Unit tests: `SafPathTest` (primary, SD card, unsupported), form validation (empty name, remote must
    start with `/`, default propagate_deletes by direction), `PairRequest` JSON shape.
  Manual: add the Obsidian pair from the phone with the SAF picker, see it appear in `androsync status`.

28c. **CLI parity** (fold into task 23): `add-pair`, `edit-pair`, `remove-pair` use `config_edit.py`.

### Phase H — fewer false conflicts, no duplicates in the folders, pair rename (reported 2026-09-07)

Context: in a `media` pair (images and videos) the first sync produced a duplicate of nearly every file
on both sides. Cause: `planner._plan_both_changed` declares a conflict whenever size or mtime differ;
content is never compared. Media copied earlier by hand (MTP does not keep timestamps) has identical
content but different mtimes on the two sides, so every file became a "conflict" and
`executor._do_conflict_copy` renamed the loser and pushed it to the other side. A second cause of
noise: the snapshot stores one mtime per file, so two identical files with different timestamps would be
re-flagged on every run. Also: the app's edit form disables the Name field (`PairEditorScreen.kt`,
`enabled = uiState.isNew`) although `PUT /v1/pairs/<name>` and `state.rename_pair_state` already
support renaming.

Decisions (user answered with the `media` pair case; media files are the priority):
1. **Content check before any conflict**: same size on both sides → hash both sides; equal hash → not a
   conflict, just record it as synced. Different size or different hash → real conflict.
2. **Snapshot stores per-side mtimes** (`mtime_local`, `mtime_remote`) plus the hash when known, so
   identical files with different timestamps stay quiet after the first run. No file is touched to "fix"
   its timestamp.
3. **Real conflicts no longer put a copy next to the file.** The losing version goes to
   `<laptop folder>/.androsync/conflicts/<YYYYMMDD-HHMMSS>/<rel>` on the laptop only (`.androsync/**`
   is added to the default ignore list, so it is never synced). Listed by `androsync conflicts <pair>`,
   `GET /v1/conflicts/<pair>` and the app; `androsync conflicts restore <pair> <rel>` copies a saved
   version back over the file (next run syncs it out). Old behaviour stays available as
   `conflict_copies: beside` (new option; default `folder`; `none` keeps nothing).
4. **Clean-up of the existing duplicates**: `androsync conflicts clean <pair>` scans both sides for
   `*.conflict-(laptop|phone)-*` files; when the content equals the original, delete the copy on both
   sides; when it differs, move it into the conflicts folder on the laptop and delete it on the phone.
   Dry-run listing first, then `--yes`.

29a. **Hashing + per-side snapshot** (laptop, no phone needed for tests).
  Files: `model.py` (`FileEntry` gains `sha256: str | None = None`; new `SnapshotEntry(size, mtime_local,
  mtime_remote, sha256)` used as the snapshot value), `state.py` (load old `{size, mtime}` files by
  copying `mtime` into both sides), `transport/base.py` (`hash_many(root, rels) -> dict[rel, sha256]`,
  default loops `fetch` + hashlib), `transport/local.py` (hashlib, 1 MB chunks), `transport/adb.py`
  (`hash_many`: one `exec_out` per ≤200 paths: `cd <root> && sha256sum -- <quoted rels>`; parse
  `<hash>  <rel>` lines; fall back to `md5sum` if `sha256sum` is missing and store as `md5:<hex>`),
  `planner.py` (`plan()` gets a `hasher: Callable[[list[str]], dict[rel, (local_hash, remote_hash)]]`;
  in `_plan_both_changed`, when sizes are equal and hashes are equal → return `[]` and add `rel` to a
  `PlanResult.resynced` list; classification uses the side's own mtime from `SnapshotEntry`),
  `executor.py` (`run_pair` builds the hasher lazily so hashing only happens for conflict candidates, one
  batch per side; `reconcile_snapshot` writes per-side mtimes for `resynced` rels; `_do_copy`/batches record
  `mtime_local` and `mtime_remote` from the source entry and the dst `stat`).
  Tests: planner table rows "both changed, same size, same hash → nothing", "same size, different hash →
  conflict", "different size → conflict without hashing (hasher not called)"; snapshot migration from the old
  JSON shape; adb `hash_many` parsing with mocked subprocess (spaces, unicode, `md5sum` fallback); e2e: copy
  a tree to both sides with different mtimes → first run makes zero copies and zero conflicts, second run
  is a no-op.

29b. **Conflicts folder, `conflicts` command, clean-up** (laptop).
  Files: `config.py` (pair option `conflict_copies: folder | beside | none`, default `folder`; add
  `.androsync/**` to `defaults.ignore` and always-ignore it in `ignore.py` like `.git/**`), `conflict.py`
  (`conflict_folder_path(local_root, when, rel)`), `executor.py` (`_do_conflict_copy`: for `folder`, fetch
  the loser side's file into the conflicts folder on the laptop, do not rename, do not push anywhere; for
  `beside` keep the current code; then the winner copy proceeds as today), new `conflicts.py`
  (`list_conflicts(pair)`, `restore(pair, rel, when)`, `find_duplicates(pair, local, remote)` and
  `clean(pair, ..., dry_run)` implementing decision 4 with `hash_many`), `cli.py` (`conflicts list|restore|clean`),
  `daemon/server.py` (`GET /v1/conflicts/<pair>`, `POST /v1/conflicts/<pair>/restore`, `POST /v1/conflicts/<pair>/clean`
  with `{dry_run}`; 409 while a job runs). `laptop/README.md`: "Conflicts" section.
  Tests: executor conflict in `folder` mode leaves no `*.conflict-*` file on either side and one file under
  `.androsync/conflicts/`; `beside` mode unchanged; `clean` over two LocalDirTransports with one identical and
  one differing duplicate; API routes.

29c. **App: rename pairs, conflicts list** (android).
  Files: `ui/pairs/PairEditorScreen.kt` (remove `enabled = uiState.isNew`; keep the field always editable),
  `PairEditorViewModel.kt` (`save()` sends the new `name` in the PUT body; on success navigate back with the
  new name), `data/api/*` (`conflicts(pair)`, `restoreConflict`, `cleanConflicts`), `ui/pairs/PairsScreen.kt`
  (card shows "N saved conflict versions" when > 0; tapping opens a sheet listing date + path with **Restore**,
  and a **Clean duplicates** action that shows the dry-run result first, then confirms).
  Unit tests: PUT body contains the renamed `name`; conflicts DTO parsing.

### Phase I — undo a sync run (requested 2026-09-07)

Context: a sync can go wrong (the `media` duplication, a bad conflict decision, a folder pair pointed at
the wrong place). Today nothing can be reverted: overwritten files are gone and deletions are final.
Goal: `androsync undo <pair>` (and an **Undo** button in the app) puts both sides back to exactly how they
were before the last run. Also works for a run that is itself an undo (= redo).

Design:
- **Journal per run.** Every non-dry `run_pair` gets a `run_id` (`YYYYMMDD-HHMMSS-<4 hex>`) and writes
  `<state_dir>/undo/<pair>/<run_id>/journal.json` + `files/<local|remote>/<rel>`. Before an action
  changes a file on a side, the executor saves what is there now:
  * COPY over an existing file → back up the destination's old version (fetch to `files/<side>/<rel>`,
    store its old `FileEntry`), journal entry `{op: "overwrite", side, rel, old: entry, new: entry}`.
  * COPY creating a new file → no backup, entry `{op: "create", side, rel, new: entry}`.
  * DELETE → back up first, entry `{op: "delete", side, rel, old: entry}`.
  * CONFLICT_COPY (`beside` mode) → entries for the rename and the pushed copy; `folder` mode (29b)
    → entry `{op: "conflict_saved", path}` (undo removes the saved version again).
  * Batched copies (`store_many`/`fetch_many`, task 24) back up all existing destinations with one
    `fetch_many` before the batch.
  The journal also stores the pre-run snapshot (`snapshot_before.json`) and, at the end, the summary,
  the pair settings used, and `finished: true`. Journal lines are appended (`journal.jsonl`) after
  each action so a crashed run can still be undone as far as it got.
- **Undo = replay the journal backwards** through the normal transports, as a job (one job at a time):
  `create` → delete the file; `overwrite`/`delete` → put the backed-up version back with its old mtime;
  `conflict_saved` → remove the saved file; `beside` rename → rename back and delete the pushed copy.
  If a file changed since the run (current entry ≠ journal `new` entry) → skip it and report "modified
  after the sync, left as is" unless `--force`. Afterwards restore `snapshot_before.json` as the pair
  snapshot, so the pair is exactly where it was. The undo is journaled too, so `undo` of an undo is redo.
  Final message: "Restored N files on laptop, M on phone. The next run would repeat the same sync, so
  change the files or the pair settings first." Optional `--pause` sets the new pair flag `enabled: false`
  (`status`/`run`/auto-sync skip disabled pairs; the app shows them greyed with an Enable switch).
- **Retention:** `undo.keep_runs` (default 10 per pair) and `undo.max_total_mb` (default 2048) in the
  config `defaults`; oldest journals pruned after each run. Files larger than `undo.max_file_mb`
  (default 500) are not backed up: the journal marks them `backup: "skipped"` and undo reports them.
  Backups live on the laptop under `$XDG_DATA_HOME/androsync/undo/`, never inside the synced folders.
- **Media pull of DCIM costs nothing extra**: created files are only listed, not copied. The cost is one
  extra fetch per overwritten or deleted file.

30a. **Journal writing in the executor** (laptop).
  Files: new `laptop/androsync/undo.py` (`RunJournal` class: `start(pair, snapshot_before, settings)`,
  `backup(side, rel, transport, root, entry)`, `record(entry_dict)`, `finish(summary)`, `list_runs(pair)`,
  `load(pair, run_id)`, `prune(pair, keep_runs, max_total_mb)`; paths under `state_dir()/undo`), `state.py`
  (`undo_dir(pair)`, included in `rename_pair_state`/`purge_pair_state`), `config.py` (`undo` block in
  `defaults`: `keep_runs`, `max_total_mb`, `max_file_mb`, `enabled: true`; pair flag `enabled: bool = True`),
  `executor.py` (`run_pair` creates the journal when not `dry_run`; `_do_copy`, `_flush_copy_batch`,
  `_do_delete`, `_do_conflict_copy` call `journal.backup` before writing and `journal.record` after;
  `Summary` gains `run_id`), `daemon/jobs.py` (job dict includes `run_ids` per pair).
  Tests: journal contents for each op with two LocalDirTransports; backup skipped above `max_file_mb`;
  prune keeps N newest; crash mid-run (raise inside a fake transport) leaves a usable partial journal.

30b. **`undo` command, API, pause flag** (laptop).
  Files: `undo.py` (`undo_run(pair, run_id, local, remote, *, dry_run, force, log, cancel) -> UndoSummary`
  replaying in reverse, journaling itself with `kind: "undo"`, restoring `snapshot_before`), `cli.py`
  (`androsync runs <pair>` lists run_id, time, kind, summary; `androsync undo <pair> [--run ID] [--dry-run]
  [--force] [--pause]`; `status`/`run` skip `enabled: false` pairs with a note), `daemon/jobs.py`
  (`submit_undo(pair, run_id, force)` runs `undo_run` as a job), `daemon/server.py` (`GET /v1/runs/<pair>`,
  `POST /v1/undo/<pair>` body `{run_id?, dry_run?, force?, pause?}` → 202 `{job_id}`; `PUT /v1/pairs/<name>`
  already accepts `enabled`). `laptop/README.md` "Undo a sync" section.
  Tests: e2e with two LocalDirTransports: first sync → undo → both trees byte-identical to before and
  snapshot empty; sync with overwrite + delete → undo restores content and mtime; file edited after the
  sync is skipped without `--force`; undo of undo = redo; `--pause` disables the pair and `run` skips it;
  API 202/404/409 paths.

30c. **App: runs list and Undo button** (android).
  Files: `data/api/*` (`runs(pair)`, `undo(pair, runId, pause)`), `ui/job/JobScreen.kt` (when the job is
  done and has a `run_id` per pair: "Undo this sync" button → confirm dialog with a "Pause the pair
  afterwards" checkbox → POST undo → navigates to the new job), `ui/pairs/PairsScreen.kt` (overflow menu
  gets "Undo last sync…" which opens a sheet listing the last runs with time and summary; disabled pairs
  shown greyed with an Enable switch calling `updatePair(enabled = true)`).
  Unit tests: runs DTO parsing; undo request body.

### Phase J — terminal console app, autostart on demand (requested 2026-09-13)

Context: the service is installed as an always-on systemd user unit with linger (starts at boot).
Measured on 2026-09-13 after 13 h uptime: 17 MB RSS, 0.2 % CPU idle, 9 min CPU total (the sync runs),
peak cgroup memory 1.5 GB = page cache of copied files, not process memory. So it is cheap, but the user
wants control: no autostart by default, a desktop app that opens a terminal and runs the service there
(closing the terminal stops it), with the option to switch to background + autostart from that terminal,
and detection of an already running service when the app is opened again.

Decision (2026-09-13): the desktop app is the **Tk GUI from task 27**, not a terminal menu. Tkinter 8.6
is installed (`python3-tk`), GNOME desktop. The GUI has a Service panel with exactly the controls below,
plus pairs/status/run/log, so one app covers both wishes. The state-detection and autostart logic lives
in a backend module shared by the GUI and the CLI (`androsync service status|autostart`), and
"closing the window stops the service" holds because in "run here" mode the daemon runs inside the GUI
process.

Design:
- **State detection** (backend `service_state.py`, used by the GUI window on open and every 5 s):
  1. `systemctl --user is-active androsync` → "running as background service" (+ autostart on/off via
     `is-enabled`, uptime via `show -p ActiveEnterTimestamp`).
  2. else a pid file `$XDG_RUNTIME_DIR/androsync/daemon.pid` (written by `Daemon.start`, removed on
     stop) whose pid is alive **and** `GET /v1/ping` on `127.0.0.1:<port>` answers → "running in another
     terminal (pid N)".
  3. else "not running".
- **GUI window `androsync gui`** (Tkinter + ttk, one `tk.Tk` root, all daemon work in threads, UI
  updates via `root.after`). Three areas:
  * **Service panel** (top). Status line: "Not running" / "Running here (this window)" / "Running in
    background, autostart on|off" / "Running in another process (pid N)", plus device and URLs from
    `/v1/ping`. Buttons by state:
    - not running: **Run here** (daemon runs inside the GUI process; closing the window stops it, after a
      confirm dialog "Stop the service too?" with a "Keep running in background instead" option that
      hands over via `systemctl --user start` then exits), **Start in background** (`systemctl --user
      start`, no autostart), checkbox **Start at boot** (= `autostart on|off`: enable/disable + linger).
    - running: **Stop**, the **Start at boot** checkbox, **Show QR** (token + URLs for the phone app).
  * **Pairs panel** (middle): table of pairs (name, direction, last run, last result, enabled), buttons
    **Preview**, **Sync**, **Sync all**, **Undo last**, **Add…/Edit…/Remove** using the existing
    `config_edit.py`, `browse.py` (laptop folder via `filedialog.askdirectory`, phone folder via a small
    tree dialog over `AdbTransport.ls`), and `conflicts.py`. Runs go through the daemon API when it is
    running (so the phone app sees them too) and through `run_pair` directly when it is not.
  * **Log panel** (bottom): scrolled text fed by the in-process daemon logger, the SSE job log, or a tail of
    the daemon log file when the daemon runs elsewhere.
  Window close = `[q]`: leaves a background service running, asks only when the daemon runs in-process.
- **Daemon log file** in every mode: `serve` and the GUI's in-process daemon add a `RotatingFileHandler`
  (`$XDG_STATE_HOME/androsync/daemon.log`, 2 MB × 3), so a daemon started elsewhere can be followed.
  A pid file `$XDG_RUNTIME_DIR/androsync/daemon.pid` is written by `Daemon.start` and removed on stop;
  `run_forever` also handles SIGHUP so a killed session shuts down cleanly.
- **Autostart becomes opt-in**: `androsync service install` writes the unit and starts nothing unless
  `--autostart`; new `androsync service autostart on|off` (enable/disable + linger on/off) and
  `androsync service status` prints the detected state (exit 0 running / 3 not running); `serve` refuses to
  start when the port already answers a ping (prints who owns it). The hints block gains the new commands.
- **Desktop launcher**: `androsync service install-launcher` writes
  `~/.local/share/applications/androsync.desktop` (`Name=AndroSync`, `Terminal=false`,
  `Exec=<abs androsync> gui`, `Icon=androsync`, `Categories=Utility;`) and
  `~/.local/share/icons/hicolor/scalable/apps/androsync.svg` (laptop + phone + two arrows, same motif as
  the Android icon), then runs `update-desktop-database` if present. `uninstall-launcher` removes both.

31a. **Service state backend, pid/log/SIGHUP, autostart opt-in** (laptop, no GUI yet).
  Files: new `laptop/androsync/service_state.py` (`detect_state() -> ServiceState(mode: systemd|inprocess|
  other|none, pid, autostart: bool, since, ping: dict|None)`, `start_background()`, `stop_background()`,
  `set_autostart(on)`, `tail_daemon_log(n)`), `daemon/watcher.py` (pid file in `Daemon.start`/`stop`;
  SIGHUP in `run_forever`), `cli.py` (`serve` refuses to double-start; shared `setup_logging(file=True)`),
  `service.py` (`install` without enable/start unless `--autostart`; `autostart on|off`; `status` uses
  `detect_state`; hints updated), `laptop/README.md` ("Autostart" section).
  Tests (mocked `subprocess.run`, fake ping, `tmp_path` runtime dir): the four modes; stale pid file ignored;
  `serve` double-start refusal; install without `--autostart` runs only `daemon-reload`; `autostart on` =
  enable + linger, `off` = disable + disable-linger; `status` exit codes.

31b. **Tk GUI** (laptop). Files: new `laptop/androsync/gui/` package (`app.py` window + `root.after` poller,
  `service_panel.py`, `pairs_panel.py`, `log_panel.py`, `dialogs.py` for pair editor / phone folder tree /
  QR), `cli.py` (`gui` subcommand; exits 2 with a hint if `tkinter` is missing: `sudo apt install
  python3-tk`), `daemon/watcher.py` if the in-process daemon needs a `start()`/`stop()` usable from a
  thread without signals. Keep every panel free of business logic: they call `service_state.py`,
  `config_edit.py`, `browse.py`, `conflicts.py`, `undo.py`, the daemon API client (add a tiny
  `laptop/androsync/apiclient.py` over `urllib` reusing the token from the config).
  Tests: the pure helpers (state → button set, pair row formatting, close-window decision table) as plain
  functions with unit tests; GUI smoke test creating the window with `Tk()` under `pytest.importorskip`
  and `xvfb-run` if available, skipped otherwise.

31c. **Launcher + icon** (laptop). Files: `service.py` (`install-launcher`/`uninstall-launcher`), new
  `laptop/androsync/assets/androsync.svg` (packaged via `package_data`), `laptop/README.md` ("Desktop app").
  Tests: launcher file contents; uninstall removes both files; svg packaged (importlib.resources).

Immediate one-off for the user, independent of the code (needs no restart of the running service):
`systemctl --user disable androsync` stops the boot autostart now and keeps the service running.

### Phase K — bug fixes from 2026-09-25: window will not close, syncs fail with a pinned Wi‑Fi device

Context (evidence gathered 2026-09-26, read-only):
- A `androsync gui` process from 2026-09-25 21:44 is still alive (pid 786907), daemon already stopped
  (port 8765 closed, no pid file), only the Tk main loop left. The user journal shows three identical
  tracebacks at 21:50:13/21:50:17 from `App._on_close` → `root.destroy()`:
  `TypeError: unhashable type: 'StringVar'` in tkinter `destroy` (`if self._name in self.master.children`).
  Cause: `gui/dialogs.py:145` `PairEditorDialog.__init__` sets `self._name = tk.StringVar(...)`, which
  overwrites Tkinter's internal widget-name attribute `_name` on that `Toplevel`. Once a pair editor
  was opened, destroying the root fails at that child, so the window never closes. The in-process daemon
  had already been stopped by the "Yes" branch, so the user was left with a dead window.
- Yesterday's syncs: four `POST /v1/run` (21:45:24, :39, :47, 21:46:38) all ended without any pair
  meta or undo journal being written → every job failed before the first file action. Job errors live only
  in the in-memory job log, so the daemon log has nothing; the Wi‑Fi reconnector logs at DEBUG. Config has
  `device: wifi:<phone-ts-ip>:5555`. `Daemon._default_transport_factory` →
  `resolve_device("wifi:...")` → `adb connect <phone-ts-ip>:5555`, which ignores the USB-attached phone
  (`adb reverse` for `<phone-serial>` was set up at 21:45:37) and fails when the phone is not listening on
  5555 (Android's Wireless debugging uses a random port; `adb tcpip 5555` resets on every phone reboot).
  So both "USB" and "wireless" runs failed for the same reason: the pinned Wi‑Fi target.

32a. **GUI: fix the close bug and make shutdown robust** (laptop).
  Files: `gui/dialogs.py` (rename `self._name/_local/_remote/_direction/_conflict/_propagate_deletes` to
  `self._name_var` etc.; add a test that constructs every dialog class under xvfb/skip and asserts
  `isinstance(dialog._name, str)`), `gui/app.py` (`_on_close`: run `daemon.stop()` in a worker thread with
  the panel showing "Stopping…" and buttons disabled, then destroy from the event poller; wrap
  `root.destroy()` in `try/except Exception` → log + `root.quit()` as fallback so the process always
  exits; on "No, keep in background" wait for `start_background` to succeed before stopping the in-process
  daemon so the port is never held by both), `gui/logic.py` (pure `shutdown_steps(mode, answer)` decision
  table with tests). Also install a `sys.excepthook`/`report_callback_exception` on the root that logs Tk
  callback exceptions to the daemon log file instead of stderr only.
  Manual: open a pair editor, cancel it, close the window → process gone (`pgrep -f "androsync gui"` empty).

32b. **Device selection: prefer what is actually connected** (laptop).
  Files: `transport/adb.py` (`resolve_device(config_device, *, wifi_target=None)`: 1) a USB device in
  state `device` wins; 2) else an already-connected TCP device; 3) else if a Wi‑Fi target is known, `adb
  connect` it; 4) else `AdbError` listing what `adb devices -l` showed and what was tried. `wifi:IP:PORT`
  stays accepted and now means "auto, with this Wi‑Fi target"), `config.py` (new optional `wifi_target:
  "IP:PORT"`; `device: auto` + `wifi_target` is the documented form; `config.example.yaml` updated),
  `daemon/watcher.py` (`_default_transport_factory` passes the target; `on_connect` for a USB serial runs
  `adb -s <serial> tcpip 5555` when `wifi_target` ends with `:5555` and `service.enable_tcpip_on_usb`
  (default true), so the phone listens for the later Tailscale sync until its next reboot; log it at INFO),
  `cli.py` (`devices` shows which one would be picked and why; `pair-wifi` writes `wifi_target` + `device:
  auto`), `laptop/README.md` (Wi‑Fi section: why 5555, that it resets on reboot, `enable_tcpip_on_usb`).
  Tests (mocked `subprocess.run`): USB + wifi target → USB picked, no `adb connect`; no USB, TCP device
  present → TCP picked; nothing → `adb connect` tried; connect failure message names the target; `tcpip
  5555` issued once per USB connect when enabled, never when disabled or when target port ≠ 5555.

32c. **Make failures visible** (laptop + app).
  Files: `daemon/jobs.py` (pair exception and job failure also go to `logging.getLogger(__name__).warning`
  with job id, pair, message; job dicts keep `error`), `executor.py`/`runlog.py` (a failed run still writes
  `last-run.log` with the error), `state.py` (`save_pair_meta` on failure too: `last_result: "FAILED: <msg>"`,
  `last_error`), `daemon/watcher.py` (`WifiReconnector` logs the first failure and every state change at
  INFO, repeats at DEBUG), Android `PairsScreen` (red "Last run failed: <msg>" line from `last_result`),
  GUI `pairs_panel.py` (same, in the Last result column). Tests: failing transport factory → warning logged
  (caplog), meta written with `FAILED:`; reconnector log cadence with a fake clock.

### Phase L — several phones and several PCs (asked 2026-09-26)

Today's limits (facts from the code):
- One phone per laptop: `Config.device` is a single value; `device: auto` errors when two phones are
  attached; the snapshot, meta, pending and undo state are keyed by pair name only
  (`state._snapshot_path(pair_name)`), so two phones syncing the same pair would corrupt each other's
  "last seen" state and produce false deletes/conflicts.
- One laptop per phone app: `SettingsRepository` stores one `base_url`, one `token`, one `tailscale_url`;
  the Tailscale preset `<laptop-ts-ip>` is hard-coded in `ApiClient.kt`, and `DEFAULT_WIFI_CONNECT_IP`
  in `cli.py` is this phone's tailnet IP.
- Different PCs already work in the sense that each PC has its own config, state and service; nothing is
  shared between PCs. A second phone works too if it is the only one attached and uses its own pairs, but
  two phones must never share a pair name.

Design:
- **Pairs bind to a phone.** New pair field `device: <serial or "any">` (default `any`). State paths
  become `<state_dir>/<pair>@<serial>.json` (and `.meta`, `.pending`, `undo/<pair>@<serial>/`), migrated
  from the old names on first use when exactly one serial is known. `resolve_device` accepts a list of
  connected devices; a run with `device: any` and two phones attached picks the one whose serial has state
  for that pair, else asks (CLI) / lists both (API 409 `{"error":"ambiguous", devices:[...]}`).
- **Phones get names.** `service.devices: {<phone-serial>: {name: "S24 FE", wifi_target: "<phone-ts-ip>:5555"}}`
  in the config (written by `pair-wifi` and by the first USB connect with a placeholder name). Wi‑Fi
  reconnect runs per device. `/v1/ping` returns `devices: [{serial, name, transport, connected}]`.
- **App holds several laptops.** `SettingsRepository` becomes a list of `LaptopProfile(id, name, urls[],
  token)`; the QR from `androsync qr` carries `{name, urls, token}` so a new PC is added by scanning once;
  a laptop chooser in the top bar; `ConnectionProbe` probes the selected profile's URLs in order and
  drops the hard-coded Tailscale preset (the QR supplies it). Tile and widget act on the selected profile.
- **Phone-side identity.** The app sends `X-Device-Serial: <Build.SERIAL is unavailable; use
  Settings.Secure.ANDROID_ID>` only for display; the laptop decides by ADB serial. `/v1/pairs` gains
  `device` and the Pairs screen filters to pairs bound to "this phone or any" (the daemon maps the
  connected USB serial to the app's ANDROID_ID when both are present during a sync and remembers it).

33a. **Laptop: per-device state + pair `device` field** (`config.py`, `state.py` with migration,
  `executor.py`/`undo.py`/`conflicts.py` path helpers, `transport/adb.py` `resolve_device` for lists,
  `daemon/jobs.py` ambiguous → error). Tests: migration, two serials keep separate snapshots, ambiguity.
33b. **Laptop: device registry + per-device Wi‑Fi** (`service.devices`, `pair-wifi`, watcher per-device
  reconnectors, `/v1/ping` devices list, `devices` CLI shows names). Tests with mocked adb.
33c. **App: laptop profiles + QR onboarding** (`SettingsRepository` migration from single values,
  chooser UI, `ConnectionProbe` per profile, QR payload `name`, tile/widget on the selected profile).
33d. **App + API: pair ↔ phone binding in the UI** (`/v1/pairs.device`, editor field "Phone: any / this
  phone", Pairs list filter, ANDROID_ID mapping).

### Questions answered 2026-09-27 (candidates, not yet planned)

- **One README for the whole system**: today there is no root `README.md`; `laptop/README.md` (15 KB),
  `android/README.md` (9 KB), `CODE_INDEX.md`, `PLAN.md`, `NEXT_TASKS.md`. Candidate: root `README.md` =
  what it is, how the pieces fit (diagram), 10‑minute setup for laptop + phone, links into the two detailed
  READMEs. About 1 hour.
- **Other platforms**: the laptop side is Python + `adb` + `systemd` + Tk. macOS: `adb` via Homebrew,
  replace `systemctl --user` with a `launchd` plist, `notify-send` with `osascript`, Tk works → 1–2 days.
  Windows: `adb` from platform-tools, service via Task Scheduler or a tray app, path handling
  (`os.path` already used; NTFS fine), Tk works → 2–3 days. iOS: impossible in this design; iOS has no ADB
  and no shared filesystem. Only a Files‑app/iCloud based approach or a paid tool would work → out of scope.
- **USB debugging**: required. ADB is the only channel that gives a PC per-file timestamps, hashes and
  writes on `/sdcard`. "File transfer (MTP)" mode gives no hashes, cannot set timestamps and is slow. The
  authorisation prompt happens once per PC ("Always allow from this computer"), after that plugging in
  is enough; the toggle itself stays on. An MTP fallback (task 27) would work without USB debugging but
  with size-only comparison and no "newest wins".
- **Logs**: the daemon keeps every job's log in memory and streams it over `/v1/jobs/<id>/log`; any
  client (phone app, GUI, `curl`) can follow a job started elsewhere, the app does it through the "Open"
  action on the "already running" message. On the laptop: `daemon.log`, `last-run.log`, `androsync runs`.
  Gap: the app has no "recent jobs" screen (`GET /v1/jobs` exists, unused) and shows only the last 20
  lines of a finished job. Candidate: Jobs screen + full log persisted per job. About 2 hours.

### Phase M — one README for the whole system (chosen 2026-09-27)

34. **Root `README.md`** (about 1.5 hours, no code changes). Written for two readers, users first,
  developers at the end. Plain language in the user part: no module names, no API paths, commands only
  where the user has to type them.

  **Part 1 — For users (start here)**
  1. *What it does*, five lines: keeps chosen folders identical on your Ubuntu laptop and your Android
     phone, both directions, over the USB cable or over Tailscale from anywhere. You start a sync from the
     phone app, the desktop window, or the command line. Nothing is uploaded to any cloud.
  2. *What you need*: an Ubuntu laptop, an Android phone (Android 11 or newer) with a USB cable, 15
     minutes. Optional: Tailscale on both for syncing away from home.
  3. *Before you start: USB debugging*. Why it is needed (the only way a PC can read timestamps and write
     files on the phone), how to turn it on (Settings → About phone → tap Build number 7 times → Developer
     options → USB debugging), and that the phone asks once per laptop: tick "Always allow from this
     computer". After that, plugging in is enough.
  4. *Install on the laptop*, numbered with exact commands and what you should see after each:
     clone, `pipx install ./laptop`, `androsync service install`, `androsync service install-launcher`,
     open "AndroSync" from the app grid, press "Run here".
  5. *Install on the phone*: install the APK (`adb install -r …` or copy the file), open the app, run
     `androsync qr` on the laptop, scan it, the badge turns "USB".
  6. *Your first folder pair*: tap "+", pick the phone folder with the phone's folder picker, pick the laptop
     folder, choose the direction with the three examples (photos: phone → laptop, backup style; Obsidian
     vault: both ways; music: laptop → phone), tap Preview, read the list, tap Sync.
  7. *Everyday use*: plug in and tap Sync, or use the Quick Settings tile or the widget; syncing over
     Tailscale when away; the Preview button before any sync you are unsure about.
  8. *When something looks wrong*: where the log is (Job screen, desktop window, `androsync service logs`),
     conflicts and the conflicts folder, "Undo this sync", `androsync conflicts clean` for old duplicate
     files. Three common errors with the fix: "unauthorized" (accept the prompt on the phone), "no device"
     (cable/USB debugging/Tailscale), "a sync is already running" (open it).
  9. *Good to know / limits*: first sync never deletes anything; one phone per laptop and one laptop per
     app today; Linux only; there will never be an iPhone version (no ADB on iOS).

  **Part 2 — For developers**
  10. How the pieces fit: Mermaid diagram (phone app → HTTP API → daemon → ADB → phone storage; GUI and
      CLI beside), one line per piece with links to `laptop/README.md`, `android/README.md`, `CODE_INDEX.md`.
  11. Project docs and history: `PLAN.md`, `NEXT_TASKS.md`; how to run both test suites; how tasks are
      handed to an agent (read NEXT_TASKS.md first).

  Also: trim the overview at the top of `laptop/README.md` and `android/README.md` to one line linking
  back to the root README. No new claims: every command in the README must exist in `androsync --help`,
  every screen name must exist in the app. Check: `grep -o 'androsync [a-z-]*' README.md | sort -u` all
  appear in `--help`; Mermaid renders on GitHub; a reader who only follows Part 1 ends with a working sync.

### Phase N — publish: GitHub, releases, F-Droid (asked 2026-09-27)

Facts: no git remote, `gh` not installed, no `LICENSE`, no CI, `versionCode 1` / `0.1.0` on both sides.
Personal data is in the repo: the author's tailnet IPs in `cli.py`, `ApiClient.kt`, both READMEs; the
phone serial, tailnet host names, pair and folder names in `NEXT_TASKS.md`/`PLAN.md`; the work e‑mail as
git author on every commit. The app depends on `play-services-code-scanner` (Google, proprietary) which
F-Droid rejects. User decisions: private repo first, then public; release on GitHub Releases and F-Droid;
license undecided, wants a way to ask for money or donations.

License recommendation (user's decision, default for the plan): **GPL-3.0-or-later** + donation links.
Reasons: F-Droid requires a free license, so "pay to download" is out there, but donations are supported
by F-Droid itself (`Donate`, `Liberapay`, `OpenCollective`, `Bitcoin` metadata fields) and by GitHub
(`FUNDING.yml`). GPL keeps forks open and still allows selling a convenience build on Google Play later,
as many GPL apps do. MIT/Apache would allow anyone to sell it.

35a. **Private GitHub repo + hygiene** (1 hour, needs the user's GitHub login once).
  Install `gh` (`sudo apt install gh`), `gh auth login`, `gh repo create androsync --private --source=.
  --push`. Before the push: `LICENSE` (GPL-3.0), `.github/FUNDING.yml` (GitHub Sponsors + Liberapay
  placeholders, user fills in), `CHANGELOG.md` (0.1.0 = everything so far), `.gitignore` check (no
  `local.properties`, `keystore.properties`, `*.jks`, `android/app/build`), `git config user.email` set
  to a personal or GitHub no‑reply address for future commits. Keep the full history in the private repo.
35b. **Scrub, then public** (2 hours, after task 33e).
  Remove personal data from tracked files: IPs (33e), phone serial, tailnet host names and IPs, real pair
  and folder names in `NEXT_TASKS.md`/`PLAN.md`/READMEs (replace with `<phone-serial>`, `100.x.y.z`,
  `vault`, `photos`); add `scripts/check_no_personal_data.sh` (grep list) wired into CI. Then publish as a
  fresh history: new orphan branch, one commit "Initial public release 0.1.0", force-push to a new public
  repo `androsync` (the private one is renamed `androsync-private` and kept as the archive). Reason:
  rewriting 100+ commits with `git filter-repo` is error-prone and the old messages carry the same data.
35c. **CI + GitHub Releases** (2 hours). `.github/workflows/ci.yml`: on push/PR run `pytest laptop`,
  `./gradlew :app:testDebugUnitTest :app:assembleDebug`, the personal-data check. `release.yml`: on tag
  `v*` build `assembleRelease` signed with a keystore from repository secrets (`KEYSTORE_B64`,
  `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`; `app/build.gradle.kts` already reads
  `keystore.properties`, add the env fallback), build the laptop wheel + sdist (`python -m build`), create
  the GitHub Release with the APK, wheel, sdist and the CHANGELOG section. `scripts/bump_version.py` keeps
  `pyproject.toml` `version`, `androsync.__version__`, `versionName`, `versionCode` (+1) in step.
  README: install from a release (`pipx install https://github.com/<user>/androsync/releases/download/…`).
35d. **F-Droid readiness** (half a day). Replace `play-services-code-scanner` with
  `com.journeyapps:zxing-android-embedded` (Apache‑2.0, needs the CAMERA permission and a runtime
  request); check every dependency against F-Droid's anti-features (Glance, Retrofit, OkHttp, DataStore,
  kotlinx are fine; the `foojay-resolver` Gradle plugin is build-only and accepted); add
  `fastlane/metadata/android/en-US/` (`title.txt`, `short_description.txt`, `full_description.txt`,
  `changelogs/<versionCode>.txt`, `images/icon.png`, phone screenshots); make the build reproducible (no
  `local.properties` dependency, pinned Gradle/AGP, `-Pandroid.injected.signing.*` off); then fork
  `fdroiddata`, add `metadata/com.androsync.remote.yml` (Builds entry with `subdir: android/app`,
  `gradle: [yes]`, `AutoUpdateMode: Version`, `UpdateCheckMode: Tags`, `Donate:`/`Liberapay:`), test
  with `fdroid build` in the fdroidserver Docker image, open the merge request. Expect 1–3 weeks review.
35e. **Donations in product** (1 hour). Donation link in the app's About screen and the GUI's About
  dialog, README badge, F-Droid metadata; text says the app is free software and donations are optional.
35f. **Google Play (optional, later).** $25 developer account; Play App Signing with an AAB
  (`bundleRelease`); privacy policy page on GitHub Pages ("no data leaves your devices"); Data safety
  form; new personal accounts need a 14‑day closed test with 12 testers before production; check the
  current target API level requirement at submission time. A paid Play build is allowed under GPL.

## Verification
- Phase N: `gh repo view` shows the private repo; CI green on the first push; `git tag v0.1.0 && git
  push --tags` produces a Release with a signed APK that installs over the debug build after uninstall
  (different signature) and a wheel that `pipx install` accepts; `scripts/check_no_personal_data.sh`
  exits 0 on the public branch; `fdroid build` succeeds locally; the app scans the QR with the new
  scanner on the real phone.
- Phase L: two phones on USB → `androsync devices` lists both with names; a pair bound to phone A syncs
  only A; `device: any` with both attached → ambiguity error, then pick with `--device`; phone B syncs its
  own pair, both state files exist; app: scan a second PC's QR, switch laptops in the chooser, both work.
- Phase K: (1) `pkill -f "androsync gui"` the stale pid 786907 first (user's call). Open the GUI, Add… a pair
  and cancel, close the window → no process left. (2) Phone on USB with `device: auto` +
  `wifi_target: <phone-ts-ip>:5555`: `androsync devices` says "picked <phone-serial> (usb)"; `androsync run
  vault --yes` succeeds; daemon log shows `tcpip 5555` once. Unplug, phone on Wi‑Fi/Tailscale:
  `androsync devices` says "picked <phone-ts-ip>:5555 (tcp, connected now)"; sync from the phone app works.
  Phone off both: `status` fails with a message naming the tried target, and the failure appears in
  `daemon.log`, `last-run.log`, the pair's meta and the app's pair card.
- Phase J: `androsync service install-launcher`; open "AndroSync" from the app grid → window shows
  "Not running"; **Run here** → phone app connects; close the window, answer "stop" → `androsync service
  status` says not running and no `androsync serve` process remains. Open the app again, **Start in
  background** + tick **Start at boot**, close the window, reopen → "Running in background, autostart on";
  untick → `systemctl --user is-enabled androsync` says disabled; **Stop** stops it. Sync a pair from the
  Pairs panel while the daemon runs in the background and see the same job in the phone app.
- `pytest laptop/` green after every task (A, B, D need no phone).
- Phase I on the phone: `androsync run media --yes` on a small test pair, then `androsync undo media
  --dry-run` lists the reverse actions, `androsync undo media` restores both sides (compare `find`
  listings and a few hashes before and after), `androsync runs media` shows the sync and the undo;
  `androsync undo media` again redoes it. From the app: sync `vault`, tap Undo on the job screen, check
  the note that was pushed is gone from the phone again.
- Phase H on the phone: run `androsync conflicts clean media` (dry run, then `--yes`) → all
  `*.conflict-*` duplicates gone from both sides; `androsync status media` → nothing to do; touch a photo's
  mtime on the laptop only → next `status` shows nothing (hash equal); edit the same note on both sides → the
  newer wins, the other version appears under `.androsync/conflicts/` and in the app, `restore` brings it
  back. Rename a pair in the app → `androsync status` lists the new name and its history is kept.
- Phone checks: `androsync ls /sdcard`; `status vault` first run shows copies, no deletes; `run vault --yes` then `status` = nothing to do; edit both sides → conflict copies on both sides; DCIM pull never deletes on laptop.
- Service: `androsync service install`, reboot, `androsync service status` active; plug phone → notification; `curl .../v1/ping` returns `transport: usb`.
- App: on cable, Setup shows "USB"; Sync of `vault` from the phone shows live log and the same summary as the CLI; unplug mid-job → job marked failed, app shows error.
