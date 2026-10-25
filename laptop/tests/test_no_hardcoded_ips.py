"""Guard against a real IP address being compiled back into the source (task 33e).

androsync must never ship with any particular machine's or network's address
built in - everything it offers (a device to sync with, a URL to try) is
either detected live (`adb devices`, `tailscale status`) or typed by the
user into the config. A `100.x.x.x` literal in `laptop/androsync/` is
almost certainly a tailnet address that leaked back in from a real setup
during development; `config.example.yaml` must only ever show placeholder
addresses too.
"""

from __future__ import annotations

import re
from pathlib import Path

_IPV4_RE = re.compile(r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_DIR = _REPO_ROOT / "laptop" / "androsync"
_EXAMPLE_CONFIG = _REPO_ROOT / "laptop" / "config.example.yaml"


def _scan(path: Path) -> list[str]:
    hits = []
    for py_file in sorted(path.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8")
        for match in _IPV4_RE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            hits.append(f"{py_file.relative_to(_REPO_ROOT)}:{line_no}: {match.group()}")
    return hits


def test_no_tailnet_style_ip_literal_in_laptop_source():
    hits = _scan(_SOURCE_DIR)
    assert hits == [], "found a real-looking 100.x.x.x address compiled into the source:\n" + "\n".join(hits)


def test_no_tailnet_style_ip_literal_in_example_config():
    text = _EXAMPLE_CONFIG.read_text(encoding="utf-8")
    assert not _IPV4_RE.search(text), "config.example.yaml should only ever show placeholder addresses"
