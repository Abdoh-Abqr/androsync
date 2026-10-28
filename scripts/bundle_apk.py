#!/usr/bin/env python3
"""Put the signed `full` APK into the Python package so `androsync app install` can use it (Phase W4).

    python3 scripts/bundle_apk.py android/app/build/outputs/apk/full/release/app-full-release.apk

The release workflow runs this before building the wheel. It writes laptop/androsync/assets/apk/androsync-remote-full.apk
and apk.json (version, code, SHA-256). An unsigned APK is refused: a phone cannot install it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bump_version  # noqa: E402

ROOT = bump_version.ROOT
DEST = ROOT / "laptop/androsync/assets/apk"
APK_NAME = "androsync-remote-full.apk"


def bundle(apk: Path, dest: Path = DEST, versions: dict | None = None) -> Path:
    if "unsigned" in apk.name:
        raise SystemExit(f"bundle_apk: {apk.name} is unsigned; a phone cannot install it")
    if not apk.is_file():
        raise SystemExit(f"bundle_apk: no such file: {apk}")
    versions = versions or bump_version.read_versions()
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / APK_NAME
    shutil.copyfile(apk, target)
    meta = {
        "versionName": versions["versionName"],
        "versionCode": int(versions["versionCode"]),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    (dest / "apk.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("apk", type=Path, help="the signed full release APK")
    args = parser.parse_args(argv)
    target = bundle(args.apk)
    print(f"bundled {args.apk.name} as {target.relative_to(ROOT)} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
