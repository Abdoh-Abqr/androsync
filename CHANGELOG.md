# Changelog

All notable changes to androsync are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- Agent mode: the battery button is always visible, Android's battery prompt appears when the agent is switched on, and the agent starts again after an app update or when the app opens.

### Added
- **Automatic sync**: a pair can sync by itself a few seconds after files change (`auto: true`). It never runs the first
  sync, never confirms a big change, pauses after three failures, and leaves no trace when nothing changed. See
  `docs/auto-sync.md`.
- **Run conditions** for automatic syncs (Wi-Fi only, charging only, a time window), a **speed limit** for agent file transfers
  (`service: bandwidth_kb_s`), and **Pause on mobile data** in the agent.
- **Report a problem**: `androsync support-bundle`, a window dialog and an app screen make a redacted log file; a GitHub
  issue form and `docs/support.md`.
- `androsync service new-token` and a New token button in the window; the config file is now readable by you only.

### Fixed
- A rare race where a failed job showed as finished before each pair's failure was saved.
- The desktop icon did not show: the launcher installer now refreshes the system icon cache, installs PNG sizes, and ties
  the window to the launcher (`StartupWMClass`); the window has its own icon.

## [0.2.0] - 2026-10-25

Safer, stoppable syncs; a guided phone setup; and sync without USB debugging.

### Added
- **Agent mode** (full app only): the phone app does the file work itself, so the laptop needs no USB or Wireless
  debugging. A pair picks `transport: adb` or `agent`; preview, undo, conflicts and the safety checks work the same.
  See `docs/agent-mode.md`. New pairs preselect what is available; the window can remember a default.
- **Two app builds**: `play` (no file permission, for Google Play) and `full` (agent mode, GitHub and F-Droid).
- **Connect your phone** screen in the app: a live checklist that opens Developer options and highlights the USB
  debugging switch, plus a notification when a cable is plugged in with debugging off.
- **Stop** a running sync from the app, the window or the API, and see a running sync on every screen and device.
  Recent syncs screen; both laptop and phone paths in every action line.
- **Safety**: phone folders that are not in shared storage are refused; changing a pair's folders resets its
  history; a sync that would delete or copy a lot waits for your OK ("Needs your OK" card and window prompt).
- **Several phones and several laptops**: every phone has its own history per pair (so a second phone can never make
  files look deleted), pairs can be bound to a phone, phones can be named (`androsync phone set`), and the app keeps a
  list of laptops, added by scanning each laptop's QR code. See `docs/several-devices.md`.
- The app can ask the laptop for a **new token** (Settings), and the laptop lets only **one phone's agent** work at a time.
- Syncthing guide (`docs/syncthing.md`), agent mode guide, install guide (`docs/install.md`), an About dialog and a
  donation link, repo links in the app, signed releases built by CI.

### Changed
- QR scanning uses ZXing instead of Google's ML Kit, so the app has no proprietary dependency (needed for F-Droid).
- A Tailscale address is recognised wherever it was saved and shown as Tailscale; a QR scan fills the right field.

### Fixed
- After a token reset the app kept using the old token until restarted.
- The app said the laptop was not reachable while "Test connection" worked (the saved address was ignored unless the
  LAN preset was chosen).

## [0.1.0] - 2026-10-14

First tagged release: everything built so far, as a single entry (task 35a) - see `NEXT_TASKS.md`
for the detailed, task-by-task history.

### Added

**Sync engine (laptop, Python)**
- Two-way, laptop-to-phone, and phone-to-laptop sync over ADB (USB or Wi-Fi/Tailscale), with
  gitignore-style ignore patterns and a configurable mtime tolerance.
- Content-hash conflict detection: a file that differs only in timestamp (common after an MTP
  copy) is recognized as already in sync rather than flagged as a conflict.
- Conflict handling: `newest-wins`/`local-wins`/`remote-wins`/`ask` policies; the losing version is
  saved to a per-run conflicts folder (`conflicts list|restore|clean`), never silently discarded.
- Undo: every sync (and undo) is journaled; `androsync undo <pair>` restores both sides to exactly
  how they were before the run, including an undo-of-an-undo (redo).
- Optional `versioning: git` per pair: commits the local side after a successful run.
- Batched transfers and case-collision detection for large/awkward folders (e.g. a DCIM backup).
- Device selection prefers whatever is actually connected (USB, then an already-connected Wi-Fi
  session, then a configured `wifi_target` as a last resort via `adb connect`); a USB connect also
  re-primes the phone's `:5555` listener so Wi-Fi/Tailscale syncing survives a phone reboot.
- Pairing over Wi-Fi: `androsync pair-wifi` walks through Android 11+ Wireless debugging pairing.

**CLI and daemon**
- `androsync` CLI: `devices`, `ls`, `status`, `run`, `history`, `runs`, `undo`, `add-pair`,
  `edit-pair`, `remove-pair`, `resolve`, `conflicts list|restore|clean`, `pair-wifi`, `qr`.
- HTTP daemon (`androsync serve`) with a bearer-token-authenticated API, SSE job log streaming, and
  pair CRUD/browse endpoints for the Android app and the desktop GUI.
- systemd user service with opt-in autostart (`androsync service install [--autostart]`,
  `service autostart on|off`, `service status`); refuses to double-start when something already
  answers on its port.
- Desktop app (`androsync gui`, Tk): service control (run here / start in background / stop /
  autostart), a pairs table (preview/sync/undo/add/edit/remove), and a live log. Installable as a
  normal Applications-menu entry (`service install-launcher`).
- Failures are visible everywhere: a failed job is logged (`daemon.log`) and recorded on the pair
  (`last_result: "FAILED: ..."`), shown in both the app and the desktop GUI.

**Android app ("AndroSync Remote")**
- Setup screen: URL/token entry, QR-code scanning, USB/Remote/LAN presets with automatic probing.
- Pairs screen: list with last-run status, per-pair sync/preview, "sync all", add/edit/remove a
  pair (with native and laptop-side folder pickers), pending-conflict and saved-conflict counts, a
  paused-pair indicator, "Undo last sync…".
- Job screen: live log (SSE) with auto-scroll, per-pair summaries, cancel, undo.
- Settings screen: editable connection URLs and token, poll interval, test connection, forget
  setup.
- Quick Settings tile and home-screen widget to start a sync without opening the app.
- Foreground service keeps a sync running with the screen off.

### Fixed
- A pair editor dialog shadowing an internal Tk attribute could leave the desktop GUI's window
  stuck open forever after closing it.
- `device: wifi:IP:PORT` always ran `adb connect`, ignoring a phone already attached over USB.
- A job that failed before touching any file (e.g. no device attached) was invisible everywhere
  except its own in-memory log.

### Security / Privacy
- No address of any particular machine or network is built into the source - every connection
  target is either detected live (`adb devices`, `tailscale status`) or provided by the user
  (typed, or via a scanned QR code). Guard tests on both sides fail the build if one leaks back in.
