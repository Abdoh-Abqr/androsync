"""scripts/release_notes.py makes the announcement texts from a CHANGELOG section (Phase V5)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("release_notes", SCRIPTS / "release_notes.py")
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)

SECTION = """### Added
- **Automatic sync**: a pair can sync by itself.
  It never runs the first sync.
- Names in the direction, like `Ubuntu <-> S24 FE`. More text follows here.

### Fixed
- The agent starts again after an update.
"""


def test_bullets_join_wrapped_lines_and_drop_markdown():
    assert notes.bullets(SECTION) == [
        "Automatic sync: a pair can sync by itself. It never runs the first sync.",
        "Names in the direction, like Ubuntu <-> S24 FE. More text follows here.",
        "The agent starts again after an update.",
    ]


def test_post_has_a_title_three_bullets_and_the_link():
    text = notes.post("0.3.0", notes.bullets(SECTION))
    lines = text.splitlines()
    assert lines[0].startswith("AndroSync 0.3.0 is out")
    assert lines[2] == "- Automatic sync: a pair can sync by itself."
    assert lines[-1].endswith(notes.REPO)
    assert sum(1 for line in lines if line.startswith("- ")) == 3


def test_fdroid_changelog_stays_within_the_limit():
    items = [f"Item number {i} " + "x" * 80 + "." for i in range(20)]
    text = notes.fdroid_changelog(items)
    assert len(text) <= notes.FDROID_LIMIT
    assert text.endswith("\n") and text.count("\n") >= 3
