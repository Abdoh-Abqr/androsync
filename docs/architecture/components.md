# Components

What is inside each container, and the classes that carry the data. All diagrams are **as built** (2026-10-16).
Back to the [architecture index](README.md).

## 1. Sync engine (C4 level 3)

The engine is plain Python with no network code. It only talks to the two sides through the `Transport`
interface, which is why tests run it on two local folders.

```mermaid
flowchart LR
    subgraph callers["Callers"]
        cli["cli.py, cli_pairs.py,<br/>cli_conflicts.py"]
        jobs["daemon/jobs.py"]
        gui["gui/backend.py"]
    end
    subgraph engine["Sync engine"]
        executor["executor.py<br/>run_pair, resolve_pending"]
        planner["planner.py<br/>plan, classify"]
        undo["undo.py<br/>RunJournal, undo_run"]
        conflicts["conflicts.py<br/>list, restore, clean"]
        conflict["conflict.py<br/>names and folder paths"]
        versioning["versioning.py<br/>git commit after a run"]
        ignore["ignore.py<br/>IgnoreRules"]
    end
    state["state.py<br/>snapshot, meta, pending"]
    config["config.py<br/>config_edit.py"]
    subgraph transports["Transports"]
        base["transport/base.py<br/>Transport"]
        local["LocalDirTransport"]
        adb["AdbTransport<br/>uses Adb"]
    end
    cli --> executor
    jobs --> executor
    gui --> executor
    cli --> conflicts
    jobs --> undo
    executor --> planner
    executor --> state
    executor --> undo
    executor --> versioning
    executor --> conflict
    executor --> ignore
    executor --> base
    planner --> conflict
    planner --> config
    undo --> state
    undo --> base
    conflicts --> conflict
    conflicts --> ignore
    conflicts --> base
    state --> config
    local -.->|"implements"| base
    adb -.->|"implements"| base
```

## 2. Daemon (C4 level 3)

```mermaid
flowchart TB
    clients["Phone app, desktop window, curl"]
    subgraph daemon["Daemon: androsync serve"]
        server["server.py<br/>ApiServer, route table, bearer auth"]
        jobs["jobs.py<br/>JobRunner, Job, LogBuffer"]
        watcher["watcher.py<br/>Daemon, DeviceWatcher,<br/>WifiReconnector"]
        sstate["service_state.py<br/>detect_state, pid file, ping"]
        browse["browse.py<br/>folder listing, local and phone"]
        runlog["runlog.py<br/>last-run.log"]
    end
    engine["Sync engine<br/>executor, undo, conflicts"]
    cedit["config_edit.py"]
    svc["service.py<br/>unit file, install, launcher"]
    system["systemctl, journalctl, loginctl,<br/>notify-send"]
    adb["adb"]
    clients -->|"HTTP"| server
    server --> jobs
    server --> browse
    server --> cedit
    server --> engine
    jobs --> engine
    jobs --> runlog
    watcher --> jobs
    watcher --> server
    watcher --> sstate
    sstate --> svc
    watcher -->|"track-devices, reverse, tcpip, connect"| adb
    watcher -->|"notify-send"| system
    sstate -->|"systemctl --user"| system
    svc -->|"writes the unit, runs systemctl, loginctl, ufw"| system
    browse -->|"ls"| adb
```

Threads in a running daemon: one `androsync-api-<addr>` per bound address, `androsync-watcher`,
`androsync-wifi-reconnect` (only for a `wifi:` device), one `androsync-job-<id>` per job, and short-lived timers for
the TCP disconnect grace period.

## 3. Android app (C4 level 3)

```mermaid
flowchart TB
    main["MainActivity<br/>NavHost, Routes"]
    subgraph ui["Screens (Compose)"]
        setup["SetupScreen"]
        pairs["PairsScreen"]
        editor["PairEditorScreen"]
        job["JobScreen"]
        settings["SettingsScreen"]
    end
    subgraph vm["ViewModels"]
        cvm["ConnectionViewModel<br/>polls ping, holds activeApiClient"]
        pvm["PairsViewModel"]
        evm["PairEditorViewModel"]
        jvm["JobViewModel"]
        other["SetupViewModel,<br/>SettingsViewModel"]
    end
    subgraph data["Data"]
        api["ApiClient<br/>AndroSyncApi, AuthInterceptor, LogStream"]
        probe["ConnectionProbe"]
        repo["SettingsRepository<br/>DataStore"]
        launcher["SyncLauncher"]
    end
    subgraph os["Android components"]
        fgs["SyncForegroundService"]
        tile["SyncTileService"]
        widget["SyncWidget"]
    end
    laptop["Laptop daemon API"]
    main --> ui
    ui --> vm
    pvm --> cvm
    evm --> cvm
    jvm --> cvm
    cvm --> probe
    cvm --> repo
    probe --> api
    pvm --> launcher
    launcher --> probe
    launcher --> repo
    launcher --> fgs
    tile --> launcher
    widget --> launcher
    fgs --> api
    api -->|"HTTP and SSE"| laptop
```

Wiring is a hand-written container: `AndroSyncApplication` creates one `AppContainer` that holds
`SettingsRepository`, `ConnectionProbe` and `SyncLauncher`. The tile, widget and foreground service reach it through
the application context. There is no dependency injection library.

## 4. Core classes (as built)

```mermaid
classDiagram
    class Transport {
        <<abstract>>
        +side Side
        +label str
        +scan(root, ignore) Snapshot
        +fetch(root, rel, tmp_path)
        +store(tmp_path, root, rel, mtime)
        +delete(root, rel)
        +rename(root, rel_from, rel_to)
        +mkdirs(root, rel_dir)
        +exists(root) bool
        +stat(root, rel) FileEntry
        +store_many(items)
        +fetch_many(items)
        +hash_many(root, rels)
        +close()
    }
    class LocalDirTransport
    class AdbTransport {
        +check_mtime_preserved()
        +ls(path)
    }
    class Adb {
        +run(args)
        +shell(cmd)
        +exec_out(cmd)
    }
    class FileEntry {
        +int size
        +float mtime
        +str sha256
    }
    class SnapshotEntry {
        +int size
        +float mtime_local
        +float mtime_remote
        +str sha256
    }
    class Action {
        +ActionKind kind
        +str rel
        +Side src
        +Side dst
        +str reason
        +FileEntry entry
        +str target
    }
    class ActionKind {
        <<enumeration>>
        COPY
        DELETE
        CONFLICT_COPY
        ASK
        NOOP
    }
    class Side {
        <<enumeration>>
        LOCAL
        REMOTE
    }
    class PlanResult {
        +list actions
        +list warnings
        +list resynced
    }
    class Summary {
        +int copied_to_local
        +int copied_to_remote
        +int deleted_local
        +int deleted_remote
        +int conflicts
        +int asks
        +int errors
        +str run_id
    }
    class RunJournal {
        +str pair_name
        +str run_id
        +str kind
        +start()
        +record()
        +backup()
        +finish()
    }
    class PairConfig {
        +str name
        +Path local
        +str remote
        +str direction
        +str conflict
        +bool propagate_deletes
        +bool enabled
        +str conflict_copies
        +str versioning
    }
    class ServiceConfig {
        +list listen
        +int port
        +str token
        +bool auto_sync_on_connect
        +bool notify
    }
    class Config {
        +str device
        +str wifi_target
        +dict defaults
    }
    Transport <|-- LocalDirTransport
    Transport <|-- AdbTransport
    AdbTransport --> Adb : runs adb through
    Action --> ActionKind
    Action --> Side
    Action --> FileEntry
    PlanResult o-- Action
    Config *-- ServiceConfig
    Config *-- PairConfig
    RunJournal ..> Summary : finish writes
    SnapshotEntry ..> FileEntry : per-side view of
```

`Snapshot` is a dict from relative path to `FileEntry` (what a scan returns). `PersistedSnapshot` is a dict from
relative path to `SnapshotEntry` (what is saved between runs, with one modified time per side).

## 5. Module dependencies

An arrow means "imports". `cli` reaches the sub-command modules both ways, lazily, to avoid a cycle.

```mermaid
flowchart LR
    cli --> cli_pairs
    cli --> cli_conflicts
    cli --> executor
    cli --> undo
    cli --> service
    cli --> watcher
    cli_pairs --> executor
    cli_pairs --> config_edit
    cli_conflicts --> conflicts
    executor --> planner
    executor --> state
    executor --> undo
    executor --> versioning
    planner --> conflict
    planner --> config
    state --> config
    undo --> state
    conflicts --> conflict
    config_edit --> config
    executor --> tbase["transport.base"]
    tlocal["transport.local"] --> tbase
    tadb["transport.adb"] --> tbase
    jobs --> executor
    jobs --> undo
    server --> jobs
    server --> config_edit
    server --> conflicts
    server --> browse
    browse --> tadb
    watcher --> jobs
    watcher --> server
    watcher --> tadb
    watcher --> service_state
    service_state --> service
```
