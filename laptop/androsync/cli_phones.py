"""``androsync phone list|set|remove``: name your phones and give each its Wi-Fi address (task 33b).

The phone's id is its hardware serial, the one `androsync devices` shows in the ID column. Pairs can be bound to a phone
with ``device: <id>``, and the daemon keeps trying to reconnect a phone that has a ``wifi_target``.
"""

from __future__ import annotations

import argparse
import sys

from androsync import config_edit
from androsync.config import ConfigError, default_config_path, load_config


def _config_path(args: argparse.Namespace):
    return args.config or default_config_path()


def cmd_phone_list(args: argparse.Namespace) -> int:
    config = load_config(_config_path(args))
    if not config.phones:
        print("no phones are named yet. Name one with: androsync phone set <id> --name \"My phone\"")
        return 0
    print(f"{'ID':<24}{'NAME':<24}WI-FI TARGET")
    for phone_id, phone in sorted(config.phones.items()):
        print(f"{phone_id:<24}{phone.name or '-':<24}{phone.wifi_target or '-'}")
    return 0


def cmd_phone_set(args: argparse.Namespace) -> int:
    path = _config_path(args)
    if args.name is None and args.wifi is None:
        print("androsync: give --name and/or --wifi", file=sys.stderr)
        return 2
    try:
        raw = config_edit.set_phone(config_edit.load_raw(path), args.id, name=args.name, wifi_target=args.wifi)
        config_edit.save_raw(path, raw)
    except ConfigError as exc:
        print(f"androsync: {exc}", file=sys.stderr)
        return 2
    print(f"saved phone {args.id}")
    return 0


def cmd_phone_remove(args: argparse.Namespace) -> int:
    path = _config_path(args)
    try:
        config_edit.save_raw(path, config_edit.remove_phone(config_edit.load_raw(path), args.id))
    except ConfigError as exc:
        print(f"androsync: {exc}", file=sys.stderr)
        return 2
    print(f"removed phone {args.id} from the config (its sync history is kept)")
    return 0


def add_phone_commands(subparsers, common: argparse.ArgumentParser) -> None:
    phone_p = subparsers.add_parser("phone", help="Name your phones and set their Wi-Fi addresses")
    sub = phone_p.add_subparsers(dest="phone_command", required=True)

    list_p = sub.add_parser("list", parents=[common], help="List the named phones")
    list_p.set_defaults(func=cmd_phone_list)

    set_p = sub.add_parser("set", parents=[common], help="Name a phone or set its Wi-Fi target")
    set_p.add_argument("id", help="The phone's id (hardware serial, see `androsync devices`)")
    set_p.add_argument("--name", help="A name you will recognise, for example \"S24 FE\"")
    set_p.add_argument("--wifi", help="host:port for Wi-Fi or Tailscale debugging, e.g. 100.x.y.z:5555")
    set_p.set_defaults(func=cmd_phone_set)

    remove_p = sub.add_parser("remove", parents=[common], help="Forget a phone's name and Wi-Fi target")
    remove_p.add_argument("id")
    remove_p.set_defaults(func=cmd_phone_remove)
