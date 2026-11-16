"""Where the calibration dots go (no GUI code, so it can be tested headless)."""
from __future__ import annotations

from .geometry import Rect

EYES, NOSE, MOVE = "eyes", "nose", "move"
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


def plan(screens) -> list:
    """Per screen, the list of (point, kind) steps in global pixels."""
    blocks = []
    for k, r in enumerate(screens):
        at = lambda fx, fy: (r.x + r.w * fx, r.y + r.h * fy)
        grid = (0.08, 0.36, 0.64, 0.92)
        eyes = [at(fx, fy) for fy in grid for fx in grid]
        for j, o in enumerate(screens):
            if j != k:
                eyes += edge_points(r, o)
        nose = [at(0.2, 0.2), at(0.8, 0.2), at(0.8, 0.8), at(0.2, 0.8), at(0.5, 0.5)]
        move = [at(0.5, 0.5), at(0.25, 0.3), at(0.75, 0.7)]
        blocks.append([(p, EYES) for p in eyes] + [(p, NOSE) for p in nose] + [(p, MOVE) for p in move])
    return blocks
