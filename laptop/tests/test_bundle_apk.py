"""scripts/bundle_apk.py puts the signed APK and its checksum into the package (Phase W4)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("bundle_apk", SCRIPTS / "bundle_apk.py")
bundle_apk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle_apk)


def test_the_apk_and_its_checksum_are_written(tmp_path):
    apk = tmp_path / "app-full-release.apk"
    apk.write_bytes(b"signed")
    dest = tmp_path / "apk"
    bundle_apk.bundle(apk, dest, {"versionName": "0.3.0", "versionCode": "5"})
    meta = json.loads((dest / "apk.json").read_text())
    assert meta == {"versionName": "0.3.0", "versionCode": 5, "sha256": hashlib.sha256(b"signed").hexdigest()}
    assert (dest / bundle_apk.APK_NAME).read_bytes() == b"signed"


def test_an_unsigned_apk_is_refused(tmp_path):
    apk = tmp_path / "app-full-release-unsigned.apk"
    apk.write_bytes(b"x")
    with pytest.raises(SystemExit, match="unsigned"):
        bundle_apk.bundle(apk, tmp_path / "apk", {"versionName": "1", "versionCode": "1"})
