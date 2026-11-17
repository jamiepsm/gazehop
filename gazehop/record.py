"""Records what a calibration saw, for comparing eye-picture variants offline (tools/measure.py).

Per calibration sample: a greyscale crop of the face (PNG), the landmarks in that crop, MediaPipe's
head pose matrix, the label, and for the gliding dot its time on the path. Debugging aid for
developers: `gazehop calibrate --record DIR`.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict


def face_crop(gray, pts):
    """(crop, (x0, y0)): the face plus a 10% margin, so the eyes can be cut out again later."""
    x0, y0 = pts[:468].min(axis=0)
    x1, y1 = pts[:468].max(axis=0)
    m = 0.1 * (x1 - x0)
    h, w = gray.shape[:2]
    x0, y0, x1, y1 = max(0, int(x0 - m)), max(0, int(y0 - m)), min(w, int(x1 + m) + 1), min(h, int(y1 + m) + 1)
    return gray[y0:y1, x0:x1].copy(), (x0, y0)


class Recorder:
    def __init__(self, folder: str):
        os.makedirs(folder, exist_ok=True)
        self.folder, self.rows = folder, []

    def add(self, sample, raw, glide=None) -> None:
        """`raw`: (crop, offset, landmarks, matrix, frame size) from the tracker; `glide`: (screen
        index, seconds) on the gliding dot's path, if the sample comes from it."""
        import cv2
        crop, off, pts, matrix, size = raw
        name = f"{len(self.rows):05d}.png"
        cv2.imwrite(os.path.join(self.folder, name), crop)
        self.rows.append({"sample": asdict(sample), "image": name, "offset": list(off), "frame": list(size),
                          "pts": (pts - off).round(2).tolist(), "matrix": matrix.tolist(), "glide": glide})

    def save(self, screens) -> None:
        with open(os.path.join(self.folder, "recording.json"), "w", encoding="utf-8") as fh:
            json.dump({"screens": [list(r) for r in screens], "rows": self.rows}, fh)
        print(f"gazehop: recorded {len(self.rows)} samples in {self.folder}  (compare: python tools/measure.py {self.folder})")
