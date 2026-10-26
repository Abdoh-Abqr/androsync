"""Tell a friend (Phase V5): the same short text as the phone app's "Tell a friend", a QR code of the project page, and
a copy to the clipboard. Nothing is sent anywhere; the person decides who gets it."""

from __future__ import annotations

import shutil
import subprocess

from androsync.support import REPO_URL

SHARE_TEXT = (
    "AndroSync keeps folders identical between your Linux computer and your Android phone - free, open source, "
    f"no cloud. {REPO_URL}"
)


def qr_matrix(text: str) -> list[list[bool]] | None:
    """The QR code of ``text`` as rows of dark/light cells, or ``None`` when the optional ``qrcode`` package is missing."""
    try:
        import qrcode
    except ImportError:
        return None
    qr = qrcode.QRCode(border=1)
    qr.add_data(text)
    qr.make(fit=True)
    return [[bool(cell) for cell in row] for row in qr.get_matrix()]


def qr_lines(text: str) -> list[str] | None:
    matrix = qr_matrix(text)
    return None if matrix is None else ["".join("##" if cell else "  " for cell in row) for row in matrix]


def copy_to_clipboard(text: str) -> str | None:
    """Copy ``text`` with ``wl-copy`` or ``xclip`` when one is installed; the tool's name, or ``None`` if none worked."""
    for argv in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(argv[0]) is None:
            continue
        try:
            subprocess.run(argv, input=text, text=True, check=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        return argv[0]
    return None
