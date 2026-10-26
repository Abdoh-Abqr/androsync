#!/usr/bin/env python3
"""Make the 1280x640 social preview image (shown when the project link is shared) from branding/logo-512.png.

    python3 branding/make_social.py            writes branding/social-preview.png

Upload it yourself in the repository's Settings, "Social preview". Needs Pillow (the same as apply_logo.py).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
WIDTH, HEIGHT = 1280, 640
BACKGROUND = (18, 22, 27)
TEXT = (230, 233, 237)
MUTED = (154, 165, 177)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> int:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    logo = Image.open(HERE / "logo-512.png").convert("RGBA").resize((320, 320))
    image.paste(logo, (120, (HEIGHT - 320) // 2), logo)
    draw = ImageDraw.Draw(image)
    draw.text((500, 190), "AndroSync", font=_font(96, bold=True), fill=TEXT)
    draw.text((500, 320), "Keeps folders identical between", font=_font(38), fill=MUTED)
    draw.text((500, 370), "your devices: Linux and Android.", font=_font(38), fill=MUTED)
    draw.text((500, 450), "Free, open source, no cloud.", font=_font(38), fill=TEXT)
    out = HERE / "social-preview.png"
    image.save(out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
