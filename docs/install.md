# Install the laptop program

The laptop program (`androsync`) runs the syncs; the phone app controls it and, in agent mode, does the file work on the
phone. This page installs the laptop part. For the phone, see the main [README](../README.md).

| System | Status |
|---|---|
| **Linux (Ubuntu)** | Supported and tested. Everything below works. |
| **macOS** | **Not supported yet.** Not tried. See "macOS and Windows" below. |
| **Windows** | **Not supported yet.** Not tried. See "macOS and Windows" below. |

## Linux (Ubuntu)

You need Python 3.10 or newer, `adb`, and `pipx`.

1. Install the tools: `sudo apt install adb pipx python3-tk`, then `pipx ensurepath` and open a new terminal.
   (`python3-tk` is for the window; skip it if you only use the command line.)
2. Download the newest `androsync-<version>-py3-none-any.whl` from the
   [Releases page](https://github.com/Abdoh-Abqr/androsync/releases).
3. Install it: `pipx install ./androsync-<version>-py3-none-any.whl`
   (add `"androsync[qr]"` extras if you want the QR code printed in the terminal).
4. Create the settings, a token and the background service file: `androsync service install`
5. Add the launcher so it appears in your app grid: `androsync service install-launcher`
6. Open **AndroSync** from the app grid and press **Run here**. The status line should say "Running here".
7. Show the pairing code for the phone app: **Show QR** in the window, or `androsync qr`.

Check that it works: `androsync devices` lists your phone once USB debugging is on (see the README), and
`androsync --version` prints the version.

**From the source code instead** (to change it): clone the repository, then `pipx install ./laptop`.

**Start at boot** is off by default. Switch it on with the **Start at boot** box in the window, or
`androsync service autostart on`. Away from home, install [Tailscale](https://tailscale.com) on the laptop and the phone;
`androsync service install` offers to listen on the Tailscale address and `--ufw` adds the firewall rule.

**Update:** download the newer wheel and run `pipx install --force ./androsync-<new version>-py3-none-any.whl`.
**Uninstall:** `androsync service uninstall`, then `pipx uninstall androsync`.

## macOS and Windows

The laptop program is not ready for these systems yet, so there is no install guide that I can honestly call tested.
What is known (tracked in [issue 29](https://github.com/Abdoh-Abqr/androsync/issues/29)):

- The sync engine, the command line and the window are plain Python and `adb`, so they may run. Nothing was tried on
  a Mac or a Windows PC.
- These parts are Linux only and need to be written for each system: the background service (systemd today, launchd
  on macOS, Task Scheduler or a tray app on Windows), "start at boot", the runtime folder (`/run/user/<uid>`), and
  desktop notifications (`notify-send`).
- Windows has one more gap: the code reads the user id (`os.getuid`) that Windows does not have.

If you want to help or try it, the tickets and tests are in the repository. Until then, use a Linux laptop or a
Linux virtual machine, or use [Syncthing](syncthing.md), which runs on all three systems and needs no AndroSync program
on the computer.
