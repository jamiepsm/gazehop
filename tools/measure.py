"""Compares ways of cutting out the eye pictures, on one recorded calibration.

    python -m gazehop calibrate --record DIR      # calibrate once, saving what the camera saw
    python tools/measure.py DIR

Prints the held-out error (median px over every 4th calibration step) for each variant, all on the
same frames, so differences come from the variant. With the gliding red dot it also tries several
eye lags (calibration.LAG). To try a new variant, add a function to VARIANTS.
"""
from __future__ import annotations

import json
import math
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gazehop.calibration_plan import glide  # noqa: E402
from gazehop.geometry import Rect  # noqa: E402
from gazehop.model import GazeModel, Sample  # noqa: E402
from gazehop.tracker import EYES, PATCH_H, PATCH_W, eye_patch, eye_tilt, features  # noqa: E402

FOV = math.radians(63)      # vertical field of view MediaPipe's head pose assumes


def level(row):
    """As gazehop does now: cut out along the line between the eyes."""
    f = features(row["pts"], row["matrix"], row["crop"])
    return f and f[8:]


def straight(row):
    """As gazehop 0.3.0 did: cut out level with the camera picture."""
    out = []
    for outer, inner, *_ in EYES:
        a, b = row["pts"][outer], row["pts"][inner]
        mid = (a + b) / 2
        patch = eye_patch(row["crop"], mid[0], mid[1], float(np.hypot(*(b - a))))
        if patch is None:
            return None
        out += patch
    return out


def normalised(row):
    """3D: each eye as a virtual camera straight in front of it would see it, turned with the head
    (Zhang et al., "Revisiting data normalization for appearance-based gaze estimation", 2018).
    Also undoes turning the head left/right, which "level" does not. The eye's distance is the
    head's (from the pose matrix); close enough for the angles."""
    (w, h), (ox, oy), m = row["frame"], row["offset"], row["matrix"]
    f = h / 2 / math.tan(FOV / 2)
    K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]])
    head_right = (np.diag([1, -1, -1]) @ m[:3, :3])[:, 0]      # MediaPipe's camera axes (y up, z back) -> OpenCV's
    W, H = PATCH_W * 4, PATCH_H * 4         # sampled 4x finer, then averaged down, as eye_patch does
    out = []
    for outer, inner, *_ in EYES:
        a, b = row["pts"][outer], row["pts"][inner]
        mid, eye_w = (a + b) / 2 + (ox, oy), float(np.hypot(*(b - a)))
        fwd = np.linalg.solve(K, [mid[0], mid[1], 1.0])
        fwd /= np.linalg.norm(fwd)
        down = np.cross(fwd, head_right)
        down /= np.linalg.norm(down)
        R = np.stack([np.cross(down, fwd), down, fwd])      # rows: the virtual camera's right, down, forward
        fn = f * W / (1.3 * eye_w)                          # the eye fills the picture as in eye_patch
        Kn = np.array([[fn, 0, W / 2], [0, fn, H / 2], [0, 0, 1]])
        to_virtual = Kn @ R @ np.linalg.inv(K) @ np.array([[1, 0, ox], [0, 1, oy], [0, 0, 1]])
        big = cv2.warpPerspective(row["crop"], to_virtual, (W, H), flags=cv2.INTER_LINEAR)
        v = cv2.resize(big, (PATCH_W, PATCH_H), interpolation=cv2.INTER_AREA).astype(float).ravel()
        out += ((v - v.mean()) / max(1.0, v.std())).tolist()
    return out


VARIANTS = {"straight (0.3.0)": straight, "level (now)": level, "3D normalised": normalised}


def load(folder: str):
    with open(os.path.join(folder, "recording.json"), encoding="utf-8") as fh:
        rec = json.load(fh)
    for row in rec["rows"]:
        row["crop"] = cv2.imread(os.path.join(folder, row["image"]), cv2.IMREAD_GRAYSCALE)
        row["pts"], row["matrix"] = np.array(row["pts"]), np.array(row["matrix"])
    return [Rect(*r) for r in rec["screens"]], rec["rows"]


def fit(screens, rows, eyes, lag=None):
    """The calibration refitted with other eye pictures (`eyes`, one per row) and, for the gliding
    dot, labels for another lag."""
    samples = []
    for row, e in zip(rows, eyes):
        if e is None:
            continue
        s = dict(row["sample"], f=row["sample"]["f"][:8] + list(e))
        if lag is not None and row["glide"]:
            k, t = row["glide"]
            s["x"], s["y"] = glide(screens[k], t - lag)
        samples.append(Sample(**s))
    return GazeModel.fit(samples, screens), len(samples)


def report(m, n) -> str:
    if m is None or m.error is None:
        return f"   too little data ({n} samples)"
    return f"{m.error:6.0f} px  ({n} samples, non-linear correction {'kept' if m.kernel else 'not kept'})"


def axes_check(rows) -> float:
    """Median angle (degrees) between the head's left-right axis from the pose matrix and the line
    between the eyes in the picture. Near 0 means "3D normalised" reads the pose matrix right;
    near twice the head tilt means an axis is flipped."""
    d = []
    for row in rows:
        right = (np.diag([1, -1, -1]) @ row["matrix"][:3, :3])[:, 0]
        mids = [(row["pts"][o] + row["pts"][i]) / 2 for o, i, *_ in EYES]
        d.append(math.degrees(math.atan2(right[1], right[0]) - eye_tilt(*mids)))
    return float(np.median(np.abs((np.array(d) + 180) % 360 - 180)))


def main(folder: str) -> None:
    screens, rows = load(folder)
    print(f"pose matrix vs eye line: {axes_check(rows):.1f} degrees apart (should be near 0)")
    eyes = {name: [fn(row) for row in rows] for name, fn in VARIANTS.items()}
    for name, e in eyes.items():
        print(f"{name:20s}", report(*fit(screens, rows, e)))
    if any(row["glide"] for row in rows):
        for lag in (0.0, 0.05, 0.1, 0.15, 0.2, 0.3):
            print(f"lag {lag:.2f} s{'':11s}", report(*fit(screens, rows, eyes["level (now)"], lag)))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
