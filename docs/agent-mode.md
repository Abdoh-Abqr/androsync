# Sync without USB debugging: agent mode

Agent mode lets the AndroSync app on the phone do the file work itself, so the laptop does not need USB debugging or
Wireless debugging. It is in the **`full`** version of the app only (see "Which version?" below).

**Status:** works. It was tried on a real phone (Android 11) syncing a folder pair over Tailscale with the
laptop's window, and the pair came back in sync. Not tried yet: Wi-Fi without Tailscale, USB tethering, an SD card.

## Which way should I sync?

| | Syncthing | ADB (USB or Wi-Fi debugging) | Agent mode |
|---|---|---|---|
| USB or Wireless debugging | not needed | needed | **not needed** |
| Cable | no | USB, or Wi-Fi debugging | **no** |
| Runs by itself all day | **yes** | only while plugged in or reachable | only while the agent is switched on |
| Preview before a sync | no | yes | **yes** |
| Undo a whole sync | no | yes | **yes** |
| Stop button and live log | no | yes | **yes** |
| Conflict loser kept in a hidden folder | no (extra files) | yes | **yes** |
| Needs the laptop to be on | no | yes | yes |
| Needs "All files access" on the phone | yes | no | yes |
| Where to get the app | F-Droid | F-Droid, GitHub, Google Play | F-Droid, GitHub (not Google Play) |

Pick **ADB** when a cable is fine: it is the most tested path and needs no extra permission. Pick **agent mode** when
you do not want debugging switched on but still want preview, undo and a Stop button. Pick **Syncthing** when you want
folders kept in sync all day with the laptop off. See [syncthing.md](syncthing.md).

You can mix them: each pair chooses `adb` or `agent`. A job that only has agent pairs needs no phone attached.

## Which version of the app?

- **`play`**: the remote control for the laptop service. No file permission. Can go to Google Play.
- **`full`**: the same plus agent mode. It asks for **All files access** and runs a background service.

Both have the same package name, so a phone has one or the other. They are signed with different keys when they come
from different stores, so switching means uninstalling first (the app only keeps the laptop address and token).

## Why not on Google Play?

Agent mode needs two things Google Play restricts:

1. **All files access** (`MANAGE_EXTERNAL_STORAGE`). Android gives an app this to read and write any folder, which a
   sync tool must do. Google Play allows it only for a short list of core purposes and reviews each app. Syncthing's own
   Android app was removed from Play for the same reason.
2. A foreground service of the **`specialUse`** type, chosen so Android does not stop the agent after six hours (the
   `dataSync` type is limited to six hours a day on Android 15).

The `play` build leaves both out, so it can be published there. The agent code is not in it at all.

## What the permission lets the app do

"All files access" lets the app read and write files in shared storage, the same as a file manager. It does **not**
give access to other apps' private data. The agent additionally refuses, by its own rule, any folder outside
shared storage (`/sdcard/...` or an SD card) and `/sdcard/Android/data` and `obb`. It only does what the laptop's
service asks for, and the laptop only talks to an app that knows the token from `androsync qr`. No data goes to any
server: the agent connects to your own laptop.

## Set it up (about 10 minutes)

Needs [Tailscale](https://tailscale.com) on the laptop and the phone, both signed in. The laptop service listens only
on `127.0.0.1` and its Tailscale address, and the app reaches it through the Tailscale address.

1. Install the **`full`** app and connect it to the laptop: run `androsync qr` and scan the code in the app's Setup
   screen. The scan saves the laptop's Tailscale address in the **Tailscale URL** field (Settings).
2. In the app: **Settings → Sync without USB debugging**.
   1. Tap **Allow access to all files** and switch it on for AndroSync.
   2. Switch on **Run the agent**. A notification "AndroSync agent" appears and the status says
      "Connected, waiting for the laptop".
   3. Tap **Battery** and let the app run in the background, or Android may stop it.
3. In the desktop window (or `androsync edit-pair <name> --transport agent`), set the pair's **Reach the phone via**
   to `agent`. For a new pair use `androsync add-pair ... --transport agent`.
4. Press **Sync** in the window or in the app. Preview and Undo work as usual.

## Speed limit and pause on mobile data

`service: bandwidth_kb_s: 300` in the laptop's `config.yaml` slows the files moved through the app to about 300 KB/s (0 = no limit;
adb transfers cannot be limited). In the app, **Pause on mobile data** (Settings, agent mode) stops the agent while the phone uses
mobile data. The agent also tells the laptop whether the phone is on Wi-Fi and charging, which [automatic sync](auto-sync.md) can use.

## Limits

- The agent runs only while the switch is on. It does not start again by itself after the phone restarts: open the
  app and check the switch.
- The laptop must be on and the phone must reach it (Tailscale connected on both).
- The command line cannot run agent pairs, because only the running service talks to the app. Use the window or the app.
- One agent at a time (one phone) per laptop.
- If the app shows "Looking for the laptop", the Tailscale URL in Settings is empty or Tailscale is off.
