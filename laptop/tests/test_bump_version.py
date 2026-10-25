"""scripts/bump_version.py keeps three version strings and the Android versionCode in step (task 35c, issue #23)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bump_version.py"
spec = importlib.util.spec_from_file_location("bump_version", SCRIPT)
bump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bump)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "laptop/androsync").mkdir(parents=True)
    (tmp_path / "android/app").mkdir(parents=True)
    (tmp_path / "laptop/pyproject.toml").write_text('[project]\nname = "androsync"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n')
    (tmp_path / "laptop/androsync/__init__.py").write_text('"""androsync."""\n\n__version__ = "0.1.0"\n')
    (tmp_path / "android/app/build.gradle.kts").write_text(
        'android {\n    defaultConfig {\n        minSdk = 26\n        versionCode = 7\n        versionName = "0.1.0"\n    }\n}\n'
    )
    return tmp_path


def test_reads_every_place(tree):
    assert bump.read_versions(tree) == {
        "pyproject": "0.1.0",
        "__init__": "0.1.0",
        "versionName": "0.1.0",
        "versionCode": "7",
    }


def test_set_changes_all_places_and_raises_version_code_by_one(tree):
    now = bump.set_versions("0.2.0", tree)
    assert now == {"pyproject": "0.2.0", "__init__": "0.2.0", "versionName": "0.2.0", "versionCode": "8"}
    assert 'requires-python = ">=3.10"' in (tree / "laptop/pyproject.toml").read_text()  # other lines untouched
    assert "minSdk = 26" in (tree / "android/app/build.gradle.kts").read_text()


def test_set_rejects_a_version_that_is_not_x_y_z(tree):
    with pytest.raises(SystemExit):
        bump.set_versions("v0.2", tree)
    assert bump.read_versions(tree)["pyproject"] == "0.1.0"


def test_check_names_each_place_that_disagrees(tree):
    assert bump.check("0.1.0", tree) == []
    (tree / "laptop/androsync/__init__.py").write_text('__version__ = "0.0.9"\n')
    assert bump.check("0.1.0", tree) == ["__init__ is 0.0.9, expected 0.1.0"]


def test_changelog_section_stops_at_the_next_heading():
    text = "# Changelog\n\n## [0.2.0] - 2026-11-01\n\nNew thing.\n\n### Added\n- a\n\n## [0.1.0] - 2026-10-14\n\nOld.\n"
    assert bump.changelog_section(text, "0.2.0") == "New thing.\n\n### Added\n- a\n"
    assert bump.changelog_section(text, "0.1.0") == "Old.\n"
    assert bump.changelog_section(text, "0.3.0") is None


def test_the_real_repo_agrees_with_itself():
    found = bump.read_versions()
    assert bump.check(found["pyproject"]) == []
