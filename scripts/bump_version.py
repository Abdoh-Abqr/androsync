#!/usr/bin/env python3
"""Keep the version numbers in step (task 35c, issue #23).

One release number lives in three places: laptop/pyproject.toml, laptop/androsync/__init__.py and the Android app
(versionName, plus versionCode which only ever goes up by one).

    python3 scripts/bump_version.py 0.2.0           set all of them, versionCode + 1
    python3 scripts/bump_version.py --show          print what is there now
    python3 scripts/bump_version.py --check 0.2.0   exit 1 unless every place says 0.2.0 (the release workflow runs this)
    python3 scripts/bump_version.py --notes 0.2.0   print that version's CHANGELOG.md section (release notes)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = "laptop/pyproject.toml"
INIT = "laptop/androsync/__init__.py"
GRADLE = "android/app/build.gradle.kts"
CHANGELOG = "CHANGELOG.md"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_PY_VERSION = re.compile(r'^(version\s*=\s*)"([^"]+)"', re.M)
_INIT_VERSION = re.compile(r'^(__version__\s*=\s*)"([^"]+)"', re.M)
_VERSION_NAME = re.compile(r'(versionName\s*=\s*)"([^"]+)"')
_VERSION_CODE = re.compile(r"(versionCode\s*=\s*)(\d+)")


def _match(pattern: re.Pattern[str], text: str, where: str) -> re.Match[str]:
    m = pattern.search(text)
    if not m:
        raise SystemExit(f"cannot find the version in {where}")
    return m


def read_versions(root: Path = ROOT) -> dict[str, str]:
    """The version string in each place, plus the Android versionCode."""
    py = _match(_PY_VERSION, (root / PYPROJECT).read_text(), PYPROJECT).group(2)
    init = _match(_INIT_VERSION, (root / INIT).read_text(), INIT).group(2)
    gradle = (root / GRADLE).read_text()
    return {
        "pyproject": py,
        "__init__": init,
        "versionName": _match(_VERSION_NAME, gradle, GRADLE).group(2),
        "versionCode": _match(_VERSION_CODE, gradle, GRADLE).group(2),
    }


def set_versions(version: str, root: Path = ROOT) -> dict[str, str]:
    """Write [version] everywhere and raise versionCode by one."""
    if not SEMVER.match(version):
        raise SystemExit(f"version must look like 1.2.3, got {version!r}")
    for rel, pattern in ((PYPROJECT, _PY_VERSION), (INIT, _INIT_VERSION)):
        path = root / rel
        path.write_text(pattern.sub(lambda m: f'{m.group(1)}"{version}"', path.read_text(), count=1))
    path = root / GRADLE
    text = path.read_text()
    code = int(_match(_VERSION_CODE, text, GRADLE).group(2)) + 1
    text = _VERSION_NAME.sub(lambda m: f'{m.group(1)}"{version}"', text, count=1)
    text = _VERSION_CODE.sub(lambda m: f"{m.group(1)}{code}", text, count=1)
    path.write_text(text)
    return read_versions(root)


def check(version: str, root: Path = ROOT) -> list[str]:
    """Problems found, empty when every place says [version]."""
    found = read_versions(root)
    return [
        f"{name} is {value}, expected {version}"
        for name, value in found.items()
        if name != "versionCode" and value != version
    ]


def changelog_section(text: str, version: str) -> str | None:
    """The body under `## [version]` up to the next `## ` heading, or None when the version has no entry."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(f"## [{version}]")), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1 : end]).strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", nargs="?", help="new version, for example 0.2.0")
    parser.add_argument("--show", action="store_true", help="print the current versions")
    parser.add_argument("--check", metavar="VERSION", help="exit 1 unless every place says VERSION")
    parser.add_argument("--notes", metavar="VERSION", help="print the CHANGELOG.md section of VERSION")
    args = parser.parse_args(argv)

    if args.show:
        for name, value in read_versions().items():
            print(f"{name}: {value}")
        return 0
    if args.check:
        problems = check(args.check)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    if args.notes:
        section = changelog_section((ROOT / CHANGELOG).read_text(), args.notes)
        if section is None:
            print(f"{CHANGELOG} has no '## [{args.notes}]' section", file=sys.stderr)
            return 1
        print(section, end="")
        return 0
    if not args.version:
        parser.error("give a version, or use --show, --check or --notes")
    now = set_versions(args.version)
    print("\n".join(f"{name}: {value}" for name, value in now.items()))
    print(f"Next: add a '## [{args.version}] - <date>' section to {CHANGELOG}, commit, then tag v{args.version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
