# Automatic sync

A pair with automatic sync on syncs by itself a few seconds after files change. It uses the same engine as a manual sync, so
preview-like safety, the undo journal and the Stop button all work. It only needs the laptop service to be running.

## Turn it on

- **App:** edit the pair, switch on **Sync automatically**.
- **Window:** edit the pair, tick **Sync automatically when files change**. The **Auto** column shows `on`, `waiting`
  or `paused`.
- **Terminal:** `androsync add-pair ... --auto`, or `androsync edit-pair <name> --auto` (`--no-auto` turns it off).

Optional settings in `config.yaml` under the pair: `auto_delay_s` (seconds to wait after the last change, default 10) and
`auto_interval_s` (how often a phone that cannot report changes is checked, default 300, at least 60).

## What it will and will not do

- **The first sync of a pair is never automatic.** Sync it once by hand, look at the result, then switch automation on.
- **It never confirms a big change.** A plan that deletes or copies a lot (see the "Needs your OK" card) pauses that pair's
  automation and shows a notification. Look at the list, press **Go ahead** or **Not now**; a manual sync that works
  resumes automation.
- **Three failed automatic syncs in a row pause the pair**, with the reason in the Auto column and the app.
- **A phone that is not connected is waited for.** It is not counted as a failure.
- **At most one automatic sync per pair per minute**, and one sync at a time on the laptop.
- **Stop works** on an automatic sync; it pauses that pair until you sync it by hand.
- **Quiet checks leave no trace.** An automatic check that finds nothing to do is not listed under Recent syncs and adds no
  undo entry.
- **Undo works** on an automatic sync that changed something, like any other.

## How changes are noticed

- **Laptop folder:** with `watchdog` installed (`pipx inject androsync watchdog`, or `pip install 'androsync[watch]'`) changes
  are noticed at once; without it the folder is looked at every 30 seconds.
- **Phone:** adb cannot report changes, so every `auto_interval_s` seconds the pair is checked. In agent mode the phone app
  can also tell the laptop about changes (`POST /v1/agent/event`, see [agent-protocol.md](agent-protocol.md)).

## When it does not seem to work

1. Is the service running (the window says "Running")?
2. Has the pair been synced by hand once? Is it enabled (not paused)?
3. Is the phone connected (USB, or the agent switched on)? Automatic sync waits for it.
4. Look at the pair's **Auto** column (`paused` shows why) and `androsync support-bundle` if you need to report it.
