# Keeping folders in sync with Syncthing (no USB debugging)

androsync needs USB debugging or Wireless debugging on the phone, or its own agent mode. If you cannot or do not want
to use either, **Syncthing** is a free, open-source way to keep some folders identical between your Linux computer and
the phone with no cable and no debugging. This guide sets it up for the three common cases: photos, a notes vault, and
an inbox.

Syncthing is a separate program from androsync. androsync does not control it. Use both on **different folders**:
never let two tools manage the **same** folder, they would fight over the same files.

## What you need

- A laptop running Ubuntu and a phone running Android.
- Both devices on the same Wi-Fi, **or** both on Tailscale (see "Away from home" below).
- About 20 minutes.

## 1. Install on the laptop

```
sudo apt install syncthing
systemctl --user enable --now syncthing
```

- If `apt` cannot find it, use the apt repository described at <https://syncthing.net/downloads/>.
- Open the web page **<http://localhost:8384>**. This is where you manage Syncthing on the laptop.
- To have it start at boot even before you log in: `sudo loginctl enable-linger $USER`.
- Set a GUI password when the page asks you to. It is only reachable from this laptop by default.

## 2. Install on the phone

- Install **Syncthing-Fork** from F-Droid (package `com.github.catfriend1.syncthingfork`).
- Why not the original app: the official Syncthing Android app was retired at the end of 2024 and left Google Play.
  Syncthing-Fork is the maintained one. Its project changed maintainers in late 2025, so look at the F-Droid page
  for the current version and date before you rely on it.
- Open it and allow **All files access** when it asks. Syncthing needs this to read and write your folders.
- Allow notifications, so you can see when it is running.

## 3. Pair the two devices

1. On the phone, open the app and show **this device's ID** (it also shows as a QR code).
2. On the laptop web page: **Add Remote Device**, then type or paste the phone's ID, give it a name, **Save**.
3. A moment later the phone asks whether to accept the laptop. Accept.
4. Both sides should now show the other as **Connected**.

Button names can differ a little between Syncthing versions. The idea is always the same: each device adds the
other by its ID, and each side accepts.

## 4. Share your folders

Pick the folder on one device, share it with the other device, and accept it on the other side. The **folder type**
decides who wins:

| Folder type | What it does |
|---|---|
| **Send & Receive** | Changes go both ways. This is the default. |
| **Send Only** | This device sends its changes and ignores everyone else's. |
| **Receive Only** | This device takes changes from the others and never sends its own. |

### Recipe A: photos backup (phone to laptop)

- Phone folder `DCIM` (and `Pictures`): **Send Only**.
- Laptop folder, for example `~/Pictures/Phone`: **Receive Only**.
- **Important:** if you delete a photo on the phone, Syncthing deletes it on the laptop too. To keep a safety net,
  turn on **File Versioning → Trash Can** for the laptop folder. Deleted or replaced files then wait in a
  `.stversions` folder inside it. Set the number of days to 0 to keep them forever.
- A **Receive Only** folder shows a red **Revert Local Changes** button if you change files on the laptop. Pressing it
  throws those changes away: files you edited go back to the phone's version and **files you added there are deleted**.

### Recipe B: a notes vault (both ways)

- Both sides: **Send & Receive**.
- Turn on **File Versioning → Staggered** on both sides. It keeps older versions: about one per 30 seconds for the
  first hour, one per hour for a day, one per day for 30 days, then one per week.
- Obsidian rewrites some workspace files all the time. Tell Syncthing to skip them. In the folder's **Ignore
  Patterns** (this is the `.stignore` file in the vault) put:

```
.obsidian/workspace*.json
.trash
```

- Edit a note on **one device at a time** and let it finish syncing before you open it on the other.

### Recipe C: an inbox (both ways)

- Both sides: **Send & Receive**, with **File Versioning → Trash Can** on the laptop.
- Drop files into the folder on either device, and they appear on the other.

## 5. Phone settings that save battery

- In Syncthing-Fork, open the settings and look for the **run conditions**. You can usually choose when it may
  run, for example only on Wi-Fi, or only while charging. The names change between versions, so read the labels.
- The F-Droid description also mentions that conditions can be set per device and per folder.
- Let Android keep the app running in the background (ignore battery optimization for it), or it will pause.

## 6. When two devices change the same file

- Syncthing keeps both. The older copy is renamed to `name.sync-conflict-DATE-TIME-DEVICE.ext`.
- That conflict copy **syncs to the other devices like any other file**, so you will see the extra file everywhere.
- You then compare the two files yourself and delete the one you do not want.
- Avoid it: edit on one device at a time, and use Staggered versioning on folders you care about.

## 7. Away from home (Tailscale)

By default Syncthing finds devices on the same Wi-Fi, and can fall back to public relay servers (a setting you can turn off). If both devices are
on Tailscale you can tell each one where to find the other:

- On the laptop web page: open the phone's entry → **Edit → Advanced → Addresses**, and enter
  `tcp://<phone-tailscale-ip>:22000`.
- On the phone: open the laptop's device entry and enter `tcp://<laptop-tailscale-ip>:22000`.
- Port **22000** is Syncthing's default. Syncthing uses the default port when you write the address without one.
- To limit who can reach the laptop, allow the port only on the Tailscale interface:

```
sudo ufw allow in on tailscale0 to any port 22000 proto tcp comment 'syncthing'
```

If you also want local discovery on your home Wi-Fi, it uses UDP port 21027 on the local network.

## 8. If something does not work

| Problem | Try this |
|---|---|
| The devices never show "Connected" | Check both are on the same Wi-Fi or both on Tailscale, and that the laptop firewall allows port 22000. |
| The phone stops syncing in the background | Allow background activity and ignore battery optimization for the app. |
| "Out of Sync" on a Receive Only folder | Someone edited files there. Use **Revert Local Changes** if you want the sender's version. |
| Photos you deleted are still in the laptop folder | Check `.stversions` inside the folder: that is the Trash Can copy. |
| Extra files named `sync-conflict` | Section 6. |

## Back to androsync

See the main [README](../README.md) for the USB route, which adds preview, undo and a Stop button.
