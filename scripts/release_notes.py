#!/usr/bin/env python3
"""Turn a CHANGELOG.md section into the texts for announcing a release (Phase V5).

    python3 scripts/release_notes.py 0.3.0            print the post and the F-Droid changelog
    python3 scripts/release_notes.py Unreleased       preview them before the version exists
    python3 scripts/release_notes.py 0.3.0 --write    also write fastlane/metadata/android/en-US/changelogs/<versionCode>.txt

The GitHub release body itself is `scripts/bump_version.py --notes <version>` (the release workflow uses it). Nothing here
posts anywhere: you copy the text.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bump_version  # noqa: E402

ROOT = bump_version.ROOT
REPO = "https://github.com/Abdoh-Abqr/androsync"
FDROID_LIMIT = 500  # F-Droid cuts a changelog file at 500 characters
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")


def bullets(section: str) -> list[str]:
    """The top-level bullets of a changelog section, one string each, markdown marks and line breaks removed."""
    items: list[str] = []
    for line in section.splitlines():
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif line.startswith("  ") and items and line.strip():
            items[-1] += " " + line.strip()
    return [_CODE.sub(r"\1", _BOLD.sub(r"\1", item)) for item in items]


def first_sentence(text: str) -> str:
    match = re.search(r"(?<=[.!?])\s", text)
    return text[: match.start()].strip() if match else text.strip()


def post(version: str, items: list[str]) -> str:
    """A short forum or Mastodon post: a title, up to three bullets and the link."""
    lines = [f"AndroSync {version} is out: keep folders identical between Linux and Android, no cloud.", ""]
    lines += [f"- {first_sentence(item)}" for item in items[:3]]
    lines += ["", f"Free and open source (GPL-3.0-or-later): {REPO}"]
    return "\n".join(lines)


def fdroid_changelog(items: list[str]) -> str:
    """One sentence per line, as many as fit in F-Droid's 500 characters."""
    out: list[str] = []
    size = 0
    for item in items:
        line = first_sentence(item)
        if size + len(line) + 1 > FDROID_LIMIT:
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", help="a version with a CHANGELOG.md entry, or Unreleased")
    parser.add_argument("--write", action="store_true", help="write the F-Droid changelog file for the current versionCode")
    args = parser.parse_args(argv)

    text = (ROOT / bump_version.CHANGELOG).read_text(encoding="utf-8")
    section = bump_version.changelog_section(text, args.version)
    if section is None:
        print(f"release_notes: no entry for {args.version} in {bump_version.CHANGELOG}", file=sys.stderr)
        return 1
    items = bullets(section)
    if not items:
        print(f"release_notes: the entry for {args.version} has no bullet points", file=sys.stderr)
        return 1
    print("== Post (copy it) ==")
    print(post(args.version, items))
    print()
    print("== F-Droid changelog ==")
    changelog = fdroid_changelog(items)
    print(changelog, end="")
    if args.write:
        code = bump_version.read_versions()["versionCode"]
        out = ROOT / "fastlane/metadata/android/en-US/changelogs" / f"{code}.txt"
        out.write_text(changelog, encoding="utf-8")
        print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
