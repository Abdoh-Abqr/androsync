# androsync (laptop side)

The Python package: sync engine, CLI, HTTP daemon, desktop app. See [the repo root
README](../README.md) for what this project is and how to get started; [`../PLAN.md`](../PLAN.md)
for architecture, config format and the task list.

## Usage

1. **Enable USB debugging** on the phone: Settings → About phone → tap "Build number" 7 times
   to unlock Developer options, then Settings → Developer options → enable "USB debugging".
2. **Plug in the phone** and authorize this computer: run

   ```
   adb devices
   ```

   The first time, the phone shows an "Allow USB debugging?" prompt - accept it (optionally
   checking "Always allow from this computer"). The device should then show as `device` (not
   `unauthorized` or `offline`).
3. **Write a config** at `~/.config/androsync/config.yaml` (or pass `--config PATH` to every
   command below). Start from [`config.example.yaml`](config.example.yaml) - set `local`/`remote`
   for each pair and leave `device: auto` if only one phone is ever plugged in at a time.
4. **Look around the phone** to find the right remote paths:

   ```
   androsync devices          # confirms the phone is seen and which one `auto` would pick
   androsync ls /sdcard       # browse a directory on the phone
   ```
5. **Preview a sync** (never touches any files):

   ```
   androsync status                # all pairs
   androsync status vault          # just the "vault" pair
   ```
6. **Run it for real**:

   ```
   androsync run --yes             # sync all pairs without asking to confirm
   androsync run vault             # sync one pair, with a y/N confirmation prompt
   androsync run --dry-run         # same preview as `status`, via the `run` command
   ```

   Add `--verbose` to any command to also print the underlying `adb` commands as they run.
   Exit codes: `0` = synced cleanly (or nothing to do), `1` = a pair reported a conflict/`ask` or
   an error partway through, `2` = a config or device problem (bad YAML, no/unauthorized device).

   Every `androsync run`/`androsync undo` (and every job the background service runs) also writes
   its log lines to `~/.local/state/androsync/last-run.log`, truncated at the start of each one -
   so the full log of the *last* run/job is always there to check, `--verbose` and all, even after
   the terminal that ran it (or the SSE stream to the phone app) is long gone.
7. **Run it as a background service** instead of the CLI, so a sync can also be triggered from the
   phone app - see [Run as a service](#run-as-a-service) below:

   ```
   androsync service install       # writes the config/systemd unit; starts nothing by default
   androsync service start         # start it now, in the background (no autostart)
   androsync serve                 # or: run it in the foreground yourself (what the service runs)
   androsync qr                    # print the token + URLs (as a QR code) for the phone app
   ```

Full command list: `devices`, `ls [PATH]`, `status [PAIR...]`, `run [PAIR...]`, `serve`,
`service install|start|stop|restart|status|logs|uninstall`, `qr`, `add-pair`, `edit-pair`,
`remove-pair`, `resolve`, `history`, `pair-wifi`, `conflicts list|restore|clean`, `runs`, `undo`.

## Run as a service

`androsync serve` is the foreground daemon: it watches for the phone over `adb track-devices`,
runs the HTTP API the phone app talks to, and (if `service.auto_sync_on_connect: true`) starts a
sync whenever the phone is plugged in.

`androsync service install` writes the systemd unit with the absolute path of whichever
`androsync` you ran it with (`sys.argv[0]`, resolved) - so if that came from a venv you later
move or delete (e.g. `.venv/bin/androsync` inside a dev checkout), the service breaks until
`service install` is run again with a working `androsync`. For a stable install that survives
moving the repo around, `pipx install ./laptop` first - it puts a symlink at `~/.local/bin/androsync`
that pipx keeps pointed at its own managed venv regardless of where the repo lives, and that is
the path systemd will pick up. Requires `pipx` (`sudo apt install pipx` on Debian/Ubuntu, or
`pip install --user pipx`) and `~/.local/bin` on `PATH` (`pipx ensurepath`, then open a new shell).

`androsync service install` only writes the systemd unit file and the config - it does **not**
start the service or enable autostart on its own, so installing it is never a surprise (measured
2026-09-13: idle it is 17 MB RSS, 0.2 % CPU, cheap to run, but the choice is yours):

```
androsync service install              # writes ~/.config/systemd/user/androsync.service; starts nothing
androsync service install --autostart  # also enables + starts it now, and runs
                                        # `loginctl enable-linger $USER` so it starts at boot too
androsync service status               # prints whether/how it's running: as the background service
                                        # (with autostart on/off), in another process, or not at all
androsync service start                # start it now, in the background, without enabling autostart
androsync service stop                 # stop it now
androsync service autostart on|off     # flip "start at boot" without changing whether it's running now
androsync service uninstall            # = systemctl --user disable --now androsync; removes the unit file
androsync service logs                 # = journalctl --user -u androsync -f
```

Switch off autostart but keep it installed: `androsync service autostart off`.
Pause auto-sync only: set `service.auto_sync_on_connect: false`, then `androsync service restart`.

`service install` also generates `service.token` (a random bearer token, written into
`config.yaml`) the first time it runs, and keeps it on later runs - the phone app needs this
token plus a URL (see `androsync qr`) to talk to the daemon. Add `--dry-run` to print every file
it would write and every command it would run without doing any of it.

`androsync serve` (whichever way it is started - the systemd unit, a plain terminal, or task
31b's GUI "Run here") refuses to start a second time if something is already answering
`GET /v1/ping` on its port, and always writes its log to
`$XDG_STATE_HOME/androsync/daemon.log` (rotated at 2 MB × 3) in addition to wherever its stdout
goes - so a daemon started any of those three ways can be followed the same way.

## Desktop app

`androsync gui` opens a window with three parts:

- **Service**: status ("Not running" / "Running here (this window)" / "Running in background,
  autostart on|off" / "Running in another process (pid N)"), with buttons for whatever that state
  allows - **Run here** (the daemon runs inside the window's own process; closing the window then
  offers to stop it or hand it over to the background service instead), **Start in background**
  (`systemctl --user start`, no autostart), **Stop**, a **Start at boot** checkbox, and **Show QR**
  (the token + URLs for the phone app, same content as `androsync qr`).
- **Pairs**: a table of configured pairs with **Preview**/**Sync**/**Sync all**/**Undo last…**/
  **Add…**/**Edit…**/**Remove…**. Actions go through the daemon's own HTTP API when one is
  reachable (so a sync started from here shows up in the phone app too) and directly through the
  sync engine otherwise - either way, on a background thread, so the window never freezes.
- **Log**: the output of whatever the Pairs panel last did.

Add an "AndroSync" entry to the Applications menu (and remove it again):

```
androsync service install-launcher
androsync service uninstall-launcher
```

Requires `python3-tk` (`sudo apt install python3-tk` on Debian/Ubuntu) - `androsync gui` prints
that hint itself and exits if tkinter is not installed, rather than a raw traceback.

## Conflicts

A real conflict (a file changed on both sides with different content since the last sync, not
just a timestamp drift - see `mtime_tolerance_s` and the content-hash check that skips a false
conflict when the bytes are actually identical) picks a winner per the pair's `conflict` policy
and, unless `keep_conflict_copies: false`, saves the losing version so nothing is silently lost:

- `conflict_copies: folder` (the default): the loser's bytes are fetched straight to
  `<local>/.androsync/conflicts/<YYYYMMDD-HHMMSS>/<rel>` on the laptop only - never renamed in
  place, never pushed to the phone. `.androsync/**` is always excluded from sync, on every pair,
  even if it is not listed under `ignore:`.
- `conflict_copies: beside` (the pre-existing behaviour): the loser is renamed in place with a
  `.conflict-<laptop|phone>-<timestamp>` suffix and pushed to both sides, so it shows up as an
  extra file next to the original on both the laptop and the phone.

Manage saved `folder`-mode backups:

```
androsync conflicts list vault                    # every saved backup for this pair, newest first
androsync conflicts restore vault notes/todo.md    # copies the most recent saved version back over
                                                    # the live local file; run a sync afterwards to
                                                    # propagate it to the phone
androsync conflicts restore vault notes/todo.md --when 20260905-153000   # a specific version
```

If a pair used `conflict_copies: beside` before switching to `folder` (or has simply been run for
a while with the old default), clean up the leftover `*.conflict-<side>-<timestamp>.*` files:

```
androsync conflicts clean vault          # dry run: lists what would happen, changes nothing
androsync conflicts clean vault --yes    # applies it
```

Each duplicate found is hashed against the file it is a copy of: identical content is just
deleted (both sides); different content is archived into the `folder`-mode conflicts directory on
the laptop first, then deleted from both sides - so nothing is lost either way.

## Undo a sync

Every non-dry-run sync journals what it did (`$XDG_DATA_HOME/androsync/undo/<pair>/<run_id>/`,
never inside the synced folders), so a run that went wrong - the wrong conflict decision, a folder
pair pointed at the wrong place - can be put back exactly how it was:

```
androsync runs vault              # every run for this pair, newest first, with its summary
androsync undo vault               # undoes the most recent run
androsync undo vault --run 20260907-153000-1a2b   # undoes a specific one instead
androsync undo vault --dry-run     # preview: what would be restored, changes nothing
```

A file someone (or another sync) touched *after* the run being undone is left alone and reported
- undoing blindly over a newer edit would just lose that edit - unless `--force` says to restore
it anyway. The undo is itself journaled, so undoing an undo redoes the original run.

If the run keeps repeating (e.g. a pair whose local/remote paths are simply wrong), pause the pair
so nothing syncs it again until you have fixed the underlying problem:

```
androsync undo vault --pause                    # undo, then set enabled: false for the pair
androsync edit-pair vault --enabled              # re-enable it once ready
```

A paused pair is skipped by `androsync status`/`run` with no pair named explicitly (and by
auto-sync) - `androsync run vault` still runs it, since typing the name is itself an explicit
override.

Retention: the last `undo.keep_runs` runs (default 10) and `undo.max_total_mb` (default 2048) of
backups are kept per pair, oldest pruned first; a file larger than `undo.max_file_mb` (default 500)
is not backed up at all, and `undo` reports it as unrestorable rather than pretending otherwise.
Set any of these under a pair's own `undo:` block in the config to override the default, or
`undo: {enabled: false}` to turn off journaling for a pair entirely.

## Safety: big changes wait for your OK

Two things protect a pair from a mistake that would touch many files:

- **Changing a pair's laptop or phone folder resets its history** (`edit-pair`, the window and the app all do this).
  The next sync then only adds files and deletes nothing. The old history is kept 30 days in
  `~/.local/share/androsync/state/<pair>.reset-<time>/`. Renaming a pair keeps its history.
- **A plan that is too big is stopped before anything changes** and has to be confirmed: deleting more than 50 files on
  one side *and* more than 20 percent of the files the pair knows, or a first sync that would copy more than 5000 files
  or 5 GB. `androsync status` shows the warning; `androsync run` asks you to type `yes` (`--yes` alone is not enough,
  use `--confirm-large`); the app and the window show a "Go ahead" button.

Change the limits per pair or in `defaults:`; `0` switches a check off:

```yaml
defaults:
  safety:
    max_delete_count: 50       # deletions on one side above this ...
    max_delete_percent: 20     # ... and above this share of the known files need confirmation
    big_first_run_files: 5000
    big_first_run_gb: 5
  transfer:
    batch_files: 50            # files per batched copy; smaller = more progress lines and a quicker Stop
    batch_mb: 64
```

## Troubleshooting

**"Phone folders must be inside internal storage..."** - the phone folder of a pair must be under `/sdcard/...` (or
`/storage/XXXX-XXXX/...` for an SD card). `/Internal storage/Docs` is a label from file managers, not a real path; it is
corrected to `/sdcard/Docs` when you save a pair. `/`, `/data` and `/system` are refused on purpose.

**`unauthorized`** - the phone has not accepted this computer's ADB key yet. Check the phone
screen for an "Allow USB debugging?" prompt (it can be hidden behind other windows) and accept it,
optionally checking "Always allow from this computer" so it does not come back every reboot.

**`more than one device found`** - `device: auto` picks a single USB device over a single
already-connected Wi-Fi one automatically, but two of the same kind at once (two phones on USB, or
USB plus a stale `wifi_target` session that is *also* still connected) is still ambiguous. Set
`device:` in the config to one specific serial instead of `auto` - `androsync devices` lists what
is currently visible and marks which one `auto` would pick (and why).

**`find: bad -printf %y`** (or similar) - some Android builds' `toybox find` do not support the
`%y` file-type specifier `find -printf` normally uses; `androsync` already falls back to a slower
`find -exec stat` scan automatically (see `transport/adb.py`) the first time it hits this, so this
message on its own is not a bug - it only matters if scans stay unexpectedly slow, which is that
fallback path.

**Daemon starts before Tailscale is up** - `service.listen` entries other than `127.0.0.1` (e.g. a
Tailscale IP) can be unbindable for a few seconds right after boot, before `tailscaled` has
brought the interface up. `androsync serve` already retries binding any address that failed at
startup for up to 30 s (every 2 s) before giving up on it - check `androsync service logs` for
"could not bind" followed later by a successful bind; if it never succeeds, confirm `tailscale
status` shows the interface up and that `service.listen` has the right IP (`tailscale ip -4`).

## Syncing over Wi-Fi/Tailscale (no cable)

`androsync pair-wifi` walks through Android 11+'s Wireless debugging pairing (Developer options →
Wireless debugging → "Pair device with pairing code") and writes `device: auto` plus a
`wifi_target: <ip>:5555` into the config - not `device: wifi:<ip>:5555` directly, because `device:
auto` always prefers an actually-connected device (USB, or an already-connected Wi-Fi session)
over `wifi_target` and only falls back to it (running `adb connect`) when nothing else is found.
That matters because **Android's Wireless debugging port is random and does not survive a
reboot** - plugging the phone into USB at all after that resets which port it is listening on, so
a sync that insists on the exact port `wifi_target` names would fail outright until re-paired by
hand. `device: auto` sidesteps that: a sync with the phone on the cable just uses the cable.

To keep `wifi_target` itself usable after a reboot without re-running `pair-wifi`, `androsync`
also runs `adb -s <serial> tcpip 5555` every time the phone connects over USB (`service.
enable_tcpip_on_usb: true`, the default) - but only when `wifi_target` ends in `:5555`, since
that is the one port this specific command fixes; if `pair-wifi` wrote a different (randomly
assigned) port, this step is skipped and re-pairing by hand is what fixes it. Plugging in over USB
at least once after each phone reboot is enough to keep Wi-Fi/Tailscale syncing working after
that; set `enable_tcpip_on_usb: false` to disable this entirely.

`androsync devices` always shows which device would currently be picked and why.

## Remote access over Tailscale

Tailscale is the recommended way to reach the daemon from outside your home network - it needs no
port forwarding and the traffic stays end-to-end encrypted - but it is not required: `service.
listen` and `wifi_target` both accept any address that can actually reach this laptop (a plain LAN
IP, a VPN of your own, etc.). Nothing about any particular network or device is built into
androsync; every address it uses is either detected live (`tailscale ip -4`, `adb devices`) or
typed into your own config.

By default the daemon only listens on `127.0.0.1:8765` - reachable over USB via
`adb reverse tcp:8765 tcp:8765` (which `androsync serve` sets up automatically once the phone is
seen), never over the network. `service.listen` accepts a list of addresses; `androsync service
install --tailscale` detects the machine's Tailscale IP (`tailscale ip -4`) and adds it, so the
phone app can also reach the daemon from anywhere the phone has internet, tunnelled through
Tailscale - the traffic never touches the open internet, and the bearer token is still required.
**Never add `0.0.0.0`** - `service install` refuses it outright.

Firewall: with a Tailscale address in `service.listen`, `service install` prints (and with
`--ufw` runs via `sudo`):

```
sudo ufw allow in on tailscale0 to any port 8765 proto tcp comment 'androsync'
```

`service uninstall --ufw` removes that rule again. The USB path needs no firewall rule at all -
`adb reverse` tunnels over the cable, nothing listens on the network for it. `androsync serve`
also retries binding the Tailscale address for up to 30s at startup, in case the tailnet is not
up yet right when the service starts (e.g. straight after boot).

Phone app presets (see `androsync qr` for the exact values for this machine):

- **USB** (cable plugged in): `http://127.0.0.1:8765`
- **Tailscale** (anywhere the phone has internet): `http://<this-laptop's-tailscale-ip>:8765`
- **LAN** (same Wi-Fi network): `http://<lan-ip>:8765`
