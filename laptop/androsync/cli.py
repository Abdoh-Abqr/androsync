"""Command-line entry point for androsync (argparse-based).

Subcommands implemented so far: ``devices``, ``ls``, ``status``, ``run``,
``serve``, ``service`` (install/start/stop/restart/status/logs/uninstall),
``qr``, ``add-pair`` and ``resolve``.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from androsync import __version__, config_edit, service, service_state, undo, versioning
from androsync.runlog import RunLog, tee as _tee_log
from androsync.cli_conflicts import add_conflicts_commands
from androsync.cli_pairs import add_pair_commands
from androsync.config import Config, ConfigError, PairConfig, default_config_path, load_config
from androsync.daemon.watcher import Daemon
from androsync.executor import ExecutorError, run_pair
from androsync.model import Side
from androsync.service import ServiceError
from androsync.transport.adb import (
    DEVICE_STATE_HINTS,
    NO_DEVICE_HINT,
    Adb,
    AdbError,
    AdbTransport,
    list_devices,
    resolve_device,
)
from androsync.transport.local import LocalDirTransport

LogFn = Callable[[str], None]


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands attached.

    ``--config``/``--verbose`` are defined twice on purpose: once on the
    top-level parser (real defaults: ``None``/``False``) so
    ``androsync --config PATH status`` works, and once on ``common`` - added
    as a parent to every subparser, with ``argparse.SUPPRESS`` defaults - so
    ``androsync status --config PATH`` also works *without* the subparser's
    own (otherwise unset) default clobbering whatever the top-level parser
    already put in the namespace.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        type=Path,
        default=argparse.SUPPRESS,
        metavar="PATH",
        help="Path to config.yaml (default: $XDG_CONFIG_HOME/androsync/config.yaml)",
    )
    common.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print adb commands as they run",
    )

    parser = argparse.ArgumentParser(
        prog="androsync",
        description="Sync folder pairs between an Ubuntu laptop and an Android phone.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to config.yaml (default: $XDG_CONFIG_HOME/androsync/config.yaml)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Print adb commands as they run",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"androsync {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")
    # Stored on the parser so later tasks can add subcommands without
    # re-deriving the subparsers action.
    parser._subparsers_action = subparsers  # type: ignore[attr-defined]

    devices_p = subparsers.add_parser(
        "devices", parents=[common], help="List devices seen by `adb devices -l`"
    )
    devices_p.set_defaults(func=cmd_devices)

    ls_p = subparsers.add_parser("ls", parents=[common], help="List a directory on the phone")
    ls_p.add_argument("path", nargs="?", default="/sdcard", help="Remote path (default: /sdcard)")
    ls_p.set_defaults(func=cmd_ls)

    status_p = subparsers.add_parser(
        "status", parents=[common], help="Dry run: show what a sync would do"
    )
    status_p.add_argument("pairs", nargs="*", metavar="PAIR", help="Pair names (default: all)")
    status_p.set_defaults(func=cmd_status)

    run_p = subparsers.add_parser("run", parents=[common], help="Sync pairs")
    run_p.add_argument("pairs", nargs="*", metavar="PAIR", help="Pair names (default: all)")
    run_p.add_argument("--dry-run", action="store_true", help="Show the plan and stop")
    run_p.add_argument("--yes", action="store_true", help="Do not prompt for confirmation")
    run_p.add_argument(
        "--confirm-large",
        action="store_true",
        help="Also go ahead with a plan that deletes many files or copies a huge first run (--yes does not cover that)",
    )
    run_p.set_defaults(func=cmd_run)

    history_p = subparsers.add_parser(
        "history", parents=[common], help="Show a `versioning: git` pair's commit history"
    )
    history_p.add_argument("pair", metavar="PAIR", help="Pair name")
    history_p.add_argument("-n", type=int, default=20, metavar="N", help="Number of commits to show (default: 20)")
    history_p.set_defaults(func=cmd_history)

    runs_p = subparsers.add_parser("runs", parents=[common], help="List a pair's undo-able sync runs")
    runs_p.add_argument("pair", metavar="PAIR", help="Pair name")
    runs_p.set_defaults(func=cmd_runs)

    undo_p = subparsers.add_parser("undo", parents=[common], help="Undo a pair's last sync run (or a specific one)")
    undo_p.add_argument("pair", metavar="PAIR", help="Pair name")
    undo_p.add_argument("--run", dest="run_id", metavar="RUN_ID", help="Run to undo (default: the most recent)")
    undo_p.add_argument("--dry-run", action="store_true", help="Show what would be restored and stop")
    undo_p.add_argument(
        "--force", action="store_true", help="Undo files modified since the run too (default: skip them)"
    )
    undo_p.add_argument("--pause", action="store_true", help="Disable the pair afterwards (status/run skip it)")
    undo_p.set_defaults(func=cmd_undo)

    serve_p = subparsers.add_parser(
        "serve", parents=[common], help="Run the foreground daemon (what the systemd service runs)"
    )
    serve_p.set_defaults(func=cmd_serve)

    gui_p = subparsers.add_parser(
        "gui", parents=[common], help="Open the desktop app (service control, pairs, log) - task 31b"
    )
    gui_p.set_defaults(func=cmd_gui)

    pair_wifi_p = subparsers.add_parser(
        "pair-wifi", parents=[common], help="Pair and connect to the phone over Wi-Fi (Android 11+ Wireless debugging)"
    )
    pair_wifi_p.add_argument("--pairing-address", help="Pairing ip:port shown on the phone (non-interactive)")
    pair_wifi_p.add_argument("--pairing-code", help="Pairing code shown on the phone (non-interactive)")
    pair_wifi_p.add_argument(
        "--connect-address", help="Connect ip:port (non-interactive; default: detected from `tailscale status`)"
    )
    pair_wifi_p.add_argument("--yes", action="store_true", help="Write the config without prompting")
    pair_wifi_p.set_defaults(func=cmd_pair_wifi)

    service.add_service_commands(subparsers, common)
    add_pair_commands(subparsers, common)
    add_conflicts_commands(subparsers, common)

    return parser


def _load_config(args: argparse.Namespace) -> Config:
    """Load the config named by ``--config``, turning a missing file into ``ConfigError``."""
    try:
        return load_config(args.config)
    except FileNotFoundError as exc:
        path = args.config if args.config is not None else exc.filename
        raise ConfigError(f"config file not found: {path}") from exc


def _select_pairs(config: Config, names: list[str]) -> list[PairConfig]:
    """Return the requested pairs in config order, or all pairs if ``names`` is empty."""
    if not names:
        return list(config.pairs)
    by_name = {pair.name: pair for pair in config.pairs}
    selected = []
    for name in names:
        pair = by_name.get(name)
        if pair is None:
            known = ", ".join(sorted(by_name)) or "(none configured)"
            raise ConfigError(f"unknown pair {name!r}; known pairs: {known}")
        selected.append(pair)
    return selected


def _skip_disabled(pairs: list[PairConfig], requested_names: list[str]) -> list[PairConfig]:
    """Drop ``enabled: false`` pairs from an implicit "all pairs" selection (Phase I `--pause`).

    A pair named explicitly on the command line always runs regardless of
    its ``enabled`` flag - typing its name is itself an explicit override.
    Only the "no names given = every pair" case skips paused ones, printing
    a note for each so the omission is not silent.
    """
    if requested_names:
        return pairs
    kept = []
    for pair in pairs:
        if pair.enabled:
            kept.append(pair)
        else:
            print(
                f"== {pair.name} == skipped: paused "
                f"(`androsync run {pair.name}` runs it anyway; `androsync edit-pair {pair.name} --enabled` un-pauses it)"
            )
    return kept


AGENT_PAIR_HINT = (
    "uses the phone app's agent, which only the running service can talk to: "
    "start it from the app, the window, or `androsync serve`"
)


def _skip_agent(pairs: list[PairConfig]) -> list[PairConfig]:
    """Drop pairs set to ``transport: agent`` (task 37d): the command line has no connection to the phone app."""
    kept = []
    for pair in pairs:
        if pair.transport == "agent":
            print(f"== {pair.name} == skipped: {AGENT_PAIR_HINT}")
        else:
            kept.append(pair)
    return kept


def _make_log(args: argparse.Namespace) -> LogFn | None:
    if getattr(args, "verbose", False):
        return lambda msg: print(f"+ {msg}")
    return None


def open_pair_transports(
    config: Config, *, log: LogFn | None = None
) -> tuple[LocalDirTransport, AdbTransport]:
    """Resolve the configured device and build the (laptop, phone) transport pair."""
    serial = resolve_device(config.device, wifi_target=config.wifi_target)
    adb = Adb(serial=serial, log=log)
    return LocalDirTransport(Side.LOCAL), AdbTransport(adb)


def _human_size(size: int) -> str:
    if size < 0:
        return "?"
    value = float(size)
    for unit in ("B", "K", "M", "G", "T"):
        if value < 1024 or unit == "T":
            return f"{int(value)}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}T"


# ---------------------------------------------------------------------------
# devices
# ---------------------------------------------------------------------------


def cmd_devices(args: argparse.Namespace) -> int:
    config = _load_config(args)
    devices = list_devices()

    picked: str | None = None
    reason = ""
    try:
        picked = resolve_device(config.device, wifi_target=config.wifi_target)
        info = next((d for d in devices if d.serial == picked), None)
        reason = f"{info.transport}, already connected" if info is not None else "wifi, connected now"
    except AdbError as exc:
        reason = str(exc)

    if not devices:
        print("No devices found (`adb devices -l` reported none).")
        print(NO_DEVICE_HINT)
    else:
        print(f"{'':2}{'SERIAL':<24}{'STATE':<14}{'MODEL':<20}{'TRANSPORT':<10}")
        for device in devices:
            mark = "*" if device.serial == picked else " "
            model = device.model or "-"
            print(f"{mark:<2}{device.serial:<24}{device.state:<14}{model:<20}{device.transport:<10}")

    for device in devices:
        hint = DEVICE_STATE_HINTS.get(device.state)
        if hint:
            print(f"{device.serial} is {device.state}: {hint}")

    print()
    if picked:
        print(f"picked: {picked} ({reason})")
    else:
        print(f"no device would be picked: {reason}")
    return 0


# ---------------------------------------------------------------------------
# ls
# ---------------------------------------------------------------------------


def cmd_ls(args: argparse.Namespace) -> int:
    config = _load_config(args)
    log = _make_log(args)
    serial = resolve_device(config.device, wifi_target=config.wifi_target)
    transport = AdbTransport(Adb(serial=serial, log=log))

    entries = transport.ls(args.path)
    entries.sort(key=lambda entry: (not entry[1], entry[0].lower()))

    for name, is_dir, size in entries:
        if is_dir:
            print(f"{'':>10}  {name}/")
        else:
            print(f"{_human_size(size):>10}  {name}")
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    config = _load_config(args)
    log = _make_log(args)
    pairs = _select_pairs(config, args.pairs)
    pairs = _skip_agent(_skip_disabled(pairs, args.pairs))

    if not pairs:
        print("nothing to do")
        return 0
    local, remote = open_pair_transports(config, log=log)

    for pair in pairs:
        print(f"== {pair.name} ==")
        lines: list[str] = []
        summary = run_pair(pair, local, remote, dry_run=True, log=lines.append)
        if lines:
            for line in lines:
                print(line)
        else:
            print("nothing to do")
        print(summary)
    return 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    config = _load_config(args)
    run_log = RunLog()
    try:
        verbose_log = _make_log(args)
        adb_log = _tee_log(verbose_log, run_log) if verbose_log is not None else None
        pairs = _select_pairs(config, args.pairs)
        pairs = _skip_agent(_skip_disabled(pairs, args.pairs))

        if not pairs:
            print("nothing to do")
            return 0
        local, remote = open_pair_transports(config, log=adb_log)

        any_action = False
        needs_confirmation: list[tuple[str, list[str]]] = []
        for pair in pairs:
            print(f"== {pair.name} ==")
            lines: list[str] = []
            dry_summary = run_pair(pair, local, remote, dry_run=True, log=lines.append)
            if dry_summary.needs_confirmation:
                needs_confirmation.append((pair.name, dry_summary.confirmation))
            if lines:
                any_action = True
                for line in lines:
                    print(line)
            else:
                print("nothing to do")

        if not any_action:
            return 0

        if args.dry_run:
            return 0

        confirmed_large = args.confirm_large
        if needs_confirmation and not confirmed_large:
            print("\nThis is a large change and needs your explicit confirmation:")
            for name, problems in needs_confirmation:
                for problem in problems:
                    print(f"  {name}: {problem}")
            if args.yes:
                print("androsync: --yes does not cover a large change; rerun with --confirm-large", file=sys.stderr)
                return 1
            if input("Type 'yes' to go ahead: ").strip().lower() != "yes":
                print("aborted")
                return 0
            confirmed_large = True
        elif not args.yes:
            answer = input("Proceed? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("aborted")
                return 0

        had_issue = False
        for pair in pairs:
            print(f"== {pair.name} ==")
            summary = run_pair(
                pair, local, remote, dry_run=False, log=_tee_log(print, run_log), confirm_large=confirmed_large
            )
            print(summary)
            if summary.asks or summary.errors:
                had_issue = True

        return 1 if had_issue else 0
    finally:
        run_log.close()


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def cmd_history(args: argparse.Namespace) -> int:
    config = _load_config(args)
    pairs = _select_pairs(config, [args.pair])
    pair = pairs[0]

    if pair.versioning != "git":
        print(f"androsync: pair {pair.name!r} does not have versioning: git enabled", file=sys.stderr)
        return 2

    try:
        log_output = versioning.history(pair.local, limit=args.n)
    except versioning.VersioningError as exc:
        print(f"androsync: {exc}", file=sys.stderr)
        return 2

    if not log_output.strip():
        print("no commits yet" if (pair.local / ".git").exists() else "not a git repo yet - run a sync first")
        return 0

    print(log_output, end="")
    return 0


# ---------------------------------------------------------------------------
# runs / undo
# ---------------------------------------------------------------------------


def cmd_runs(args: argparse.Namespace) -> int:
    config = _load_config(args)
    pairs = _select_pairs(config, [args.pair])
    pair = pairs[0]

    runs = undo.list_runs(pair.name)
    if not runs:
        print("no runs recorded yet")
        return 0

    for run in runs:
        summary = run.get("summary") or {}
        parts = [f"{key}={value}" for key, value in summary.items() if key != "run_id" and value]
        summary_str = ", ".join(parts) if parts else "no changes"
        status = "done" if run.get("finished") else "unfinished (crashed or cancelled)"
        kind = run.get("kind", "sync")
        started = run.get("started", "")
        print(f"{run['run_id']}  {kind:5}  {started:26}  {status:32}  {summary_str}")
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    config = _load_config(args)
    pairs = _select_pairs(config, [args.pair])
    pair = pairs[0]
    if pair.transport == "agent":
        print(f"androsync: pair {pair.name!r} {AGENT_PAIR_HINT}", file=sys.stderr)
        return 2

    run_id = args.run_id
    if run_id is None:
        runs = undo.list_runs(pair.name)
        if not runs:
            print(f"androsync: no runs recorded for pair {pair.name!r}", file=sys.stderr)
            return 2
        run_id = runs[0]["run_id"]

    run_log = RunLog()
    try:
        verbose_log = _make_log(args)
        adb_log = _tee_log(verbose_log, run_log) if verbose_log is not None else None
        local, remote = open_pair_transports(config, log=adb_log)

        try:
            summary = undo.undo_run(
                pair, run_id, local, remote, dry_run=args.dry_run, force=args.force, log=_tee_log(print, run_log)
            )
        except undo.UndoError as exc:
            print(f"androsync: {exc}", file=sys.stderr)
            return 2

        print(f"Restored {summary.restored_local} file(s) on laptop, {summary.restored_remote} on phone.")
        if summary.skipped:
            print(f"Skipped {len(summary.skipped)} file(s) modified after the sync: {', '.join(summary.skipped)}")
        if not args.dry_run:
            print("The next run would repeat the same sync, so change the files or the pair settings first.")

        if args.pause and not args.dry_run:
            config_path = args.config if getattr(args, "config", None) else default_config_path()
            raw = config_edit.load_raw(config_path)
            current = next((p for p in raw.get("pairs") or [] if p.get("name") == pair.name), None)
            pair_dict = dict(current) if current is not None else {"name": pair.name}
            pair_dict["enabled"] = False
            try:
                new_raw = config_edit.update_pair(raw, pair.name, pair_dict)
                config_edit.save_raw(config_path, new_raw)
                print(f"paused {pair.name!r} - `androsync edit-pair {pair.name} --enabled` to resume")
            except ConfigError as exc:
                print(f"androsync: {exc}", file=sys.stderr)
                return 2

        return 1 if summary.errors else 0
    finally:
        run_log.close()


# ---------------------------------------------------------------------------
# pair-wifi
# ---------------------------------------------------------------------------


def _connect_failed(result: subprocess.CompletedProcess, output: str) -> bool:
    """Same heuristic ``transport.adb.resolve_device`` uses for a `wifi:` device.

    ``adb connect`` can exit 0 while still printing a failure message.
    """
    return result.returncode != 0 or "unable to connect" in output.lower() or "failed to connect" in output.lower()


def _detect_android_tailscale_ip() -> str | None:
    """The IPv4 of an Android peer in ``tailscale status --json``, or None.

    Used only to suggest a default for ``pair-wifi``'s connect-address
    prompt (task 33e: no address of any particular machine or network is
    built into androsync - everything it offers is detected from whatever
    is actually running on *this* machine, or typed by the user).
    """
    if shutil.which("tailscale") is None:
        return None
    try:
        result = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
    except Exception:  # noqa: BLE001 - this is best-effort, never fatal
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    for peer in (data.get("Peer") or {}).values():
        if not isinstance(peer, dict) or peer.get("OS") != "android":
            continue
        for ip in peer.get("TailscaleIPs") or []:
            if ":" not in ip:  # the first/IPv4 entry; skip the IPv6 one
                return ip
    return None


def cmd_pair_wifi(args: argparse.Namespace) -> int:
    """Walk through Android 11+ Wireless debugging pairing, then offer to save ``device: wifi:...``."""
    log: LogFn = print
    config_path = args.config if getattr(args, "config", None) else default_config_path()

    pairing_address = args.pairing_address
    if not pairing_address:
        pairing_address = input(
            "Pairing ip:port (phone: Developer options > Wireless debugging > Pair device with pairing code): "
        ).strip()
    pairing_code = args.pairing_code or input("Pairing code shown on the phone: ").strip()
    if not pairing_address or not pairing_code:
        log("androsync: pairing ip:port and code are required")
        return 2

    log(f"+ adb pair {pairing_address} <code>")
    try:
        pair_result = subprocess.run(
            ["adb", "pair", pairing_address, pairing_code], capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError as exc:
        log(f"androsync: adb executable not found: {exc}")
        return 2
    except subprocess.TimeoutExpired:
        log("androsync: `adb pair` timed out")
        return 2
    pair_output = f"{pair_result.stdout or ''}{pair_result.stderr or ''}".strip()
    if pair_result.returncode != 0:
        log(f"androsync: adb pair failed: {pair_output}")
        return 2
    log(pair_output)

    connect_address = args.connect_address
    if not connect_address:
        suggested_ip = _detect_android_tailscale_ip()
        if suggested_ip:
            default_connect = f"{suggested_ip}:5555"
            raw = input(f"Connect ip:port [{default_connect}]: ").strip()
            connect_address = raw or default_connect
        else:
            connect_address = input("Connect ip:port (shown on the phone, or its Tailscale IP): ").strip()
        if not connect_address:
            log("androsync: connect ip:port is required")
            return 2

    log(f"+ adb connect {connect_address}")
    try:
        connect_result = subprocess.run(["adb", "connect", connect_address], capture_output=True, text=True, timeout=30)
    except FileNotFoundError as exc:
        log(f"androsync: adb executable not found: {exc}")
        return 2
    except subprocess.TimeoutExpired:
        log("androsync: `adb connect` timed out")
        return 2
    connect_output = f"{connect_result.stdout or ''}{connect_result.stderr or ''}".strip()
    if _connect_failed(connect_result, connect_output):
        log(f"androsync: adb connect {connect_address} failed: {connect_output}")
        return 2
    log(connect_output)

    if not args.yes:
        answer = input(
            f"Write device: auto + wifi_target: {connect_address} into the config? [Y/n] "
        ).strip().lower()
        if answer not in ("", "y", "yes"):
            log("not written; run again or edit the config by hand")
            return 0

    raw = config_edit.load_raw(config_path)
    # device: auto (task 32b), not device: wifi:<address> - that form always ran
    # `adb connect` even with the phone sitting right there on the USB cable,
    # which failed outright whenever the phone's Wireless-debugging port had
    # reset (it does not survive a reboot). `auto` + `wifi_target` prefers
    # whatever is actually connected and only falls back to this address.
    raw["device"] = "auto"
    raw["wifi_target"] = connect_address
    try:
        config_edit.save_raw(config_path, raw)
    except ConfigError as exc:
        log(f"androsync: {exc}")
        return 2

    log(f"wrote device: auto, wifi_target: {connect_address} to {config_path}")
    log("restart the service (`androsync service restart`) for the daemon to pick it up")
    return 0


def setup_logging(*, verbose: bool = False, file: bool = False) -> None:
    """Configure the root logger: stderr always, plus a rotating daemon.log when ``file``.

    Shared by ``serve`` and (task 31b) the GUI's in-process daemon, so a
    daemon started either way can be followed the same way -
    `androsync service logs`-style via journald for the systemd unit, or by
    tailing :func:`androsync.service_state.daemon_log_path` for the other two.
    """
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(fmt)
    root.addHandler(stream_handler)

    if file:
        from logging.handlers import RotatingFileHandler

        path = service_state.daemon_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)


def cmd_serve(args: argparse.Namespace) -> int:
    config_path = args.config if getattr(args, "config", None) else default_config_path()
    config = _load_config(args)

    if config.service.token:
        existing = service_state.ping(config.service.port, config.service.token)
        if existing is not None:
            print(
                f"androsync: port {config.service.port} already answers as androsync "
                f"(device {existing.get('device')!r}, transport {existing.get('transport')!r}) "
                "- not starting a second instance",
                file=sys.stderr,
            )
            return 3

    # Only touch global logging state once we are actually about to run - the
    # double-start refusal above must stay side-effect-free.
    setup_logging(verbose=getattr(args, "verbose", False), file=True)

    daemon = Daemon(lambda: _load_config(args), __version__, config_path=config_path)
    daemon.start()
    daemon.run_forever()
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from androsync.gui.app import main as gui_main
    except ImportError:
        print("androsync: tkinter is not installed - try `sudo apt install python3-tk`", file=sys.stderr)
        return 2
    return gui_main(__version__)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the selected subcommand."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "command", None) is None:
        parser.print_help()
        return 0

    try:
        return args.func(args)
    except (ConfigError, AdbError, ExecutorError, ServiceError) as exc:
        print(f"androsync: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
