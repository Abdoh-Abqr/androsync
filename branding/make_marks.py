#!/usr/bin/env python3
"""Generates the three logo candidates as SVG (Phase T1). Run: python3 branding/make_marks.py

Every mark is drawn on a 108 x 108 canvas with the artwork inside the central 66 x 66 safe zone of an Android adaptive
icon, so the same geometry can be the app icon's foreground. `branding/logo.svg` is the chosen one (copied by hand).
"""
from __future__ import annotations

import math
from pathlib import Path

OUT = Path(__file__).parent
C = 54.0  # canvas centre


def pt(r: float, deg: float, cx: float = C, cy: float = C) -> tuple[float, float]:
    a = math.radians(deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def arc(r: float, start: float, end: float) -> str:
    x1, y1 = pt(r, start)
    x2, y2 = pt(r, end)
    sweep = 1 if end > start else 0
    large = 1 if abs(end - start) > 180 else 0
    return f"M{x1:.2f},{y1:.2f} A{r},{r} 0 {large} {sweep} {x2:.2f},{y2:.2f}"


def head(r: float, deg: float, size: float, direction: int) -> str:
    """A triangle arrowhead at angle ``deg`` on a circle of radius ``r``, pointing along the circle."""
    tx, ty = pt(r, deg)
    tangent = math.radians(deg + 90 * direction)
    nx, ny = math.cos(tangent), math.sin(tangent)  # direction of travel
    px, py = -ny, nx  # perpendicular
    tip = (tx + nx * size, ty + ny * size)
    a = (tx + px * size * 0.95, ty + py * size * 0.95)
    b = (tx - px * size * 0.95, ty - py * size * 0.95)
    return f"M{tip[0]:.2f},{tip[1]:.2f} L{a[0]:.2f},{a[1]:.2f} L{b[0]:.2f},{b[1]:.2f} Z"


def svg(background: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108" width="108" height="108">\n'
        f'  <rect width="108" height="108" rx="24" fill="{background}"/>\n{body}</svg>\n'
    )


def mark_a() -> str:
    """A loop of two arrows chasing each other around a folder."""
    r, w = 25.0, 6.5
    top = arc(r, 205, 335)
    bottom = arc(r, 25, 155)
    folder = (
        '  <path d="M42,46 h8 l3,3.5 h13 a2.5,2.5 0 0 1 2.5,2.5 v13 a2.5,2.5 0 0 1 -2.5,2.5 h-23 a2.5,2.5 0 0 1 -2.5,-2.5 v-16.5 a2.5,2.5 0 0 1 2.5,-2.5 z" fill="#FFB300"/>\n'
    )
    return (
        f'  <path d="{top}" fill="none" stroke="#FFFFFF" stroke-width="{w}" stroke-linecap="round"/>\n'
        f'  <path d="{bottom}" fill="none" stroke="#FFFFFF" stroke-width="{w}" stroke-linecap="round"/>\n'
        f'  <path d="{head(r, 335, 8.5, 1)}" fill="#FFFFFF"/>\n'
        f'  <path d="{head(r, 155, 8.5, 1)}" fill="#FFFFFF"/>\n' + folder
    )


def mark_b() -> str:
    """A folder with two clean arrows crossing it: one each way."""
    folder = (
        '  <path d="M24,34 a4,4 0 0 1 4,-4 h14 l6,7 h32 a4,4 0 0 1 4,4 v41 a4,4 0 0 1 -4,4 h-52 a4,4 0 0 1 -4,-4 z" fill="#FFFFFF"/>\n'
    )
    right = "M34,48 h24 v-6 l16,8 l-16,8 v-6 h-24 z"
    left = "M74,66 h-24 v-6 l-16,8 l16,8 v-6 h24 z"
    return folder + f'  <path d="{right}" fill="#00796B"/>\n  <path d="{left}" fill="#00796B"/>\n'


def mark_c() -> str:
    """Minimal: two chevron arrows in opposite directions, offset, in two colours."""
    top = "M30,42 H70 M60,32 L72,42 L60,52"
    bottom = "M78,66 H38 M48,56 L36,66 L48,76"
    return (
        f'  <path d="{top}" fill="none" stroke="#FFFFFF" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>\n'
        f'  <path d="{bottom}" fill="none" stroke="#4DD0E1" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>\n'
    )


MARKS = {
    "mark-a": ("#283593", mark_a),
    "mark-b": ("#00796B", mark_b),
    "mark-c": ("#263238", mark_c),
}

if __name__ == "__main__":
    for name, (background, body) in MARKS.items():
        (OUT / f"{name}.svg").write_text(svg(background, body()))
        print("wrote", OUT / f"{name}.svg")
