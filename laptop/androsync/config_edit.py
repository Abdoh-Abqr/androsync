"""Shared raw-YAML config editing helpers (PLAN.md task 23b).

Used by ``cli_pairs.py`` (``add-pair``/``edit-pair``/``remove-pair``),
``daemon/server.py`` (the phone app's pair CRUD routes) and ``service.py``
(install). Every write round-trips the modified raw dict through
``config.load_config`` on a temp file first, so invalid input raises
``ConfigError`` and never reaches the real file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from androsync.config import TRANSPORT_MODES, ConfigError, load_config, secure_file
from androsync.phone_path import PhonePathError, check_remote, normalize_remote


def load_raw(path: Path) -> dict[str, Any]:
    """Load the YAML config as a plain dict, or ``{}`` if the file does not exist yet."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_raw(path: Path, raw: dict[str, Any]) -> None:
    """Validate ``raw`` (round-tripped through ``load_config``) then atomically write it.

    Writes to a ``.tmp`` file next to ``path`` and ``os.replace``s it into
    place, so a crash mid-write - or an invalid ``raw`` - never corrupts the
    real config.
    """
    text = yaml.safe_dump(raw, sort_keys=False)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(text, encoding="utf-8")
    secure_file(tmp_path)  # the token is in here: owner only, before the file takes the real name
    try:
        load_config(tmp_path)
    except ConfigError:
        tmp_path.unlink(missing_ok=True)
        raise
    os.replace(tmp_path, path)


def set_default_transport(raw: dict[str, Any], transport: str) -> dict[str, Any]:
    """Return a new raw dict whose ``defaults.transport`` is ``transport`` (pairs without their own value use it).

    Raises ``ConfigError`` for anything but ``adb`` or ``agent``.
    """
    if transport not in TRANSPORT_MODES:
        raise ConfigError(f"transport must be one of {TRANSPORT_MODES}, got {transport!r}")
    new_raw = dict(raw)
    defaults = dict(new_raw.get("defaults") or {})
    defaults["transport"] = transport
    new_raw["defaults"] = defaults
    return new_raw


def set_service_token(raw: dict[str, Any], token: str) -> dict[str, Any]:
    """Return a new raw dict whose ``service.token`` is ``token`` (a new token for the app and the other clients)."""
    new_raw = dict(raw)
    service = dict(new_raw.get("service") or {})
    service["token"] = token
    new_raw["service"] = service
    return new_raw


def set_phone(raw: dict[str, Any], phone_id: str, *, name: str | None = None, wifi_target: str | None = None) -> dict[str, Any]:
    """Return a new raw dict with ``phones[phone_id]`` created or updated (task 33b). ``None`` leaves a field as it is."""
    new_raw = dict(raw)
    phones = dict(new_raw.get("phones") or {})
    entry = dict(phones.get(phone_id) or {})
    if name is not None:
        entry["name"] = name
    if wifi_target is not None:
        entry["wifi_target"] = wifi_target
    phones[phone_id] = entry
    new_raw["phones"] = phones
    return new_raw


def remove_phone(raw: dict[str, Any], phone_id: str) -> dict[str, Any]:
    """Return a new raw dict without ``phones[phone_id]``; ``ConfigError`` if it is not listed."""
    phones = dict(raw.get("phones") or {})
    if phone_id not in phones:
        raise ConfigError(f"no phone {phone_id!r} in the config")
    del phones[phone_id]
    new_raw = dict(raw)
    if phones:
        new_raw["phones"] = phones
    else:
        new_raw.pop("phones", None)
    return new_raw


def _with_checked_remote(pair_dict: dict[str, Any]) -> dict[str, Any]:
    """Copy of ``pair_dict`` with the phone folder tidied and checked (task 36c).

    ``/Internal storage/x`` becomes ``/sdcard/x``; ``/`` or ``/data`` raise ``ConfigError`` with a hint.
    A missing or non-text ``remote`` is left for ``load_config`` to report.
    """
    out = dict(pair_dict)
    if isinstance(out.get("remote"), str) and out["remote"].startswith("/"):
        try:
            out["remote"] = check_remote(out["remote"])
        except PhonePathError as exc:
            raise ConfigError(f"pair {out.get('name', '?')}: {exc}") from exc
    return out


def _same_laptop_folder(a: Any, b: Any) -> bool:
    def tidy(value: Any) -> str:
        text = os.path.expandvars(os.path.expanduser(str(value or "")))
        return text.rstrip("/") or text

    return tidy(a) == tidy(b)


def folders_changed(old_pair: dict[str, Any], new_pair: dict[str, Any]) -> bool:
    """True when ``new_pair`` points at a different laptop folder or phone folder than ``old_pair``.

    Spellings of the same folder (``~`` versus the full path, a trailing slash, ``/Internal storage/x`` versus
    ``/sdcard/x``) do not count as a change. A field missing from ``new_pair`` is read as unchanged.
    """
    if "local" in new_pair and not _same_laptop_folder(old_pair.get("local"), new_pair.get("local")):
        return True
    if "remote" in new_pair:
        old_remote = normalize_remote(str(old_pair.get("remote") or ""))
        new_remote = normalize_remote(str(new_pair.get("remote") or ""))
        if old_remote.rstrip("/") != new_remote.rstrip("/"):
            return True
    return False


def add_pair(raw: dict[str, Any], pair_dict: dict[str, Any]) -> dict[str, Any]:
    """Return a new raw dict with ``pair_dict`` appended to ``pairs``.

    Raises ``ConfigError`` if a pair with that name already exists.
    """
    pair_dict = _with_checked_remote(pair_dict)
    pairs = list(raw.get("pairs") or [])
    name = pair_dict.get("name")
    if any(p.get("name") == name for p in pairs):
        raise ConfigError(f"a pair named {name!r} already exists")
    new_raw = dict(raw)
    new_raw["pairs"] = pairs + [pair_dict]
    return new_raw


#: The pair settings the app and the window edit. An edit sends all of these it knows; a key left out means "back to the
#: default". Every OTHER key a pair has (versioning, conflict_copies, undo, safety, transfer, ...) is not on those forms
#: and must survive an edit instead of being silently erased.
FORM_KEYS = frozenset(
    {"name", "local", "remote", "direction", "conflict", "propagate_deletes", "ignore", "enabled", "transport", "device"}
)


def update_pair(raw: dict[str, Any], name: str, pair_dict: dict[str, Any]) -> dict[str, Any]:
    """Return a new raw dict with the pair named ``name`` replaced by ``pair_dict``.

    ``pair_dict`` may carry a different ``name`` (a rename); raises
    ``ConfigError`` if ``name`` does not exist, or the new name collides
    with a different existing pair.
    """
    pairs = list(raw.get("pairs") or [])
    idx = next((i for i, p in enumerate(pairs) if p.get("name") == name), None)
    if idx is None:
        raise ConfigError(f"no pair named {name!r}")
    # Only a changed phone folder is checked, so a pair whose old folder is invalid can still be paused or fixed.
    if pair_dict.get("remote") != pairs[idx].get("remote"):
        pair_dict = _with_checked_remote(pair_dict)
    new_name = pair_dict.get("name", name)
    if new_name != name and any(p.get("name") == new_name for p in pairs):
        raise ConfigError(f"a pair named {new_name!r} already exists")
    # Keep what the edit form does not know about (see FORM_KEYS); what it does know replaces the old values.
    kept = {k: v for k, v in pairs[idx].items() if k not in FORM_KEYS and k not in pair_dict}
    pairs = list(pairs)
    pairs[idx] = {**pair_dict, **kept}
    new_raw = dict(raw)
    new_raw["pairs"] = pairs
    return new_raw


def remove_pair(raw: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a new raw dict with the pair named ``name`` removed.

    Raises ``ConfigError`` if it does not exist.
    """
    pairs = list(raw.get("pairs") or [])
    new_pairs = [p for p in pairs if p.get("name") != name]
    if len(new_pairs) == len(pairs):
        raise ConfigError(f"no pair named {name!r}")
    new_raw = dict(raw)
    new_raw["pairs"] = new_pairs
    return new_raw


def update_pair_and_state(config_path: Path, name: str, pair_dict: dict[str, Any]) -> bool:
    """Save an edited pair and keep its saved history consistent (task 36d, issue #9). Returns whether history was reset.

    * Changing the laptop or phone folder resets the pair's history (``state.reset_pair_state``), so the next sync
      only adds files and never deletes what the old folders no longer match.
    * A rename moves the history along (``state.rename_pair_state``).
    The config is written first and validated; a rejected edit changes nothing, history included.
    Raises ``ConfigError`` like :func:`update_pair`.
    """
    from androsync import state  # local import: state imports config, and config_edit stays importable on its own

    raw = load_raw(config_path)
    current = next((p for p in raw.get("pairs") or [] if p.get("name") == name), None)
    new_raw = update_pair(raw, name, pair_dict)
    save_raw(config_path, new_raw)

    reset = current is not None and folders_changed(current, pair_dict)
    if reset:
        state.reset_pair_state(name)
    new_name = pair_dict.get("name", name)
    if new_name != name:
        state.rename_pair_state(name, new_name)
    return reset
