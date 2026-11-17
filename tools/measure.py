"""Compares ways of cutting out the eye pictures, on one recorded calibration.

    python -m gazehop calibrate --record DIR      # calibrate once, saving what the camera saw
    python tools/measure.py DIR

Prints the held-out error (median px over every 4th calibration step) for each variant, all on the
same frames, so differences come from the variant. With the gliding red dot it also tries several
eye lags (calibration.LAG). To try a new variant, add a function to VARIANTS.
"""
from __future__ import annotations

import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gazehop.calibration_plan import glide  # noqa: E402
from gazehop.geometry import Rect  # noqa: E402
from gazehop.model import GazeModel, Sample  # noqa: E402
from gazehop.tracker import EYES, eye_patch, features  # noqa: E402


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


VARIANTS = {"straight (0.3.0)": straight, "level (now)": level}


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


def main(folder: str) -> None:
    screens, rows = load(folder)
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
