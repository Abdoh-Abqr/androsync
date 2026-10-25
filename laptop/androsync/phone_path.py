"""Rules for folders on the phone (task 36c, issue #8).

A phone folder must live in shared storage: internal storage (``/sdcard/...``) or an SD card or USB drive
(``/storage/<id>/...``). Names that file managers show, such as ``/Internal storage``, are not real paths, so
they are mapped to ``/sdcard``. Anything else, such as ``/`` or ``/data``, is refused: syncing it would pull the
phone's system folders.

The Android app has a Kotlin copy of these rules (``PhonePath.kt``); keep both in step.
"""
from __future__ import annotations

import re

STORAGE_HINT = (
    "Phone folders must be inside internal storage (/sdcard/...) or an SD card (/storage/XXXX-XXXX/...)"
)

# Names and paths that all mean "the phone's internal shared storage". Matched case-insensitively
# on a whole path segment, then replaced by /sdcard.
ALIASES = (
    "/storage/emulated/0",
    "/storage/self/primary",
    "/mnt/shell/emulated/0",
    "/mnt/sdcard",
    "/internal shared storage",
    "/internal storage",
)

# Top-level folders of Android's own file system. Used only to decide whether a "Did you mean" hint makes sense.
SYSTEM_DIRS = frozenset({
    "data", "system", "vendor", "proc", "sys", "dev", "mnt", "storage", "etc", "cache", "product", "odm",
    "apex", "config", "oem", "persist", "efs", "prism", "optics", "bin", "sbin", "lib", "root", "acct",
    "metadata", "debug_ramdisk", "linkerconfig", "system_ext", "vendor_dlkm", "odm_dlkm", "system_dlkm",
    "data_mirror", "postinstall", "init",
})

PRIVATE_APP_DIRS = ("/sdcard/Android/data", "/sdcard/Android/obb")

# /storage/<id>, but not /storage/emulated or /storage/self.
_REMOVABLE = re.compile(r"^/storage/(?!emulated(?:/|$)|self(?:/|$))[^/]+(?:/.*)?$")
# A second user or work profile: /storage/emulated/10/...
_OTHER_USER = re.compile(r"^/storage/emulated/[1-9][0-9]*(?:/.*)?$")
# Roots of the above, where "up" must stop.
_ROOT_OF_VOLUME = re.compile(r"^/storage/(?:emulated/[1-9][0-9]*|(?!emulated$|self$)[^/]+)$")


class PhonePathError(ValueError):
    """The path cannot be used as a phone folder; the message says why and what to do."""


def normalize_remote(path: str) -> str:
    """Tidy a phone path: trim spaces, collapse ``//``, drop a trailing ``/``, map storage names to ``/sdcard``.

    A path that does not start with ``/`` is returned trimmed but otherwise untouched, so that
    :func:`validate_remote` can report it.
    """
    text = path.strip()
    if not text.startswith("/"):
        return text
    text = re.sub(r"/{2,}", "/", text)
    if len(text) > 1:
        text = text.rstrip("/")
    lower = text.lower()
    for alias in ALIASES:
        if lower == alias or lower.startswith(alias + "/"):
            return "/sdcard" + text[len(alias):]
    return text


def _is_under(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + "/")


def validate_remote(path: str) -> str | None:
    """Return an error message for an already normalized phone path, or ``None`` if it is usable."""
    if not path.startswith("/"):
        return "Phone folder must be an absolute path starting with /"
    segments = path.split("/")
    if ".." in segments:
        return "Phone folder must not contain '..'."
    for private in PRIVATE_APP_DIRS:
        if _is_under(path, private):
            return (
                f"{path} belongs to other apps and Android does not allow access to it. "
                "Choose another folder under /sdcard."
            )
    if _is_under(path, "/sdcard") or _REMOVABLE.match(path) or _OTHER_USER.match(path):
        return None

    first = segments[1] if len(segments) > 1 else ""
    if path == "/" or first in SYSTEM_DIRS:
        return f"{STORAGE_HINT}, not {path}. Pick a folder under /sdcard, for example /sdcard/Documents."
    return f"{STORAGE_HINT}, not {path}. Did you mean /sdcard{path}?"


def check_remote(path: str) -> str:
    """Normalize ``path`` and return it, or raise :class:`PhonePathError`."""
    normalized = normalize_remote(path)
    message = validate_remote(normalized)
    if message is not None:
        raise PhonePathError(message)
    return normalized


def remote_parent(path: str) -> str | None:
    """The folder one level up, or ``None`` at the top of a volume (``/sdcard``, ``/storage/<id>``)."""
    if path == "/sdcard" or _ROOT_OF_VOLUME.match(path):
        return None
    parent = path.rsplit("/", 1)[0]
    return parent or None
