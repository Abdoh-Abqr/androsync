#!/usr/bin/env python3
"""Applies the logo in `branding/logo.svg` (or `branding/logo.png`) everywhere it is used (Phase T1).

    python3 branding/apply_logo.py --background "#283593"      # an SVG logo
    python3 branding/apply_logo.py                              # a PNG logo (the colour behind it is read from its corner)

A PNG logo is sniffed by its content, so a PNG saved with an `.svg` name works. It should be square and at least 512 px;
`--shape circle` (default) makes everything outside the circle transparent, `--shape square` keeps it as it is. The PNG route
needs Pillow (`pip install pillow`).

Input: `branding/logo.svg`, a SQUARE svg with a TRANSPARENT background (viewBox 108 x 108 is ideal) whose artwork stays
inside the central 66 x 66 area (Android crops an adaptive icon to a circle or rounded square). `--background` is the
colour behind it.

It writes: the Android adaptive icon (foreground png, a white monochrome layer for themed icons, background colour), the
desktop launcher icon (svg and the 48/128/256 px pngs the window and launcher use), the F-Droid store icon (512 px) and
`branding/logo-512.png`. Needs ImageMagick (`convert`).
"""
from __future__ import annotations

import argparse
import base64
import io
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
    assets = root / "laptop" / "androsync" / "assets"
    for size in (48, 128, 256):  # the window icon and the launcher's PNG icons (the desktop cannot use the svg everywhere)
        png = assets / f"androsync-{size}.png"
        convert(["-background", "none", "-density", "800", str(desktop), "-resize", f"{size}x{size}", f"PNG32:{png}"])
        written.append(png)
    big = root / "branding" / "logo-512.png"
    convert(["-background", "none", "-density", "800", str(desktop), "-resize", "512x512", f"PNG32:{big}"])
    written.append(big)
    return written


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def is_png(path: Path) -> bool:
    return path.exists() and path.read_bytes()[:8] == PNG_MAGIC


def find_logo(root: Path) -> Path | None:
    for name in ("logo.svg", "logo.png"):
        candidate = root / "branding" / name
        if candidate.exists():
            return candidate
    return None


def apply_png(root: Path, logo: Path, background: str | None, shape: str) -> list[Path]:
    """The PNG route: one square picture becomes the Android icon, the desktop icons and the store icon."""
    try:
        from PIL import Image, ImageChops, ImageDraw
    except ImportError:
        sys.exit("a PNG logo needs Pillow: pip install pillow")

    source = Image.open(logo).convert("RGBA")
    if source.width != source.height:
        sys.exit(f"{logo} must be square, it is {source.width} x {source.height}")
    if source.width < 512:
        sys.exit(f"{logo} is only {source.width} px; use at least 512")
    if background is None:
        r, g, b, _a = source.getpixel((2, 2))
        background = f"#{r:02X}{g:02X}{b:02X}"
    master = source.resize((1024, 1024), Image.LANCZOS)
    if shape == "circle":
        mask = Image.new("L", (1024 * 4, 1024 * 4), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 1024 * 4 - 1, 1024 * 4 - 1), fill=255)
        master.putalpha(ImageChops.multiply(mask.resize((1024, 1024), Image.LANCZOS), master.getchannel("A")))

    def save(image: "Image.Image", path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "PNG", optimize=True)  # saved without the metadata of the original
        return path

    written: list[Path] = [save(master, root / "branding" / "logo.png")]
    res = root / "android" / "app" / "src" / "main" / "res"
    # Android crops an adaptive icon to a mask and only 66 of 108 dp are sure to stay visible: put the whole badge inside.
    canvas = Image.new("RGBA", (432, 432), (0, 0, 0, 0))
    badge = master.resize((int(432 * 0.62), int(432 * 0.62)), Image.LANCZOS)
    canvas.paste(badge, ((432 - badge.width) // 2, (432 - badge.height) // 2), badge)
    foreground = save(canvas, res / "drawable-nodpi" / "ic_launcher_foreground.png")
    written.append(foreground)
    gray = canvas.convert("L")  # themed icons use one colour: white, as bright as the artwork is
    mono = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
    mono.putalpha(ImageChops.multiply(gray, canvas.getchannel("A")))
    written.append(save(mono, res / "drawable-nodpi" / "ic_launcher_monochrome.png"))
    old_vector = res / "drawable" / "ic_launcher_foreground.xml"
    if old_vector.exists():
        old_vector.unlink()
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
    colors = res / "values" / "colors.xml"
    colors.write_text(
        re.sub(r'(name="ic_launcher_background">)#[0-9A-Fa-f]{6,8}(<)', rf"\g<1>{background}\g<2>", colors.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    written.append(colors)

    assets = root / "laptop" / "androsync" / "assets"
    for size in (48, 128, 256):
        written.append(save(master.resize((size, size), Image.LANCZOS), assets / f"androsync-{size}.png"))
    buffer = io.BytesIO()
    master.resize((256, 256), Image.LANCZOS).save(buffer, "PNG", optimize=True)
    desktop = assets / "androsync.svg"  # the scalable icon: the same picture inside an svg, so every size shows the new logo
    desktop.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 256 256" width="256" height="256">\n'
        f'  <image width="256" height="256" xlink:href="data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode()}"/>\n'
        "</svg>\n",
        encoding="utf-8",
    )
    written.append(desktop)
    written.append(save(master.resize((512, 512), Image.LANCZOS), root / "fastlane" / "metadata" / "android" / "en-US" / "images" / "icon.png"))
    written.append(save(master.resize((512, 512), Image.LANCZOS), root / "branding" / "logo-512.png"))
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--background", help="colour behind the logo, like #283593 (default: the current one; for a PNG: its corner)")
    parser.add_argument("--shape", choices=["circle", "square"], default="circle", help="PNG logos only: cut a circle (default) or keep the square")
    parser.add_argument("--root", default=str(ROOT), help="project folder (default: this repository)")
    parser.add_argument("--logo", help="the logo file to use instead of branding/logo.svg or branding/logo.png")
    parser.add_argument("--check", action="store_true", help="only check that branding/logo.svg is usable")
    args = parser.parse_args()
    root = Path(args.root)
    logo = Path(args.logo) if args.logo else find_logo(root)
    if logo is None or not logo.exists():
        sys.exit(f"put your logo at {root / 'branding' / 'logo.svg'} (or logo.png) first")
    if is_png(logo):
        if args.check:
            print(f"{logo.name} is a PNG logo and is usable")
            return 0
        for path in apply_png(root, logo, args.background, args.shape):
            print("wrote", path.relative_to(root))
        print("Next: rebuild the app, run `androsync service install-launcher`, reopen the window.")
        return 0
    if args.check:
        check(logo)
        print(f"{logo.name} is usable")
        return 0
    for path in apply(root, args.background):
        print("wrote", path.relative_to(root))
    print("Next: rebuild the app (cd android && ./gradlew :app:assembleFullDebug) and look at the icon on the phone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
