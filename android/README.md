# AndroSync Remote (Android app)

A thin remote control for the `androsync` laptop daemon - it has no file access of its own, all
sync work happens on the laptop. See [the repo root README](../README.md) for what this project
is and how to get started, [`../PLAN.md`](../PLAN.md) for architecture and the task list, and
[`../CODE_INDEX.md`](../CODE_INDEX.md) for a one-line-per-file map of this directory.

## Build

Requires a JDK (the project declares a Java 17 toolchain in
`app/build.gradle.kts`; Gradle will auto-download a matching JDK via the
foojay resolver plugin if none is found locally) and the Android SDK
(`compileSdk`/`targetSdk` 35, `minSdk` 26).

```sh
cd android
./gradlew assembleDebug
```

Two builds come out of one code base (`distribution` flavors, task 37g):

| Build | APK | For |
|---|---|---|
| `play` | `app/build/outputs/apk/play/debug/app-play-debug.apk` | Google Play. A remote control for the laptop service, no file permission. |
| `full` | `app/build/outputs/apk/full/debug/app-full-debug.apk` | GitHub Releases and F-Droid. Same app plus the "All files access" permission for the on-phone agent that is planned (tasks 37a-37e); the agent itself is not built yet, so today both builds behave the same. |

Both have the same application id, so a phone has one or the other. APKs signed with different keys cannot update each
other: to switch, uninstall first (the app keeps only the laptop URL and token).

Run the unit tests (MockWebServer-backed tests for `ApiClient` and
`LogStream`, no device/emulator needed):

```sh
./gradlew :app:testDebugUnitTest
```

`local.properties` (not checked in) must point `sdk.dir` at your Android
SDK, e.g.:

```
sdk.dir=/home/<you>/Android/Sdk
```

## Install

With a device connected over USB (`adb devices` shows it) or already paired
over Wi-Fi:

```sh
adb install -r app/build/outputs/apk/play/debug/app-play-debug.apk
```

## Release build

`assembleRelease` works unsigned out of the box (useful for size/proguard
checks), but a real release APK needs a signing key:

```sh
keytool -genkey -v -keystore ~/androsync-release.jks -keyalg RSA -keysize 2048 \
  -validity 10000 -alias androsync
```

Then create `android/keystore.properties` (gitignored - never commit it)
pointing at that key:

```properties
storeFile=/home/<you>/androsync-release.jks
storePassword=<store password>
keyAlias=androsync
keyPassword=<key password>
```

`app/build.gradle.kts` reads this file at configuration time and wires it up
as the `release` build type's signing config only when the file exists, so a
checkout without it still builds (just unsigned):

```sh
./gradlew assembleRelease
```

The signed APK is written to `app/build/outputs/apk/release/app-release.apk`.

### Make a release (GitHub Actions)

Done once: create the signing key (above), then in the GitHub repository open Settings, Secrets and variables, Actions
and add four secrets: `KEYSTORE_B64` (the output of `base64 -w0 androsync-release.jks`), `KEYSTORE_PASSWORD`,
`KEY_ALIAS`, `KEY_PASSWORD`. Keep a copy of the key and passwords somewhere safe: without them updates can no
longer be signed.

For every release:

1. `python3 scripts/bump_version.py 0.2.0` sets the version in `laptop/pyproject.toml`, `laptop/androsync/__init__.py`
   and `app/build.gradle.kts` (versionCode goes up by one).
2. Add a `## [0.2.0] - <date>` section to `CHANGELOG.md`; it becomes the release notes.
3. Commit, then `git tag v0.2.0 && git push --tags`.
4. The **Release** workflow checks the tag against the three version numbers, builds the signed `play` and `full`
   APKs, the Python wheel and sdist, and creates the GitHub Release with those files. The Play bundle (`.aab`) is kept
   as a workflow artifact only.

To try the pipeline without releasing anything, run **Release** by hand from the Actions tab: it builds everything
(unsigned when the secrets are missing) and skips the release step.

## Quick Settings tile and home-screen widget

Two ways to start a sync without opening the app (PLAN.md task 21), both
going through the same `data/SyncLauncher` the Pairs screen uses (probe the
reachable transport, `POST /v1/run`, start the foreground sync service):

- **Quick Settings tile**: pull down the notification shade twice, tap the
  pencil/edit icon, and drag "AndroSync" into the active tiles. Tap it to
  sync every pair; its subtitle shows which transport is reachable ("USB",
  "Tailscale", "LAN", "Laptop not reachable") and briefly "Syncing…" while a
  job is running, or "Already running" if one already was.
- **Widget**: long-press the home screen, "Widgets", find "AndroSync
  Remote", and place it. It shows the most recently synced pair's result and
  how long ago that was (cached in DataStore, so it still shows something
  offline), refreshes every 30 minutes and whenever a sync finishes, and has
  a "Sync now" button; tapping anywhere else opens the app.

Both need Setup to be completed first (a saved URL + token) - with neither,
the tile shows "Not set up" and the widget shows "No syncs yet". Starting a
sync from either requests the `POST_NOTIFICATIONS` permission on Android 13+
the same way the Pairs screen does, so the foreground service's progress
notification can actually show; the sync itself still runs if that's denied.

## Connecting to the laptop daemon

The app needs the daemon's base URL and its bearer token. Get the token (and
a printable summary of every URL below) from the laptop with:

```sh
androsync qr
```

Three presets, matching `PLAN.md`'s "Network, Tailscale and firewall"
section (the app tries them in this order automatically - see
`ConnectionProbe`). Only USB has a fixed URL - "Remote" (Tailscale) and LAN
have no address built in (task 33e): both come from the scanned QR code, or
typed into Settings by hand.

| Preset    | URL                              | When it works                                                        |
|-----------|-----------------------------------|-------------------------------------------------------------------------|
| USB       | `http://127.0.0.1:8765`          | Phone plugged in via USB with `adb reverse tcp:8765 tcp:8765` active     |
| Remote    | from the QR code / Settings      | Phone has any internet connection and can reach this laptop's Tailscale IP |
| LAN       | from the QR code / Settings      | Phone and laptop are on the same local Wi-Fi network                    |

The daemon requires the bearer token on every request, including from
`127.0.0.1` - there is no unauthenticated route. Traffic is plain HTTP (see
PLAN.md for why that's acceptable on these transports); the app's network
security config explicitly allows cleartext traffic for that reason.

## Project layout

```
app/src/main/java/com/androsync/remote/
  AndroSyncApplication.kt, AppContainer.kt   hand-rolled DI (no Hilt): one AppContainer per
                   process; the Application also creates the sync notification channel
  data/api/        ApiClient, AndroSyncApi (Retrofit), DTOs (incl. RunDto/UndoRequest, task 30c),
                   LogStream (SSE), ConnectionProbe
  data/settings/   SettingsRepository (DataStore), Preset enum
  data/SafPath.kt        treeUriToPath(): a SAF tree URI -> an absolute /sdcard/... path
  data/SyncLauncher.kt   the one place that resolves a reachable ApiClient, POSTs `/v1/run`
                   and starts SyncForegroundService - Pairs screen, tile and widget all call it
  service/         SyncForegroundService (foreground notification while a job runs) +
                   SyncNotifications.kt (pure notification text builder)
  tile/            SyncTileService (Quick Settings tile "AndroSync")
  widget/          SyncWidget/SyncWidgetReceiver (Glance app widget), SyncNowAction,
                   WidgetUpdater (refreshWidgets), WidgetState (pure (de)serialisation)
  ui/              MainActivity (NavHost), Routes, ConnectionViewModel (shared, polls at the
                   user-configurable interval while foregrounded), AppViewModelProvider
                   (factories), theme
  ui/common/       ConnectionBadge/AndroSyncTopBar composables, FolderBrowserSheet (task 28b:
                   browses /v1/browse for either the pair editor's folder pickers) + pure
                   helpers: RelativeTime, DirectionText, QrPayload, ErrorMapping, ConnectionState
  ui/setup/        SetupScreen (URL/token form, USB/Tailscale/LAN presets, QR scan via
                   play-services-code-scanner, test connection) + SetupViewModel
  ui/pairs/        PairsScreen (list, preview bottom sheet incl. whole-config preview, sync/
                   sync-all, pull-to-refresh, per-pair overflow menu incl. "Undo last sync…",
                   Enable switch for a paused pair, 401/409/connection-error -> snackbar) +
                   PairsViewModel; PairEditorScreen (add/edit/rename a pair, both folder
                   pickers) + PairEditorViewModel
  ui/job/          JobScreen (state chip, per-pair summary each with its own inline Undo action,
                   auto-scrolling log, cancel) + JobViewModel (poll + reconnecting SSE stream +
                   undo()) + JobLogic.kt (pure helpers: ring buffer, auto-scroll decision, chip
                   mapping, summary/undo-summary formatting)
  ui/settings/     SettingsScreen (editable Tailscale/LAN URLs, masked token, poll interval,
                   test connection, forget setup, about) + SettingsViewModel
app/src/test/java/com/androsync/remote/
  data/api/ApiClientTest.kt, LogStreamTest.kt, ConnectionProbeTest.kt   MockWebServer tests
  data/SafPathTest.kt, ui/pairs/PairEditorValidationTest.kt   pure-logic unit tests
  ui/common/*Test.kt, ui/job/JobLogicTest.kt, service/SyncNotificationsTest.kt,
  widget/WidgetStateTest.kt   JUnit tests for the pure helpers listed above
```
