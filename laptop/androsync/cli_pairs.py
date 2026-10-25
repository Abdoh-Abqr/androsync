"""``add-pair``/``edit-pair``/``remove-pair`` and ``resolve`` subcommands (PLAN.md tasks 23/23b).

``add-pair`` appends a new pair to the config YAML - interactively (name,
local path with tab completion, remote path via a numbered browser over
the phone, direction, conflict rule, propagate_deletes) or non-interactively
when ``--name``/``--local``/``--remote``/``--direction`` are all given (for
scripting and tests). ``edit-pair``/``remove-pair`` are flag-driven,
non-interactive CLI surfaces over the same ``config_edit`` helpers the
phone app's pair CRUD HTTP routes use (``daemon/server.py``). ``resolve``
walks the pending ``conflict: ask`` entries the executor persists per pair
(``state.load_pending``/``save_pending``) and lets the user pick a winner
per file.
"""

from __future__ import annotations

import argparse
import os
import readline
from pathlib import Path
from typing import Callable

from androsync import config_edit, state
from androsync.config import (
    CONFLICT_POLICIES,
    DIRECTIONS,
    TRANSPORT_MODES,
    ConfigError,
    default_config_path,
    load_config,
)
from androsync.executor import ExecutorError, resolve_pending
from androsync.transport.adb import Adb, AdbError, AdbTransport, resolve_device
from androsync.phone_path import PhonePathError, check_remote, remote_parent

LogFn = Callable[[str], None]

_RESOLVE_PROMPT = "local / remote / both (keep conflict copies) / skip [l/r/b/s]: "
_RESOLVE_CHOICE_MAP = {"l": "local", "r": "remote", "b": "both", "s": "skip"}


# ---------------------------------------------------------------------------
# add-pair
# ---------------------------------------------------------------------------


class _PathCompleter:
    """A ``readline`` completer that offers filesystem entries under ``~``-expanded text."""

    def __call__(self, text: str, state: int) -> str | None:
        expanded = os.path.expanduser(text)
        directory, _, prefix = expanded.rpartition(os.sep)
        directory = directory or "."
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            names = []
        matches = [n for n in names if n.startswith(prefix)]
        if state >= len(matches):
            return None
        name = matches[state]
        full = os.path.join(directory, name)
        if os.path.isdir(full):
            name += os.sep
        prefix_dir = text[: len(text) - len(prefix)] if prefix else text
        return prefix_dir + name


def _prompt_local_path(log: LogFn) -> Path:
    old_completer = readline.get_completer()
    old_delims = readline.get_completer_delims()
    readline.set_completer(_PathCompleter())
    readline.set_completer_delims(" \t\n")
    readline.parse_and_bind("tab: complete")
    try:
        while True:
            raw = input("Local path (Tab to complete): ").strip()
            if not raw:
                continue
            path = Path(os.path.expandvars(os.path.expanduser(raw)))
            if path.exists():
                if not path.is_dir():
                    log(f"{path} exists but is not a directory")
                    continue
                return path
            answer = input(f"{path} does not exist. Create it? [y/N] ").strip().lower()
            if answer in ("y", "yes"):
                path.mkdir(parents=True, exist_ok=True)
                return path
    finally:
        readline.set_completer(old_completer)
        readline.set_completer_delims(old_delims)


def _prompt_remote_path_typed() -> str:
    while True:
        raw = input("Phone folder (for example /sdcard/Documents): ").strip()
        try:
            return check_remote(raw)
        except PhonePathError as exc:
            print(exc)


def _prompt_remote_path_browsed(transport: AdbTransport, log: LogFn) -> str:
    current = "/sdcard"
    while True:
        try:
            entries = transport.ls(current)
        except AdbError as exc:
            log(f"could not list {current}: {exc}")
            return _prompt_remote_path_typed()
        dirs = sorted((name for name, is_dir, _ in entries if is_dir), key=str.lower)
        print(f"\n{current}")
        for i, name in enumerate(dirs, start=1):
            print(f"  {i}) {name}/")
        print("  .. ) up a level   . ) pick this folder   (or type an absolute path)")
        choice = input("> ").strip()
        if choice == ".":
            return current
        if choice == "..":
            up = remote_parent(current)
            if up is None:
                print("This is the top of the phone's shared storage.")
            else:
                current = up
            continue
        if choice.startswith("/"):
            try:
                return check_remote(choice)
            except PhonePathError as exc:
                print(exc)
                continue
        if choice.isdigit() and 1 <= int(choice) <= len(dirs):
            current = f"{current.rstrip('/')}/{dirs[int(choice) - 1]}"
            continue
        print("not understood")


def _prompt_choice(prompt: str, choices: tuple[str, ...], default: str) -> str:
    choice_str = "/".join(c if c != default else c.upper() for c in choices)
    while True:
        raw = input(f"{prompt} [{choice_str}]: ").strip().lower()
        if not raw:
            return default
        if raw in choices:
            return raw
        print(f"must be one of {choices}")


def _prompt_yes_no(prompt: str, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = input(f"{prompt} {suffix} ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def cmd_add_pair(args: argparse.Namespace) -> int:
    log: LogFn = print
    config_path = args.config if getattr(args, "config", None) else default_config_path()

    non_interactive = bool(args.name and args.local and args.remote and args.direction)

    if non_interactive:
        name = args.name
        local = Path(os.path.expandvars(os.path.expanduser(args.local)))
        remote = args.remote
        direction = args.direction
        conflict = args.conflict or "newest-wins"
        propagate_deletes = args.propagate_deletes
    else:
        name = args.name or input("Pair name: ").strip()
        if not name:
            log("androsync: a pair name is required")
            return 2
        if args.local:
            local = Path(os.path.expandvars(os.path.expanduser(args.local)))
        else:
            local = _prompt_local_path(log)

        if args.remote:
            remote = args.remote
        else:
            try:
                loaded = load_config(config_path) if config_path.exists() else None
                serial = resolve_device(
                    loaded.device if loaded else "auto", wifi_target=loaded.wifi_target if loaded else None
                )
                transport = AdbTransport(Adb(serial=serial))
                remote = _prompt_remote_path_browsed(transport, log)
            except (AdbError, ConfigError) as exc:
                log(f"no phone available to browse ({exc}); type the path instead")
                remote = _prompt_remote_path_typed()

        direction = args.direction or _prompt_choice("Direction", DIRECTIONS, "two-way")
        conflict = args.conflict or _prompt_choice("Conflict policy", CONFLICT_POLICIES, "newest-wins")
        default_propagate = direction != "phone-to-laptop"
        propagate_deletes = (
            args.propagate_deletes
            if args.propagate_deletes is not None
            else _prompt_yes_no("Propagate deletes?", default_propagate)
        )

    if direction not in DIRECTIONS:
        log(f"androsync: direction must be one of {DIRECTIONS}, got {direction!r}")
        return 2
    if conflict not in CONFLICT_POLICIES:
        log(f"androsync: conflict must be one of {CONFLICT_POLICIES}, got {conflict!r}")
        return 2
    if not str(remote).startswith("/"):
        log("androsync: remote must be an absolute path starting with '/'")
        return 2

    pair_dict: dict = {"name": name, "local": str(local), "remote": remote, "direction": direction}
    if conflict != "newest-wins":
        pair_dict["conflict"] = conflict
    if getattr(args, "transport", None) and args.transport != "adb":
        pair_dict["transport"] = args.transport
    if propagate_deletes is not None and propagate_deletes is not True:
        pair_dict["propagate_deletes"] = propagate_deletes

    raw = config_edit.load_raw(config_path)
    try:
        new_raw = config_edit.add_pair(raw, pair_dict)
        config_edit.save_raw(config_path, new_raw)
    except ConfigError as exc:
        log(f"androsync: {exc}")
        return 2

    log(f"added pair {name!r} to {config_path}")
    return 0


# ---------------------------------------------------------------------------
# edit-pair / remove-pair
# ---------------------------------------------------------------------------


def cmd_edit_pair(args: argparse.Namespace) -> int:
    log: LogFn = print
    config_path = args.config if getattr(args, "config", None) else default_config_path()

    raw = config_edit.load_raw(config_path)
    pairs = raw.get("pairs") or []
    current = next((p for p in pairs if p.get("name") == args.name), None)
    if current is None:
        log(f"androsync: no pair named {args.name!r}")
        return 2

    pair_dict = dict(current)
    if args.rename:
        pair_dict["name"] = args.rename
    if args.local:
        pair_dict["local"] = args.local
    if args.remote:
        pair_dict["remote"] = args.remote
    if args.direction:
        pair_dict["direction"] = args.direction
    if args.conflict:
        pair_dict["conflict"] = args.conflict
    if getattr(args, "transport", None):
        pair_dict["transport"] = args.transport
    if args.propagate_deletes is not None:
        pair_dict["propagate_deletes"] = args.propagate_deletes
    if args.enabled is not None:
        pair_dict["enabled"] = args.enabled

    try:
        state_reset = config_edit.update_pair_and_state(config_path, args.name, pair_dict)
    except ConfigError as exc:
        log(f"androsync: {exc}")
        return 2

    log(f"updated pair {args.name!r}" + (f" (renamed to {args.rename!r})" if args.rename else ""))
    if state_reset:
        log("folders changed: the pair's history was reset, so the next sync only adds files and deletes nothing")
    return 0


def cmd_remove_pair(args: argparse.Namespace) -> int:
    log: LogFn = print
    config_path = args.config if getattr(args, "config", None) else default_config_path()

    raw = config_edit.load_raw(config_path)
    try:
        new_raw = config_edit.remove_pair(raw, args.name)
        config_edit.save_raw(config_path, new_raw)
    except ConfigError as exc:
        log(f"androsync: {exc}")
        return 2

    if args.purge_state:
        state.purge_pair_state(args.name)

    log(f"removed pair {args.name!r}" + (" and its sync history" if args.purge_state else ""))
    return 0


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def _describe_entry(side_label: str, entry: dict | None) -> str:
    if entry is None:
        return f"{side_label}: missing"
    return f"{side_label}: {entry['size']} bytes, mtime {entry['mtime']:.0f}"


def cmd_resolve(args: argparse.Namespace) -> int:
    from androsync.cli import _load_config, _select_pairs, open_pair_transports  # local import: avoid a cycle

    log: LogFn = print
    config = _load_config(args)
    pairs = _select_pairs(config, [args.pair] if args.pair else [])
    if not pairs:
        pairs = config.pairs

    pending_by_pair = {pair.name: state.load_pending(pair.name) for pair in pairs}
    if not any(pending_by_pair.values()):
        print("nothing pending")
        return 0

    local, remote = open_pair_transports(config, log=None)

    resolved = 0
    skipped = 0
    for pair in pairs:
        pending = pending_by_pair[pair.name]
        if not pending:
            continue
        print(f"== {pair.name} ==")
        for entry in list(pending):
            rel = entry["rel"]
            print(f"\n{rel}")
            print(f"  {entry.get('reason', '')}")
            print(f"  {_describe_entry('local', entry.get('local'))}")
            print(f"  {_describe_entry('remote', entry.get('remote'))}")
            while True:
                raw = input(f"  {_RESOLVE_PROMPT}").strip().lower()
                choice = _RESOLVE_CHOICE_MAP.get(raw)
                if choice is not None:
                    break
                print("  not understood")
            if choice == "skip":
                skipped += 1
                continue
            try:
                resolve_pending(pair, local, remote, rel, choice)
                resolved += 1
            except ExecutorError as exc:
                log(f"  androsync: {exc}")

    print(f"\nresolved {resolved}, skipped {skipped}")
    return 0


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def add_pair_commands(subparsers, common: argparse.ArgumentParser) -> None:
    """Attach ``add-pair`` and ``resolve`` to ``subparsers``."""
    add_pair_p = subparsers.add_parser(
        "add-pair", parents=[common], help="Add a new sync pair to the config"
    )
    add_pair_p.add_argument("--name", help="Pair name")
    add_pair_p.add_argument("--local", help="Local (laptop) folder")
    add_pair_p.add_argument("--remote", help="Remote (phone) folder, must start with /")
    add_pair_p.add_argument("--direction", choices=DIRECTIONS, help="Sync direction")
    add_pair_p.add_argument("--conflict", choices=CONFLICT_POLICIES, help="Conflict policy (default: newest-wins)")
    add_pair_p.add_argument(
        "--transport", choices=TRANSPORT_MODES, help="How to reach the phone: adb (default) or agent (the phone app, no debugging)"
    )
    add_pair_p.add_argument(
        "--propagate-deletes",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Propagate deletes (default: on, off for phone-to-laptop)",
    )
    add_pair_p.set_defaults(func=cmd_add_pair)

    edit_pair_p = subparsers.add_parser(
        "edit-pair", parents=[common], help="Change fields of an existing sync pair"
    )
    edit_pair_p.add_argument("name", help="Pair name")
    edit_pair_p.add_argument("--rename", help="New name for the pair")
    edit_pair_p.add_argument("--local", help="New local (laptop) folder")
    edit_pair_p.add_argument("--remote", help="New remote (phone) folder, must start with /")
    edit_pair_p.add_argument("--direction", choices=DIRECTIONS, help="New sync direction")
    edit_pair_p.add_argument("--conflict", choices=CONFLICT_POLICIES, help="New conflict policy")
    edit_pair_p.add_argument("--transport", choices=TRANSPORT_MODES, help="New way to reach the phone: adb or agent")
    edit_pair_p.add_argument(
        "--propagate-deletes", action=argparse.BooleanOptionalAction, default=None, help="New propagate_deletes"
    )
    edit_pair_p.add_argument(
        "--enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable (pause) the pair; --no-enabled is what `androsync undo --pause` sets",
    )
    edit_pair_p.set_defaults(func=cmd_edit_pair)

    remove_pair_p = subparsers.add_parser("remove-pair", parents=[common], help="Remove a sync pair")
    remove_pair_p.add_argument("name", help="Pair name")
    remove_pair_p.add_argument(
        "--purge-state", action="store_true", help="Also delete the pair's saved snapshot/history"
    )
    remove_pair_p.set_defaults(func=cmd_remove_pair)

    resolve_p = subparsers.add_parser(
        "resolve", parents=[common], help="Resolve pending conflict: ask entries, per file"
    )
    resolve_p.add_argument("pair", nargs="?", help="Pair name (default: all pairs)")
    resolve_p.set_defaults(func=cmd_resolve)
