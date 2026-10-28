"""Installing the phone app from the computer (Phase W4): the bundled APK, adb install, the one-time link."""

from __future__ import annotations

import hashlib
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_server import _one_pair_config, _Server

from androsync import app_install
from androsync.app_install import AppInstallError, ShareLinks


def make_bundle(folder: Path, payload: bytes = b"apk-bytes", code: int = 5) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / app_install.APK_NAME).write_bytes(payload)
    (folder / "apk.json").write_text(
        json.dumps({"versionName": "0.3.0", "versionCode": code, "sha256": hashlib.sha256(payload).hexdigest()})
    )
    return folder


def test_a_source_checkout_has_no_bundle(tmp_path):
    assert app_install.bundled_app(tmp_path) is None


def test_the_bundle_is_read_and_checked(tmp_path):
    app = app_install.bundled_app(make_bundle(tmp_path))
    assert (app.version_name, app.version_code) == ("0.3.0", 5)
    (tmp_path / app_install.APK_NAME).write_bytes(b"tampered")
    with pytest.raises(AppInstallError, match="checksum"):
        app_install.bundled_app(tmp_path)


def test_parse_package_dump_and_needs_install():
    dump = "Packages:\n  versionCode=2 minSdk=26 targetSdk=35\n  versionName=0.2.0\n  lastUpdateTime=2026-10-26 18:31:21\n"
    installed = app_install.parse_package_dump(dump)
    assert (installed.version_name, installed.version_code, installed.updated) == ("0.2.0", 2, "2026-10-26 18:31:21")
    assert app_install.parse_package_dump("Unable to find package") is None
    bundle = app_install.BundledApp(Path("x"), "0.3.0", 5, "0" * 64)
    assert app_install.needs_install(bundle, None) and app_install.needs_install(bundle, installed)
    assert not app_install.needs_install(bundle, app_install.InstalledApp("0.3.0", 5, ""))


@pytest.mark.parametrize(
    "output, words",
    [
        ("Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]", "Uninstall it on the phone"),
        ("Failure [INSTALL_FAILED_VERSION_DOWNGRADE]", "newer version"),
        ("error: device unauthorized.", "Allow USB debugging"),
        ("something odd", "something odd"),
    ],
)
def test_install_errors_are_explained(output, words):
    assert words in app_install.explain_install_error(output)


def test_install_runs_adb_and_reports_failure(monkeypatch, tmp_path):
    calls = []

    def fake(argv, **kwargs):
        calls.append(argv)
        ok = "GOOD" in argv[2]
        return subprocess.CompletedProcess(argv, 0 if ok else 1, "Performing Streamed Install\nSuccess\n" if ok else "", "" if ok else "Failure [INSTALL_FAILED_VERSION_DOWNGRADE]")

    monkeypatch.setattr(app_install, "_run_process", fake)
    apk = tmp_path / "a.apk"
    apk.write_bytes(b"x")
    app_install.install_apk("GOOD1", apk)
    assert calls[0][:4] == ["adb", "-s", "GOOD1", "install"] and "-r" in calls[0]
    with pytest.raises(AppInstallError, match="newer version"):
        app_install.install_apk("BAD1", apk)


def test_share_links_work_once_expire_and_are_rate_limited():
    now = [0.0]
    links = ShareLinks(clock=lambda: now[0])
    token = links.create(ttl=60)
    assert links.consume(token) is True
    assert links.consume(token) is False  # a second use fails
    token = links.create(ttl=60)
    now[0] += 61
    assert links.consume(token) is False  # expired
    for _ in range(app_install.MAX_FAILURES_PER_MINUTE):
        links.consume("nope-nope-nope")
    assert links.blocked() is True
    now[0] += 61
    assert links.blocked() is False


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def test_the_download_route_serves_the_apk_once_without_a_token_header(tmp_path, monkeypatch):
    monkeypatch.setattr(app_install, "ASSET_DIR", make_bundle(tmp_path / "apk", payload=b"signed-apk"))
    config = _one_pair_config(tmp_path)
    with _Server(config, config_path=tmp_path / "config.yaml") as srv:
        status, info = srv.request("GET", "/v1/app")
        assert status == 200 and info == {"available": True, "version_name": "0.3.0", "version_code": 5, "size": 10}
        status, shared = srv.request("POST", "/v1/app/share")
        assert status == 200 and shared["expires_in"] == app_install.LINK_SECONDS
        status, body, headers = _get(srv.base_url + shared["path"])
        assert (status, body) == (200, b"signed-apk")
        assert headers["Content-Type"] == "application/vnd.android.package-archive"
        assert _get(srv.base_url + shared["path"])[0] == 404  # one time only
        assert _get(srv.base_url + "/install/abcdefghijklmnop/androsync.apk")[0] == 404
        # every other route still needs the token
        assert _get(srv.base_url + "/v1/app")[0] == 401


def test_share_without_a_bundle_says_where_to_get_the_app(tmp_path, monkeypatch):
    monkeypatch.setattr(app_install, "ASSET_DIR", tmp_path / "empty")
    config = _one_pair_config(tmp_path)
    with _Server(config, config_path=tmp_path / "config.yaml") as srv:
        assert srv.request("GET", "/v1/app")[1] == {"available": False}
        status, body = srv.request("POST", "/v1/app/share")
        assert status == 404 and "releases/latest" in body["error"]
