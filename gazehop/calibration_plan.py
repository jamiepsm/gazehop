"""Where the calibration dots go (no GUI code, so it can be tested headless)."""
from __future__ import annotations

import math

from .geometry import Rect

EYES, NOSE, MOVE, GLIDE = "eyes", "nose", "move", "glide"
GLIDE_S = 40.0      # seconds the gliding red dot takes to go round a screen


def glide(r: Rect, t: float) -> tuple:
    """Where the gliding red dot is `t` seconds in: a 3:4 Lissajous curve over 8%..92% of the
    screen. It never jumps or turns sharply, so the eyes can follow it, and it leaves smaller gaps
    than a 4x4 grid of dots (largest 12% of the screen, not 20%)."""
    u = 2 * math.pi * t / GLIDE_S
    return r.x + r.w * (0.5 + 0.42 * math.sin(3 * u)), r.y + r.h * (0.5 + 0.42 * math.sin(4 * u))

def edge_points(r: Rect, o: Rect) -> list:
    """Three points just inside `r` along the edge it shares with `o` — where screen mix-ups happen."""
    along = (0.25, 0.5, 0.75)
    y0, y1, x0, x1 = max(r.y, o.y), min(r.bottom, o.bottom), max(r.x, o.x), min(r.right, o.right)
    pts = []
    if y1 - y0 > 50:
        if abs(r.right - o.x) < 2:
            pts += [(r.right - 0.05 * r.w, y0 + (y1 - y0) * a) for a in along]
        if abs(r.x - o.right) < 2:
            pts += [(r.x + 0.05 * r.w, y0 + (y1 - y0) * a) for a in along]
    if x1 - x0 > 50:
        if abs(r.bottom - o.y) < 2:
            pts += [(x0 + (x1 - x0) * a, r.bottom - 0.05 * r.h) for a in along]
        if abs(r.y - o.bottom) < 2:
            pts += [(x0 + (x1 - x0) * a, r.y + 0.05 * r.h) for a in along]
    return pts


def preview_spot(r: Rect, points, w: float, h: float, clear: float = 44):
    """Top-left corner (relative to `r`) for a w x h camera preview on screen `r` that keeps
    `clear` pixels away from every calibration dot in `points` (global pixels). Near the top first,
    under the hint line; None if no spot is free."""
    spots = [(r.w / 2 - w / 2, 120), (r.w / 2 - w / 2, r.h - h - 60),
             (60, r.h / 2 - h / 2), (r.w - w - 60, r.h / 2 - h / 2)]
    for x, y in spots:
        if all(not (x - clear < px - r.x < x + w + clear and y - clear < py - r.y < y + h + clear)
               for px, py in points):
            return x, y
    return None


def plan(screens) -> list:
    """Per screen, the list of (point, kind) steps in global pixels."""
    blocks = []
    for k, r in enumerate(screens):
        at = lambda fx, fy: (r.x + r.w * fx, r.y + r.h * fy)
        eyes = []
        for j, o in enumerate(screens):
            if j != k:
                eyes += edge_points(r, o)
        nose = [at(0.2, 0.2), at(0.8, 0.2), at(0.8, 0.8), at(0.2, 0.8), at(0.5, 0.5)]
        move = [at(0.5, 0.5), at(0.25, 0.3), at(0.75, 0.7)]
        blocks.append([(glide(r, 0), GLIDE)] + [(p, EYES) for p in eyes] + [(p, NOSE) for p in nose] + [(p, MOVE) for p in move])
    return blocks
