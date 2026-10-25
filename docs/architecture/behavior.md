# Behavior

Decisions, step-by-step flows and states. **As built** unless a heading says **target**. Back to the
[architecture index](README.md).

## 1. What the planner decides for one file

`planner.plan` looks at every path found on either side or in the last snapshot. Hashing is asked for only when both
sides changed, the sizes are equal and the times differ by more than `mtime_tolerance_s`.

```mermaid
flowchart TD
    A["Path seen on the laptop, the phone or in the last snapshot"] --> B{"Two names differ only by case?"}
    B -- yes --> W["Warning, no action"]
    B -- no --> C["Classify each side against the snapshot:<br/>unchanged, changed or deleted"]
    C --> D{"Local and remote"}
    D -- "changed and changed" --> E{"Equal within the time tolerance?"}
    E -- yes --> Z["No action"]
    E -- no --> F{"Both hashes known and equal?"}
    F -- yes --> R["Record as in sync with both times"]
    F -- no --> G{"One-way pair?"}
    G -- yes --> H["Copy from the source side"]
    G -- no --> I{"Already a conflict copy?"}
    I -- yes --> J["Copy the newest version"]
    I -- no --> K{"Conflict rule"}
    K -- ask --> L["ASK: write a pending entry"]
    K -- "local, remote or newest wins" --> M["Save the loser if kept,<br/>then copy the winner"]
    D -- "changed, other unchanged" --> N["Copy to the other side"]
    D -- "deleted, other unchanged" --> O{"propagate_deletes on?"}
    O -- yes --> P["Delete on the other side"]
    O -- no --> Z
    D -- "deleted, other changed" --> Q["Copy the changed side back"]
    H --> S{"Direction allows writing there?"}
    N --> S
    P --> S
    Q --> S
    M --> S
    J --> S
    S -- yes --> T["Action goes to the executor"]
    S -- no --> Z
```

## 2. What `run_pair` does

```mermaid
flowchart TD
    A["Check the laptop folder exists,<br/>create the phone folder if the direction writes there"] --> B["Scan both sides, load the snapshot"]
    B --> C["plan with the lazy hasher"]
    C --> D["Log warnings, record resynced files,<br/>save pending asks"]
    D --> E{"Dry run?"}
    E -- yes --> F["Log each action, return counts"]
    E -- no --> G["Start the undo journal"]
    G --> H{"Next action"}
    H -- "none left" --> M
    H --> I{"Cancel requested?"}
    I -- yes --> X["Log cancelled and return:<br/>no reconcile, no meta, journal left unfinished"]
    I -- no --> J{"Kind"}
    J -- COPY --> K["Add to the batch,<br/>flush on direction change, 200 files or 200 MB"]
    J -- "DELETE, CONFLICT_COPY, ASK" --> L["Flush the batch, then run the action"]
    K --> N["Save the snapshot after each flush"]
    L --> N
    N --> H
    M["Flush the last batch, reconcile the snapshot,<br/>save the pair meta"] --> O["Finish the journal, prune old runs"]
    O --> P{"No errors?"}
    P -- yes --> Q["Commit the laptop folder if versioning is git"]
    P -- no --> R["Done"]
    Q --> R
```

A failing action is logged and counted in `errors`; the loop carries on with the next one.

## 3. Start, follow and stop a sync from the app

### 3a. As built

Stop moves the job to `cancelling` at once and kills the adb call that is running, so even a long transfer or scan
ends within seconds. Any other device sees the running sync through the ping, section 3b.

```mermaid
sequenceDiagram
    actor U as You
    participant A as Phone app
    participant S as Daemon API
    participant J as JobRunner
    participant E as run_pair
    participant D as adb and phone storage
    U->>A: tap Sync on a pair
    A->>S: POST /v1/run with the pair names
    S->>J: submit
    J-->>S: job id
    S-->>A: 202 with job id
    J->>E: job thread runs the pair
    E->>D: scan, hash, copy in batches of 50 files or 64 MB
    A->>S: GET /v1/jobs/id/log with since
    S-->>A: log lines as they are written
    A->>S: GET /v1/jobs/id every 2 seconds, foreground service
    U->>A: tap Stop
    A->>S: POST /v1/jobs/id/cancel
    S->>J: cancel
    J->>J: state cancelling, flag set
    J->>D: abort kills the running adb process
    D-->>E: the call raises TransportCancelled
    E-->>J: returns the summary of finished work, no error
    J-->>S: state cancelled
    S-->>A: end event with state cancelled
```

Files are pushed to a hidden `.androsync/incoming/` folder on the phone and moved into place only when complete, so a
Stop never leaves a half-copied file in the synced folder.

### 3b. Any device sees and stops the running sync (built in #7, except log files on disk)

```mermaid
sequenceDiagram
    actor U as You
    participant A as Phone app, window or curl
    participant S as Daemon API
    participant J as JobRunner
    participant D as adb and phone storage
    A->>S: GET /v1/ping every 5 seconds
    S-->>A: current_job with pairs and last line
    U->>A: open the running sync
    A->>S: GET /v1/jobs/id/log with since 0
    S-->>A: full log, from disk if needed
    U->>A: tap Stop
    A->>S: POST /v1/jobs/id/cancel
    S->>J: cancel
    J->>D: abort the running adb call
    J-->>S: state cancelling, then cancelled
    S-->>A: ping shows no current job
```

## 4. First connection and the 5 second ping

```mermaid
sequenceDiagram
    actor U as You
    participant A as Phone app
    participant R as SettingsRepository
    participant C as ConnectionViewModel
    participant P as ConnectionProbe
    participant S as Laptop daemon
    U->>A: scan the QR code from androsync qr
    A->>A: parseQrPayload gives urls and token
    A->>R: save token, remote URL, preset
    loop every poll interval, 5 s by default, while the app is in front
        C->>P: probe
        P->>S: GET /v1/ping on the USB address
        S--x P: no answer
        P->>S: GET /v1/ping on the saved Tailscale address
        S-->>P: name, version, transport, device
        P-->>C: reachable via Tailscale
        C->>C: publish Connected and rebuild the ApiClient if the address changed
    end
```

## 5. Phone plugged in

```mermaid
sequenceDiagram
    participant D as adb server
    participant W as DeviceWatcher
    participant Y as Daemon
    participant N as notify-send
    participant J as JobRunner
    W->>D: adb track-devices
    D-->>W: frame with serial and state device
    W->>Y: on_connect with the serial
    Y->>D: adb reverse tcp 8765 to tcp 8765, USB only
    Y->>D: adb tcpip 5555, if enabled and a Wi-Fi target ends in 5555
    Y->>N: Phone connected
    opt auto_sync_on_connect is on
        Y->>J: submit all pairs
    end
    D-->>W: frame without the serial
    W->>Y: on_disconnect
    Y->>J: cancel the running job, after 8 s for a TCP device
    Y->>N: Phone disconnected
```

## 6. Undo a run

```mermaid
sequenceDiagram
    actor U as You
    participant A as Phone app
    participant S as Daemon API
    participant J as JobRunner
    participant X as undo_run
    participant T as Transports
    U->>A: Undo last sync, optionally pause the pair
    A->>S: POST /v1/undo/pair with run id and pause
    S->>J: submit_undo
    S-->>A: 202 with job id
    J->>X: job thread
    X->>X: load the journal, start a new journal of kind undo
    loop entries in reverse order
        X->>T: delete created files, restore backed-up bytes, undo renames
    end
    X->>X: save snapshot_before as the pair snapshot
    X-->>J: summary with restored and skipped files
    opt pause was requested
        S->>S: set enabled false in config.yaml
    end
```

Files that changed after the run are skipped unless `--force` or the force flag is sent. Undoing an undo is a redo,
because the undo is journaled like any run.

## 7. A real conflict

```mermaid
sequenceDiagram
    participant E as run_pair
    participant H as hash_many
    participant L as Laptop folder
    participant P as Phone folder
    actor U as You
    participant S as Daemon API
    E->>H: same size, hash both sides
    H-->>E: hashes differ
    E->>E: conflict rule picks the winner, newest wins by default
    E->>L: save the losing version under .androsync/conflicts/stamp/rel
    E->>E: journal conflict_saved
    E->>P: copy the winner to the loser's side
    U->>S: POST /v1/conflicts/pair/restore with rel and when
    S->>L: store the saved version back over the file
    Note over L,P: the next run copies the restored file to the other side
```

`conflicts clean` removes old `name.conflict-...` copies: if the copy equals the original it is deleted on both sides,
otherwise it is archived into the conflicts folder first.

## 8. Desktop window: run here and close

```mermaid
sequenceDiagram
    actor U as You
    participant G as Window (App)
    participant B as Worker thread
    participant Y as Daemon
    participant Z as systemd
    U->>G: Run here
    G->>B: start a thread, Daemon.start can wait up to 30 s
    B->>Y: setup_logging, Daemon.start
    B-->>G: run_here_result through the event queue
    U->>G: close the window
    G->>G: close_window_decision, mode inprocess asks
    alt keep it running in the background
        G->>Z: systemctl --user start androsync.service
        G->>Y: stop the in-process daemon
    else stop it
        G->>Y: stop the in-process daemon
    end
    G->>G: destroy the window
```

For any other service mode the window just closes and leaves things as they are.

## 9. States

### 9a. Job (as built)

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running : thread starts at once
    running --> done : every pair finished
    running --> failed : a pair raised, or transports could not open
    running --> needs_confirmation : the plan deletes or copies too much, nothing was changed
    running --> cancelling : Stop pressed
    queued --> cancelling : Stop pressed
    cancelling --> cancelled : the killed call returned and the run ended
    cancelling --> cancelled : grace period of 10 s over
    done --> [*]
    failed --> [*]
    cancelled --> [*]
    needs_confirmation --> [*]
```

`cancelling` still counts as busy: a new sync answers 409 until the job has ended. `needs_confirmation` is final: the
user then starts a new job for the blocked pairs with `confirm_large: true` ("Go ahead").

### 9b. Connection state in the app

```mermaid
stateDiagram-v2
    [*] --> Unreachable : start
    Unreachable --> Unreachable : no token saved, or every ping failed
    Unreachable --> ConnectedUSB : ping on the USB address works
    Unreachable --> ConnectedTailscale : ping on the saved remote address works
    Unreachable --> ConnectedLAN : ping on the saved LAN address works
    ConnectedUSB --> Unreachable : all pings fail
    ConnectedTailscale --> Unreachable : all pings fail
    ConnectedLAN --> Unreachable : all pings fail
    ConnectedUSB --> ConnectedTailscale : cable unplugged, remote answers
    ConnectedTailscale --> ConnectedUSB : cable plugged in
```

Polling runs only while the app is in front (`ON_START` to `ON_STOP`); the last state is frozen otherwise.

### 9c. How the service is running (`detect_state`)

```mermaid
stateDiagram-v2
    [*] --> none
    none --> systemd : start in background, or service start
    systemd --> none : stop
    none --> inprocess : Run here in the window
    inprocess --> none : window closed and stopped
    inprocess --> systemd : close and keep it in the background
    none --> other : androsync serve in a terminal
    other --> none : terminal closed
    systemd --> systemd : autostart on or off
```

Detection order: `systemctl --user is-active` first, then the pid file plus a ping (pid equal to the current process
means `inprocess`, otherwise `other`), else `none`.

### 9d. A pair

```mermaid
stateDiagram-v2
    [*] --> enabled : added
    enabled --> paused : undo with pause, or enabled false
    paused --> enabled : Enable switch
    enabled --> enabled : folder changed, history reset (built, issue #9)
    enabled --> [*] : removed, history kept or purged
    paused --> [*] : removed
```
