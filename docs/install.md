# Install AndroSync on your Linux computer

The AndroSync program (`androsync`) runs on a **Linux computer**: a laptop, a desktop PC or a server. It runs the syncs;
the phone app controls it and, in agent mode, does the file work on the phone. This page installs the computer part.
For the phone, see the main [README](../README.md).

| System | Status |
|---|---|
| **Linux** (Ubuntu, Debian, Fedora, Arch; any Linux with systemd) | Supported. Tested on Ubuntu. Everything below works. |
| **macOS** | **Not supported yet.** Not tried. See "macOS and Windows" below. |
| **Windows** | **Not supported yet.** Not tried. See "macOS and Windows" below. |

## Linux

You need systemd, Python 3.10 or newer, `adb`, and `pipx`.

1. Install the tools, then run `pipx ensurepath` and open a new terminal.
   - Ubuntu or Debian: `sudo apt install adb pipx python3-tk`
   - Fedora: `sudo dnf install android-tools pipx python3-tkinter`
   - Arch: `sudo pacman -S android-tools python-pipx tk`

   (`python3-tk` is for the desktop window; skip it on a server or if you only use the command line.)
2. Download the newest `androsync-<version>-py3-none-any.whl` from the
   [Releases page](https://github.com/Abdoh-Abqr/androsync/releases).
3. Install it: `pipx install ./androsync-<version>-py3-none-any.whl`
   (add `"androsync[qr]"` extras if you want the QR code printed in the terminal).
4. Create the settings, a token and the background service file: `androsync service install`
5. On a computer with a screen, add the launcher so it appears in your app grid: `androsync service install-launcher`
6. Open **AndroSync** from the app grid and press **Run here**. The status line should say "Running here".
   (A server without a screen has no window: run `androsync service install --autostart` in step 4 instead, and skip 5 and 6.)
7. Show the pairing code for the phone app: **Show QR** in the window, or `androsync qr`.

Check that it works: `androsync devices` lists your phone once USB debugging is on (see the README), and
`androsync --version` prints the version.

**From the source code instead** (to change it): clone the repository, then `pipx install ./laptop`.

## Install the phone app from the computer

The release wheel carries the signed `full` APK, so the computer can put the app on a phone:

- `androsync app status` lists each phone adb sees with the app version it has (or "not installed").
- `androsync app install` installs or updates it on every ready phone (`--phone <serial>` for one, `--apk FILE` for
  your own file). If the phone has the app from another store, Android refuses the update: uninstall it first.
- No cable, no adb? `androsync app share` (or Help, "Install the app on a phone..." in the window) makes a one-time link
  and a QR code. The link works once, for 10 minutes, and only on the addresses the service listens on (Tailscale, or the
  local network if you added it). Open it on the phone, allow installing from that source, then scan the pairing QR.

A source checkout does not bundle the APK; download it from the Releases page and use `--apk`.

**Start at boot** is off by default. Switch it on with the **Start at boot** box in the window, or
`androsync service autostart on`. Away from home, install [Tailscale](https://tailscale.com) on the computer and the phone;
`androsync service install` offers to listen on the Tailscale address and `--ufw` adds the firewall rule.

**Update:** download the newer wheel and run `pipx install --force ./androsync-<new version>-py3-none-any.whl`.
**Uninstall:** `androsync service uninstall`, then `pipx uninstall androsync`.

## macOS and Windows

The AndroSync program is not ready for these systems yet, so there is no install guide that I can honestly call tested.
What is known (tracked in [issue 8](https://github.com/Abdoh-Abqr/androsync/issues/8)):

- The sync engine, the command line and the window are plain Python and `adb`, so they may run. Nothing was tried on
  a Mac or a Windows PC.
- These parts are Linux only and need to be written for each system: the background service (systemd today, launchd
  on macOS, Task Scheduler or a tray app on Windows), "start at boot", the runtime folder (`/run/user/<uid>`), and
  desktop notifications (`notify-send`).
- Windows has one more gap: the code reads the user id (`os.getuid`) that Windows does not have.

If you want to help or try it, the tickets and tests are in the repository. Until then, use a Linux computer or a
Linux virtual machine.
