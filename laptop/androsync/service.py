"""``androsync service`` (install/start/stop/restart/status/logs/uninstall) and ``androsync qr``.

``install`` writes ``~/.config/androsync/config.yaml`` (creating a minimal
one if none exists yet), generates ``service.token`` if it is missing,
optionally adds the machine's Tailscale IP to ``service.listen``, writes
the systemd user unit and enables it, and optionally opens the matching
ufw hole on ``tailscale0``. The other ``service`` subcommands are thin
wrappers around ``systemctl --user``/``journalctl --user``. ``qr`` prints
the token and every configured URL, rendered as an ASCII QR code when the
optional ``qrcode`` package is installed.

Every external command this module might run (``systemctl``, ``loginctl``,
``sudo ufw``, ``tailscale ip -4``) is printed before it runs, and
``--dry-run`` on ``install``/``uninstall`` prints without running anything
or touching disk.
"""

from __future__ import annotations

import argparse
import getpass
import importlib.resources
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

import yaml

from androsync.config import Config, ConfigError, default_config_path, load_config

LogFn = Callable[[str], None]

UNIT_NAME = "androsync.service"

# Verbatim from PLAN.md's "Service start/stop hints" section - also printed
# by `androsync service install` and copied into the README.
SERVICE_HINTS = """\
androsync service install                # writes ~/.config/systemd/user/androsync.service; starts nothing
androsync service install --autostart    # also enables it and runs `loginctl enable-linger $USER`
                                          # so it starts at boot, not only at login
androsync service status                 # prints whether/how it's running (systemd, another process, or not)
androsync service stop                   # stop now (only matters if it's running as the background service)
androsync service start                  # start now, in the background, without enabling autostart
androsync service autostart on|off       # enable/disable "start at boot" without touching whether it's
                                          # running right now
androsync service uninstall              # = systemctl --user disable --now androsync; removes the unit file
androsync service logs                   # = journalctl --user -u androsync -f
androsync service install-launcher       # adds "AndroSync" to the Applications menu (`androsync gui`)
androsync service uninstall-launcher     # removes it
androsync gui                            # the desktop app itself: service control, pairs, log"""

SERVICE_HINTS_FOOTER = (
    "Switch off autostart but keep it installed: `androsync service autostart off`.\n"
    "Pause auto-sync only: set `service.auto_sync_on_connect: false`, then `androsync service restart`."
)

UNIT_TEMPLATE = """\
[Unit]
Description=androsync - USB/Wi-Fi folder sync daemon
After=default.target network-online.target

[Service]
ExecStart={exec_start} serve
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


class ServiceError(Exception):
    """Raised for a bad --listen value or another install-time problem."""


def _systemd_unit_path() -> Path:
    """``$XDG_CONFIG_HOME/systemd/user/androsync.service`` (default: ``~/.config/...``)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "systemd" / "user" / UNIT_NAME


def _androsync_executable() -> str:
    """The absolute path systemd should exec: the running script, or `which androsync`."""
    argv0 = Path(sys.argv[0])
    try:
        resolved = argv0.resolve()
    except OSError:
        resolved = argv0
    if resolved.exists():
        return str(resolved)
    found = shutil.which("androsync")
    if found:
        return str(Path(found).resolve())
    return str(resolved)


def render_unit(exec_start: str) -> str:
    """Render the systemd user unit file content for ``exec_start``."""
    return UNIT_TEMPLATE.format(exec_start=exec_start)


def _run(argv: list[str], *, log: LogFn, dry_run: bool, check: bool = True, capture: bool = False):
    """Print ``argv``, then (unless ``dry_run``) run it. Returns None when dry-run."""
    log("+ " + " ".join(argv))
    if dry_run:
        return None
    return subprocess.run(argv, check=check, capture_output=capture, text=True)


def _is_placeholder_token(token: str) -> bool:
    """True for the example config's ``<generated on first ...>`` placeholder.

    That literal string is published in ``config.example.yaml`` (and this
    module's docstrings), so treating it as "already set" would deploy a
    publicly-known bearer token. Any value shaped like ``<...>`` is treated
    as unset rather than pattern-matching the exact placeholder text, so a
    future wording change here still gets caught.
    """
    stripped = token.strip()
    return stripped.startswith("<") and stripped.endswith(">")


def _as_listen_list(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    return list(value or [])


def _load_raw_config(config_path: Path, *, log: LogFn, dry_run: bool) -> dict:
    """Load the YAML config as a plain dict, creating a minimal one if missing.

    Under ``dry_run`` nothing is written to disk; a fresh in-memory
    ``{"service": {}}`` is returned instead so the rest of ``install`` can
    proceed exactly as it would for real.
    """
    if not config_path.exists():
        log(f"no config found; creating a minimal one at {config_path}")
        raw: dict = {"service": {}}
        if not dry_run:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(yaml.safe_dump(raw, sort_keys=False))
        return raw
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _detect_tailscale_ip(*, log: LogFn) -> str | None:
    """Return the machine's Tailscale IPv4 address, or None if unavailable."""
    if shutil.which("tailscale") is None:
        return None
    result = _run(["tailscale", "ip", "-4"], log=log, dry_run=False, check=False, capture=True)
    if result is None or result.returncode != 0:
        return None
    stdout = (result.stdout or "").strip()
    if not stdout:
        return None
    return stdout.splitlines()[0].strip()


def _want_tailscale(tailscale_flag: bool | None, tailscale_ip: str, *, isatty: bool = None) -> bool:
    """Decide whether to add ``tailscale_ip`` to ``service.listen``.

    ``--tailscale``/``--no-tailscale`` decide outright; with neither given,
    ask interactively in a TTY (default no), and default to no otherwise.
    """
    if tailscale_flag is True:
        return True
    if tailscale_flag is False:
        return False
    tty = sys.stdin.isatty() if isatty is None else isatty
    if not tty:
        return False
    answer = input(f"Also listen on Tailscale IP {tailscale_ip}? [y/N] ").strip().lower()
    return answer in ("y", "yes")


# ---------------------------------------------------------------------------
# service install
# ---------------------------------------------------------------------------


def cmd_service_install(args: argparse.Namespace) -> int:
    log: LogFn = print
    dry_run = bool(getattr(args, "dry_run", False))
    config_path = args.config if getattr(args, "config", None) else default_config_path()

    for addr in args.listen:
        if addr == "0.0.0.0":
            raise ServiceError("refusing to bind 0.0.0.0 - list specific addresses instead")

    raw = _load_raw_config(config_path, log=log, dry_run=dry_run)
    service_raw = dict(raw.get("service") or {})

    token = service_raw.get("token")
    if not token or _is_placeholder_token(token):
        token = secrets.token_urlsafe(32)
        service_raw["token"] = token
        log("generated a new service.token")
    else:
        log("service.token already set, keeping it")

    listen = _as_listen_list(service_raw.get("listen") or ["127.0.0.1"])
    for addr in args.listen:
        if addr not in listen:
            listen.append(addr)

    tailscale_ip = _detect_tailscale_ip(log=log)
    added_tailscale = False
    if tailscale_ip:
        if _want_tailscale(args.tailscale, tailscale_ip):
            if tailscale_ip not in listen:
                listen.append(tailscale_ip)
            added_tailscale = True
    elif args.tailscale:
        log("--tailscale given but `tailscale ip -4` did not return an address; skipping")

    if "0.0.0.0" in listen:
        raise ServiceError("refusing to bind 0.0.0.0 - list specific addresses instead")

    service_raw["listen"] = listen
    port = int(service_raw.get("port", 8765))
    raw["service"] = service_raw

    log(f"+ write {config_path}")
    if not dry_run:
        config_path.write_text(yaml.safe_dump(raw, sort_keys=False))

    exec_start = _androsync_executable()
    unit_path = _systemd_unit_path()
    unit_content = render_unit(exec_start)
    log(f"+ write {unit_path}")
    if not dry_run:
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(unit_content)

    _run(["systemctl", "--user", "daemon-reload"], log=log, dry_run=dry_run)
    if args.autostart:
        _run(["systemctl", "--user", "enable", "--now", UNIT_NAME], log=log, dry_run=dry_run)
        _run(["loginctl", "enable-linger", getpass.getuser()], log=log, dry_run=dry_run)
    else:
        log(
            "not starting or enabling autostart (pass --autostart, or run "
            "`androsync service start` / `androsync service autostart on` later)"
        )

    if added_tailscale:
        ufw_display = (
            f"sudo ufw allow in on tailscale0 to any port {port} proto tcp comment 'androsync'"
        )
        log(ufw_display)
        if args.ufw:
            result = _run(
                [
                    "sudo", "ufw", "allow", "in", "on", "tailscale0",
                    "to", "any", "port", str(port), "proto", "tcp", "comment", "androsync",
                ],
                log=lambda _msg: None,
                dry_run=dry_run,
                check=False,
            )
            if result is not None and result.returncode != 0:
                log(f"WARNING: the ufw command above failed (exit {result.returncode}); run it yourself")
        else:
            log("(pass --ufw to run this automatically, or run it yourself)")
    else:
        log("no firewall rule needed (localhost/USB only)")

    log("")
    log(SERVICE_HINTS)
    log("")
    log(SERVICE_HINTS_FOOTER)
    log("")
    log(f"Token: {token}")
    log(f"URL (USB):       http://127.0.0.1:{port}")
    if added_tailscale:
        log(f"URL (Tailscale): http://{tailscale_ip}:{port}")

    return 0


# ---------------------------------------------------------------------------
# service start/stop/restart/status/logs/uninstall
# ---------------------------------------------------------------------------


def _systemctl_action(action: str, args: argparse.Namespace) -> int:
    log: LogFn = print
    dry_run = bool(getattr(args, "dry_run", False))
    result = _run(["systemctl", "--user", action, UNIT_NAME], log=log, dry_run=dry_run, check=False)
    if dry_run or result is None:
        return 0
    return result.returncode


def cmd_service_start(args: argparse.Namespace) -> int:
    return _systemctl_action("start", args)


def cmd_service_stop(args: argparse.Namespace) -> int:
    return _systemctl_action("stop", args)


def cmd_service_restart(args: argparse.Namespace) -> int:
    return _systemctl_action("restart", args)


def cmd_service_status(args: argparse.Namespace) -> int:
    """Print the detected run state (task 31a). Exit 0 if running, 3 if not."""
    from androsync import service_state

    config_path = args.config if getattr(args, "config", None) else default_config_path()
    try:
        config = load_config(config_path)
    except (ConfigError, OSError):
        config = None

    state = service_state.detect_state(config)
    if state.mode == "systemd":
        autostart = "on" if state.autostart else "off"
        since = f" (running since {state.since})" if state.since else ""
        print(f"running as the background service, autostart {autostart}{since}")
    elif state.mode == "inprocess":
        print("running in this process")
    elif state.mode == "other":
        print(f"running in another process (pid {state.pid})")
    else:
        autostart = "on" if state.autostart else "off"
        print(f"not running (autostart {autostart})")

    if state.ping is not None:
        print(f"device: {state.ping.get('device')!r}, transport: {state.ping.get('transport')!r}")

    return 0 if state.running else 3


def cmd_service_autostart(args: argparse.Namespace) -> int:
    """``androsync service autostart on|off`` (task 31a)."""
    from androsync import service_state

    log: LogFn = print
    dry_run = bool(getattr(args, "dry_run", False))
    on = args.autostart_state == "on"
    if dry_run:
        action = "enable" if on else "disable"
        linger_action = "enable-linger" if on else "disable-linger"
        log("+ systemctl --user " + action + " " + UNIT_NAME)
        log("+ loginctl " + linger_action + " " + getpass.getuser())
        return 0
    service_state.set_autostart(on, log=log)
    return 0


def cmd_service_logs(args: argparse.Namespace) -> int:
    log: LogFn = print
    dry_run = bool(getattr(args, "dry_run", False))
    cmd = ["journalctl", "--user", "-u", UNIT_NAME, "-f"]
    log("+ " + " ".join(cmd))
    if dry_run:
        return 0
    result = subprocess.run(cmd, check=False)
    return result.returncode


def cmd_service_uninstall(args: argparse.Namespace) -> int:
    log: LogFn = print
    dry_run = bool(getattr(args, "dry_run", False))

    _run(["systemctl", "--user", "disable", "--now", UNIT_NAME], log=log, dry_run=dry_run, check=False)

    unit_path = _systemd_unit_path()
    log(f"+ rm {unit_path}")
    if not dry_run and unit_path.exists():
        unit_path.unlink()

    if args.ufw:
        port = 8765
        try:
            config_path = args.config if getattr(args, "config", None) else default_config_path()
            config: Config = load_config(config_path)
            port = config.service.port
        except (ConfigError, OSError):
            pass
        ufw_display = f"sudo ufw delete allow in on tailscale0 to any port {port} proto tcp comment 'androsync'"
        log(ufw_display)
        _run(
            [
                "sudo", "ufw", "delete", "allow", "in", "on", "tailscale0",
                "to", "any", "port", str(port), "proto", "tcp", "comment", "androsync",
            ],
            log=lambda _msg: None,
            dry_run=dry_run,
            check=False,
        )

    return 0


# ---------------------------------------------------------------------------
# install-launcher / uninstall-launcher (task 31c)
# ---------------------------------------------------------------------------

DESKTOP_FILE_TEMPLATE = """\
[Desktop Entry]
Type=Application
Name=AndroSync
Comment=USB/Wi-Fi folder sync between this laptop and an Android phone
Exec={exec_start} gui
Icon=androsync
Terminal=false
Categories=Utility;
"""


def _xdg_data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def _desktop_file_path() -> Path:
    return _xdg_data_home() / "applications" / "androsync.desktop"


def _icon_file_path() -> Path:
    return _xdg_data_home() / "icons" / "hicolor" / "scalable" / "apps" / "androsync.svg"


def render_desktop_file(exec_start: str) -> str:
    return DESKTOP_FILE_TEMPLATE.format(exec_start=exec_start)


def cmd_service_install_launcher(args: argparse.Namespace) -> int:
    """Write an Applications-menu entry (`androsync gui`) and its icon."""
    log: LogFn = print
    dry_run = bool(getattr(args, "dry_run", False))

    exec_start = _androsync_executable()
    desktop_path = _desktop_file_path()
    log(f"+ write {desktop_path}")
    if not dry_run:
        desktop_path.parent.mkdir(parents=True, exist_ok=True)
        desktop_path.write_text(render_desktop_file(exec_start))

    icon_path = _icon_file_path()
    icon_content = (
        importlib.resources.files("androsync").joinpath("assets", "androsync.svg").read_text(encoding="utf-8")
    )
    log(f"+ write {icon_path}")
    if not dry_run:
        icon_path.parent.mkdir(parents=True, exist_ok=True)
        icon_path.write_text(icon_content)

    if shutil.which("update-desktop-database") is not None:
        _run(
            ["update-desktop-database", str(desktop_path.parent)], log=log, dry_run=dry_run, check=False
        )
    else:
        log("(update-desktop-database not found; the launcher may not appear until next login)")

    return 0


def cmd_service_uninstall_launcher(args: argparse.Namespace) -> int:
    log: LogFn = print
    dry_run = bool(getattr(args, "dry_run", False))

    for path in (_desktop_file_path(), _icon_file_path()):
        log(f"+ rm {path}")
        if not dry_run and path.exists():
            path.unlink()

    return 0


# ---------------------------------------------------------------------------
# qr
# ---------------------------------------------------------------------------


def build_urls(config: Config) -> list[str]:
    """Return every URL the phone app could use, USB/localhost first."""
    urls = [f"http://127.0.0.1:{config.service.port}"]
    for addr in config.service.listen:
        if addr in ("127.0.0.1", "0.0.0.0"):
            continue
        url = f"http://{addr}:{config.service.port}"
        if url not in urls:
            urls.append(url)
    return urls


def laptop_name() -> str:
    """The name the phone app shows for this laptop, taken from its host name (task 33c)."""
    import socket

    return socket.gethostname() or "laptop"


def qr_payload(urls: list[str], token: str) -> str:
    """The JSON a pairing QR code carries: the laptop's name, every address it can be reached at, and the token."""
    return json.dumps({"name": laptop_name(), "urls": urls, "token": token})


def cmd_qr(args: argparse.Namespace) -> int:
    config_path = args.config if getattr(args, "config", None) else default_config_path()
    try:
        config = load_config(config_path)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_path}") from exc

    if not config.service.token:
        raise ConfigError("service.token is not set - run `androsync service install` first")

    urls = build_urls(config)
    print(f"Token: {config.service.token}")
    for url in urls:
        print(f"URL: {url}")

    payload = qr_payload(urls, config.service.token)

    try:
        import qrcode
    except ImportError:
        print()
        print(payload)
        print("(install `pip install 'androsync[qr]'` to render this as a scannable QR code)")
        return 0

    qr = qrcode.QRCode(border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    print()
    for row in qr.get_matrix():
        print("".join("##" if cell else "  " for cell in row))
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

_SERVICE_SUBCOMMANDS: dict[str, tuple[str, Callable[[argparse.Namespace], int]]] = {
    "start": ("Start the service now", cmd_service_start),
    "stop": ("Stop the service now (it starts again at next boot)", cmd_service_stop),
    "restart": ("Restart the service", cmd_service_restart),
    "status": ("Show whether/how androsync is currently running", cmd_service_status),
    "logs": ("Follow the service logs (`journalctl --user -u androsync -f`)", cmd_service_logs),
}


def cmd_service_default(args: argparse.Namespace) -> int:
    """``androsync service`` with no subcommand: print the hints block."""
    print(SERVICE_HINTS)
    print()
    print(SERVICE_HINTS_FOOTER)
    return 0


def add_service_commands(subparsers, common: argparse.ArgumentParser) -> None:
    """Attach ``service`` (with its subcommands) and ``qr`` to ``subparsers``."""
    service_p = subparsers.add_parser(
        "service", help="Manage the systemd user service (install/start/stop/status/logs/uninstall)"
    )
    service_p.set_defaults(func=cmd_service_default)
    service_sub = service_p.add_subparsers(dest="service_command")

    install_p = service_sub.add_parser(
        "install", parents=[common], help="Write the config/unit file (pass --autostart to also enable and start it)"
    )
    install_p.add_argument(
        "--autostart",
        action="store_true",
        help="Also enable + start it now, and enable start-at-boot (loginctl linger). Default: install only.",
    )
    install_p.add_argument(
        "--ufw", action="store_true", help="Also run the ufw allow rule via sudo (otherwise just print it)"
    )
    install_p.add_argument(
        "--tailscale",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Add the machine's Tailscale IP to service.listen (default: ask if run interactively)",
    )
    install_p.add_argument(
        "--listen",
        action="append",
        default=[],
        metavar="ADDR",
        help="Additional address to add to service.listen (repeatable)",
    )
    install_p.add_argument(
        "--dry-run", action="store_true", help="Print what would be done without changing anything"
    )
    install_p.set_defaults(func=cmd_service_install)

    for name, (help_text, func) in _SERVICE_SUBCOMMANDS.items():
        sub_p = service_sub.add_parser(name, parents=[common], help=help_text)
        sub_p.add_argument("--dry-run", action="store_true", help="Print the command without running it")
        sub_p.set_defaults(func=func)

    autostart_p = service_sub.add_parser(
        "autostart", parents=[common], help="Enable/disable starting at boot without changing whether it runs now"
    )
    autostart_p.add_argument("autostart_state", choices=["on", "off"])
    autostart_p.add_argument("--dry-run", action="store_true", help="Print the commands without running them")
    autostart_p.set_defaults(func=cmd_service_autostart)

    uninstall_p = service_sub.add_parser(
        "uninstall", parents=[common], help="Disable the service and remove the unit file"
    )
    uninstall_p.add_argument(
        "--ufw", action="store_true", help="Also remove the ufw allow rule via sudo"
    )
    uninstall_p.add_argument(
        "--dry-run", action="store_true", help="Print what would be done without changing anything"
    )
    uninstall_p.set_defaults(func=cmd_service_uninstall)

    install_launcher_p = service_sub.add_parser(
        "install-launcher", parents=[common], help="Write an Applications-menu entry and icon for `androsync gui`"
    )
    install_launcher_p.add_argument(
        "--dry-run", action="store_true", help="Print what would be written without writing it"
    )
    install_launcher_p.set_defaults(func=cmd_service_install_launcher)

    uninstall_launcher_p = service_sub.add_parser(
        "uninstall-launcher", parents=[common], help="Remove the Applications-menu entry and icon"
    )
    uninstall_launcher_p.add_argument(
        "--dry-run", action="store_true", help="Print what would be removed without removing it"
    )
    uninstall_launcher_p.set_defaults(func=cmd_service_uninstall_launcher)

    qr_p = subparsers.add_parser(
        "qr", parents=[common], help="Print the service token and URLs, with an ASCII QR code"
    )
    qr_p.set_defaults(func=cmd_qr)
