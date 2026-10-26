"""Names for the computer and for phones (Phase V2): config, ping, pairs list, rename routes."""

import platform

import yaml
from test_server import _one_pair_config, _Server

from androsync.config import ConfigError, validate_display_name
import pytest


def test_laptop_name_defaults_to_the_host_name_and_is_renamed_through_the_api(tmp_path):
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        _, ping = srv.request("GET", "/v1/ping")
        assert ping["laptop_name"] == (platform.node() or "Laptop")
        status, _ = srv.request("PUT", "/v1/laptop", body={"name": "  Home server "})
        assert status == 200
        _, ping = srv.request("GET", "/v1/ping")
        assert ping["laptop_name"] == "Home server"
    assert yaml.safe_load(config_path.read_text())["service"]["name"] == "Home server"


@pytest.mark.parametrize("bad", ["", "   ", "x" * 41, "a\nb"])
def test_a_bad_laptop_name_is_refused(tmp_path, bad):
    config = _one_pair_config(tmp_path)
    with _Server(config, config_path=tmp_path / "config.yaml") as srv:
        status, body = srv.request("PUT", "/v1/laptop", body={"name": bad})
        assert status == 400 and "name" in body["error"]


def test_a_phone_name_is_saved_and_shows_on_pairs_bound_to_it(tmp_path):
    config = _one_pair_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    with _Server(config, config_path=config_path) as srv:
        status, _ = srv.request("PUT", "/v1/phones/HW123", body={"name": "S24 FE"})
        assert status == 200
        status, _ = srv.request(
            "POST",
            "/v1/pairs",
            body={"name": "bound", "local": str(tmp_path / "b"), "remote": "/sdcard/b", "direction": "two-way", "device": "HW123"},
        )
        assert status == 201
        _, pairs = srv.request("GET", "/v1/pairs")
        rows = {p["name"]: p for p in pairs}
        assert rows["bound"]["device_name"] == "S24 FE"
        assert rows["alpha"]["device_name"] is None
    assert yaml.safe_load(config_path.read_text())["phones"]["HW123"]["name"] == "S24 FE"


def test_phone_rename_needs_a_body_with_a_name(tmp_path):
    config = _one_pair_config(tmp_path)
    with _Server(config, config_path=tmp_path / "config.yaml") as srv:
        status, _ = srv.request("PUT", "/v1/phones/HW123", body={"nope": 1})
        assert status == 400


def test_validate_display_name_trims():
    assert validate_display_name("  Ubuntu ") == "Ubuntu"
    with pytest.raises(ConfigError):
        validate_display_name(None)
