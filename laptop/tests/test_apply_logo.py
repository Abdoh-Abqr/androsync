"""branding/apply_logo.py with a PNG logo (Phase T1): everything it must write, on a throwaway copy of the folders."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("convert") is None and shutil.which("magick") is None, reason="no ImageMagick")


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    for rel in (
        "android/app/src/main/res/values",
        "android/app/src/main/res/drawable",
        "android/app/src/main/res/mipmap-anydpi-v26",
        "laptop/androsync/assets",
        "branding",
    ):
        (tmp_path / rel).mkdir(parents=True)
    shutil.copy(REPO / "android/app/src/main/res/values/colors.xml", tmp_path / "android/app/src/main/res/values/colors.xml")
    (tmp_path / "android/app/src/main/res/drawable/ic_launcher_foreground.xml").write_text("<vector/>")
    return tmp_path


def make_logo(path: Path, size: int = 600) -> None:
    image = Image.new("RGB", (size, size), (1, 2, 1))  # a dark square with a bright disc, like a badge on a black background
    ImageDraw.Draw(image).ellipse((40, 40, size - 40, size - 40), fill=(40, 200, 60))
    image.save(path, "PNG")


def run(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "branding/apply_logo.py"), "--root", str(tree), *args], capture_output=True, text=True
    )


def test_a_png_logo_is_recognised_even_with_an_svg_name_and_applied_everywhere(tree):
    make_logo(tree / "branding" / "logo.svg")  # a PNG saved as .svg, as delivered by some image tools
    result = run(tree)
    assert result.returncode == 0, result.stderr

    res = tree / "android/app/src/main/res"
    assert Image.open(res / "drawable-nodpi/ic_launcher_foreground.png").size == (432, 432)
    assert Image.open(res / "drawable-nodpi/ic_launcher_monochrome.png").size == (432, 432)
    assert not (res / "drawable/ic_launcher_foreground.xml").exists()  # the drawn vector is replaced by the picture
    assert "monochrome" in (res / "mipmap-anydpi-v26/ic_launcher.xml").read_text()
    assert 'ic_launcher_background">#010201<' in (res / "values/colors.xml").read_text()  # read from the corner

    assets = tree / "laptop/androsync/assets"
    for size in (48, 128, 256):
        assert Image.open(assets / f"androsync-{size}.png").size == (size, size)
    assert "data:image/png;base64," in (assets / "androsync.svg").read_text()
    assert Image.open(tree / "fastlane/metadata/android/en-US/images/icon.png").size == (512, 512)
    master = Image.open(tree / "branding/logo.png")
    assert master.size == (1024, 1024)
    assert master.getpixel((3, 3))[3] == 0  # the corners of a circle logo are transparent
    assert master.getpixel((512, 512))[3] == 255


def test_the_square_shape_keeps_the_corners(tree):
    make_logo(tree / "branding" / "logo.png")
    assert run(tree, "--shape", "square").returncode == 0
    assert Image.open(tree / "branding/logo.png").getpixel((3, 3))[3] == 255


def test_a_logo_that_is_not_square_or_too_small_is_refused(tree):
    Image.new("RGB", (600, 400)).save(tree / "branding" / "logo.png")
    assert run(tree).returncode != 0
    make_logo(tree / "branding" / "logo.png", size=300)
    assert "at least 512" in run(tree).stderr
