#!/usr/bin/env python3
"""Applies the logo in `branding/logo.svg` everywhere it is used (Phase T1).

    python3 branding/apply_logo.py --background "#283593"

Input: `branding/logo.svg`, a SQUARE svg with a TRANSPARENT background (viewBox 108 x 108 is ideal) whose artwork stays
inside the central 66 x 66 area (Android crops an adaptive icon to a circle or rounded square). `--background` is the
colour behind it.

It writes: the Android adaptive icon (foreground png, a white monochrome layer for themed icons, background colour), the
desktop launcher icon, the F-Droid store icon (512 px) and `branding/logo-512.png`. Needs ImageMagick (`convert`).
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def convert(args: list[str]) -> None:
    exe = shutil.which("magick") or shutil.which("convert")
    if exe is None:
        sys.exit("ImageMagick is needed (sudo apt install imagemagick)")
    argv = [exe, *args] if exe.endswith("convert") else [exe, "convert", *args]
    subprocess.run(argv, check=True)


def inner_svg(text: str) -> str:
    """The svg without its XML declaration, ready to be nested in another svg."""
    return re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", text)


def check(logo: Path) -> str:
    text = logo.read_text(encoding="utf-8")
    match = re.search(r'viewBox="\s*([\d.\-]+)[ ,]+([\d.\-]+)[ ,]+([\d.]+)[ ,]+([\d.]+)\s*"', text)
    if not match:
        sys.exit(f"{logo} needs a viewBox, for example viewBox=\"0 0 108 108\"")
    width, height = float(match.group(3)), float(match.group(4))
    if abs(width - height) > 0.01:
        sys.exit(f"{logo} must be square, its viewBox is {width} x {height}")
    return text


def apply(root: Path, background: str | None) -> list[Path]:
    logo = root / "branding" / "logo.svg"
    if not logo.exists():
        sys.exit(f"put your logo at {logo} first (square svg, transparent background)")
    text = check(logo)
    res = root / "android" / "app" / "src" / "main" / "res"
    colors = res / "values" / "colors.xml"
    if background is None:
        found = re.search(r'name="ic_launcher_background">(#[0-9A-Fa-f]{6,8})<', colors.read_text(encoding="utf-8"))
        background = found.group(1) if found else "#1565C0"
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", background):
        sys.exit("--background must look like #283593")

    written: list[Path] = []
    nodpi = res / "drawable-nodpi"
    nodpi.mkdir(parents=True, exist_ok=True)
    foreground = nodpi / "ic_launcher_foreground.png"
    convert(["-background", "none", "-density", "600", str(logo), "-resize", "432x432", f"PNG32:{foreground}"])
    written.append(foreground)
    mono = nodpi / "ic_launcher_monochrome.png"  # themed icons use only the shape: white where the logo is opaque
    convert([str(foreground), "-alpha", "extract", "-background", "white", "-alpha", "shape", f"PNG32:{mono}"])
    written.append(mono)
    old_vector = res / "drawable" / "ic_launcher_foreground.xml"
    if old_vector.exists():
        old_vector.unlink()  # the png replaces the drawn vector of the old icon

    adaptive = res / "mipmap-anydpi-v26" / "ic_launcher.xml"
    adaptive.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background" />\n'
        '    <foreground android:drawable="@drawable/ic_launcher_foreground" />\n'
        '    <monochrome android:drawable="@drawable/ic_launcher_monochrome" />\n'
        '</adaptive-icon>\n',
        encoding="utf-8",
    )
    written.append(adaptive)
    colors.write_text(
        re.sub(
            r'(name="ic_launcher_background">)#[0-9A-Fa-f]{6,8}(<)',
            rf"\g<1>{background}\g<2>",
            colors.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )
    written.append(colors)

    # Desktop and store icons are not cropped, so the logo goes on a rounded square of the background colour.
    wrapped = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108" width="108" height="108">\n'
        f'  <rect width="108" height="108" rx="24" fill="{background}"/>\n'
        f'  {inner_svg(text).replace("<svg", "<svg x=\"0\" y=\"0\" width=\"108\" height=\"108\"", 1)}\n'
        "</svg>\n"
    )
    desktop = root / "laptop" / "androsync" / "assets" / "androsync.svg"
    desktop.write_text(wrapped, encoding="utf-8")
    written.append(desktop)
    store = root / "fastlane" / "metadata" / "android" / "en-US" / "images" / "icon.png"
    store.parent.mkdir(parents=True, exist_ok=True)
    convert(["-background", "none", "-density", "800", str(desktop), "-resize", "512x512", f"PNG32:{store}"])
    written.append(store)
    big = root / "branding" / "logo-512.png"
    convert(["-background", "none", "-density", "800", str(desktop), "-resize", "512x512", f"PNG32:{big}"])
    written.append(big)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--background", help="colour behind the logo, like #283593 (default: the current one)")
    parser.add_argument("--root", default=str(ROOT), help="project folder (default: this repository)")
    parser.add_argument("--check", action="store_true", help="only check that branding/logo.svg is usable")
    args = parser.parse_args()
    root = Path(args.root)
    if args.check:
        check(root / "branding" / "logo.svg")
        print("logo.svg is usable")
        return 0
    for path in apply(root, args.background):
        print("wrote", path.relative_to(root))
    print("Next: rebuild the app (cd android && ./gradlew :app:assembleFullDebug) and look at the icon on the phone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
