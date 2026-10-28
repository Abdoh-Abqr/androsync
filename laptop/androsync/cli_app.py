"""``androsync app status|install|share``: put the phone app on a phone from this computer (Phase W4)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from androsync import app_install, share
from androsync.apiclient import ApiClient, ApiError
from androsync.app_install import AppInstallError
from androsync.config import default_config_path, load_config


def _bundled_or_fail(apk: str | None) -> tuple[Path, str] | None:
    """``(apk path, "version label")`` to install, or ``None`` after printing why there is nothing to install."""
    if apk:
        path = Path(apk)
        if not path.is_file():
            print(f"androsync: no such file: {apk}", file=sys.stderr)
            return None
        return path, "your file"
    try:
        app = app_install.bundled_app()
    except AppInstallError as exc:
        print(f"androsync: {exc}", file=sys.stderr)
        return None
    if app is None:
        print(f"androsync: {app_install.missing_text()}", file=sys.stderr)
        return None
    return app.path, f"{app.version_name} (code {app.version_code})"


def cmd_app_status(args: argparse.Namespace) -> int:
    try:
        app = app_install.bundled_app()
        serials = app_install.ready_phones()
    except AppInstallError as exc:
        print(f"androsync: {exc}", file=sys.stderr)
        return 2
    print(f"this install carries: {f'{app.version_name} (code {app.version_code})' if app else 'no app file'}")
    if not serials:
        print("no phone is attached by adb. Plug one in with USB debugging on, or use `androsync app share`.")
        return 0
    for serial in serials:
        try:
            installed = app_install.installed_app(serial)
        except AppInstallError as exc:
            print(f"{serial}: {exc}")
            continue
        if installed is None:
            note = "AndroSync is not installed" + (": run `androsync app install`" if app else "")
        else:
            note = f"AndroSync {installed.version_name} (code {installed.version_code}), updated {installed.updated}"
            if app and app_install.needs_install(app, installed):
                note += " - older than this install: run `androsync app install`"
        print(f"{serial}: {note}")
    return 0


def cmd_app_install(args: argparse.Namespace) -> int:
    chosen = _bundled_or_fail(args.apk)
    if chosen is None:
        return 2
    apk, label = chosen
    try:
        serials = [args.phone] if args.phone else app_install.ready_phones()
    except AppInstallError as exc:
        print(f"androsync: {exc}", file=sys.stderr)
        return 2
    if not serials:
        print("androsync: no phone is attached by adb (USB debugging on, cable plugged in).", file=sys.stderr)
        return 2
    failed = 0
    for serial in serials:
        try:
            app_install.install_apk(serial, apk)
        except AppInstallError as exc:
            failed += 1
            print(f"{serial}: not installed: {exc}", file=sys.stderr)
            continue
        print(f"{serial}: installed AndroSync {label}")
    if not failed:
        print("Open the app on the phone, then run `androsync qr` and scan the code to pair it.")
    return 1 if failed else 0


def cmd_app_share(args: argparse.Namespace) -> int:
    path = Path(args.config) if getattr(args, "config", None) else default_config_path()
    config = load_config(path)
    if not config.service.token:
        print("androsync: service.token is not set - run `androsync service install` first", file=sys.stderr)
        return 2
    client = ApiClient(f"http://127.0.0.1:{config.service.port}", config.service.token, timeout=10)
    try:
        answer = client._request("POST", "/v1/app/share")
    except ApiError as exc:
        print(f"androsync: {exc}", file=sys.stderr)
        return 2
    except OSError:
        print("androsync: the service is not running. Start it (`androsync service start`) and try again.", file=sys.stderr)
        return 2
    urls = answer.get("urls") or []
    if not urls:
        print("androsync: the service listens only on this computer, so a phone cannot reach it. Add the Tailscale or LAN address.", file=sys.stderr)
        return 2
    print(f"One-time link, valid for {answer.get('expires_in', 600) // 60} minutes. Open it on the phone (or scan the QR code):")
    for url in urls:
        print(f"  {url}")
    lines = share.qr_lines(urls[0])
    if lines:
        print("\n".join(lines))
    print("Android asks you to allow installing from this source; after that, open the app and scan the pairing QR.")
    return 0


def add_app_commands(subparsers, common: argparse.ArgumentParser) -> None:
    app_p = subparsers.add_parser("app", help="Install the AndroSync phone app from this computer")
    sub = app_p.add_subparsers(dest="app_command", required=True)

    status_p = sub.add_parser("status", parents=[common], help="Which phones have the app, and which version")
    status_p.set_defaults(func=cmd_app_status)

    install_p = sub.add_parser("install", parents=[common], help="Install (or update) the app on the phones adb sees")
    install_p.add_argument("--phone", help="One adb serial (see `androsync devices`); default: every ready phone")
    install_p.add_argument("--apk", help="Install this APK file instead of the one that came with androsync")
    install_p.set_defaults(func=cmd_app_install)

    share_p = sub.add_parser("share", parents=[common], help="Make a one-time download link and QR code for a phone without adb")
    share_p.set_defaults(func=cmd_app_share)
