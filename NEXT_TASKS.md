# NEXT_TASKS — hand-off for the next implementation session

> **Issue numbers.** Numbers like "issue #10" in this file and in `PLAN.md` refer to the project's first, private repository
> (`androsync-private`, kept as an archive). The work still open was recreated in this repository as issues #1 to #8.


Read this file first, then `PLAN.md` (design, decision table, API, config) and `CODE_INDEX.md`
(one-line-per-file map of the codebase). As of 2026-10-14, every task through Phase J, Phase K
(32a–32c), task 33e and Phase M (task 34) below is DONE and committed - see each task's own
write-up for what was built, real-device/manual verification notes, and commit hashes. Task 27 (Tk
GUI) was superseded by Phase J and the MTP fallback stays build-only-if-asked. Open work as of 2026-10-15: **Phase O** (tasks 36a–36d: Stop that really stops, the running sync and its log
visible on every device, phone-path validation, safety guards for pair edits and mass deletes) and **Phase P**
(Syncthing guide, in-app USB-debugging helper, play/full build flavors, optional agent mode without ADB), both
near the end of this file. Earlier open work as of 2026-10-14: the rest of the optional **Phase L** (tasks 33a–33d: several phones and several PCs -
33e, the no-hardcoded-addresses task, is already done), then **Phase N** (tasks 35a–35f: GitHub,
releases, F-Droid, donations - needs the user's go-ahead on the license and on each step that
touches GitHub/a public release before acting). Both near the end of this file. This file is kept
as the detailed history, and new work gets its own task block appended in the same style.

## Current state (2026-09-06)

- Repo root: `<repo-root>` (NTFS drive: no symlinks,
  `core.filemode false` is set). Old broken copy `../Android_synconizer.broken-git` can be deleted.
- WARNING: the NTFS drive was remounted read-only at the end of the last session and is 100 % full
  (1.4 GB free). Before working: free space, then `sudo umount <data-drive-mount> && sudo ntfsfix /dev/nvme1n1p1`
  and let the automount remount it, or reboot. Check `mount | grep 612C` shows `rw`.
- Laptop side `laptop/` (Python 3.12, venv at `.venv/`): `.venv/bin/pytest laptop -q` → 231 passed, 2 skipped
  (the 2 skipped need a phone). CLI: `.venv/bin/androsync --help`.
- Android app `android/`: `cd android && ./gradlew --offline :app:testDebugUnitTest :app:assembleDebug`
  → 75 tests, APK at `android/app/build/outputs/apk/debug/app-debug.apk`.
- Nothing has been run against a real phone yet. `adb devices` was empty during the whole build.
- Config file is not created yet: copy `laptop/config.example.yaml` to `~/.config/androsync/config.yaml`.
- Tailnet facts: laptop `<laptop-ts-ip>`, phone (Galaxy S24 FE) `<phone-ts-ip>`. ufw is enabled.

## Conventions for every task

- Work only inside the repo. Keep modules small, type hints everywhere, tests next to the code
  (`laptop/tests/`, `android/app/src/test/`).
- Run the full test suite before committing. Commit per task with message
  `"<what> (task N)"`. No `Co-Authored-By` line and no "generated with" footer: the author chose not to list Claude
  as co-author (2026-10-17). Author is the GitHub username with the GitHub no-reply email; the `git-identity` skill asks again
  at the start of any new project.
- Never run `sudo`, `ufw`, `systemctl`, `loginctl` for real without telling the user first; the
  `androsync service` commands print each command before running it.
- Never bind `0.0.0.0`. Services listen on `127.0.0.1` and the Tailscale IP only (see PLAN.md).
- When a task needs the phone and none is attached (`adb devices` empty), say so and move to the next task
  that does not need it.

---

## Phase V — verification on the real phone (needs the phone on the USB cable, USB debugging on)

### V1. Milestone 1 device check and ADB fixes — DONE (2026-09-06)
Files: `laptop/androsync/transport/adb.py`, `laptop/tests/test_adb_device.py`.
Phone: Galaxy S24 FE (`<phone-serial>`), authorized over USB.
1. DONE — `adb devices` shows `device`.
2. DONE — both `test_adb_device.py` tests passed with no code changes needed.
3. DONE — but `ls` was actually broken and needed two fixes, both in `transport/adb.py`
   (commit `bd84cae`, tests added in `test_adb_transport.py`/`test_adb_device.py`):
   - Samsung's toybox `find` silently returns **zero** matches when the search root is a mount
     point (`/sdcard` → bind-mounted to `/storage/emulated/0`) given *without* a trailing slash;
     `find /sdcard/ ...` works. Fixed via a `_find_root()` helper used by `scan()` and `ls()`.
   - toybox `find -printf` rejects GNU find's `%y` file-type specifier ("bad -printf %y"); only
     `%M` (ls-style mode string, e.g. `drwxr-xr-x`) is supported. `ls()` now parses `%M` and checks
     `mode.startswith("d")`.
4. DONE — config created at `~/.config/androsync/config.yaml` (not in the repo):
   `vault`: local `~/Documents/Obsidian Vault` (665 files) ↔ remote `/sdcard/Obsidian/Notes` (the
   phone's real vault is `/sdcard/Obsidian/Notes` with only 5 files, unrelated content to the laptop
   vault — user chose to merge them as-is rather than point at a fresh phone folder; first run copied
   664→phone, 1→laptop, 3 conflicts, 0 deletes, ~0.5s no-op `status` afterwards).
   `photos`: local `~/Pictures/Phone/DCIM` (created, was missing — local root is never
   auto-created by the executor, only the remote root is for one-way-to-remote pairs) ↔ remote
   `/sdcard/DCIM` (13 GB on phone, 114 GB free there / 93 GB free on `/home`); first run pulled 1651
   files, 0 conflicts, 0 deletes, ~0.35s no-op `status` afterwards.
5. DONE — verified with disposable `androsync-test-*.md` files (not the user's real notes), cleaned
   up after: a laptop-only edit and a phone-only edit each propagated to the other side correctly;
   editing the same file on both sides (phone 2s newer) picked the phone version as winner on both
   sides and left `*.conflict-laptop-<timestamp>.md` (the laptop's losing version) on both sides too.
6. DONE — verified with a disposable `androsync-delete-test.txt` in `/sdcard/DCIM`: deleting it on
   the phone and re-running `photos` did NOT delete the laptop copy (`propagate_deletes: false` held).
Acceptance: all six passed. Timings for task 24: vault first run (665 files, 3 conflicts) a few
seconds; photos first run (1651 files) well under a minute; no-op `status` ~0.35–0.5s for both.

### V2. Service and app first run — ALL STEPS DONE (2026-09-06/07)
Files: `laptop/androsync/service.py`, `laptop/androsync/daemon/watcher.py`, `android/`.

Done: service installed and active, logs show the watcher; unplug/replug logs connect+disconnect
and fires notifications; `/v1/ping` returns `transport: "usb"`; app installed, connected over USB,
Pairs screen loads; tapped Sync on the app, job ran and a notification appeared.

Bugs found and fixed along the way (each its own commit):
- `service.py`: a config copied from `config.example.yaml` kept the literal placeholder
  `token: <generated on first...>` instead of getting a real token - fixed.
- `service.py`: the `sudo ufw` step failed silently (no TTY for the password) - now warns instead
  of pretending it worked. The user ran it manually afterward; confirmed working (step 6 below).
- `watcher.py`: phone disconnect wasn't logged at all - fixed.
- Android `PairsViewModel`: cold start raced the connection probe, so the Pairs screen got stuck
  forever on "Laptop not reachable" even after the badge went green and syncs worked - fixed,
  verified on the phone.
- Android `SyncTileService`: the Quick Settings tile stayed permanently lit ("active") at rest
  instead of only while syncing - `setTile()` forced `STATE_ACTIVE` on every call. Fixed: active
  only while syncing/blocked-by-a-running-job, inactive when idle, unavailable when it can't do
  anything (no token, unreachable). Confirmed on the phone: lights only while pressed, off after.
- Android `SyncWidget`: the home-screen widget had no background (Glance widgets are transparent
  by default) and "Sync now" was plain text with no button styling. Fixed: day/night-aware
  background + rounded corners on the widget and on a boxed "Sync now" button. Confirmed on the
  phone.

6. DONE (partial) — ufw rule applied; phone on mobile data, cable unplugged: badge shows
   "Tailscale", `/v1/ping` and job dispatch work over the tailnet. But the sync job and Preview
   both fail with "no device found": the daemon only talks ADB (`adb -s <serial>`), which needs
   the phone physically attached (USB or `adb connect`'d). File sync over Tailscale-only needs
   task 25 (remote ADB over Wi-Fi/Tailscale) — expected, not a bug.
7. DONE — Quick Settings tile and home-screen widget both fixed and confirmed on the phone.
8. DONE (2026-09-07) — rebooted the laptop; `androsync service status` was active without manual
   intervention (`loginctl enable-linger` holds), and it auto-set-up `adb reverse` for the phone
   ~25s after boot. Unplug-mid-job retest not separately re-run (step 6 already exercised the
   equivalent failure path via Tailscale disconnection).

---

## Phase F — extensions (independent of each other; do in this order)

### 23. `add-pair` and `resolve` CLI commands — DONE (2026-09-07)
`add-pair` (interactive + `--name/--local/--remote/--direction` non-interactive) and `resolve`
(picks local/remote/both per pending `conflict: ask` entry) both work; `executor.py` now persists
pending asks after every plan and exposes `resolve_pending()`; `GET /v1/pending/<pair>` added too.
Commits `9bb151b`, `34d14b1`.

Files: new `laptop/androsync/cli_pairs.py` (register via `add_pair_commands(subparsers, common)` from `cli.py`,
same pattern as `service.add_service_commands`), `laptop/androsync/state.py`, `laptop/androsync/executor.py`.
- `androsync add-pair`: prompts for name, local path (readline tab completion, must exist or offer to create),
  remote path via a numbered browser over `AdbTransport.ls` starting at `/sdcard` (`..` to go up, number to
  enter, `.` to pick current), direction, conflict rule, `propagate_deletes`. Appends to the YAML with
  `yaml.safe_dump(sort_keys=False)` preserving the other keys. `--name/--local/--remote/--direction` flags
  make it non-interactive (for tests).
- `resolve`: the planner already emits `ASK` actions for `conflict: ask`. Persist them: executor writes
  pending asks to `<pair>.pending.json` in the state dir (rel, local entry, remote entry). `androsync resolve [pair]`
  lists them and asks per file `local / remote / both (keep conflict copies) / skip`, then executes the
  chosen copies through the executor helpers and clears the entry. The HTTP API gets
  `GET /v1/pending/<pair>` (read-only) and the app's Pairs card shows a "N conflicts waiting" line.
Tests: YAML append preserves keys; non-interactive add-pair; pending file round trip; resolve with
scripted stdin over LocalDirTransports.

### 23b. Manage pairs from the phone app — laptop side (pair CRUD + folder browser API) — DONE (2026-09-07)
Built as specified below except `service.py` itself was left untouched (its own raw-config
write path is already tested and working; only `cli_pairs.py`/`server.py` needed `config_edit.py`
- not worth the regression risk for this feature). 320 passed, 4 skipped (no phone). Verified for
real: local browse against the actual `~`/`<media-mount-root>` roots, and full pair create/rename/
delete over HTTP against a throwaway daemon on a spare port (real deployed config.yaml untouched).
Commits `b70a626`, `34d14b1`.

Do this right after task 23; it shares the YAML editing code. Requested by the user on 2026-09-07:
"an add button in the app, then choose both locations".
Files: new `laptop/androsync/config_edit.py`, `laptop/androsync/daemon/server.py`, `laptop/androsync/service.py`,
`laptop/androsync/config.py` (new `service.browse_roots: list[str]`, default `["~"]` plus `/media/<user>` if it exists).
- `config_edit.py`: move the raw-YAML load/`safe_dump(sort_keys=False)` code out of `service.py` into
  `load_raw(path)`, `save_raw(path, raw)` (atomic: tmp + `os.replace`), `add_pair(raw, pair_dict)`,
  `update_pair(raw, name, pair_dict)`, `remove_pair(raw, name)`. Every write first builds a `Config` from
  the modified raw dict through `config.py`, so invalid input raises `ConfigError` and never reaches disk.
  Task 23's `add-pair` and new `edit-pair` / `remove-pair` CLI commands use these helpers.
- New routes in `_ROUTES`:
  * `POST /v1/pairs` body `{name, local, remote, direction, conflict?, propagate_deletes?, ignore?}` →
    201 with the updated pairs list; 400 `{"error": <ConfigError message>}`; 409 if the name exists.
  * `PUT /v1/pairs/<name>` same body (rename allowed: rename the `<pair>.json` / `.meta.json` state files too) → 200.
  * `DELETE /v1/pairs/<name>?purge_state=1` → 200; the state files are removed only with `purge_state=1`.
  * `GET /v1/browse?side=local|remote&path=<abs>` → `{path, parent, entries: [{name, is_dir, size, hidden}], writable}`,
    directories first, sorted case-insensitively. Local: `os.scandir`; `path` empty → list `browse_roots`;
    a path outside the roots → 403. Remote: `AdbTransport.ls` via the runner's transport factory; 409 while
    a job is running; 503 `{"error": "phone not connected"}` when `device_info()["device"]` is None.
  * `POST /v1/mkdir` body `{side, path}` → 201; same root/403 and device/503 rules.
  The daemon reloads the config per job already, so edits take effect without a restart.
Tests: `laptop/tests/test_config_edit.py` (add/update/delete keep other keys; invalid direction raises;
duplicate raises; rename moves state files) and new cases in `test_server.py` (201/400/409 paths,
browse local dirs-first and 403 for `/etc`, browse remote 503 with a mocked `device_info`, mkdir).

### 23c. Manage pairs from the phone app — app side (Add/Edit screen + folder pickers) — DONE (2026-09-07)
Built essentially as specified, two small deviations: direction and conflict policy use `FilterChip`
rows (matching `SetupScreen.kt`'s existing preset chips) rather than segmented buttons/a dropdown -
same UX, reuses a proven pattern instead of an unfamiliar Material3 API. Folder icons in
`FolderBrowserSheet` are text buttons, not icons: checked the actual `material-icons-core` AAR first -
`ArrowUpward`/`CreateNewFolder`/`Folder` are not in the small core icon set this app depends on (only
`material-icons-extended`, not pulled in, has them), matching `SettingsScreen.kt`'s own precedent of
avoiding that dependency. Full build green (`assembleDebug` + `testDebugUnitTest`), 33 new/changed
unit tests (SafPathTest, PairEditorValidationTest, 8 new ApiClientTest cases). Commits `f0df0c7`, `5a35cb9`.

Manually verified on the real phone: added a pair with both pickers (native SAF "Pick on phone" -
correctly resolved a URI with a space in the folder name to `/sdcard/Documents/Documents personal`,
confirming `SafPath` works for real - and "Browse" for the laptop side), confirmed on the laptop CLI
(`androsync status <name>` - no errors, clean sync), edited it, then deleted it without "also delete
sync history" checked and confirmed the state files correctly survived (only cleaned up by hand
afterward since it was disposable test data). The "N conflicts waiting" line itself is covered only by
the `pendingCounts` plumbing + unit tests, not exercised against a real `conflict: ask` pair this
session - low risk, same code path as the already-verified `/v1/pending` endpoint.

Files: `android/app/src/main/java/com/androsync/remote/data/api/{Dto,AndroSyncApi,ApiClient}.kt`,
new `ui/pairs/PairEditorScreen.kt` + `PairEditorViewModel.kt`, new `ui/common/FolderBrowserSheet.kt`,
new `data/SafPath.kt`, `ui/Routes.kt` (`pair/new`, `pair/{name}`), `ui/pairs/PairsScreen.kt`.
- API client: `BrowseDto`, `PairRequest`, `createPair`, `updatePair`, `deletePair(name, purgeState)`, `browse(side, path)`, `mkdir(side, path)`.
- Pairs screen: `FloatingActionButton` "+" (bottom right) → `pair/new`. Each pair card gets an overflow
  menu: **Edit** (→ `pair/{name}`, form prefilled) and **Delete** (confirm dialog with a checkbox
  "also delete sync history" → `purge_state`).
- Editor form: name; **Laptop folder** text field + "Browse" (opens `FolderBrowserSheet(side = local)`);
  **Phone folder** text field + "Pick on phone" (Android folder picker, `ActivityResultContracts.OpenDocumentTree`,
  no persistable permission taken because the app never reads files) + "Browse via laptop"
  (`FolderBrowserSheet(side = remote)`); direction as three segmented buttons `laptop → phone`, `↔`, `←`;
  conflict rule dropdown (newest wins / laptop wins / phone wins / ask); "Propagate deletes" switch,
  default off when direction is phone → laptop (backup case) and on otherwise; ignore patterns as a
  comma-separated field. Save → POST or PUT → back to Pairs with a snackbar; a 400 shows the server
  message under the form; 503 from browse shows "Connect the phone by USB first".
- `FolderBrowserSheet(side, start)`: modal bottom sheet with breadcrumb, "Up", folder list from
  `/v1/browse`, "New folder" (→ `/v1/mkdir`), "Choose this folder".
- `SafPath.treeUriToPath(uri: String): String?`:
  `content://com.android.externalstorage.documents/tree/primary%3ADocuments%2FObsidian` → `/sdcard/Documents/Obsidian`;
  `primary%3A` alone → `/sdcard`; another volume id `XXXX-XXXX%3A...` → `/storage/XXXX-XXXX/...`;
  any other authority (Downloads provider, cloud) → null and the UI says "Pick a folder on internal storage or SD card".
- Unit tests (pure JVM): `SafPathTest` (primary root, primary subfolder, SD card, unsupported authority,
  URL-decoding of spaces), form validation (empty name, remote must start with `/`, default
  `propagate_deletes` by direction), `PairRequest` JSON shape against the server's expected keys.
Manual: on the phone, add the Obsidian pair with the picker, then `androsync status` on the laptop lists it;
edit its direction from the app; delete it with "also delete sync history" and confirm the state files are gone.

### 24. Performance: batched push, media scan, case-collision warning — DONE (2026-09-07)
All three pieces built, tested, and verified against the real phone. Commits `f6b5033`
(case-collision warning - `plan()` now returns `PlanResult(actions, warnings)`, a breaking
signature change with only one production caller, `executor.py`), `557c967` (AdbTransport
store_many/fetch_many, standalone), `cfb568a` (wired into the executor's copy loop - two real
correctness bugs found and fixed along the way: a transport-capability check that wrongly assumed
`Side.REMOTE` always means `AdbTransport`, and a per-file verification failure that used to
double-count already-processed files in a batch when it aborted the whole batch), `bbc76e7` (media
scan broadcast). Cancellation granularity moves from per-file to per-batch (up to 200 files/200MB)
as a documented trade-off - three tests updated to match. Real-phone numbers: pushing 100 files
took ~3s batched vs 6.5s for the same 100 via individual unbatched calls; media scan broadcast
confirmed completing (`result=0`) after a real DCIM push. Full suite: 350 passed.

Files: `laptop/androsync/transport/adb.py`, `laptop/androsync/executor.py`, `laptop/androsync/planner.py`.
- Add optional `Transport.store_many(items: list[(tmp_path, root, rel, mtime)])`; default implementation
  loops over `store`. `AdbTransport.store_many`: stage files into a temp tree mirroring `rel`, one
  `adb push <tmp>/. <root>/`, then one `adb shell` script that runs `touch -m -d @<epoch>` per file
  (only if the mtime self-test says push does not preserve mtime). Executor groups consecutive COPY
  actions to the same destination side into batches of up to 200 files / 200 MB and calls `store_many`;
  snapshot is still saved after each batch. Same idea for phone→laptop: `fetch_many` pulling a whole
  directory with `adb pull -a` when more than 80 % of its files are in the batch.
- After storing under `/sdcard/DCIM|Pictures|Movies|Music|Download`, broadcast
  `am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://<path>` once per file
  (batched in the same shell script).
- Planner: if two rels differ only by case (`.lower()` collision), emit no action for them and add a
  warning list to the plan output (`plan()` returns `(actions, warnings)` or a `PlanResult`; update callers).
Tests: batching boundaries with mocked subprocess; media scan command generated only for media dirs;
case-collision warning. Report vault sync time before/after (from V1) in the commit message.

### 25. Remote ADB over Wi‑Fi / Tailscale — DONE (2026-09-07)
`androsync pair-wifi`, `on_connect` skipping `adb reverse` for tcp, and the `WifiReconnector` retry
loop all built and unit-tested (commit `2e923c8`). Verified against the real phone for real: `adb
tcpip 5555` while on the cable, `adb connect` over the actual tailnet, then physically unplugged -
`/v1/ping` reported `transport: "tcp"`, the journal confirmed `adb reverse` was skipped, and a real
sync job dispatched through the HTTP API completed successfully with 0 errors, phone off the cable
the whole time. (Along the way: the phone's Tailscale was found offline/stale at first - not a code
issue, just needed the Tailscale app brought to the foreground on the phone.)

Files: `laptop/androsync/transport/adb.py` (`resolve_device` already handles `wifi:IP:PORT`),
`laptop/androsync/daemon/watcher.py`, `laptop/androsync/service.py`, `laptop/androsync/cli.py`.
- `androsync pair-wifi`: walks through Android 11+ Wireless debugging pairing: asks for the pairing
  `ip:port` and code shown on the phone, runs `adb pair`, then asks for the connect `ip:port`, runs
  `adb connect`, and offers to write `device: wifi:<ip>:5555` into the config. Default suggestion for
  the IP: the phone's tailnet IP `<phone-ts-ip>`.
- Daemon: when `config.device` starts with `wifi:` and the device is not connected, try `adb connect`
  every 30 s (log at DEBUG to avoid spam); `device_info()` reports `transport: "tcp"`. The watcher treats
  a connected TCP device the same as USB except no `adb reverse` (the phone reaches the API over Tailscale).
- `/v1/ping` `transport` values: `usb`, `tcp`, `none`. App badge already shows Tailscale from its own side.
Tests: mocked `adb pair`/`connect` flows; daemon reconnect loop with a fake clock.
Manual: phone off the cable on mobile data, `androsync run vault` from the app over Tailscale.

### 26. Version control for a pair (`versioning: git`) — DONE (2026-09-07)
Built and tested (commit `3a75c6b`) - real `git` against real temp dirs, not mocked. Not yet
enabled on the real `vault` pair; that's the user's call, not made automatically on their real
Obsidian vault. 383 passed, 4 skipped (no phone attached that session; irrelevant here since
versioning is entirely local-side, no ADB involved).

Files: `laptop/androsync/config.py` (new pair field `versioning: "none" | "git"`, default none),
new `laptop/androsync/versioning.py`, `laptop/androsync/executor.py` (hook after a successful `run_pair`).
- After a non-dry run with no errors, if `versioning == "git"`: `git -C <local> init` if missing (add a
  `.gitignore` with `.obsidian/workspace*` and `.trash/`), `git add -A`, `git commit -q -m "androsync <ISO time>: <summary>"`
  only if there are staged changes. Never push. `.git/**` is already in the default ignore list so it is not synced.
- CLI `androsync history <pair> [-n 20]` = `git log --oneline`.
Tests: temp repo, two runs → two commits; no-change run → no commit; dry run → no commit.

### 27. Optional: desktop Tk GUI and MTP fallback
Only if the user asks. GUI = `laptop/androsync/gui.py` (Tkinter): pair list, add/edit dialog with
`filedialog.askdirectory` for local and the `ls` tree for remote, defaults tab, Run button streaming the
job log via the daemon API. MTP = `transport/mtp.py` over the gvfs mount (`gio mount -l`), size-only compare.

---

## Phase H — fewer false conflicts, no duplicates in the folders, pair rename (reported 2026-09-07)

Priority: do Phase H BEFORE task 27 and the leftovers. The user hit this on 2026-09-07 with a
`media` pair (images/videos).

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

### 29a. Hashing + per-side snapshot (laptop, no phone needed for tests) — DONE (2026-09-07)
Built as specified. `SnapshotEntry(size, mtime_local, mtime_remote, sha256)` replaces the old
one-mtime snapshot shape (`state.load_snapshot` migrates old files transparently: shared `mtime`
copied into both sides). `plan()` takes an optional `hasher` callback; it collects every rel where
both sides changed to the *same size* first, calls `hasher` once with that whole list (so the
executor's real `hasher` does at most one `hash_many` per side per run, not one per file), then
resolves: equal hash -> `PlanResult.resynced` (no action, no conflict-copy, snapshot records each
side's own current mtime); different hash -> the existing conflict-policy path, unchanged.
Different size never calls the hasher at all (can't be equal content). One-way pairs and
already-named `*.conflict-*` files never hash either (their outcome doesn't depend on content).
`AdbTransport.hash_many`: one `sha256sum` (falls back to `md5sum`, stored `md5:<hex>`, if the
device lacks `sha256sum` - probed once via `command -v`) per batch of up to 200 paths via
`exec_out`; output parsed by fixed digest length (not whitespace-split) so a filename containing
spaces still parses correctly. `LocalDirTransport.hash_many` hashes the file directly (no
fetch round-trip, unlike the `Transport.hash_many` default every other transport gets for free).
402 passed (391 + 11 new: 5 planner hash/resync table rows, 4 adb `hash_many` parsing cases, 1
snapshot-migration test, 1 executor e2e regression test reproducing the exact real `media` bug -
identical content, very different mtimes, on both sides - and asserting zero copies/conflicts on
the first run and a full no-op on the second). No phone attached this session; `hash_many` is
exercised only via mocked `adb` subprocess calls, not yet against the real device - do that before
trusting it on the real `media` pair.

Files: `laptop/androsync/model.py` (`FileEntry.sha256`, new `SnapshotEntry`/`PersistedSnapshot`),
`laptop/androsync/state.py` (migration in `load_snapshot`), `laptop/androsync/transport/base.py`
(`hash_file` helper, default `hash_many`), `laptop/androsync/transport/local.py`,
`laptop/androsync/transport/adb.py`, `laptop/androsync/planner.py` (`HasherFn`, `_needs_hash`,
`PlanResult.resynced`), `laptop/androsync/executor.py` (`_make_hasher`, `_entry_to_snapshot`,
resynced-rel handling in `run_pair`).

### 29b. Conflicts folder, `conflicts` command, clean-up (laptop) — DONE (2026-09-07)
Built essentially as specified, one deliberate simplification: `conflict_copies` is a two-value
field (`folder` default, `beside`), not three - "none" is already the existing
`keep_conflict_copies: false` (whether to keep a copy at all is orthogonal to where it goes, and
that flag is used throughout the planner/executor/CLI/Android app already, not worth a bigger
migration for this task). `.androsync/**` is hardcoded in `IgnoreRules.__init__` (always appended,
regardless of the pair's configured `ignore`) so it excludes itself even on a config that predates
this feature - also added to `config.example.yaml`'s `defaults.ignore` as documentation.
`_do_conflict_copy` branches on `pair.conflict_copies`: `folder` fetches the loser's current bytes
straight to `.androsync/conflicts/<stamp>/<rel>` on the laptop (via `transports[Side.LOCAL].store`,
reusing the existing local transport instance) and does not touch the live file at all, so the
plan's very next action (winner COPY) just overwrites it normally; `beside` is the untouched
pre-29b rename-then-push-both-sides code. `resolve`'s manual `"both"` choice honours the same
setting. 431 passed, 4 skipped (no phone this session - `conflicts.py`/the new routes are only
exercised through `LocalDirTransport` stand-ins for both sides, not the real device or a real
`AdbTransport.hash_many`, so still worth a real-device pass before trusting `conflicts clean` on
the actual duplicate-laden `media` pair).

Files: `config.py` (`CONFLICT_COPIES_MODES`, `PairConfig.conflict_copies: "folder"|"beside" = "folder"`),
`ignore.py` (`_ALWAYS_IGNORE`), `conflict.py` (`CONFLICTS_DIR`, `conflict_stamp`, `conflict_folder_rel`,
`conflict_folder_path`, `strip_conflict_marker`), `executor.py` (`_do_conflict_copy` mode branch,
`_format_action` handles a `None` target), `planner.py` (`_plan_both_changed` builds the
mode-appropriate reason/target), new `conflicts.py` (`list_conflicts`, `restore`, `find_duplicates`,
`clean` - hashing via `Transport.hash_many`), new `cli_conflicts.py` (`conflicts list|restore|clean`,
registered from `cli.py`), `daemon/server.py` (`GET /v1/conflicts/<pair>`,
`POST /v1/conflicts/<pair>/restore`, `POST /v1/conflicts/<pair>/clean` with `{dry_run}` - gated
503/409 like browse/mkdir), `laptop/README.md` "Conflicts" section.

### 29c. App: rename pairs, conflicts list (android) — DONE (2026-09-07)
Built as specified; one thing turned out already done: `PairEditorViewModel.save()` already sent
`state.name` in the `PairRequest` body for both create and update (the old name only ever reached
the URL path via `updatePair(pairName!!, request)`) - the Name field being disabled was the *only*
thing blocking a rename, so that was the actual fix. No extra "navigate back with the new name"
plumbing was needed either: `onSaved` already pops back to `PairsScreen`, and the daemon's
`PUT /v1/pairs/<name>` already renames the pair's state files server-side (task 23b) - the list the
user lands back on simply reflects the new name once refreshed.

Files: `ui/pairs/PairEditorScreen.kt` (dropped `enabled = uiState.isNew` from the Name field),
`data/api/Dto.kt` (`ConflictVersionDto`, `ConflictRestoreRequest/Response`, `ConflictsCleanRequest`,
`CleanActionDto`, `ConflictsCleanResponse` - `when` is a Kotlin keyword, so the wire field is
renamed to `whenStamp` via `@SerialName`), `data/api/AndroSyncApi.kt` + `ApiClient.kt`
(`conflicts(pair)`, `restoreConflict(pair, rel, when?)`, `cleanConflicts(pair, dryRun)`),
`ui/pairs/PairsViewModel.kt` (`conflictCounts` alongside the existing `pendingCounts`, fetched the
same best-effort way in `refresh()`; `ConflictsSheetState` sealed class + `openConflicts`/
`restoreConflictVersion`/`previewCleanDuplicates`/`cancelCleanPreview`/`confirmCleanDuplicates`),
`ui/pairs/PairsScreen.kt` (card shows a clickable "N saved conflict versions" line when > 0;
`ConflictsBottomSheet` lists versions with per-version **Restore**, plus a **Clean duplicates**
button that fetches the dry-run preview first and only applies it after a second confirm tap).

Full build green (`testDebugUnitTest` + `assembleDebug`): 105 tests, +4 from this task (a rename
PUT-body test, conflicts-list parsing, restore request/response, clean request/response). Not yet
exercised on the real phone this session - no device attached - so the sheet's actual look/
restore/clean flow still needs a manual pass before trusting it.

## Phase I — undo a sync run (requested 2026-09-07)

Do Phase I after Phase H (29b's `folder` conflict mode and 29a's `SnapshotEntry` are assumed).

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

### 30a. Journal writing in the executor (laptop) — DONE (2026-09-07)
Built as specified, with the write-side helper methods on `RunJournal` shaped around what
`executor.py` actually needed to call cleanly rather than the exact bare-bones method list in the
original sketch: `record_write`/`record_delete` do their own `transport.stat()` to decide
create-vs-overwrite (so the executor never has to work that out itself), `record_write_many` is the
batched (task 24) equivalent using one `backup_many`/`fetch_many` call, and
`record_conflict_saved`/`record_conflict_rename` cover the two `CONFLICT_COPY` modes from task 29b.
`journal.jsonl` is opened, appended, and closed on every `record()` call (never held open) so a
crash mid-run really does leave every already-recorded line durable on disk. 461 passed (435 + 26
new: `RunJournal` unit tests plus `run_pair` integration tests journaling a create, an overwrite
(with the old bytes recoverable from `files/<side>/<rel>`), a delete, both `CONFLICT_COPY` modes, a
batched copy, `undo_enabled: false`/`dry_run` both skipping the journal entirely, a backup skipped
above `max_file_mb`, and a fake flaky transport proving one file's `store()` failure does not lose
the journal entry already recorded for a different file that succeeded in the same run).

Files: new `laptop/androsync/undo.py` (`RunJournal.start/backup/backup_many/record/record_write/
record_write_many/record_delete/record_conflict_saved/record_conflict_rename/finish`, plus module
functions `list_runs`, `load`, `prune`, `new_run_id`), `laptop/androsync/state.py` (`undo_dir`,
`snapshot_to_raw`/`raw_to_snapshot` factored out of `save_snapshot`/`load_snapshot` for `undo.py` to
reuse for `snapshot_before.json`; both wired into `rename_pair_state`/`purge_pair_state`),
`laptop/androsync/config.py` (`UNDO_DEFAULTS`; pair fields `enabled`, `undo_keep_runs`,
`undo_max_total_mb`, `undo_max_file_mb`, `undo_enabled` - a pair-level `undo:` dict merges over
`defaults.undo` key-by-key, same "pair wins" semantics as everything else), `laptop/androsync/model.py`
(`Summary.run_id`), `laptop/androsync/executor.py` (`run_pair` starts the journal right after loading
the pre-run snapshot, when `pair.undo_enabled` and not `dry_run`; every write/delete/conflict-copy
call site journals before it touches anything; `finish()` + `undo.prune()` run at the very end),
`laptop/androsync/daemon/jobs.py` (`job_to_dict` adds `run_ids: {pair: run_id}`).

### 30b. `undo` command, API, pause flag (laptop) — DONE (2026-09-08)
Built as specified, plus two real bugs caught by the CLI-level e2e tests (not visible at the
`undo.py`-unit level, both fixed):
- `list_runs`/`prune` ordered runs by the `run_id` string, but `run_id`'s own timestamp component is
  only second-resolution - two runs in the same second (trivially reproducible: sync, edit, sync
  again in a test, or just a fast machine) sorted in a coin-flip order because the tiebreak was
  each run's random 4-hex suffix. Fixed by sorting on `meta.json`'s `started` field instead
  (`datetime.isoformat()`, microsecond precision) - see `_run_sort_key`/`_run_dir_sort_key`.
- `--pause`/`pause: true` called `config_edit.update_pair(raw, name, {"enabled": False})` - but
  `update_pair` *replaces* the whole pair dict with whatever is passed, it does not merge (same
  contract `cmd_edit_pair` already relied on correctly). That silently dropped `local`/`remote`/
  everything else, failing validation with `'name' is required`. Fixed by loading the pair's
  current raw dict first and setting `enabled: False` on a copy of it, in both `cli.py` and
  `daemon/server.py`.

`undo_run` replays a run's journal in *reverse* order deliberately, not just because it undoes most
recent first: a beside-mode conflict's rename can only be undone (target renamed back to its
original name) after the winner-copy write that followed it in the same run is itself undone first
(otherwise the rename target collides with what is currently sitting at the original name). Each
entry's drift check compares the *current* file against the journaled `new` fingerprint (or, for a
delete, just checks nothing exists there) so a file touched after the run is left alone and
reported unless `--force`; a file whose backup was skipped (`max_file_mb`) is reported as
unrestorable rather than silently doing nothing. The undo replays through the exact same
`RunJournal.record_write`/`record_delete` calls a normal sync uses, so it journals itself
(`kind: "undo"`) for free - undo of an undo is redo with no special-case code. The CLI honours an
explicitly-named pair even if paused (typing the name is the override); the daemon (auto-sync and
the app) always skips a paused pair regardless, on the reasoning that unattended paths should never
silently repeat whatever a pause was meant to stop.

497 passed (488 + 9: `submit_undo`/disabled-pair-skip in `daemon/jobs.py`, `runs`/`undo` HTTP routes
including the two bugs above surfacing through CLI-level e2e tests first). No phone attached this
session - undo is only exercised through `LocalDirTransport` stand-ins, not real `AdbTransport`
fetch/store, so still worth a real-device pass before trusting it on an actual bad sync.

Files: `laptop/androsync/undo.py` (`UndoSummary`, `undo_run` and its per-op `_undo_create`/
`_undo_overwrite`/`_undo_delete`/`_undo_conflict_saved`/`_undo_conflict_rename` helpers,
`_matches`/`_run_sort_key`/`_run_dir_sort_key`), `laptop/androsync/cli.py` (`cmd_runs`, `cmd_undo`,
`_skip_disabled` wired into `cmd_status`/`cmd_run`), `laptop/androsync/cli_pairs.py` (`edit-pair
--enabled/--no-enabled`), `laptop/androsync/daemon/jobs.py` (`Job.kind`/`undo_run_id`/`undo_force`/
`undo_summary`; `submit_undo`; `_run` split into `_run_sync`/`_run_undo`, `_run_sync` skips
`enabled: false` pairs unconditionally), `laptop/androsync/daemon/server.py` (`GET /v1/runs/<pair>`,
`POST /v1/undo/<pair>` body `{run_id?, dry_run?, force?, pause?}`), `laptop/README.md` "Undo a sync"
section.

### 30c. App: runs list and Undo button (android) — DONE (2026-09-08)
Built as specified, plus one laptop-side gap found and fixed along the way: `pairs_status()` (the
`GET /v1/pairs` response `PairsScreen` loads) never included `enabled` at all, so the app had no
way to know a pair was paused - added it there.

`SummaryDto` now doubles as an undo run's summary shape too (`restoredLocal`/`restoredRemote`/
`skipped` alongside the existing sync counters - the two never collide on a field name) rather than
adding a second near-identical DTO class; `JobDto`/`RunDto` both reuse it. `JobScreen` shows a
per-pair "Undo" button next to each summary row once the job is terminal and that pair's summary
has a `runId`; an undo job itself (no `summaries`, only `undoSummary`) gets its own single-row
"Undo this" for redo. `PairsScreen`'s "Undo last sync…" opens a sheet over `GET /v1/runs/<pair>`
with the same confirm-dialog-with-pause pattern, reusing the existing `PairsEvent.JobStarted`
navigation path (undoRun() just posts to the same event `sync()` already uses).

Fixed one real data-loss risk while adding `enabled` to the wire format: `PUT /v1/pairs/<name>` is
a full replace, not a merge (already known from task 30b's server-side `--pause` bug) - so
`PairEditorViewModel`'s save(), which builds a fresh `PairRequest` from the form fields only, would
have silently un-paused any paused pair the moment someone edited it for an unrelated reason. Fixed
by carrying `enabled` through `PairEditorUiState` on load and always resending it, even though the
form has no control for it. The dedicated Enable switch (`PairsViewModel.enablePair`) sidesteps the
same trap differently: it sends a full `PairRequest` built from the already-loaded `PairDto` rather
than a partial one.

Full build green (`testDebugUnitTest` + `assembleDebug`): 110 tests (+5: runs DTO parsing for both
run kinds, undo request body with/without `run_id`, 404 mapping). Laptop: 498 passed (+1:
`pairs_status` reports `enabled`). Not exercised on the real phone this session - no device
attached.

Files: `laptop/androsync/daemon/jobs.py` (`pairs_status` adds `enabled`),
`android/app/src/main/java/com/androsync/remote/data/api/Dto.kt` (`SummaryDto` gains the undo
fields; `JobDto` gains `kind`/`undoRunId`/`undoSummary`; new `RunDto`, `UndoRequest`; `PairDto`/
`PairRequest` gain `enabled`), `data/api/AndroSyncApi.kt` + `ApiClient.kt` (`runs(pair)`,
`undo(pair, runId, pause, force, dryRun)`), `ui/job/JobViewModel.kt` (`JobEvent`, `undo()`),
`ui/job/JobScreen.kt` (per-row Undo button + confirm dialog, `onNavigateToJob`), `ui/job/JobLogic.kt`
(`formatUndoSummary`), `ui/MainActivity.kt` (wires `onNavigateToJob` for the job route),
`ui/pairs/PairEditorViewModel.kt` (`enabled` carried through unchanged), `ui/pairs/PairsViewModel.kt`
(`RunsSheetState`, `openRuns`/`dismissRuns`/`undoRun`, `enablePair`), `ui/pairs/PairsScreen.kt`
(greyed card + Enable switch + "Undo last sync…" menu item + `RunsBottomSheet`).

## Real-device verification of Phase H/I — DONE (2026-09-08)
Tasks 29a/29b/30a/30b (laptop side) verified for real against the phone over Tailscale, using
disposable test files on the real `media` pair (cleaned up after each check):
- 29a: a file pushed to the phone with identical content but a 1-hour-different mtime was
  correctly recognized as already-synced (no conflict, no duplicate); second run a full no-op.
- 29b folder mode: a genuine conflict (different content, phone newer) put the loser under
  `.androsync/conflicts/<stamp>/`, not a `*.conflict-laptop-*` file beside it; `conflicts list` and
  `conflicts restore` both round-tripped correctly.
- 29b `conflicts clean`: found 192 real pre-existing `*.conflict-laptop-*` duplicates in `media`
  (left over from before this session's fix), confirmed all byte-identical to their originals via
  dry run, then deleted them for real on the user's go-ahead - 0 remain, pair stable afterward. A
  real cleanup of real data, not just a test.
- 30a/30b: create → sync → `undo` (removed from phone) → `undo` again (undo-of-undo = redo,
  recreated) → drift check correctly skipped a file someone had edited on the phone after the sync
  → `--force` correctly overrode it (needed `--run <id>` explicitly, since the skipped attempt
  itself had already become the new "most recent run" - see the note below) → `--pause` confirmed
  `status`/`run` skip the pair with no name given but still run it when named explicitly, and
  `edit-pair --enabled` un-pauses it.

Noted (not fixed, not a bug): an `undo` attempt that skips everything due to drift still records
its own empty "undo" journal run, which becomes "most recent" - so retrying immediately after a
skip needs `--run <original-id> --force`, a bare `--force` alone will target the empty skip-run
instead. Worth a UX pass later if it proves confusing in practice; not urgent.

Not yet verified for real: task 30c (the Android app's runs sheet / Undo button / Enable switch) -
no interactive phone session this pass, only the laptop CLI/API paths above.

## Phase J — desktop GUI app, autostart on demand (requested 2026-09-13)

Do Phase J after Phases H and I. It replaces the optional task 27 GUI.

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

### 31a. Service state backend, pid/log/SIGHUP, autostart opt-in — DONE (2026-09-13)
Built as specified. `detect_state()`'s four modes: `systemd` (`systemctl --user is-active`),
`inprocess`/`other` (a live pid from the pid file, split by comparing it to `os.getpid()` -
whoever calls `detect_state()` either is or isn't the process that wrote it), `none`. `cmd_serve`'s
double-start guard pings the port directly rather than calling `detect_state()`, specifically to
dodge a self-deadlock: systemd marks a `Type=simple` unit "active" the instant it forks the
process, before that process has bound its own port - trusting `is-active` there would make
`serve` refuse to start itself every time systemd launches it. 532 passed (30 new: the four modes,
stale pid file ignored, `serve` double-start refusal, install without `--autostart` runs only
`daemon-reload`, `autostart on/off`, `status` exit codes).

One real bug found by a test interaction, fixed before committing: `cmd_serve` originally called
`setup_logging(file=True)` *before* the double-start guard, so even the "refuse to start" path
permanently added a `StreamHandler`/`RotatingFileHandler` to the root logger - harmless in real
usage (one process, one call) but would double-log if a long-lived process ever called it twice
(exactly what task 31b's GUI does if "Run here" is clicked, backed off, then clicked again).
Reordered so logging is only touched once actually starting.

Files: new `laptop/androsync/service_state.py`, `daemon/watcher.py` (pid file in `Daemon.start`/
`stop`; SIGHUP in `run_forever`), `cli.py` (`setup_logging`, `cmd_serve`'s guard), `service.py`
(`install --autostart`, `autostart on|off`, `status` via `detect_state`), `laptop/README.md`.

### 31b. Tk GUI — DONE (2026-09-13)
Built as specified: `gui/logic.py` (pure, no `tkinter` import) holds `service_panel_view`/
`format_pair_row`/`close_window_decision`; `gui/backend.py`'s `Backend` runs one action at a time
on a background thread, reporting through a `queue.Queue` app.py drains via `root.after` so the
window never blocks on ADB or the network; new `apiclient.py` (urllib, bearer token) is used when
`ServiceState.ping` is set, `run_pair`/`undo_run` directly otherwise. `daemon/watcher.py` needed no
change for thread-safety - `Daemon.start()`/`stop()` were already non-blocking and signal-free;
only `run_forever()` (which the GUI never calls) touches signals.

Manually verified against the real running service (screenshot + `xdotool`, disposable demo config
in `/tmp`, real config/service untouched): the window renders correctly, selecting a pair and
clicking Preview correctly ran a real dry-run `adb shell test -d ...` check and printed the summary
to the Log panel. That check surfaced a real bug, fixed before committing: `detect_state()`'s
`systemd` branch never pinged the API at all, so `state.ping` was always `None` for the by-far-most
-common case (the background service) - `service status` never showed device/transport, and
`reachable_client()` could never pick the API path for it, silently falling back to direct calls
always. Fixed by pinging (when a token is available) before branching on which mode to report, for
all three running modes. 543 passed (11 new: `gui/logic.py`'s pure helpers, one GUI smoke test
building a real `Tk()` window - this machine has a display, so it is not actually skipped here).

Files: new `laptop/androsync/apiclient.py`, `laptop/androsync/gui/` (`app.py`, `backend.py`,
`logic.py`, `service_panel.py`, `pairs_panel.py`, `log_panel.py`, `dialogs.py`),
`laptop/androsync/service_state.py` (the ping-in-systemd-mode fix above), `cli.py` (`gui`
subcommand).

Deliberate scope trim vs. the original sketch: the pair editor dialog covers name/both folders/
direction/conflict policy/propagate-deletes (matching the CLI's `add-pair`/`edit-pair`), not
`ignore` patterns - editable directly in `config.yaml` for now, same as several other advanced
per-pair settings the Android app's editor doesn't expose either.

### 31c. Launcher + icon — DONE (2026-09-13)
Built as specified. The icon reuses the Android app icon's exact path geometry
(`ic_launcher_foreground.xml`: laptop + phone + two sync arrows) as an SVG, on a solid fill of the
app's own accent colour (`#1565C0`) since a desktop icon needs to stand alone rather than layer
over a separate adaptive-icon background. Rendered with ImageMagick+librsvg to confirm it actually
looks right before committing. 549 passed (6 new: desktop file contents, `update-desktop-database`
invoked when present, dry-run writes nothing, uninstall removes both files (and is a no-op when
nothing was installed), the packaged svg resolves via `importlib.resources`).

Files: `service.py` (`install-launcher`/`uninstall-launcher`, `render_desktop_file`), new
`laptop/androsync/assets/androsync.svg`, `laptop/pyproject.toml` (`[tool.setuptools.package-data]`),
`laptop/README.md` ("Desktop app" section).

Immediate one-off for the user, independent of the code (needs no restart of the running service):
`systemctl --user disable androsync` stops the boot autostart now and keeps the service running -
or now, equivalently, `androsync service autostart off`.

## Phase K — bug fixes from 2026-09-25: window will not close, syncs fail with a pinned Wi‑Fi device — DONE (2026-10-14)

Do Phase K first: both are live bugs. 32a and 32b are independent; 32c after 32b.

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

### 32a. GUI: fix the close bug and make shutdown robust (laptop).
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

### 32b. Device selection: prefer what is actually connected (laptop).
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

### 32c. Make failures visible (laptop + app).
  Files: `daemon/jobs.py` (pair exception and job failure also go to `logging.getLogger(__name__).warning`
  with job id, pair, message; job dicts keep `error`), `executor.py`/`runlog.py` (a failed run still writes
  `last-run.log` with the error), `state.py` (`save_pair_meta` on failure too: `last_result: "FAILED: <msg>"`,
  `last_error`), `daemon/watcher.py` (`WifiReconnector` logs the first failure and every state change at
  INFO, repeats at DEBUG), Android `PairsScreen` (red "Last run failed: <msg>" line from `last_result`),
  GUI `pairs_panel.py` (same, in the Last result column). Tests: failing transport factory → warning logged
  (caplog), meta written with `FAILED:`; reconnector log cadence with a fake clock.

Verification:
(1) `pkill -f "androsync gui"` the stale pid 786907 first (user's call). Open the GUI, Add… a pair
  and cancel, close the window → no process left. (2) Phone on USB with `device: auto` +
  `wifi_target: <phone-ts-ip>:5555`: `androsync devices` says "picked <phone-serial> (usb)"; `androsync run
  vault --yes` succeeds; daemon log shows `tcpip 5555` once. Unplug, phone on Wi‑Fi/Tailscale:
  `androsync devices` says "picked <phone-ts-ip>:5555 (tcp, connected now)"; sync from the phone app works.
  Phone off both: `status` fails with a message naming the tried target, and the failure appears in
  `daemon.log`, `last-run.log`, the pair's meta and the app's pair card.

All three built essentially as specified; 567 passed, 4 skipped (575 after task 33e). One real
design wrinkle found and fixed before committing 32a: `cmd_serve`'s double-start-refusal path used
to call `setup_logging(file=True)` *before* checking whether to actually start, so a process that
hit that path more than once (irrelevant for the CLI, which always exits right after, but exactly
what 32a's "Run here" lets happen in a long-lived GUI process) would double up logging handlers -
reordered so logging is only touched once actually starting. A second real bug surfaced only by
manually reproducing the GUI against the real running service: `detect_state()`'s `systemd` branch
never pinged the API, so `service status`/the GUI could never show device info or route through the
daemon API for the by-far-most-common case (the background service) - fixed by pinging in all three
running modes before branching. Full write-up, including the `self._name` shadowing root cause, the
`shutdown_steps()` decision table, the `resolve_device` preference order, and the failure-visibility
plumbing, is in the commit message ("Phase K: fix GUI close hang, Wi-Fi device selection, silent
sync failures") - not duplicated here since the spec above already matches what was built closely.
One deliberate scope trim: the GUI's folder-browse dialog and "Run here" were *also* found (by
reproducing a real reported freeze) to block the Tk main thread on a slow/stuck `adb` call or
`Daemon.start()`'s bind-retry loop - fixed in the same pass (backgrounded via the same queue-poll
pattern `gui/backend.py` already used) even though it was not explicitly in the 32a file list, since
it is the same class of bug 32a was fixing and was directly responsible for one of the two reported
symptoms ("the content disappeared but the window stayed open").

## Phase L — several phones and several PCs (asked 2026-09-26)

Do Phase L after Phase K. Optional: only needed for a second phone or a second PC.

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

### 33a. Laptop: per-device state + pair `device` field (`config.py`, `state.py` with migration,
  `executor.py`/`undo.py`/`conflicts.py` path helpers, `transport/adb.py` `resolve_device` for lists,
  `daemon/jobs.py` ambiguous → error). Tests: migration, two serials keep separate snapshots, ambiguity.
### 33b. Laptop: device registry + per-device Wi‑Fi (`service.devices`, `pair-wifi`, watcher per-device
  reconnectors, `/v1/ping` devices list, `devices` CLI shows names). Tests with mocked adb.
### 33c. App: laptop profiles + QR onboarding (`SettingsRepository` migration from single values,
  chooser UI, `ConnectionProbe` per profile, QR payload `name`, tile/widget on the selected profile).
### 33d. App + API: pair ↔ phone binding in the UI (`/v1/pairs.device`, editor field "Phone: any / this
  phone", Pairs list filter, ANDROID_ID mapping).

### 33e. No addresses of this household in the code (do this first in Phase L; 1 hour) — DONE (2026-10-14)
Built as specified, with one deliberate scope trim on the Android side: rather than merging
`Preset.TAILSCALE`/`Preset.LAN` into a single enum value (a bigger change - `ConnectionProbe` tries
both as *separate* automatic-fallback candidates, and `SettingsRepository` persists them through
genuinely different fields, `tailscaleUrl` vs the plain `url`/`baseUrl` LAN uses), only the Setup
screen's **label** for the `TAILSCALE` chip changed, from "Tailscale" to "Remote" - the enum value,
persisted key and `ConnectionProbe` candidate stay named `TAILSCALE` internally. `laptop/androsync/
cli.py`: dropped `DEFAULT_WIFI_CONNECT_IP`; `pair-wifi`'s connect-address prompt now suggests the
IP of an Android peer from `tailscale status --json` (new `_detect_android_tailscale_ip()`), or
plain-prompts with no default when tailscale isn't installed or has no Android peer.
`android/.../ApiClient.kt`: dropped `PRESET_TAILSCALE_URL`; `Preset.fixedUrlFor` returns null for
both `TAILSCALE` and `LAN` now (only `USB`'s loopback stays fixed), so `SettingsRepository.
tailscaleUrl` defaults to `""` and `ConnectionProbe.probe` skips a blank/null `tailscaleUrl`
candidate entirely rather than trying to reach an empty URL. One real regression caught while
checking `QrPayload.pickUrlForPreset` (the QR-scan auto-fill matcher): with no fixed URL left to
match against for `TAILSCALE`, it would have started always returning `urls.first()` regardless of
which preset the user had selected - usually the *USB* URL, not the Tailscale/LAN one the user
actually chose a non-USB preset to get. Fixed by preferring whichever scanned URL is not the USB
loopback host, instead of blindly taking the first one.

575 passed on the laptop side (567 → 575: `_detect_android_tailscale_ip`'s four scenarios, the
updated `pair-wifi` interactive-prompt tests, the laptop grep-guard test). Android: `NoHardcodedIpsTest`
(walks `app/src/main/**/*.kt` for a `100.x.x.x` literal), plus updated `ConnectionProbeTest`/
`QrPayloadTest` coverage for the new "no fixed URL, skip if blank" behaviour. Verified for real
(read-only): `_detect_android_tailscale_ip()` run directly against this laptop's actual `tailscale
status --json` correctly found a real Android peer's IP, proving the detection logic works end to
end, not just against mocked JSON.

Files: `laptop/androsync/cli.py`, `laptop/README.md`, `android/README.md`,
`android/app/src/main/java/com/androsync/remote/data/api/{ApiClient,ConnectionProbe}.kt`,
`data/settings/{Preset,SettingsRepository}.kt`, `ui/common/QrPayload.kt`,
`ui/settings/SettingsViewModel.kt`, `ui/setup/SetupScreen.kt`, new
`laptop/tests/test_no_hardcoded_ips.py`, `android/.../NoHardcodedIpsTest.kt`.

Not done (out of scope for this pass, left for the rest of Phase L if it is picked up later):
33a-33d (per-device state, device registry, app laptop-profiles, pair↔phone binding in the UI) and
Phase L's full verification, which depends on them: two phones on USB → `androsync devices` lists
both with names; a pair bound to phone A syncs only A; `device: any` with both attached → ambiguity
error, then pick with `--device`; phone B syncs its own pair, both state files exist; app: scan a
second PC's QR, switch laptops in the chooser, both work.

## Questions answered 2026-09-27 (candidates, not yet planned)

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

## Phase M — one README for the whole system (chosen 2026-09-27)

Can be done at any time, independent of K and L; docs only.

### 34. Root `README.md` (about 1.5 hours, no code changes). Written for two readers, users first,
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

  DONE (2026-10-14). Built essentially as specified. One honest adaptation: Part 1 step 4's "install
  on the laptop" cannot reference a `git clone <url>` yet (Phase N - no public remote exists yet, see
  below) - worded as "open a terminal in this repository" instead. Step 5 similarly notes that a
  pre-built APK is not published yet and points at `android/README.md`'s Build section, rather than
  inventing a download link. The `grep -o 'androsync [a-z-]*' README.md | sort -u` check was run for
  real: every `androsync <command>` mentioned (`service install`, `service install-launcher`, `qr`,
  `run`, `conflicts restore`, `conflicts clean`, `undo`) exists in `androsync --help`; the two false
  positives the regex also catches ("androsync needs", "androsync in") are plain prose, not commands.
  `laptop/README.md`/`android/README.md` trimmed to a one-line overview + link, as specified.
  Files: new root `README.md`, `laptop/README.md`, `android/README.md`.

## Phase N — publish: GitHub, releases, F-Droid (asked 2026-09-27)

Order: 35a any time; 35b needs 33e first; 35c after 35a; 35d after 35b; 35f only if the user asks.
The license is the user's decision: confirm GPL-3.0 (plan default) before 35a.

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

### 35a. Private GitHub repo + hygiene (1 hour, needs the user's GitHub login once). — DONE (2026-10-15)
  `gh` was already installed and already logged in (account `Abdoh-Abqr`) - that step was free. Built
  as specified: `LICENSE` (full GPL-3.0-or-later text), `.github/FUNDING.yml` (GitHub Sponsors +
  Liberapay placeholders - still need the user's actual usernames before going public), `CHANGELOG.md`
  (0.1.0 entry summarizing everything built so far, organized by area rather than by task), `pyproject.toml`
  gained a `license` field, `.gitignore` hardened with `*.jks`/`*.keystore` (neither was actually
  tracked, but nothing stopped one from being added by accident later). `git config user.email` set
  locally (this repo only) to the GitHub no-reply address `<id>+<username>@users.noreply.github.com` -
  the user explicitly said never to use their work email for commits unless asked, so that is now the
  default going forward for this repo, not just something to revisit before going public. Then
  `gh repo create androsync --private --source=. --push`, confirmed by the user first (a genuinely
  external, if reversible, action): https://github.com/Abdoh-Abqr/androsync, verified private via
  `gh repo view --json isPrivate`.
### 35b. Scrub, then public (2 hours, after task 33e). — scrub DONE (2026-10-15), publish NOT done
  Removed personal data from tracked files: the real phone's ADB serial (→ `<phone-serial>`), real
  tailnet IPs and hostnames (→ `<laptop-ts-ip>`/`<phone-ts-ip>`/`<tablet-ts-ip>`/`<*-hostname>`,
  distinct placeholders per device rather than one generic token, so the existing comparisons
  between devices in `PLAN.md`'s "Network, Tailscale and firewall" section stay readable), the
  `memes` pair name (→ `media` - genuinely less generic-sounding than `vault`/`photos`, which were
  already fine as-is and did not need changing), a reference to another of the user's own
  self-hosted projects by name and its install path (now described only as "another self-hosted app
  on this laptop"), and the local Linux username embedded in a few `/media/<user>/...` path examples
  (→ `<repo-root>` etc.) - in `NEXT_TASKS.md`, `PLAN.md`. The READMEs and `CODE_INDEX.md` were
  already clean (fixed earlier, in task 33e and task 34's own writing). `scripts/
  check_no_personal_data.sh` added: greps the whole tracked tree (excluding the script's own file,
  which necessarily contains the identifier patterns themselves, and `laptop/tests/`/
  `android/app/src/test/`, which use IP-shaped values only as synthetic test fixtures, same
  reasoning as the source-only guards from task 33e) for the same identifier list, with one explicit
  exception for Tailscale's own public CGNAT range (`100.64.0.0/10`, mentioned in the Android
  network security config as documentation, not a real address). Verified by deliberately
  reintroducing the real serial into a tracked file and confirming the script caught it (exit 1,
  names the file:line), then reverting. Not yet wired into CI (no CI exists until task 35c).

  **Not done**: the actual publish step - new orphan branch, one commit "Initial public release
  0.1.0", force-push to a new public repo `androsync` (renaming the current private one to
  `androsync-private` first and keeping it as the archive). This is a one-way move (the project
  becomes visible to the entire public internet, not just reversible-in-principle like a private
  push) - stopped here deliberately to get explicit confirmation before doing it, rather than
  inferring consent from a general "continue".
### 35c. CI + GitHub Releases — DONE in code (2026-10-19, issue #23; `ci.yml` earlier, `release.yml` and `scripts/bump_version.py` now). The dry run (Actions, Release, Run workflow) passed on GitHub with unsigned APKs. Signing and the release step verified 2026-10-23: the four secrets were set, a signed dry run passed (apksigner verifies, subject `CN=androsync`), and the tag `v0.1.0` produced a GitHub Release with the play and full APKs, wheel and sdist. See "Make a release" in `android/README.md`. Original text: (2 hours). `.github/workflows/ci.yml`: on push/PR run `pytest laptop`,
  `./gradlew :app:testDebugUnitTest :app:assembleDebug`, the personal-data check. `release.yml`: on tag
  `v*` build `assembleRelease` signed with a keystore from repository secrets (`KEYSTORE_B64`,
  `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`; `app/build.gradle.kts` already reads
  `keystore.properties`, add the env fallback), build the laptop wheel + sdist (`python -m build`), create
  the GitHub Release with the APK, wheel, sdist and the CHANGELOG section. `scripts/bump_version.py` keeps
  `pyproject.toml` `version`, `androsync.__version__`, `versionName`, `versionCode` (+1) in step.
  README: install from a release (`pipx install https://github.com/<user>/androsync/releases/download/…`).
### 35d. F-Droid readiness (half a day). Replace `play-services-code-scanner` with
  `com.journeyapps:zxing-android-embedded` (Apache‑2.0, needs the CAMERA permission and a runtime
  request); check every dependency against F-Droid's anti-features (Glance, Retrofit, OkHttp, DataStore,
  kotlinx are fine; the `foojay-resolver` Gradle plugin is build-only and accepted); add
  `fastlane/metadata/android/en-US/` (`title.txt`, `short_description.txt`, `full_description.txt`,
  `changelogs/<versionCode>.txt`, `images/icon.png`, phone screenshots); make the build reproducible (no
  `local.properties` dependency, pinned Gradle/AGP, `-Pandroid.injected.signing.*` off); then fork
  `fdroiddata`, add `metadata/com.androsync.remote.yml` (Builds entry with `subdir: android/app`,
  `gradle: [yes]`, `AutoUpdateMode: Version`, `UpdateCheckMode: Tags`, `Donate:`/`Liberapay:`), test
  with `fdroid build` in the fdroidserver Docker image, open the merge request. Expect 1–3 weeks review.
### 35e. Donations in product — DONE except the F-Droid metadata line (2026-10-25, issue #25; link is buymeacoffee.com/abqr): `.github/FUNDING.yml`, README License section, the window's Help → About AndroSync dialog, and a "Support" section in the `full` app's Settings (the `play` build shows none because of Google Play's rules on outside payment links). The F-Droid `Donate:` field is added with #24. Original text: Donations in product (1 hour). Donation link in the app's About screen and the GUI's About
  dialog, README badge, F-Droid metadata; text says the app is free software and donations are optional.
### 35f. Google Play (optional, later). $25 developer account; Play App Signing with an AAB
  (`bundleRelease`); privacy policy page on GitHub Pages ("no data leaves your devices"); Data safety
  form; new personal accounts need a 14‑day closed test with 12 testers before production; check the
  current target API level requirement at submission time. A paid Play build is allowed under GPL.

Verification:
`gh repo view` shows the private repo; CI green on the first push; `git tag v0.1.0 && git
  push --tags` produces a Release with a signed APK that installs over the debug build after uninstall
  (different signature) and a wheel that `pipx install` accepts; `scripts/check_no_personal_data.sh`
  exits 0 on the public branch; `fdroid build` succeeds locally; the app scans the QR with the new
  scanner on the real phone.

## Phase O — stop, see and protect a sync (reported 2026-10-15)

Do Phase O first (36c and 36a stop the incident), then 37-doc, 37f, 37g; agent mode (37a–37e) last.

Evidence (read-only, from `daemon.log`, the pair's undo journals and the config):
- 12:14 a run on an inbox pair started pulling the phone's **root filesystem** (journal: 12 785 planned
  creates, paths `prism/`, `optics/`, `odm/`, `dpolicy_system/`). It never finished (`finished: false`), the
  user could not stop it, 785 files had landed in the laptop folder and into the snapshot.
- 12:18 the pair's remote was edited by hand to `/Internal storage/Inbox/` (the label file managers
  show). The next run called `mkdir -p '/Internal storage/Inbox/'` on the phone → "Read-only file system".
- 12:31 remote edited to `/sdcard/Inbox/`. `PUT /v1/pairs` kept the old snapshot, so the run saw 785
  snapshot entries missing on the new remote and **deleted 785 laptop files** (all of them the junk from the
  12:14 run, backed up: 371 MB in the undo journal `20261015-123153-651f`). No user data was lost, but the
  mechanism would delete real files after any folder change.
- Why Stop does nothing: `JobRunner.cancel` only sets a flag; the executor checks it between actions
  (`executor.py` loop), but the work is one blocking `subprocess.run` per batch (`Adb.run`, timeout 600 s) or one
  scan, so a 200 MB / 200 file batch or a scan of `/` cannot be interrupted.
- Why the log is missing on other devices: the daemon already keeps one log per job and streams it
  (`GET /v1/jobs/<id>/log`), but no client looks for a job it did not start. The app only opens a job after it
  started it or after a 409 "Open"; the GUI only follows jobs started from its own buttons; `/v1/ping` does not
  mention the running job; finished jobs keep only the last 20 lines.

### 36a. Cancel that works (laptop) — DONE (2026-10-17, issue #6; verified on the real phone) Files: `transport/adb.py` (`Adb.run` becomes `Popen` + wait loop; the
  in-flight process is stored; `Adb.abort()` = terminate, kill after 2 s; aborted runs raise `AdbCancelled`),
  `transport/base.py` (`Transport.abort()` default no-op), `daemon/jobs.py` (`cancel()` sets the flag **and**
  calls `abort()` on the job's transports; new state `cancelling`; if the job thread is still alive after 10 s the
  job is marked `cancelled` anyway), `executor.py` (`AdbCancelled` or the flag = cancelled run, not an error:
  log "cancelled by user", keep snapshot of completed actions, journal `cancelled: true`; no `ERROR` lines),
  `config.py` (`transfer.batch_files` 50 and `transfer.batch_mb` 64 instead of 200/200 so progress lines and
  cancel points are frequent; log "batch 3/12, 41 MB"; log "scanning phone folder…" / "scanned N files").
  Tests: fake slow transport cancelled mid-batch stops in under 2 s; `AdbCancelled` is not counted as an error;
  state sequence `running → cancelling → cancelled`; a stuck thread is force-marked after the grace period.

**What was built.** `Adb.run` now starts adb through `_run_process` (Popen) and remembers the process;
`Adb.abort()` terminates it (kill after 2 s) and makes every later call raise `AdbCancelled`
(an `AdbError` and a new `TransportCancelled`). `Transport.abort()` (default no-op) reaches it through
`AdbTransport.abort()`. `JobRunner.cancel()` sets state `cancelling`, aborts the job's transports and starts a
`CANCEL_GRACE_S` (10 s) timer that shows the job as `cancelled` if its thread still has not ended;
`cancelling` counts as busy. `run_pair` is now a wrapper around `_run_pair`: a `TransportCancelled` returns the
summary of finished work (log "cancelled", no error, no retry, no pair meta, journal left unfinished); every
`except Exception` in the executor and undo lets it through. New pair option `transfer: {batch_files: 50,
batch_mb: 64}` (defaults.transfer, then pair.transfer), replacing the fixed 200 files / 200 MB. Real runs (not
dry runs) log "scanning...", file counts, "plan: N actions" and "batch N: X files, Y MB".
**Safety change found while doing it:** a push killed in the middle used to leave a truncated file at its final
place, which a later two-way run could copy back over the good laptop file. Pushes now land in
`<phone folder>/.androsync/incoming/<token>/` (never synced) and are moved into place with `mv -f` once complete.
**Also fixed:** a stopped *undo* used to write back the "before" snapshot as if it had finished; it no longer does.
**App:** the job screen shows a "Stopping..." chip and a disabled Stop button while `cancelling`.
**Verified:** `pytest laptop` 664 passed (stable over 3 runs), app 124 passed, debug APK builds. On the real phone,
with 6 x 120 MB files: Stop during a running `adb push` ended the job as `cancelled` in 0.11 s, no adb process was
left, nothing reached the destination folder; a normal 4-file sync afterwards delivered identical hashes, kept the
original timestamp and left the incoming folder empty. **Not done:** Stop from the app on the phone (the daemon
side is verified; the button text change is only unit-tested).

### 36b. See and stop a sync from any device (laptop API, app, GUI) — MOSTLY DONE (2026-10-17, issues #7 and #27) Files: `daemon/server.py`
  (`/v1/ping` gains `current_job {id, pairs, state, started, last_line}`; `GET /v1/jobs/current`; job dict
  gains `cancel_requested`), `daemon/jobs.py` (every job's full log written to
  `$XDG_STATE_HOME/androsync/jobs/<id>.log`, last 30 kept; `GET /v1/jobs/<id>/log?since=0` falls back to the
  file for finished or evicted jobs; `GET /v1/jobs` rows carry kind, pairs, state, summary counts), Android
  `ConnectionViewModel` (reads `current_job` from the 5 s ping), new `ui/common/ActiveJobBar.kt` shown under the
  top bar on every screen: pair names, elapsed time, last log line, **Open** and **Stop** (confirm; shows
  "Stopping…"), `ui/job/JobScreen.kt` (Stop always visible; becomes **Stop now** after 5 s = second cancel;
  log reloaded from `since=0` when reopened so leaving the screen loses nothing), new `ui/jobs/JobsScreen.kt`
  (history icon in the top bar: recent jobs with state chip, tap opens the saved log), tile and widget use
  `current_job` for "Syncing…", GUI `gui/app.py` + `gui/backend.py` (poller attaches the log panel to
  `current_job` whatever started it, Stop button next to the log, "Jobs…" dialog), pure helper
  `should_attach(ping, attached_id)` in `gui/logic.py`.
  Result: phone, GUI and `curl` all show the same live log and can stop the same job.
  Tests: ping contains `current_job`; log file persisted and served after eviction; Android unit tests for the
  bar state (idle / running / stopping); GUI helper table test.

**What was built.** `GET /v1/ping` now carries `current_job {id, pairs, kind, state, dry_run, started, last_line}` (null
when idle; `daemon/jobs.py` `current_job_dict`), so every client sees a sync that anyone started. **App:** `CurrentJobDto`
in the ping, `ConnectionState.Connected.currentJob`, `ui/common/ActiveJobBar.kt` + `ActiveJobLogic.kt` shown under
the top bar of every screen (what, elapsed, latest log line, **Open**, **Stop** with a confirmation; "Stopping..."
while cancelling; hidden on the job screen itself), actions provided through `LocalJobActions` from the navigation host,
`ConnectionViewModel.stopJob/refreshNow`, and a new **Recent syncs** screen (`ui/jobs/`, list icon in the Pairs top bar,
`GET /v1/jobs`, tap opens the full log). **Window:** the log panel got a strip with the running sync and a **Stop**
button (daemon job or a sync running in the window itself); `should_attach` follows the full log of a sync someone else
started (`ApiClient.stream_job_log`, server-sent events, with polling as fallback); issue #27 fixed (`failed` is a final
state, the window no longer polls a failed job forever). Undo jobs are followed the same way.
**Not built (left on purpose):** the per-job log files on disk (`$XDG_STATE_HOME/androsync/jobs/<id>.log`, last 30) - logs
live in memory (50 jobs, 5000 lines each) and are lost when the daemon restarts; the second "Stop now" press; tile and
widget showing "Syncing..." from the ping; a "Jobs..." dialog in the window.
**Verified:** `pytest laptop` 688 passed; app 136 passed, debug APK built and installed on the phone; on the real
laptop service a temporary pair of four 100 MB files: the ping showed the running job with its latest line, Stop ended it
as `cancelled`, the ping went back to `null`. **Not done:** tapping the new bar in the app (needs your eyes).

### 36c. Phone path validation (laptop, app, GUI) — DONE (2026-10-17, issue #8; not yet checked on the real phone) Files: `config.py` (`normalize_remote()` and
  `validate_remote()`), `config_edit.py` (used by add/update so API, CLI, GUI and app all get it),
  `transport/adb.py` (`mkdirs` of a pair root only when its parent exists), `gui/dialogs.py`,
  `ui/pairs/PairEditorViewModel.kt` + `validatePairForm` (same rule, shown under the field), remote folder
  browsers (start at `/sdcard`, "Up" stops there).
  Normalise: collapse `//`, drop trailing `/`, map `/Internal storage`, `/Internal Storage`,
  `/storage/emulated/0`, `/storage/self/primary`, `/mnt/sdcard` to `/sdcard`.
  Reject with a message that names the fix ("Phone folders must be inside internal storage (/sdcard/…) or an SD
  card (/storage/XXXX-XXXX/…). Did you mean /sdcard/Inbox?"): `/`, `/data`, `/system`, `/vendor`,
  `/proc`, `/sys`, `/dev`, `/mnt`, `/storage` alone, `/sdcard/Android/data`, `/sdcard/Android/obb`, and anything
  not under `/sdcard` or `/storage/<id>`. A hand-edited config is checked again at run time.
  Tests: normalisation table, rejection table, API 400, the exact `/Internal storage/Inbox/` case.

**What was built (differs from the spec above in one place).** New `laptop/androsync/phone_path.py`
(`normalize_remote`, `validate_remote`, `check_remote`, `remote_parent`) and a Kotlin copy
`android/.../data/PhonePath.kt`; both have the same test tables. Applied in:
- `config_edit.add_pair` / `update_pair` (API, CLI, window, app all go through it; a pair whose phone folder is
  unchanged is not re-checked, so an old bad pair can still be paused or fixed),
- `AdbTransport` (`exists`, `mkdirs`, `store`, `store_many`, `delete`, `rename` check and normalize the root, so a
  hand-edited config fails early with the same message; `mkdirs` of a pair root also needs an existing folder at most
  two levels above it),
- `browse.py` (`browse_remote` and `mkdir_remote` raise `BrowseForbidden`, the server answers 403; "up" stops at
  `/sdcard` or at the top of an SD card),
- the window's pair editor and the CLI `add-pair` prompts, and the app's `validatePairForm` plus save and browse start.
**Deviation:** the check is deliberately NOT in `config.load_config`. The test suites use real temporary folders as a
fake phone, so a loader-level check rejected them; the transport is the layer that talks to the real phone.
Verified: `pytest laptop` 645 passed, `./gradlew :app:testDebugUnitTest` 124 passed, debug APK builds, the user's
seven real pairs all pass the rules. **Not done:** a run against the real phone (none attached); check with
`androsync status <pair>` and by creating a pair with `/Internal storage/Test` in the app.

### 36d. Safety net for pair edits and mass deletes (laptop, then app and GUI dialogs) — DONE (2026-10-18, issue #9) Files:
  `daemon/server.py` + `config_edit.py` (a `PUT` that changes `local` or `remote` moves the pair's snapshot and
  pending file to `state/<pair>.reset-<timestamp>/`, kept 30 days, so the next run is a first run that never
  deletes; response `state_reset: true`; a rename keeps state as today), `executor.py` (before acting, if
  planned deletions on one side exceed `max(safety.max_delete_count 50, safety.max_delete_percent 20 % of that
  side's snapshot)` or a first run would copy more than `safety.big_first_run_files 5000` or `…_gb 5`, the run
  stops without changes and reports counts; `POST /v1/run {confirm_large: true}`, CLI `--confirm-large`, GUI and
  app dialog with counts and examples; setting a threshold to 0 disables that guard), `daemon/jobs.py` (job
  state `needs_confirmation`), `ui/pairs/PairEditorScreen.kt` and `gui/dialogs.py` (note "Folders changed:
  history reset, the next sync only adds files").
  Tests: path edit resets state, rename does not; the 785-delete scenario is blocked and names the counts;
  confirmation lets it through; 12 785-file first run is blocked; thresholds read from config; undo still works.

**What was built.** *History reset:* `state.reset_pair_state` moves a pair's snapshot, pending asks and undo journals to
`<state>/<pair>.reset-<time>/` (deleted after 30 days, `prune_resets`); `config_edit.folders_changed` decides whether a
pair's laptop or phone folder really changed (spellings of the same folder do not count) and
`config_edit.update_pair_and_state` saves the edit, resets on a folder change and moves the history on a rename. The
pair update endpoint, `edit-pair` and the window all use it; the endpoint still answers with the pairs list and adds the
header `X-Androsync-State-Reset: 1`. *Guard:* new `safety.py` (`check_plan`, `SafetyLimits`); config block `safety:`
(`max_delete_count` 50, `max_delete_percent` 20, `big_first_run_files` 5000, `big_first_run_gb` 5, 0 = off; same merge
order as `undo:`). `run_pair(confirm_large=)`: a plan over a limit changes nothing, logs "STOPPED, nothing was changed",
writes "needs confirmation: ..." as the pair's last result and sets `Summary.needs_confirmation` / `confirmation`; a
dry run lists the same warning as a `!  needs your confirmation:` line. Job state `needs_confirmation` is final;
`GET /v1/jobs/<id>` carries `confirmation {pair: [sentences]}`; `POST /v1/run` accepts `confirm_large`. CLI: `run` asks to
type `yes` for a large change, `--yes` alone refuses (exit 1), `--confirm-large` goes ahead. Window: a dialog with the
reasons and a Go ahead choice (`Backend` event `confirm`), also for syncs run inside the window. App (done in a parallel
session): `needs_confirmation` is a final state with a "Needs your OK" chip, a card on the job screen with the reasons,
Go ahead and Not now (sends only the blocked pairs), a notification text, and an editor note that a changed folder resets
the history.
**Deviation:** the undo journals also move into the reset folder (they describe files in the old folders); the spec
listed only snapshot and pending file.
**Verified:** `pytest laptop` 734 passed, app 145 passed, debug APK built and installed; on the real service a throwaway
pair with the limit set to 2 files: the first run ended as `needs_confirmation` naming "would copy 4 files", and after
Go ahead it copied all 4. **Not done:** tapping Go ahead in the app on the phone.

Order: 36c and 36a first (they stop the incident), then 36d, then 36b. Each is independent enough for one session.

## Phase P — syncing without USB or Wireless debugging (asked 2026-10-15)

Assessment (sources: Syncthing docs, F-Droid listing, Privacy Guides threads; checked 2026-10-15):
- Debugging cannot be avoided while the laptop reaches the phone through ADB. USB debugging or Wireless
  debugging *is* the permission ADB needs; plain "File transfer" (MTP) cannot set timestamps or hash files.
- Syncthing is open source, peer to peer, TLS encrypted, no cloud. Android needs "All files access" for it.
  The official Android app was retired in December 2024 and left Google Play (that permission is restricted
  there). The maintained app is **Syncthing-Fork** (F-Droid `com.github.catfriend1.syncthingfork`, 2.1.2.0 from
  July 2026); its repository and maintainers changed in late 2025, so check the F-Droid page before relying on it.
  Laptop side: `sudo apt install syncthing`, `systemctl --user enable --now syncthing`, web GUI on
  `127.0.0.1:8384`, REST API with an `X-API-Key` header. Phone and laptop connect by local discovery, relays,
  or a fixed address such as `tcp://<tailscale-ip>:22000`.
- What it gives: no cable, no debugging, continuous background sync, folder types Send Only / Receive Only /
  Send & Receive, versioning (trash can, simple, staggered), `.stignore`. What it does not give: preview before a
  sync, undo of a run, the conflicts folder (it writes `name.sync-conflict-DATE-ID.ext` beside the file, the same
  kind of duplicates you disliked), per-pair "sync now", one place to see everything. It also runs all day (set
  "only on Wi‑Fi / only charging" in the app).

| Need | Syncthing | androsync today (ADB) | androsync Agent mode (new) |
|---|---|---|---|
| USB or Wireless debugging | not needed | needed | not needed |
| Cable required | no | USB, or Wi‑Fi debugging | no |
| Preview, undo, conflicts folder | no | yes | yes |
| On-demand "sync now" with live log | no (continuous) | yes | yes |
| Effort | 30 min setup | done | about 5 days |
| Store | F-Droid / GitHub | F-Droid / GitHub / Play (remote control only) | F-Droid / GitHub; Play only without the agent |

Why agent mode and Google Play clash: the agent must read and write arbitrary folders, which needs the
"All files access" permission (`MANAGE_EXTERNAL_STORAGE`). Play allows it only for a short list of core
purposes (file managers, backup and restore, document management, antivirus, device migration) and reviews each
request; Syncthing's Android app was removed from Play for exactly this reason. Today's androsync app never
touches files, so it is not affected. Ways to keep a Play listing if wanted:
1. **Two build flavors** (recommended if agent mode is built): `play` = remote control for the ADB daemon, no
   file permission; `full` = same plus the agent, for F-Droid and GitHub. One code base, the agent code and the
   permission live only in the `full` flavor's manifest and source set.
2. **SAF-only agent**: the user picks each folder once with the system picker (no special permission, allowed on
   Play). Costs: much slower scans (one content-resolver query per file), and Android has no call to set a
   file's modified time through SAF, so timestamps are not preserved; the engine would rely on per-side mtimes
   and hashes (already stored since task 29a). Workable for notes, poor for 10 000 photos.
3. Apply to Play for the permission anyway: likely rejected for a sync tool; not worth planning around.

**Decision (user, 2026-10-15): two builds of the app, plus a guided setup.** Reason given: users will find it
hard to keep USB debugging on, so the app must lead them to the right Settings window, and an agent build that
needs no debugging should exist next to the Play-friendly one. So the plan is: Syncthing guide (37-doc), guided
setup assistant (37f, helps every ADB user at once), build flavors (37g), then agent mode (37a–37e) in the
`full` flavor only. Order: 37-doc, 37f, 37g, 37a (go/no-go), 37b, 37c, 37d, 37e. Do not bridge the two systems
through Syncthing's REST API: it would duplicate its own UI without adding undo.

Flavors (37g): Gradle dimension `distribution` with `play` and `full`, same `applicationId`. `play` = remote
control for the ADB daemon, no file permission, can go to Google Play (`bundlePlayRelease`). `full` = everything in
`play` plus the agent, the All-files-access permission and its screens, for GitHub Releases and F-Droid
(`gradle: [full]` in the fdroiddata entry, task 35d). The agent code lives in `src/full`, a stub in `src/play`.
Caveat for the README: APKs from different stores are signed with different keys and cannot update each other;
switching store means uninstalling first (the app keeps no data besides URL and token).

**Follow-up (2026-10-18, from the user):** in the laptop window the reset was only reported in the log *after* saving
("folders changed: history reset..."), never asked before. `App._on_edit` now asks OK/Cancel first
(`gui/logic.py` `folder_change_prompt`, `config_edit.folders_changed`); Cancel keeps the old folders and the history.
Tests in `test_gui_logic.py` and `test_gui_smoke.py` (cancel, OK, unrelated edit). The window is a separate process:
close and reopen it to get the change.

### 37-doc. Syncthing guide — DONE (2026-10-19, issue #11; written from Syncthing's own docs and the F-Droid page; followed by the user on spare folders on 2026-10-25 and it worked) (1 hour, docs only): `docs/syncthing.md` + a short "No USB debugging?" box in the
  root README. Steps: install both sides, pair the devices (QR from Syncthing-Fork), folder types for the three
  common cases (photos: phone Send Only, laptop Receive Only; vault: Send & Receive with staggered versioning;
  inbox: Send & Receive), phone run conditions, Tailscale fixed address, how to avoid conflict files (one writer
  per file, `maxConflicts`), and when to prefer androsync instead.

### 37f. Guided setup assistant: lead the user to the debugging switch — DONE (2026-10-19, issue #10; laptop part in d89d480, app part below; the on-phone test with debugging switched off is still to be done by the user). As built: no ViewModel (the screen re-reads the two `Settings.Global` switches on `ON_RESUME`), the notification receiver is registered in `AndroSyncApplication` because Android does not deliver `USB_STATE` to manifest receivers, so the hint only appears while the app process is alive; the wall-charger fallback (`ACTION_POWER_CONNECTED`) was left out because it would fire for every charger. The `full` row (All files access) exists in `computeChecklist` but is switched off until #12. (1 day, both flavors). Android does not
  let an app flip USB debugging itself (that needs `WRITE_SECURE_SETTINGS`, which only an ADB grant can give), so
  the app shows where it is and opens the exact Settings page. Files: new `ui/setup/ConnectPhoneScreen.kt` +
  `ConnectPhoneViewModel.kt`, pure `SetupChecklist.kt`, `ui/common/SetupHintNotifier.kt`, `UsbStateReceiver.kt`,
  `ui/MainActivity.kt` routes, laptop `daemon/server.py` + `daemon/watcher.py` (`/v1/ping` gains
  `devices: [{serial, state: device|unauthorized|offline}]`), `cli.py` (`devices` prints the same hint).
  Screen "Connect your phone", a live checklist, each row with a status mark and one button:
  1. *Developer options on?* read `Settings.Global.DEVELOPMENT_SETTINGS_ENABLED`. If off: text "Open About phone
     and tap Build number 7 times (on Samsung: Software information)", button **Open About phone**
     (`Settings.ACTION_DEVICE_INFO_SETTINGS`).
  2. *USB debugging on?* read `Settings.Global.ADB_ENABLED`. If off: button **Open Developer options**
     (`Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS`, fallback `ACTION_SETTINGS`) with "Switch on USB
     debugging"; the screen re-checks when the user returns (`ON_RESUME`) and ticks the row by itself.
  3. *This computer allowed?* from the daemon: `unauthorized` → "Look at the phone for 'Allow USB debugging?' and
     tick Always allow"; `offline` → "Unplug and replug the cable"; `device` → done.
  4. *Stays on?* hint with the same Developer options button for "Disable adb authorization timeout" where the
     phone has it, and a note that some phones switch debugging off after a reboot.
  5. (`full` flavor only) *All files access* via `Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION` with
     the package URI, status from `Environment.isExternalStorageManager()`.
  Opened from: Setup (after the QR scan), Settings, and an inline "Phone not detected — fix it" link on the Pairs
  screen whenever `/v1/ping` says `device: null` or `unauthorized`.
  Proactive nudge: a receiver for the sticky `android.hardware.usb.action.USB_STATE` broadcast (extras
  `connected`, `adb`; best effort, fall back to `ACTION_POWER_CONNECTED` + `ADB_ENABLED`) posts one notification
  per plug-in, "USB connected but USB debugging is off. Tap to open the setting", which opens Developer options
  directly. Notification channel "Setup hints", off switch in Settings.
  Tests (pure JVM): `SetupChecklist.compute(devOptions, adbEnabled, deviceState, isFull, allFiles)` gives the
  right rows, order and active button for every combination; intent action strings; daemon ping devices list.
  Manual: with debugging off, plug in → notification → tap → Developer options opens → switch on → return →
  row 2 ticks → authorise prompt on the phone → row 3 ticks.

### 37g. Build flavors — DONE (2026-10-19, issue #12). As built: flavor dimension `distribution` (`play`, `full`), `Distribution.IS_FULL` from `BuildConfig`, `src/full/AndroidManifest.xml` adds `MANAGE_EXTERNAL_STORAGE`, `scripts/check_flavor_permissions.sh` (also a CI step) proves play has no all-files permission and full has it; plain `testDebugUnitTest` is a small aggregate task over both flavors so old commands still work. The checklist row for All files access stays hidden until the agent exists (`Distribution.HAS_AGENT`, flip it in 37c). Not done here: the release workflow part (task 35c / #23) and a `bundlePlayRelease` upload. Original text: Build flavors `play` and `full` (half a day). `android/app/build.gradle.kts` `productFlavors`, source
  sets `src/play` and `src/full`, a `Distribution.IS_FULL` constant, CI builds both (task 35c), release workflow
  attaches `app-full-release.apk` to GitHub Releases and uploads the `play` bundle as an artifact, README table
  "Which version do I install?". Unit tests: the checklist hides row 5 and the agent settings in `play`.

Agent mode (decided: second build, `full` flavor only):
### 37a. Spike — DONE, **GO** (2026-10-23, issue #13). Run on the real phone (S24 FE, Android 16-class FUSE storage `/storage/emulated`), with All files access, from a throwaway debug-only activity (removed again in the next commit; the code is in git history at commit 6782440). Results:
  - **Modified time sticks**: `File.setLastModified(1577836800123)` read back exactly `1577836800123` (millisecond precision) in `DCIM` and `Documents`, on a file the app created and on a file pushed by `adb`. `adb shell stat` showed the same time, so the laptop sees what the app set.
  - **Rename keeps the time**, so the safe-push scheme (write to `.androsync/incoming/`, then move) works unchanged. Delete works.
  - **Scan speed**: 1762 files in 36 folders of the real `DCIM` listed in 565 ms (about 3100 files per second).
  - **1 GiB**: written in 0.8 s (about 1270 MB/s), SHA-256 in 0.77 s (about 1330 MB/s). Hashing on the phone is not a bottleneck; the network will be.
  - **Gallery**: `MediaScannerConnection.scanFile` called back after 41 ms and the new file had a `MediaStore` row.
  - Caveat: all numbers are from one phone with a fast internal disk; an SD card or an older phone will be slower. The only grant used was `MANAGE_EXTERNAL_STORAGE`, given through `appops` for the test and revoked afterwards.
  - Decision: agent mode is feasible; go on with 37b (#14).
  Original text: Spike (half a day): a screen in the Android app that requests All files access and, on the real phone,
  tests `File.setLastModified`, SHA-256 of a 1 GB file, writing into `/sdcard/DCIM`, and `MediaScannerConnection`.
  Go / no-go: mtime must stick on FUSE storage.
### 37b. Protocol + `AgentTransport` — DONE (2026-10-23, issue #14). As built: `transport/agent.py` (`AgentBroker` mailbox + `AgentTransport`), routes `GET /v1/agent/next`, `POST /v1/agent/result/<id>`, `GET`/`PUT /v1/agent/blob/<id>`, `/v1/ping` gains `"agent": "connected"|"absent"`, `Daemon.agent_broker`; wire format in `docs/agent-protocol.md`. The laptop filters ignore rules itself so the Kotlin agent needs no glob code. Tests: `fake_agent.py` speaks the real protocol over real HTTP; `test_agent_transport.py` (19) and `test_e2e_agent.py` (13: the ten `test_e2e_local` scenarios through the agent, plus 60 files in one run and an undo). Not yet wired to pairs: that is 37d (`transport: agent` option). Original text: Protocol + `AgentTransport` (laptop, 1.5 days): `Transport` implementation that queues operations
  (`scan`, `stat`, `hash_many`, `fetch`, `store`, `delete`, `rename`, `mkdirs`, `exists`, `ls`) for a connected
  agent; endpoints `GET /v1/agent/next` (long-poll, 25 s), `POST /v1/agent/result/<op>`, `PUT/GET
  /v1/agent/blob/<op>` (streamed bodies); `/v1/ping` reports `agent: connected|absent`. A Python fake agent in
  the tests runs the whole engine suite against it.
### 37c. Android agent — DONE in code (2026-10-25, issue #15; not yet tried on a phone with debugging off). As built, `src/full` only: `agent/AgentOps.kt` (the ten operations on `java.io.File`, folder guard = `PhonePath`, 18 JVM tests), `AgentClient.kt` (OkHttp, streamed bodies), `AgentLoop.kt` (poll, run, answer, back off 2 to 30 s, re-probe the laptop after 3 failed polls; 8 tests with MockWebServer), `AgentService.kt` (foreground service, wake lock only while an operation runs, media scan for DCIM/Pictures/Movies/Music/Download/Documents), `ui/settings/AgentSettingsSection.kt` (switch, All files access button, battery hint, "the laptop sees the agent"). The play build has an empty stub. Deviation from the text below: the service type is `specialUse`, not `dataSync`, because Android 15 stops `dataSync` after six hours. No autostart at boot yet. Original text: Android agent (2 days, in `src/full` only): foreground service (type `dataSync`, wake lock while an op runs) that long-polls
  the laptop it is already configured for, executes ops with `java.io.File` under All files access, streams
  bodies, ignores the SAF; permission screen and battery-optimisation hint; notification "AndroSync agent
  connected to <laptop>".
### 37d. Wiring — DONE (2026-10-25, issue #16). As built: pair option `transport: adb | agent` (default adb; `config.TRANSPORT_MODES`), `JobRunner.open_transports` opens adb only when some pair in the job needs it (agent-only jobs work with no phone attached; a mixed job fails only the adb pairs), `/v1/status`, undo, conflicts clean and remote browse use the pair's transport (browse falls back to the agent when no adb phone is attached), `/v1/pairs` carries `transport`, CLI `add-pair`/`edit-pair --transport`, CLI/window direct runs skip agent pairs with a hint, window "Via" column and dialog selector, app editor chips (full build only) and "via this app" on the card. The play build keeps a saved `agent` transport when editing. Tests: `test_agent_pairs.py` (12), `test_cli_pairs.py` (+4). Original text: Wiring (1 day): pair option `transport: adb | agent` (default adb), agent pairs need no device,
  Preview, Undo, Conflicts and the guards from Phase O work unchanged; GUI and app show which transport a pair
  uses and whether the agent is connected. Works over Tailscale, LAN and USB tethering.
### 37e. Docs and F-Droid notes — DONE (2026-10-25, issue #17): `docs/agent-mode.md` (comparison with Syncthing and ADB, versions, permission, the Google Play limit, setup, limits) and a comparison table in the root README. F-Droid specifics stay with #24. Original text: Docs and F-Droid notes (half a day): README comparison table, permission explanation, Play Store caveat.

Verification:
(1) point a test pair at `/` → rejected with the "Did you mean" message; `/Internal storage/x` is
  normalised to `/sdcard/x`. (2) Start a sync of a large folder, press Stop in the app → state shows
  "Stopping…" then "cancelled" within seconds, no `adb` child process left (`pgrep -f "adb.*pull"`). (3) Start a
  sync from the app, leave the screen and return: the bar shows the running job with its last line, Open shows
  the full log from the start. (4) The same job shows its log in the desktop window and `curl
  /v1/jobs/current`; Stop from the GUI stops the job the app started. (5) Edit a pair's phone folder → "history
  reset" note, next run deletes nothing. (6) A pair whose plan deletes more than 20 % of its files stops with
  counts until confirmed.
- Phase P, 37f: on the real phone with USB debugging switched off, plug in → the "USB debugging is off"
  notification appears → tapping it opens Developer options → after switching on and returning, the checklist
  ticks the row by itself; unauthorised and offline states show their hints. 37g: `./gradlew assemblePlayDebug
  assembleFullDebug` both build; the `play` APK has no All-files-access permission in its manifest
  (`aapt dump permissions`), the `full` APK has.
- Phase P: 37-doc: follow `docs/syncthing.md` on a spare folder until a file edited on the phone appears on the
  laptop without any ADB command. Agent mode: with `adb kill-server` and USB debugging off, an agent pair previews
  and syncs over Tailscale; undo restores; timestamps match on both sides.

Phase P verification: on the real phone with USB debugging switched off, plug in → the "USB debugging is off"
  notification appears → tapping it opens Developer options → after switching on and returning, the checklist
  ticks the row by itself; unauthorised and offline states show their hints. 37g: `./gradlew assemblePlayDebug
  assembleFullDebug` both build; the `play` APK has no All-files-access permission in its manifest
  (`aapt dump permissions`), the `full` APK has.
- Phase P: 37-doc: follow `docs/syncthing.md` on a spare folder until a file edited on the phone appears on the
  laptop without any ADB command. Agent mode: with `adb kill-server` and USB debugging off, an agent pair previews
  and syncs over Tailscale; undo restores; timestamps match on both sides.

## Action lines show both paths — DONE (2026-10-18, user request; issue #28)

A preview or log line used to show only the path relative to the pair's folder, so a deletion read "deleted on phone ...
deleted on laptop" without saying where the file was on the phone. Now every action line (`executor._format_action`)
is still one line (the live log is sent line by line) and ends with `(laptop: <full path>  ·  phone: <full path>)`
(`PATHS_SEPARATOR`); a delete says which side it removes from: `x  phone  notes/a.md   deleted on laptop, so removed from
the phone   (laptop: ...  ·  phone: ...)`. Conflict and ask lines carry the paths too. The CLI, the laptop window and the
job log show the line as is; the app's Preview sheet splits it (`ui/common/ActionLine.kt`, `parseActionLine`) into the
action and the two paths on lines of their own, and shows older daemons' lines unchanged. Tests: 6 laptop (line shapes,
trailing slashes, a real dry run) and 4 app (parser incl. spaces and parentheses). Verified on the real service with a
throwaway pair: sync, delete on the laptop, preview showed both full paths. **Not done:** the log buffer holds 5000 lines,
so a run of thousands of actions shows only the latest (unchanged).

## Small leftovers (do whenever convenient)
- L1. Run log file — DONE (2026-09-08). `androsync run`/`androsync undo` and every job the daemon
  runs write their log lines to `~/.local/state/androsync/last-run.log` (via new `runlog.py`:
  `RunLog` truncates on construction, `tee()` fans a log callback out to both the file and the
  original destination), truncated fresh per run/job. `status`'s dry-run preview does not write it.
  `--verbose` adb commands go in too, only when `--verbose` is passed (matches existing behaviour:
  without it, `Adb` gets no log callback at all, so there is nothing to tee). Verified for real:
  `status --verbose` wrote nothing, `run --verbose` wrote the adb commands, a second run truncated
  the first one away. 504 passed (498 + 6 new: `RunLog`/`tee` unit tests, CLI/daemon integration).
- L2. `pipx install ./laptop` note — DONE (2026-09-08), in "Run as a service": explains why
  `service install`'s unit breaks if the `androsync` it ran with moves/is deleted (it bakes in
  `sys.argv[0]`, resolved), and that `pipx install ./laptop` gives a stable `~/.local/bin/androsync`
  that survives moving the repo.
- L3. `laptop/README.md` troubleshooting section — DONE (2026-09-08): `unauthorized`, `more than
  one device found` (ties into task 25's dual-transport-visible case), the toybox `find -printf %y`
  fallback (already automatic, just documented), Tailscale IP not bound at boot (the daemon retries
  for 30 s / every 2 s, matching `watcher.py`'s `_BIND_RETRY_TIMEOUT`/`_BIND_RETRY_INTERVAL`).
- L4. App — DONE (2026-09-08). The pending-conflicts count was already shipped as part of task 23c
  (`PairCard`'s "N conflicts waiting" line) - nothing left to do there. Added the "Preview" for
  "Sync all": a top-bar button next to "Sync all" that calls `GET /v1/status/<pair>` for every
  configured pair in parallel (`PairsViewModel.previewAll`) and shows a compact per-pair summary
  line in the existing preview bottom sheet (`PreviewState.LoadedAll`, `PreviewAllContent`) - drill
  into one pair's own **Preview** for the full itemized file list. Build green
  (`testDebugUnitTest` + `assembleDebug`).

  Found and fixed a real gap while installing the app for a check: the systemd daemon had been
  running since *before* today's session started, so it was silently serving every task from 29a
  onward (hashing, conflicts folder, undo, this L4 change) with stale pre-session code - confirmed
  by a real `KeyError: 'mtime'` from `GET /v1/status/media` (old code reading a snapshot file
  written in today's new per-side-mtime format). Restarted the service on the user's go-ahead;
  confirmed the error is gone and `GET /v1/status/<pair>` now works for every pair. This means
  none of today's app-facing (HTTP/daemon-path) work was actually exercised for real until this
  restart - only the CLI path was (which never touches the long-running daemon process). Worth
  remembering for next time: a code session spanning daemon changes should end with a service
  restart before calling anything "verified against the app" specifically.
