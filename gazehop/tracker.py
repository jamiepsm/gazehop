"""Camera -> face features, on a background thread.

Feature vector (same layout as Eyeshift's tracker):
    [yaw, pitch, roll, faceX, faceY, faceWidth, irisX, irisY] + two 12x6 greyscale eye patches.
Frames stay in memory and are dropped as soon as they have been read.
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
import urllib.request

import numpy as np

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
PATCH_W, PATCH_H = 12, 6
# MediaPipe face-mesh landmark indices: (outer corner, inner corner, upper lid, lower lid, iris centre)
EYES = ((33, 133, 159, 145, 468), (263, 362, 386, 374, 473))


def ensure_model(data_dir: str) -> str:
    """The MediaPipe face landmark model (~4 MB) is downloaded once, from Google's model store."""
    path = os.path.join(data_dir, "face_landmarker.task")
    if not os.path.exists(path):
        os.makedirs(data_dir, exist_ok=True)
        print(f"gazehop: downloading the face landmark model to {path} (one time)...", file=sys.stderr)
        try:
            urllib.request.urlretrieve(MODEL_URL, path + ".part")
            os.replace(path + ".part", path)
        except Exception as e:
            raise RuntimeError(f"Could not download the face model: {e}\n"
                               f"Download it by hand from\n  {MODEL_URL}\nand save it as\n  {path}")
    return path


def pose_angles(m) -> tuple[float, float, float]:
    """Yaw, pitch, roll (radians) from a 4x4 face transformation matrix."""
    r = np.asarray(m, float)[:3, :3]
    r = r / max(np.linalg.norm(r[:, 0]), 1e-9)      # drop any scale
    yaw = math.atan2(r[0, 2], r[2, 2])
    pitch = math.asin(max(-1.0, min(1.0, -r[1, 2])))
    roll = math.atan2(r[1, 0], r[1, 1])
    return yaw, pitch, roll


def eye_box(cx: float, cy: float, eye_w: float, angle: float) -> list:
    """The four corners of the eye picture in the camera frame: 1.3 x 0.65 eye widths around the
    eye's centre, turned by `angle` (radians)."""
    pw, ph, c, s = eye_w * 1.3, eye_w * 0.65, math.cos(angle), math.sin(angle)
    return [(cx + c * dx - s * dy, cy + s * dx + c * dy) for dx, dy in
            ((-pw / 2, -ph / 2), (pw / 2, -ph / 2), (pw / 2, ph / 2), (-pw / 2, ph / 2))]


def eye_patch(gray, cx: float, cy: float, eye_w: float, angle: float = 0.0):
    """A small greyscale picture of the eye, turned level by `angle` (the tilt of the line between
    the eyes, radians) so a tilted head shows the same eye picture, and normalised so lighting
    doesn't matter. None if off-frame."""
    import cv2
    h, w = gray.shape[:2]
    if eye_w < 8 or any(not (0 <= x <= w and 0 <= y <= h) for x, y in eye_box(cx, cy, eye_w, angle)):
        return None
    k = 4       # sampled 4x finer, then averaged down: sampling a small eye directly would alias
    W, H = PATCH_W * k, PATCH_H * k
    sx, sy, c, s = eye_w * 1.3 / W, eye_w * 0.65 / H, math.cos(angle), math.sin(angle)
    to_frame = np.array([[c * sx, -s * sy, cx - c * sx * W / 2 + s * sy * H / 2],     # picture -> frame pixels
                         [s * sx, c * sy, cy - s * sx * W / 2 - c * sy * H / 2]])
    big = cv2.warpAffine(gray, to_frame, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    v = cv2.resize(big, (PATCH_W, PATCH_H), interpolation=cv2.INTER_AREA).astype(float).ravel()
    return ((v - v.mean()) / max(1.0, v.std())).tolist()


def eye_tilt(left_mid, right_mid) -> float:
    """The tilt of the line between the eyes' centres (radians): how far the head rolls."""
    return math.atan2(right_mid[1] - left_mid[1], right_mid[0] - left_mid[0])


def features(landmarks, matrix, gray):
    """landmarks: (478, 2) array in pixels. Returns the feature list, or None if the eyes are unreadable."""
    lm = np.asarray(landmarks, float)
    h, w = gray.shape[:2]
    yaw, pitch, roll = pose_angles(matrix)
    x0, y0 = lm[:468].min(axis=0)
    x1, y1 = lm[:468].max(axis=0)
    iris, eyes, patches = [], [], []
    for outer, inner, upper, lower, centre in EYES:
        a, b = lm[outer], lm[inner]
        axis = b - a
        eye_w = float(np.hypot(*axis))
        if eye_w < 8:
            return None     # face too small or too far to read the eyes
        u = axis / eye_w
        n = np.array([-u[1], u[0]])
        mid = (a + b) / 2
        d = lm[centre] - mid
        # iris position along / across the eye, in eye widths; 0.5 = centred
        iris.append((0.5 + float(d @ u) / eye_w, 0.5 + float(d @ n) / eye_w))
        eyes.append((mid, eye_w))
    tilt = eye_tilt(eyes[0][0], eyes[1][0])
    for mid, eye_w in eyes:
        patch = eye_patch(gray, mid[0], mid[1], eye_w, tilt)
        if patch is None:
            return None
        patches += patch
    # the two eyes' axes point in opposite image directions; flip one so "left"/"down" mean the same for both
    ix = (iris[0][0] + (1 - iris[1][0])) / 2
    iy = (iris[0][1] + (1 - iris[1][1])) / 2
    base = [yaw, pitch, roll, (x0 + x1) / 2 / w, (y0 + y1) / 2 / h, (x1 - x0) / w, ix, iy]
    return base + patches


# closed outlines of the two eyes, and the points around each iris (MediaPipe face-mesh indices)
EYE_RINGS = ((33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246),
             (263, 249, 390, 373, 374, 380, 381, 382, 362, 398, 384, 385, 386, 387, 388, 466))
IRIS_RINGS = ((468, 469, 470, 471, 472), (473, 474, 475, 476, 477))
NOSE_TIP, MOUTH = 1, (61, 291)
PREVIEW_W = 480


def draw_overlay(frame, pts, readable: bool):
    """The camera picture as the user should see it: mirrored like a selfie, with what gazehop
    reads marked on it — face box, eye outlines, irises, the two eye patches the model is fed, and
    an arrow for where the head points. Returns a small RGB array."""
    import cv2
    h, w = frame.shape[:2]
    scale = PREVIEW_W / w
    img = cv2.flip(cv2.resize(frame, (PREVIEW_W, int(round(h * scale))), interpolation=cv2.INTER_AREA), 1)
    green, amber, red, white = (90, 220, 60), (40, 180, 255), (70, 70, 255), (255, 255, 255)     # BGR

    def label(text, colour):
        # on a dark box, readable on any picture (a thick black copy as outline spaces its letters
        # wider than the text and shows as a second, shifted copy)
        (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(img, (4, 22 - th - 5), (16 + tw, 22 + base + 2), (0, 0, 0), -1)
        cv2.putText(img, text, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 1, cv2.LINE_AA)

    if pts is None or len(pts) < 478:
        label("No face found", red)
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    p = np.asarray(pts, float) * scale
    p[:, 0] = img.shape[1] - 1 - p[:, 0]        # mirror the points along with the picture
    q = lambda i: (int(round(p[i, 0])), int(round(p[i, 1])))
    colour = green if readable else amber
    x0, y0 = p[:468].min(axis=0)
    x1, y1 = p[:468].max(axis=0)
    cv2.rectangle(img, (int(x0), int(y0)), (int(x1), int(y1)), colour, 1, cv2.LINE_AA)
    # the picture is mirrored, so the eyes swap sides: the tilt is measured the other way round
    tilt = eye_tilt((p[EYES[1][0]] + p[EYES[1][1]]) / 2, (p[EYES[0][0]] + p[EYES[0][1]]) / 2)
    for ring, iris, (outer, inner, *_rest) in zip(EYE_RINGS, IRIS_RINGS, EYES):
        cv2.polylines(img, [np.array([q(i) for i in ring], np.int32)], True, colour, 1, cv2.LINE_AA)
        rad = max(2, int(round(np.mean([np.hypot(*(p[i] - p[iris[0]])) for i in iris[1:]]))))
        cv2.circle(img, q(iris[0]), rad, white, 1, cv2.LINE_AA)
        cv2.circle(img, q(iris[0]), 1, red, 2, cv2.LINE_AA)
        mid, eye_w = (p[outer] + p[inner]) / 2, float(np.hypot(*(p[outer] - p[inner])))
        box = np.array(eye_box(mid[0], mid[1], eye_w, tilt), np.int32)     # the pixels the model looks at
        cv2.polylines(img, [box], True, white, 1, cv2.LINE_AA)
    # head direction: how far the nose tip sits from the middle of the face, exaggerated
    centre = (p[EYES[0][0]] + p[EYES[1][0]] + p[MOUTH[0]] + p[MOUTH[1]]) / 4
    tip = centre + (p[NOSE_TIP] - centre) * 4
    cv2.arrowedLine(img, (int(centre[0]), int(centre[1])), (int(tip[0]), int(tip[1])), amber, 2, cv2.LINE_AA, tipLength=0.25)
    label("Eyes readable" if readable else "Face found, eyes not readable (too far or turned away)", colour)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def open_camera(index: int):
    import cv2
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        return None
    # compressed video first: uncompressed 720p is capped at about 10 frames/s on most USB webcams
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    # bigger eyes in the frame = better gaze; cameras that can't do 720p fall back on their own
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return cap


def list_cameras(skip=()) -> list:
    """(index, width, height) of every camera that delivers a picture. `skip`: indices to leave
    alone — a camera that is in use cannot be opened a second time."""
    found = []
    for i in range(6):
        cap = None if i in skip else open_camera(i)
        if cap is None:
            continue
        ok, frame = cap.read()
        cap.release()
        if ok:
            found.append((i, frame.shape[1], frame.shape[0]))
    return found


class Tracker:
    FPS = 25

    def __init__(self, camera: int, model_path: str):
        self.camera, self.model_path = camera, model_path
        self.error: str | None = None
        self._latest = None         # (seq, features | None, monotonic time, raw | None)
        self.record = False             # also hand out the raw face (record.Recorder) with each reading
        self._seq = 0
        self.frames = self.faces = 0    # counters for the speed report
        self.preview = False            # set True while a camera view is open
        self._preview = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gazehop-camera", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def latest(self):
        with self._lock:
            return self._latest

    def latest_preview(self):
        """The newest annotated camera picture (RGB array), if the preview is switched on."""
        with self._lock:
            return self._preview

    def _publish(self, f, raw=None) -> None:
        with self._lock:
            self._seq += 1
            self.frames += 1
            self.faces += f is not None
            self._latest = (self._seq, f, time.monotonic(), raw)

    def _run(self) -> None:
        try:
            import cv2
            import mediapipe as mp
            # OpenCV's worker threads (one per core) keep spinning between frames; our pictures are
            # too small for them to help. One thread: same frame rate, the camera loop's CPU from
            # 118% of a core to 32% (measured at 17 fps on 12 cores).
            cv2.setNumThreads(1)
            vision = mp.tasks.vision
            cap = open_camera(self.camera)
            if cap is None:
                self.error = f"Cannot open camera {self.camera}. Try `gazehop cameras` and --camera N."
                return
            options = vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=self.model_path),
                running_mode=vision.RunningMode.VIDEO, num_faces=1,
                output_facial_transformation_matrixes=True)
            t0, last_ts = time.monotonic(), -1
            with vision.FaceLandmarker.create_from_options(options) as detector:
                while not self._stop.is_set():
                    start = time.monotonic()
                    ok, frame = cap.read()
                    if not ok:
                        self._publish(None)
                        time.sleep(0.2)
                        continue
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    ts = max(last_ts + 1, int((start - t0) * 1000))
                    last_ts = ts
                    res = detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
                    f = pts = raw = None
                    if res.face_landmarks and res.facial_transformation_matrixes:
                        h, w = frame.shape[:2]
                        pts = np.array([(p.x * w, p.y * h) for p in res.face_landmarks[0]])
                        if len(pts) >= 478:
                            gray, matrix = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), res.facial_transformation_matrixes[0]
                            f = features(pts, matrix, gray)
                            if self.record and f is not None:
                                from .record import face_crop
                                raw = (*face_crop(gray, pts), pts, np.asarray(matrix), (w, h))
                    if self.preview:
                        picture = draw_overlay(frame, pts, f is not None)
                        with self._lock:
                            self._preview = picture
                    self._publish(f, raw)
                    self._stop.wait(max(0.0, 1 / self.FPS - (time.monotonic() - start)))
            cap.release()
        except Exception as e:
            self.error = f"Camera/tracker failed: {e}"
