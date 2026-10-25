# Code index

A one-line-per-file map of the codebase, for finding things fast. See `docs/architecture/` for the diagrams, `PLAN.md` for
the design, `NEXT_TASKS.md` for what was built when and why, `laptop/README.md`/`android/README.md`
for usage.

## Laptop (`laptop/androsync/`, Python 3.12)

### Entry points
- `cli.py` — argparse entry point; `devices`, `ls`, `status`, `run`, `history`, `runs`, `undo`,
  `serve` (double-start guard + `setup_logging`, task 31a), `gui` (task 31b), `pair-wifi`, plus
  registers `cli_pairs.py`/`cli_conflicts.py`'s subcommands.
- `cli_pairs.py` — `add-pair`/`edit-pair`/`remove-pair` (interactive folder browser for `add-pair`)
  and `resolve` (per-file `conflict: ask` resolution).
- `cli_conflicts.py` — `conflicts list|restore|clean` subcommands.
- `service.py` — systemd user unit install (opt-in `--autostart`, task 31a)/uninstall/start/stop/
  restart/status/logs/`autostart on|off`/`install-launcher`/`uninstall-launcher` (task 31c), `qr`
  (prints the token + URLs as an ASCII QR for the phone app).
- `service_state.py` — `detect_state()` (task 31a): is androsync running as the systemd unit, in
  this process, in another process, or not at all; pid file, daemon log tail, `start_background`/
  `stop_background`/`set_autostart`, `ping()` (`GET /v1/ping` used both to detect a running daemon
  and to decide whether the GUI can use the API instead of calling the sync engine directly).
- `apiclient.py` — tiny `urllib`-based client for the daemon's own HTTP API (task 31b): what
  `gui/backend.py` uses instead of `run_pair`/`undo_run` directly when a daemon is reachable.

### Sync engine (pure logic + execution)
- `model.py` — core dataclasses/enums: `FileEntry` (scan-time fingerprint), `SnapshotEntry`/
  `PersistedSnapshot` (per-side-mtime persisted snapshot, task 29a), `ChangeKind`, `Side`,
  `ActionKind`/`Action`, `Summary` (gains `run_id`, task 30a).
- `ignore.py` — gitignore-flavoured glob matcher (`IgnoreRules`); `.androsync/**` is always
  ignored regardless of config (task 29b).
- `state.py` — per-pair JSON persistence: snapshot, meta (last run/result), pending `ask` entries;
  `undo_dir()`; rename/purge helpers used by pair CRUD.
- `planner.py` — **pure** `plan(scan_local, scan_remote, last, pair, hasher?) -> PlanResult`: turns
  two scans + the last snapshot into `Action`s. Content-hash resync for same-size conflict
  candidates (task 29a), case-collision warnings (task 24).
- `executor.py` — `run_pair()` carries out a `PlanResult` through two `Transport`s: batched copies
  (task 24), conflict handling (folder/beside modes, task 29b), undo journaling (task 30a),
  `versioning.py` hook, snapshot persistence after every action. Also `resolve_pending()`.
- `safety.py` — `check_plan`/`SafetyLimits`: when a plan (many deletes, huge first run) must wait for the user's confirmation (task 36d).
- `conflict.py` — beside-mode conflict-copy naming (`conflict_name`/`is_conflict_copy`) and
  folder-mode paths (`conflict_folder_path`/`conflict_folder_rel`, task 29b).
- `conflicts.py` — `list_conflicts`/`restore` (folder-mode backups) and `find_duplicates`/`clean`
  (hash-based clean-up of pre-task-29b `*.conflict-*` duplicates), task 29b.
- `undo.py` — `RunJournal` (written by `executor.py` as a run happens) and `undo_run()` (replays a
  journal backwards through the transports; itself journals as `kind="undo"`), task 30a/30b.
- `versioning.py` — `versioning: git` pair option: commits the local side after a successful run
  with no errors (task 26).

### Config and shared helpers
- `config.py` — YAML → validated `Config`/`PairConfig`; merges `defaults` into each pair; `~`
  expansion; `state_dir()`/`default_config_path()`.
- `config_edit.py` — raw-YAML pair CRUD (`add_pair`/`update_pair`/`remove_pair`), atomic write,
  always round-tripped through `config.py` for validation before touching disk. Shared by
  `cli_pairs.py` and the daemon's pair-CRUD HTTP routes.
- `phone_path.py` — rules for phone folders (task 36c): maps `/Internal storage` to `/sdcard`, refuses `/`, `/data`
  and app-private folders with a hint, `remote_parent` stops "up" at the top of a volume.
- `browse.py` — folder-browse/mkdir logic (local `os.scandir`, restricted to `service.browse_roots`;
  remote via `AdbTransport.ls`) shared by `daemon/server.py`'s `/v1/browse`, `/v1/mkdir`.
- `runlog.py` — `~/.local/state/androsync/last-run.log`, truncated fresh per run/job (`RunLog`,
  `tee()`), task L1.

### Transports (`transport/`)
- `transport/base.py` — `Transport` ABC every side implements: `scan`/`fetch`/`store`/`delete`/
  `rename`/`mkdirs`/`exists`/`stat`, batched `store_many`/`fetch_many` (default loops), `hash_many`
  (default: fetch + hashlib, task 29a).
- `transport/local.py` — `LocalDirTransport`: the laptop side, and (since any local directory
  behaves the same) stands in for the phone in almost every test.
- `transport/adb.py` — `AdbTransport` + `Adb`/`list_devices`/`resolve_device`: everything that
  shells out to `adb` - scan (`find -printf`/`-exec stat` fallback), batched push/pull, on-device
  `sha256sum`/`md5sum` hashing, media-scanner broadcasts, mtime self-test.

### Daemon (`daemon/`)
- `daemon/jobs.py` — `JobRunner`: one sync-or-undo job at a time, background thread, ring-buffer
  live log, cancel flag; `submit()`/`submit_undo()`; `pairs_status()` for `GET /v1/pairs`.
- `daemon/server.py` — HTTP API (`http.server.ThreadingHTTPServer`, stdlib only): every route in
  `_ROUTES`, bearer-token auth, SSE job-log streaming.
- `daemon/watcher.py` — `adb track-devices` loop: on connect sets up `adb reverse` (skipped for a
  TCP/Wi-Fi device), desktop notification, optional auto-sync; `WifiReconnector` retry loop; a
  transport-aware grace period before cancelling a job on a flaky TCP disconnect; `Daemon.start`/
  `stop` write/remove the pid file `service_state.py` detects, `run_forever` also handles SIGHUP
  (task 31a).

### Desktop GUI (`gui/`, task 31b - `androsync gui`)
- `gui/logic.py` — pure, no `tkinter` import: `service_panel_view` (run state → status text/which
  buttons are enabled), `format_pair_row`, `close_window_decision`. Unit tested without a display.
- `gui/backend.py` — `Backend`: runs one Pairs-panel action at a time on a background thread,
  reporting log lines/completion through a `queue.Queue`; `apiclient.py` when a daemon is reachable
  (`ServiceState.ping` set), `run_pair`/`undo_run` directly otherwise.
- `gui/app.py` — the `Tk` window: wires the three panels to `service_state.py`/`backend.py`, polls
  both on `root.after`, handles the close-window decision (offer to stop or hand over to the
  background service only when the daemon is running in this process).
- `gui/service_panel.py`, `gui/pairs_panel.py`, `gui/log_panel.py` — the three panels; thin, call
  into `gui/logic.py` for what to show.
- `gui/dialogs.py` — `FolderBrowserDialog` (local via `filedialog.askdirectory`, phone via
  `browse.py`'s `browse_remote`), `PairEditorDialog`, `QrDialog`.
- `assets/androsync.svg` (task 31c) — the desktop launcher icon (packaged via
  `pyproject.toml`'s `[tool.setuptools.package-data]`, read with `importlib.resources`): the same
  laptop+phone+arrows path geometry as the Android app's `ic_launcher_foreground.xml`.

## Laptop tests (`laptop/tests/`)
Mirrors the module list above 1:1 in most cases (`test_<module>.py`); notable exceptions and
integration-level suites:
- `test_e2e_local.py` — end-to-end scenarios (create/edit/delete/rename/conflict/ignore) through
  `run_pair` with two `LocalDirTransport`s, no mocking below that layer.
- `test_adb_device.py` — the only suite that talks to a **real** phone; skipped outright when
  `adb devices` shows nothing attached.
- `test_local_transport.py` / `test_adb_transport.py` — `LocalDirTransport`/`AdbTransport` unit
  tests (the latter with `subprocess.run` mocked - see its own docstring).
- `test_cli.py` — `status`/`run`/`serve`/`setup_logging` and general CLI plumbing;
  `test_cli_pairs.py`, `test_cli_conflicts.py`, `test_cli_history.py`, `test_cli_pair_wifi.py`,
  `test_cli_undo.py` — one per CLI subcommand group.
- `test_jobs.py`, `test_server.py`, `test_watcher.py` — daemon-side, `LocalDirTransport` standing in
  for the phone throughout.
- `test_service_state.py` — `detect_state()`'s four modes, pid file, ping, `start_background`/
  `stop_background`/`set_autostart`, all with `subprocess.run`/`urlopen` mocked.
- `test_gui_logic.py` — `gui/logic.py`'s pure helpers, no `tkinter` needed.
- `test_gui_smoke.py` — builds a real `Tk()` window (skipped without a display or without
  `tkinter`), proving the GUI at least constructs without raising.

## Android (`android/app/src/main/java/com/androsync/remote/`, Kotlin/Compose, minSdk 26)

- `AndroSyncApplication.kt`, `AppContainer.kt` — hand-rolled DI (no Hilt): one `AppContainer` per
  process; the `Application` also creates the sync notification channel.
- `ui/MainActivity.kt` — the single-Activity `NavHost` wiring every route to its screen.
- `ui/Routes.kt` — route string constants/builders (`setup`, `pairs`, `pair/new`, `pair/{name}`,
  `job/{id}`, `settings`).
- `ui/AppViewModelProvider.kt` — `rememberXxxViewModel` factories for every screen's ViewModel.
- `ui/ConnectionViewModel.kt` — shared, polls `/v1/ping` at the user-configured interval while the
  app is foregrounded; the single source of truth for the connection badge and the active
  `ApiClient` every other ViewModel reads from.

### `data/api/` — talking to the daemon
- `AndroSyncApi.kt` — the Retrofit interface, one function per HTTP route.
- `ApiClient.kt` — wraps `AndroSyncApi`, unwraps `Response<T>` into either the body or a thrown
  `ApiException` built from the daemon's `{"error": ...}` JSON.
- `Dto.kt` — every wire-format data class (`PairDto`/`PairRequest`, `JobDto`, `SummaryDto` (doubles
  as an undo run's summary shape too), `RunDto`, `ConflictVersionDto`, `BrowseDto`, ...).
- `ApiException.kt`, `AuthInterceptor.kt` — the exception type and the OkHttp interceptor that adds
  the bearer token to every request.
- `ConnectionProbe.kt` — tries USB/Tailscale/LAN presets in order, returns whichever responds.
- `LogStream.kt` — SSE client for `GET /v1/jobs/<id>/log`, used by both `JobViewModel` and (via
  `sseClient`) anything else that wants a live log.

### `data/` — everything else
- `ui/common/ActiveJobBar.kt` + `ActiveJobLogic.kt` — the "sync running" bar under every top bar (what, elapsed, last line, Open, Stop) from the ping's `current_job`.
- `ui/common/ActionLine.kt` — `parseActionLine` and `ActionLineItem`: splits an action line into the description and the laptop and phone paths.
- `ui/jobs/` — Recent syncs list (`JobsScreen`, `JobsViewModel`, `JobsLogic`).
- `PhonePath.kt` — Kotlin copy of `phone_path.py` (normalize and validate a phone folder), used by the pair editor.
- `SafPath.kt` — `treeUriToPath()`: turns a SAF tree URI (from the native folder picker) into an
  absolute `/sdcard/...` path when possible, else `null`.
- `SyncLauncher.kt` — the one place that starts a sync/undo job: resolves the reachable transport,
  `POST`s to the daemon, starts `SyncForegroundService`. Pairs screen, Quick Settings tile and
  widget all call this instead of talking to `ApiClient` directly.
- `settings/SettingsRepository.kt`, `settings/Preset.kt` — DataStore-backed URL/token/preset/poll
  interval storage.

### `service/`, `tile/`, `widget/` — background entry points
- `service/SyncForegroundService.kt` — keeps a job alive on screen-off: polls the job, updates a
  persistent "Syncing…" notification, posts a final done/failed notification.
- `service/SyncNotifications.kt` — pure notification-text builder (unit tested separately).
- `tile/SyncTileService.kt` — Quick Settings tile "AndroSync": syncs every pair, subtitle shows the
  reachable transport.
- `widget/SyncWidget.kt`/`SyncWidgetReceiver.kt` (Glance app widget), `SyncNowAction.kt`,
  `WidgetUpdater.kt` (`refreshWidgets`), `WidgetState.kt` (pure (de)serialisation, unit tested).

### `ui/common/` — shared composables and pure helpers
- `AndroSyncTopBar.kt`, `ConnectionBadge.kt` — the top bar every screen uses, and the USB/Tailscale/
  LAN/unreachable badge composable.
- `FolderBrowserSheet.kt` — modal bottom sheet browsing `/v1/browse` (local or remote), "New
  folder", "Choose this folder" - used by the pair editor for both folder pickers.
- `ConnectionState.kt`, `DirectionText.kt`, `ErrorMapping.kt`, `QrPayload.kt`, `RelativeTime.kt`,
  `LastResultText.kt` (task 32c: reworded/flags a `"FAILED: ..."` `last_result` for the red line on
  a pair's card) — pure logic each with its own `*Test.kt`.

### Agent mode (laptop side, task 37b)
- `transport/agent.py` — `AgentBroker` (queue between job threads and the polling phone app, "connected" bookkeeping) and `AgentTransport` (the `Transport` implementation on top of it). Wire format: `docs/agent-protocol.md`.
- `daemon/jobs.py` `JobRunner.open_transports` picks adb or agent per pair (`OpenedTransports.remote_for`).
- Android, `app/src/full/.../agent/`: `AgentOps` (file operations), `AgentClient` (HTTP), `AgentLoop`, `AgentService` (foreground service), `AgentState`; settings switch in `ui/settings/AgentSettingsSection.kt` (empty stub under `src/play`).
- `daemon/server.py` routes `/v1/agent/*`; `Daemon.agent_broker` is the shared mailbox. Tests: `tests/fake_agent.py`, `test_agent_transport.py`, `test_e2e_agent.py`.

### `ui/setup/`, `ui/settings/` — connecting to the laptop
- `setup/SetupScreen.kt` + `SetupViewModel.kt` — URL/token form, presets, QR scan.
- `setup/SetupChecklist.kt` — pure rules for the "Connect your phone" rows (`computeChecklist`, `deviceReadiness`); `ConnectPhoneScreen.kt` shows them, `SettingsPages.kt` opens the Settings pages and reads `ADB_ENABLED`.
- `setup/UsbHintLogic.kt`, `UsbStateReceiver.kt`, `SetupHintNotifier.kt` — "USB connected, debugging is off" notification (channel "Setup hints", switch in Settings).
- `settings/SettingsScreen.kt` + `SettingsViewModel.kt` — editable URLs/token, poll interval, test
  connection, forget setup, about.

### `ui/pairs/` — the main screen and pair management
- `PairsScreen.kt` + `PairsViewModel.kt` — pair list, pull-to-refresh, per-pair Preview/Sync,
  "Sync all" + whole-config "Preview" (task L4), the conflicts bottom sheet (task 29c), the runs/
  undo bottom sheet with a "Pause the pair afterwards" confirm (task 30c), the Enable switch for a
  paused pair.
- `PairEditorScreen.kt` + `PairEditorViewModel.kt` — Add/Edit pair form: name (rename included,
  task 29c), both folder pickers, direction, conflict policy, propagate-deletes, ignore patterns;
  carries `enabled` through unchanged even though the form has no control for it (task 30c).

### `ui/job/` — one sync or undo job
- `JobScreen.kt` + `JobViewModel.kt` — state chip, per-pair summary (each with its own inline
  "Undo" action, task 30c) or an undo job's own summary + "Undo this" (redo), auto-scrolling SSE
  log, cancel.
- `JobLogic.kt` — pure helpers backing the above (ring buffer, auto-scroll decision, chip mapping,
  summary formatting) - see `JobLogicTest.kt`.

### `ui/theme/`
- `Color.kt`, `Theme.kt`, `Type.kt` — Material3 theme setup.

## Android tests (`android/app/src/test/java/com/androsync/remote/`)
All MockWebServer/JUnit, no device or emulator needed (`./gradlew :app:testDebugUnitTest`):
`data/api/ApiClientTest.kt` (every endpoint, request bodies, DTO parsing), `data/api/
ConnectionProbeTest.kt`, `data/api/LogStreamTest.kt`, `data/SafPathTest.kt`, `service/
SyncNotificationsTest.kt`, `ui/common/*Test.kt` (one per pure helper), `ui/job/JobLogicTest.kt`,
`ui/pairs/PairEditorValidationTest.kt`, `widget/WidgetStateTest.kt`.
