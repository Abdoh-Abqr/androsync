# androsync architecture

How the system is built, drawn from the code as of 2026-10-16. Every name in the diagrams exists in the repo;
`CODE_INDEX.md` is the file-by-file map, `NEXT_TASKS.md` the history of each task.

## Reading guide

| Question | Read |
|---|---|
| What is the system, who and what is around it, what runs where? | This file |
| What is inside the engine, the daemon and the app? Which classes carry the data? | [components.md](components.md) |
| Where does data move, what is stored, in which files? | [data.md](data.md) |
| What happens step by step, and which states exist? | [behavior.md](behavior.md) |
| Who can do what, how is it secured, why was it built this way, what is still weak? | [overview.md](overview.md) |

**Legend.** Diagrams are marked **as built** (matches the code today) or **target** (planned in
`NEXT_TASKS.md`, with the issue number). Solid arrows are calls or data, dashed arrows are optional or background
links. Stadium shapes are people, cylinders are stored data, rounded boxes are programs.

## 1. System context (C4 level 1, as built)

```mermaid
flowchart TB
    user([You])
    sys["androsync<br/>keeps chosen folders identical<br/>on a laptop and a phone"]
    adbd["Android Debug Bridge<br/>USB or Wi-Fi debugging"]
    ts["Tailscale<br/>reach the laptop away from home"]
    st["Syncthing<br/>alternative without debugging"]
    user -->|"starts, watches and stops syncs, edits folder pairs"| sys
    sys -->|"lists, reads and writes phone storage"| adbd
    user -.->|"phone app reaches the laptop through"| ts
    sys -.->|"the API is reachable through"| ts
    user -.->|"may use instead for some folders"| st
```

## 2. Containers (C4 level 2, as built)

```mermaid
flowchart TB
    user([You])
    subgraph laptop["Laptop (Ubuntu)"]
        cli["CLI<br/>androsync"]
        gui["Desktop window<br/>Tk, androsync gui"]
        subgraph daemon["Daemon: androsync serve"]
            api["HTTP API<br/>daemon/server.py"]
            jobs["Job runner<br/>daemon/jobs.py"]
            watch["Device watcher<br/>daemon/watcher.py"]
        end
        engine["Sync engine<br/>planner, executor, undo, conflicts"]
        adbt["ADB transport<br/>transport/adb.py"]
        state[("State files<br/>snapshots, undo journals")]
        cfg[("config.yaml")]
    end
    subgraph phone["Phone (Android)"]
        app["App AndroSync Remote<br/>Compose, Retrofit"]
        storage[("Shared storage<br/>/sdcard")]
    end
    user --> app
    user --> gui
    user --> cli
    app -->|"HTTP and bearer token"| api
    gui --> api
    cli --> engine
    api --> jobs
    jobs --> engine
    engine --> adbt
    adbt -->|"adb"| storage
    watch -.->|"adb track-devices"| adbt
    engine --> state
    api --> cfg
```

Notes:
- The window talks to the daemon only when one is running; otherwise it runs the engine directly
  (`gui/backend.py`, `reachable_client`). Pair add, edit and remove always go straight to `config_edit`.
- The app never touches files. All file work happens on the laptop through ADB.

## 3. Containers (C4 level 2, target)

What the open epics change. Issue numbers are in the private repo `Abdoh-Abqr/androsync`.

```mermaid
flowchart TB
    user([You])
    subgraph laptop["Laptop"]
        gui["Desktop window"]
        cli["CLI"]
        subgraph daemon["Daemon"]
            api["HTTP API<br/>+ current job in ping (#7)<br/>+ agent endpoints (#14)"]
            jobs["Job runner<br/>cancelling state, abort adb (#6)<br/>full job logs on disk (#7)"]
        end
        engine["Sync engine<br/>mass-delete guard (#9)<br/>history per phone (#18)"]
        adbt["ADB transport"]
        agentt["AgentTransport (#14)"]
        state[("State files<br/>keyed by pair and phone")]
    end
    subgraph phone["Phone"]
        play["App, play flavor (#12)<br/>remote control only"]
        full["App, full flavor (#12)<br/>remote control + agent (#15)"]
        storage[("Shared storage")]
    end
    user --> play
    user --> full
    user --> gui
    user --> cli
    play -->|"HTTP"| api
    full -->|"HTTP"| api
    full -->|"long-poll for work (#14)"| agentt
    gui --> api
    cli --> engine
    api --> jobs --> engine
    engine --> adbt
    engine --> agentt
    adbt -->|"adb"| storage
    full -.->|"All files access (#13 spike)"| storage
    engine --> state
```

## 4. Deployment (as built)

Which programs run where, on which port, and the three ways the phone reaches the laptop.

```mermaid
flowchart LR
    subgraph laptop["Laptop"]
        unit["systemd user unit<br/>androsync.service<br/>optional autostart"]
        serve["androsync serve<br/>HTTP API on port 8765<br/>listens on 127.0.0.1<br/>and optionally the tailnet IP"]
        track["adb track-devices"]
        adbsrv["adb server<br/>port 5037"]
        guip["androsync gui<br/>separate Tk process"]
        files[("config.yaml, state, logs, pid file")]
    end
    subgraph phone["Phone"]
        adbd["adbd<br/>USB debugging"]
        app["App AndroSync Remote"]
        sd[("/sdcard")]
    end
    unit -->|"starts"| serve
    guip -.->|"or runs the engine itself"| serve
    serve --> track --> adbsrv
    serve -->|"adb shell, push, pull"| adbsrv
    adbsrv ==>|"USB cable"| adbd
    adbsrv -.->|"adb connect, port 5555 after adb tcpip"| adbd
    adbd --> sd
    app ==>|"USB: adb reverse tcp 8765<br/>phone 127.0.0.1:8765 reaches the laptop"| serve
    app -.->|"Tailscale: laptop tailnet IP, port 8765"| serve
    app -.->|"LAN: laptop LAN address, port 8765"| serve
    serve --> files
```

Connection paths, in the order the app tries them (`ConnectionProbe`): USB, then Tailscale (if a URL is saved), then
LAN (if a URL is saved). Only the first one that answers `GET /v1/ping` becomes the active client.
Files the laptop writes are listed in [data.md](data.md).
