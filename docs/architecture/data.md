# Data

Where data moves, what is stored, and in which files. **As built** (2026-10-16). Back to the
[architecture index](README.md).

## 1. Data flow, level 0

```mermaid
flowchart LR
    you([You: app, window, CLI])
    laptopf([Laptop folder])
    phonef([Phone folder])
    sys((androsync))
    you -->|"pairs, start, stop, confirm, choices"| sys
    sys -->|"plan, warnings, progress, log, results"| you
    laptopf -->|"file names, sizes, times, bytes"| sys
    sys -->|"new and changed files, deletions"| laptopf
    phonef -->|"file names, sizes, times, bytes (via ADB)"| sys
    sys -->|"new and changed files, deletions (via ADB)"| phonef
```

Nothing leaves the two devices and the link between them. There is no cloud service and no telemetry.

## 2. Data flow, level 1

```mermaid
flowchart LR
    laptopf([Laptop folder])
    phonef([Phone folder])
    you([You: app, window, CLI])
    p1((1. Scan both sides))
    p2((2. Plan changes))
    p3((3. Transfer files))
    p4((4. Record run))
    d1[(Snapshot)]
    d2[(Undo journal and backups)]
    d3[(config.yaml pairs)]
    d4[(Job log)]
    you -->|"pair, start, stop, confirm"| p1
    d3 -->|"folders, direction, rules"| p1
    laptopf -->|"names, sizes, times"| p1
    phonef -->|"names, sizes, times via ADB"| p1
    p1 -->|"two listings"| p2
    d1 -->|"last seen state"| p2
    p2 -->|"actions, counts"| p3
    p2 -->|"warnings, questions"| you
    laptopf -->|"file bytes"| p3
    phonef -->|"file bytes via ADB"| p3
    p3 -->|"file bytes"| laptopf
    p3 -->|"file bytes via ADB"| phonef
    p3 -->|"old versions"| d2
    p3 -->|"finished actions"| p4
    p4 -->|"new state"| d1
    p4 -->|"summary"| d2
    p4 -->|"log lines"| d4
    d4 -->|"live log"| you
```

## 3. Stored data (ER)

Everything persistent is a file; there is no database. The two entities marked in memory are not written to disk.

```mermaid
erDiagram
    CONFIG ||--o{ PAIR : defines
    CONFIG ||--|| SERVICE_SETTINGS : has
    PAIR ||--o{ SNAPSHOT_ENTRY : "remembers files as"
    PAIR ||--o| PAIR_META : "last result"
    PAIR ||--o{ PENDING_ASK : "waits for a choice"
    PAIR ||--o{ UNDO_RUN : journals
    UNDO_RUN ||--o{ JOURNAL_ENTRY : contains
    JOURNAL_ENTRY |o--o| BACKUP_FILE : "may keep old bytes"
    PAIR ||--o{ CONFLICT_VERSION : "saves losing versions"
    JOB }o--o{ PAIR : runs
    JOB ||--o{ LOG_LINE : emits

    CONFIG {
        string device
        string wifi_target
        dict defaults
    }
    SERVICE_SETTINGS {
        list listen
        int port
        string token
        bool auto_sync_on_connect
        bool notify
        list browse_roots
        bool enable_tcpip_on_usb
    }
    PAIR {
        string name PK
        string local
        string remote
        string direction
        string conflict
        string conflict_copies
        bool propagate_deletes
        bool enabled
        string versioning
    }
    SNAPSHOT_ENTRY {
        string pair FK
        string rel PK
        int size
        float mtime_local
        float mtime_remote
        string sha256
    }
    PAIR_META {
        string pair PK
        string last_run
        string last_result
    }
    PENDING_ASK {
        string pair FK
        string rel PK
        string reason
    }
    UNDO_RUN {
        string pair FK
        string run_id PK
        string kind
        string started
        bool finished
    }
    JOURNAL_ENTRY {
        string run_id FK
        string op
        string side
        string rel
        string backup
    }
    BACKUP_FILE {
        string run_id FK
        string side
        string rel
    }
    CONFLICT_VERSION {
        string stamp PK
        string rel
        int size
    }
    JOB {
        string id PK
        string kind
        string state
        string error
        float created
        float started
        float finished
    }
    LOG_LINE {
        int seq PK
        string line
    }
```

`JOB` and `LOG_LINE` live in memory (the daemon keeps 50 jobs and a 5000-line ring buffer per job). The log is also
copied line by line into `last-run.log`, which is overwritten at the start of each run.

## 4. Data dictionary

`<state>` is `$XDG_DATA_HOME/androsync/state`, default `~/.local/share/androsync/state`. Every JSON file is written
to a `.tmp` file and renamed, so a crash never leaves half a file.

| Data | Path | Shape | Written by | Read by |
|---|---|---|---|---|
| Config | `~/.config/androsync/config.yaml` | See section 5 | `config_edit.save_raw` (validates first), `service install` | `config.load_config` |
| Snapshot | `<state>/<pair>.json` | `{rel: {size, mtime_local, mtime_remote, sha256}}`. Old files with one `mtime` are migrated on read. | `executor.run_pair` after every action, `resolve_pending`, `undo_run` | executor, undo |
| Pair meta | `<state>/<pair>.meta.json` | `{last_run, last_result, summary}` | end of a real run, failed jobs (`FAILED: ...`) | `pairs_status`, CLI |
| Pending asks | `<state>/<pair>.pending.json` | list of `{rel, local, remote, reason}` | `executor._save_pending_asks` after each plan | `resolve`, `GET /v1/pending` |
| Undo run | `<state>/undo/<pair>/<run_id>/meta.json` | `{pair, run_id, kind, started, finished, summary}` | `RunJournal.start` and `finish` | `undo.list_runs` |
| Snapshot before | `.../<run_id>/snapshot_before.json` | same shape as the snapshot | `RunJournal.start` | `undo_run` restores it |
| Journal | `.../<run_id>/journal.jsonl` | one JSON line per change, see below | `RunJournal.record*` | `undo.load` |
| Backed-up bytes | `.../<run_id>/files/<local or remote>/<rel>` | raw file | `RunJournal.backup` | `undo_run` |
| Saved conflict version | `<laptop folder>/.androsync/conflicts/<YYYYMMDD-HHMMSS>/<rel>` | raw file, laptop only | `executor._do_conflict_copy` | `conflicts.list_conflicts`, `restore` |
| Beside-mode conflict copy | next to the original, on both sides | `name.conflict-<laptop or phone>-<stamp>.ext` | `_do_conflict_copy` (option `beside`) | `conflicts.find_duplicates` |
| Run log | `~/.local/state/androsync/last-run.log` | text, truncated per run | `runlog.RunLog` | humans |
| Daemon log | `~/.local/state/androsync/daemon.log` | rotating, 2 MB, 3 backups | `cli.setup_logging` | `service logs`, window |
| Pid file | `$XDG_RUNTIME_DIR/androsync/daemon.pid` | pid | `Daemon.start` | `service_state.detect_state` |

Journal operations (`side` is `local` or `remote`):

| `op` | Keys | Undone by |
|---|---|---|
| `create` | `side, rel, new{size,mtime}` | deleting the file, unless it changed since |
| `overwrite` | `side, rel, old, new, backup` (`saved` or `skipped`) | restoring the backed-up bytes |
| `delete` | `side, rel, old, backup` | restoring the backed-up bytes |
| `conflict_saved` | `path` | deleting the saved version |
| `conflict_rename` | `side, rel, target, old` | renaming back |

## 5. Config schema

Top-level keys: `device` (default `auto`), `wifi_target`, `service`, `defaults`, `pairs`.

`service` (`ServiceConfig`):

| Key | Default |
|---|---|
| `listen` | `["127.0.0.1"]`; `0.0.0.0` is refused |
| `port` | 8765 |
| `token` | generated by `service install` |
| `auto_sync_on_connect` | false |
| `notify` | true |
| `browse_roots` | `["~"]` plus the removable-media folder if it exists |
| `enable_tcpip_on_usb` | true |

`pairs` (`PairConfig`), after `defaults` are merged underneath (only the `ignore` lists are concatenated):

| Key | Default | Allowed |
|---|---|---|
| `name` | required | unique |
| `local` | required | path, `~` and variables expanded |
| `remote` | required | must start with `/` |
| `direction` | required | `two-way`, `laptop-to-phone`, `phone-to-laptop` |
| `conflict` | `newest-wins` | `local-wins`, `remote-wins`, `ask` |
| `conflict_copies` | `folder` | `beside` |
| `keep_conflict_copies` | true | |
| `propagate_deletes` | true | |
| `mtime_tolerance_s` | 2 | |
| `ignore` | defaults plus pair list | `.androsync/**` is always ignored |
| `versioning` | none | `git` |
| `enabled` | true | |
| `undo.keep_runs`, `undo.max_total_mb`, `undo.max_file_mb`, `undo.enabled` | 10, 2048, 500, true | merged as built-in, then `defaults.undo`, then the pair's `undo` |
| `transfer.batch_files`, `transfer.batch_mb` | 50, 64 | files and MB per batched copy (same merge order) |
| `safety.max_delete_count`, `safety.max_delete_percent`, `safety.big_first_run_files`, `safety.big_first_run_gb` | 50, 20, 5000, 5 | when a plan needs confirmation; 0 switches a check off (same merge order) |
