"""Tests for androsync.config.load_config and validation."""

from pathlib import Path

import pytest
import yaml

from androsync.config import ConfigError, load_config

EXAMPLE_CONFIG = Path(__file__).resolve().parents[1] / "config.example.yaml"


def test_loads_example_config():
    cfg = load_config(EXAMPLE_CONFIG)
    assert cfg.device == "auto"
    assert cfg.service.port == 8765
    assert cfg.service.listen == ["127.0.0.1"]
    names = [p.name for p in cfg.pairs]
    assert names == ["photos", "vault"]


def test_defaults_merged_into_pair():
    cfg = load_config(EXAMPLE_CONFIG)
    photos = next(p for p in cfg.pairs if p.name == "photos")
    # conflict/keep_conflict_copies/mtime_tolerance_s come from defaults.
    assert photos.conflict == "newest-wins"
    assert photos.keep_conflict_copies is True
    assert photos.mtime_tolerance_s == 2
    # pair-level override wins.
    assert photos.propagate_deletes is False


def test_conflict_copies_defaults_to_folder():
    cfg = load_config(EXAMPLE_CONFIG)
    photos = next(p for p in cfg.pairs if p.name == "photos")
    assert photos.conflict_copies == "folder"


def test_conflict_copies_beside_accepted(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "pairs": [
                    {
                        "name": "p",
                        "local": str(tmp_path / "local"),
                        "remote": "/sdcard/p",
                        "direction": "two-way",
                        "conflict_copies": "beside",
                    }
                ]
            }
        )
    )
    cfg = load_config(config_path)
    assert cfg.pairs[0].conflict_copies == "beside"


def test_conflict_copies_invalid_value_raises(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "pairs": [
                    {
                        "name": "p",
                        "local": str(tmp_path / "local"),
                        "remote": "/sdcard/p",
                        "direction": "two-way",
                        "conflict_copies": "nope",
                    }
                ]
            }
        )
    )
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_local_path_expanded():
    cfg = load_config(EXAMPLE_CONFIG)
    photos = next(p for p in cfg.pairs if p.name == "photos")
    assert isinstance(photos.local, Path)
    assert "~" not in str(photos.local)


def test_ignore_lists_concatenated():
    cfg = load_config(EXAMPLE_CONFIG)
    vault = next(p for p in cfg.pairs if p.name == "vault")
    assert vault.ignore == [".git/**", ".trash/**", "*.tmp", ".obsidian/workspace*.json"]

    photos = next(p for p in cfg.pairs if p.name == "photos")
    assert photos.ignore == [".git/**", ".trash/**", "*.tmp"]


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def _base_pair(**overrides):
    pair = {
        "name": "p1",
        "local": "/tmp/local",
        "remote": "/sdcard/x",
        "direction": "two-way",
    }
    pair.update(overrides)
    return pair


def test_duplicate_pair_names_rejected(tmp_path):
    cfg_path = _write(
        tmp_path,
        {"pairs": [_base_pair(name="dup"), _base_pair(name="dup")]},
    )
    with pytest.raises(ConfigError, match="dup"):
        load_config(cfg_path)


def test_remote_must_be_absolute(tmp_path):
    cfg_path = _write(
        tmp_path,
        {"pairs": [_base_pair(name="badremote", remote="sdcard/x")]},
    )
    with pytest.raises(ConfigError, match="badremote"):
        load_config(cfg_path)


def test_invalid_direction_rejected(tmp_path):
    cfg_path = _write(
        tmp_path,
        {"pairs": [_base_pair(name="baddir", direction="sideways")]},
    )
    with pytest.raises(ConfigError, match="baddir"):
        load_config(cfg_path)


def test_invalid_conflict_policy_rejected(tmp_path):
    cfg_path = _write(
        tmp_path,
        {"pairs": [_base_pair(name="badconflict", conflict="coinflip")]},
    )
    with pytest.raises(ConfigError, match="badconflict"):
        load_config(cfg_path)


def test_versioning_defaults_to_none(tmp_path):
    cfg_path = _write(tmp_path, {"pairs": [_base_pair(name="p1")]})
    cfg = load_config(cfg_path)
    assert cfg.pairs[0].versioning == "none"


def test_versioning_git_accepted(tmp_path):
    cfg_path = _write(tmp_path, {"pairs": [_base_pair(name="p1", versioning="git")]})
    cfg = load_config(cfg_path)
    assert cfg.pairs[0].versioning == "git"


def test_invalid_versioning_rejected(tmp_path):
    cfg_path = _write(
        tmp_path,
        {"pairs": [_base_pair(name="badversioning", versioning="svn")]},
    )
    with pytest.raises(ConfigError, match="badversioning"):
        load_config(cfg_path)


def test_invalid_port_rejected(tmp_path):
    cfg_path = _write(
        tmp_path,
        {
            "service": {"port": 99999},
            "pairs": [_base_pair(name="p1")],
        },
    )
    with pytest.raises(ConfigError, match="port"):
        load_config(cfg_path)


def test_missing_pair_name_rejected(tmp_path):
    pair = _base_pair()
    del pair["name"]
    cfg_path = _write(tmp_path, {"pairs": [pair]})
    with pytest.raises(ConfigError):
        load_config(cfg_path)
