"""Gaze model: face features -> screen point.

A Python port of the model in Eyeshift (MIT, (c) 2026 Sarthak Chhabra):
two ridge regressions (head pose only picks the screen; head + eyes finds the
point on it) plus a local correction layer learned from everyday clicks.
gazehop adds a non-linear correction of the point, learned from the calibration
and kept only for people it measurably helps.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field

import numpy as np

from .geometry import Rect

VERSION = 2             # 2: eye pictures are turned level; version 1 calibrations no longer fit
N_POSE = 8              # yaw, pitch, roll, faceX, faceY, faceWidth, irisX, irisY
PER_SITUATION = 3
MAX_LEARNED = 1500
NUDGE_PRIOR = 3.0
# the non-linear correction (RBF kernel ridge): settings that cut the held-out error by 24% on the
# first real 4-screen calibration; it is only kept where it beats the linear model by 5%
KERNEL_GAMMA, KERNEL_LAM, KERNEL_GAIN = 1.0, 0.3, 0.95
KERNEL_MAX = 3000       # calibration samples it keeps: fitting time grows with the cube of this


@dataclass
class Sample:
    """One face reading paired with where on screen the user was looking."""
    f: list
    x: float
    y: float
    hx: float | None = None     # where the head pointed, if known (trains the screen model)
    hy: float | None = None
    w: float = 1.0              # corrections 3, clicks 1, confirmations 0.5
    key: str | None = None      # learned samples: which situation this belongs to
    rx: float | None = None     # ...and how far off the calibrated model was for it
    ry: float | None = None
    g: int | None = None        # calibration step (a dot, a second of a gliding dot), for held-out checks


def step_of(s: Sample):
    """Which group a sample is held out with: its calibration step, or (saved before steps were
    numbered) its dot."""
    return s.g if s.g is not None else (int(s.x), int(s.y))


def numbered(keys) -> np.ndarray:
    """Group keys -> 0, 1, 2... in order of first appearance; groups with a number divisible by 4
    are held out."""
    ids: dict = {}
    return np.array([ids.setdefault(k, len(ids)) for k in keys], int)


def head_features(r) -> list:
    """Dividing by face width makes the same head turn count for more when you sit back."""
    w = max(r[5], 0.05)
    return [r[0], r[1], r[2], r[3], r[4], r[5], r[0] / w, r[1] / w]


def full_features(r) -> list:
    w = max(r[5], 0.05)
    return head_features(r) + [r[6], r[7], (r[6] - 0.5) / w, (r[7] - 0.5) / w] + list(r[8:])


@dataclass
class Regressor:
    mean: list
    std: list
    wx: list
    wy: list
    lam: float = 3.0

    def __post_init__(self):
        self._mean = np.asarray(self.mean, float)
        self._std = np.asarray(self.std, float)
        self._w = np.stack([np.asarray(self.wx, float), np.asarray(self.wy, float)], axis=1)

    def predict(self, v) -> tuple[float, float]:
        z = np.concatenate(([1.0], (np.asarray(v, float) - self._mean) / self._std))
        p = z @ self._w
        return float(p[0]), float(p[1])

    def predict_many(self, X):
        Z = (np.asarray(X, float) - self._mean) / self._std
        return np.hstack([np.ones((len(Z), 1)), Z]) @ self._w

    @staticmethod
    def fit(xs, targets, weights, lam: float) -> "Regressor | None":
        X = np.asarray(xs, float)
        if X.ndim != 2 or len(X) < 20:
            return None
        Y = np.asarray(targets, float)
        w = np.asarray(weights, float)
        mean = X.mean(axis=0)
        std = np.maximum(X.std(axis=0), 1e-6)
        Z = np.hstack([np.ones((len(X), 1)), (X - mean) / std])
        A = Z.T @ (Z * w[:, None])
        A[np.arange(1, A.shape[0]), np.arange(1, A.shape[0])] += lam    # don't penalise the intercept
        try:
            W = np.linalg.solve(A, Z.T @ (Y * w[:, None]))
        except np.linalg.LinAlgError:
            return None
        return Regressor(mean.tolist(), std.tolist(), W[:, 0].tolist(), W[:, 1].tolist(), lam)

    @staticmethod
    def best_fit(xs, targets, weights, lam: float | None, groups=None) -> "Regressor | None":
        """Picks the regularisation by holding out every 4th group (by default: target point) with
        all of its frames, so neighbouring frames can't leak, then refits on everything with the winner."""
        if lam is not None:
            return Regressor.fit(xs, targets, weights, lam)
        group = numbered(groups if groups is not None else [(int(t[0]), int(t[1])) for t in targets])
        test = [i for i, g in enumerate(group) if g % 4 == 0]
        train = [i for i, g in enumerate(group) if g % 4 != 0]
        if len(test) < 5 or len(train) < 20:
            return Regressor.fit(xs, targets, weights, 3.0)
        X, Y, Wt = np.asarray(xs, float), np.asarray(targets, float), np.asarray(weights, float)

        def err(l):
            r = Regressor.fit(X[train], Y[train], Wt[train], l)
            if r is None:
                return math.inf
            Z = np.hstack([np.ones((len(test), 1)), (X[test] - r._mean) / r._std])
            return float(np.hypot(*(Z @ r._w - Y[test]).T).sum())

        return Regressor.fit(xs, targets, weights, min([0.3, 3.0, 30.0, 300.0], key=err))

    def to_json(self):
        return {"mean": self.mean, "std": self.std, "wx": self.wx, "wy": self.wy, "lam": self.lam}


def _rbf(A, B, gamma: float):
    """Similarity of every row of A to every row of B: 1 for identical, toward 0 for far apart."""
    d2 = (A * A).sum(axis=1)[:, None] + (B * B).sum(axis=1)[None, :] - 2 * A @ B.T
    return np.exp(-gamma * np.maximum(d2, 0) / A.shape[1])


@dataclass
class Kernel:
    """What the linear model gets wrong, learned non-linearly from calibration samples: a
    prediction is nudged by how wrong the linear model was for similar-looking face readings."""
    mean: list
    std: list
    rows: list          # which calibration samples it compares against
    alpha: list         # their weights, [x, y] each
    gamma: float = KERNEL_GAMMA

    def attach(self, calib) -> "Kernel":
        """Rebuilds the arrays from the calibration samples (they are not stored twice)."""
        self._mean, self._std, self._alpha = np.asarray(self.mean), np.asarray(self.std), np.asarray(self.alpha)
        self._a = (np.array([full_features(calib[i].f) for i in self.rows], float) - self._mean) / self._std
        return self

    def predict_many(self, X):
        return _rbf((np.asarray(X, float) - self._mean) / self._std, self._a, self.gamma) @ self._alpha

    @staticmethod
    def fit(X, R, rows) -> "Kernel":
        """X: features of the calibration samples `rows`; R: the linear model's error on them."""
        mean, std = X.mean(axis=0), np.maximum(X.std(axis=0), 1e-6)
        A = (X - mean) / std
        alpha = np.linalg.solve(_rbf(A, A, KERNEL_GAMMA) + KERNEL_LAM * np.eye(len(A)), R)
        return Kernel(mean.tolist(), std.tolist(), [int(i) for i in rows], alpha.tolist())


def choose_kernel(calib, lam: float):
    """(lambda for the linear model under the kernel, held-out error in px), or (None, error of
    the linear model alone) if no kernel helps. Every 4th calibration step is held out; on them a
    linear model plus kernel learned on the other steps must beat the linear model alone. The
    linear model may be smoother under a kernel (it learns the curves), so 10 x lambda is tried too."""
    group = numbered(step_of(s) for s in calib)
    test, train = group % 4 == 0, group % 4 != 0
    if test.sum() < 5 or train.sum() < 20:
        return None, None
    X = np.array([full_features(s.f) for s in calib], float)
    Y, W = np.array([(s.x, s.y) for s in calib], float), np.array([s.w for s in calib], float)
    rows = _cap(np.flatnonzero(train))
    err = lambda P: float(np.median(np.hypot(*(P[test] - Y[test]).T)))
    plain, best = None, (None, None)
    for l in (lam, 10 * lam):
        lin = Regressor.fit(X[train], Y[train], W[train], l)
        if lin is None:
            continue
        P = lin.predict_many(X)
        plain = err(P) if plain is None else plain      # the linear model alone, as the app fits it
        e = err(P + Kernel.fit(X[rows], Y[rows] - P[rows], rows).attach(calib).predict_many(X))
        if best[1] is None or e < best[1]:
            best = (l, e)
    if plain is None or best[1] > KERNEL_GAIN * plain:
        return None, plain
    return best


def _cap(idx):
    return idx[np.linspace(0, len(idx) - 1, min(len(idx), KERNEL_MAX)).astype(int)]


def kernel_for(calib, full: Regressor) -> Kernel:
    """The kernel learned on all calibration samples, against the final linear model."""
    X = np.array([full_features(s.f) for s in calib], float)
    rows = _cap(np.arange(len(calib)))
    return Kernel.fit(X[rows], np.array([(s.x, s.y) for s in calib], float)[rows] - full.predict_many(X[rows]), rows)


def screen_signature(screens) -> str:
    return ";".join(f"{int(r.x)},{int(r.y)},{int(r.w)},{int(r.h)}" for r in screens)


@dataclass
class GazeModel:
    head: Regressor
    full: Regressor
    screens: list                       # list[Rect] the calibration was made on
    calib: list = field(default_factory=list)
    keyboard: list = field(default_factory=list)   # readings while looking at the keyboard
    learned: list = field(default_factory=list)
    keyboard_line: float | None = None  # head/gaze below this y = looking at the keyboard
    median_width: float | None = None
    pose_std: list | None = None
    kernel: Kernel | None = None        # the non-linear correction, if it helps this person
    error: float | None = None          # held-out median error in px (every 4th calibration step), measured when fitted

    def __post_init__(self):
        if self.kernel is not None:
            self.kernel.attach(self.calib)
        self._index()

    def _index(self):
        """Caches the learned samples as arrays for the per-frame correction."""
        l = [s for s in self.learned if s.rx is not None]
        if l and self.pose_std:
            self._lf = np.array([s.f[:N_POSE] for s in l], float) / np.asarray(self.pose_std, float)
            self._lp = np.array([[s.x - s.rx, s.y - s.ry] for s in l], float)   # where the calibrated model put it
            self._lr = np.array([[s.rx, s.ry] for s in l], float)
            self._lw = np.array([s.w for s in l], float)
        else:
            self._lf = None

    # ---- prediction

    def predict_head(self, f) -> tuple[float, float]:
        return self.head.predict(head_features(f))

    def _base(self, f) -> tuple[float, float]:
        """Linear model plus the non-linear correction learned from the calibration."""
        v = full_features(f)
        p = self.full.predict(v)
        if self.kernel is None:
            return p
        c = self.kernel.predict_many([v])[0]
        return p[0] + float(c[0]), p[1] + float(c[1])

    def predict(self, f) -> tuple[float, float]:
        """The calibrated prediction plus the local correction learned from everyday use."""
        p = self._base(f)
        c = self.correction(f, p)
        return p[0] + c[0], p[1] + c[1]

    def correction(self, f, p) -> tuple[float, float]:
        """Nearby remembered situations (similar pose and predicted spot) vote with how far off the
        calibrated model was for them. Few or distant neighbours -> little correction."""
        if self._lf is None:
            return 0.0, 0.0
        d2 = ((np.asarray(f[:N_POSE], float) / np.asarray(self.pose_std, float) - self._lf) ** 2).sum(axis=1)
        d2 += (((np.asarray(p, float) - self._lp) / 300.0) ** 2).sum(axis=1)
        w = np.exp(-d2 / 2) * self._lw
        # the prior pulls toward "no correction": one click nudges ~25% of the way, a correction ~50%
        c = (w[:, None] * self._lr).sum(axis=0) / (w.sum() + NUDGE_PRIOR)
        return float(c[0]), float(c[1])

    # ---- learning

    def situation(self, s: Sample) -> str:
        """How you sit (face left/centre/right, up/middle/down, near/normal/far) x where you looked."""
        f, mw = s.f, self.median_width or 0.25
        px, py = min(2, max(0, int(f[3] * 3))), min(2, max(0, int(f[4] * 3)))
        dist = 0 if f[5] < mw * 0.88 else 2 if f[5] > mw * 1.12 else 1
        for k, r in enumerate(self.screens):
            if r.contains((s.x, s.y)):
                return f"{px}{py}{dist}-{k}-{int((s.x - r.x) / r.w * 4)}{int((s.y - r.y) / r.h * 3)}"
        return f"{px}{py}{dist}-off"

    def remember(self, s: Sample) -> None:
        """Stores a sample. It only pushes out an older one from the same situation, so a posture
        you use rarely is never forgotten just because it is old. Call refit() to learn fully."""
        s.key = self.situation(s)
        p = self._base(s.f)
        s.rx, s.ry = s.x - p[0], s.y - p[1]
        same = [i for i, o in enumerate(self.learned) if o.key == s.key]
        if len(same) >= PER_SITUATION:
            del self.learned[same[0]]
        self.learned.append(s)
        if len(self.learned) > MAX_LEARNED:
            groups: dict = {}
            for i, o in enumerate(self.learned):
                groups.setdefault(o.key, []).append(i)
            del self.learned[max(groups.values(), key=len)[0]]
        self._index()

    def refit(self) -> "GazeModel | None":
        # ponytail: the kernel learned the calibration's errors against the linear model as it was;
        # clicks move that model only gently (weight 0.3), so it is reused rather than refitted
        # (fitting takes seconds, and refits happen while you work)
        return GazeModel.fit(self.calib, self.screens, self.learned, self.keyboard,
                             lambdas=(self.head.lam, self.full.lam), reuse=(self.kernel, self.error))

    @staticmethod
    def fit(calib, screens, learned=(), keyboard=(), lam=None, lambdas=None, reuse=None) -> "GazeModel | None":
        """`reuse`: (kernel, error) to keep instead of fitting a new kernel."""
        calib, learned, keyboard = list(calib), list(learned), list(keyboard)
        # everyday samples steer the overall model only gently; local errors are the correction layer's job
        rows = [(s, s.w) for s in calib] + [(s, s.w * 0.3) for s in learned]
        h = [(s, w) for s, w in rows if s.hx is not None]
        head = Regressor.best_fit([head_features(s.f) for s, _ in h],
                                  [(s.hx, s.hy if s.hy is not None else s.y) for s, _ in h],
                                  [w for _, w in h], lambdas[0] if lambdas else lam)
        full = Regressor.best_fit([full_features(s.f) for s, _ in rows], [(s.x, s.y) for s, _ in rows],
                                  [w for _, w in rows], lambdas[1] if lambdas else lam,
                                  groups=[step_of(s) for s, _ in rows])
        if head is None or full is None:
            return None
        if reuse is not None:
            kernel, error = reuse
        else:
            lam_k, error = choose_kernel(calib, full.lam)
            if lam_k is not None and lam_k != full.lam:     # smoother under the kernel
                full = Regressor.fit([full_features(s.f) for s, _ in rows], [(s.x, s.y) for s, _ in rows],
                                     [w for _, w in rows], lam_k)
            kernel = kernel_for(calib, full) if lam_k is not None and full is not None else None
        if full is None:
            return None
        m = GazeModel(head, full, [Rect(*r) for r in screens], calib, keyboard, learned, kernel=kernel, error=error)
        for s in learned:       # residuals against the new calibrated model drive the local correction
            p = m._base(s.f)
            s.rx, s.ry = s.x - p[0], s.y - p[1]
        if calib:
            pose = np.array([s.f[:N_POSE] for s in calib], float)
            m.median_width = float(np.median(pose[:, 5]))
            m.pose_std = np.maximum(pose.std(axis=0), 1e-3).tolist()
        if keyboard and m.screens:
            bottom = max(r.bottom for r in m.screens)
            ky = float(np.mean([m.predict_head(s.f)[1] for s in keyboard]))
            m.keyboard_line = (bottom + ky) / 2 if ky > bottom else None
        m._index()
        return m

    # ---- persistence

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {"version": VERSION, "head": self.head.to_json(), "full": self.full.to_json(),
                "screens": [list(r) for r in self.screens],
                "calib": [asdict(s) for s in self.calib], "keyboard": [asdict(s) for s in self.keyboard],
                "learned": [asdict(s) for s in self.learned], "keyboard_line": self.keyboard_line,
                "median_width": self.median_width, "pose_std": self.pose_std,
                "kernel": asdict(self.kernel) if self.kernel else None, "error": self.error}
        for key in ("calib", "keyboard", "learned"):       # 5 decimals is plenty and keeps the file small
            for s in data[key]:
                s["f"] = [round(v, 5) for v in s["f"]]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
        os.replace(tmp, path)

    @staticmethod
    def load(path: str, screens) -> "GazeModel | None":
        """None if there is no model, or it was calibrated on a different screen layout."""
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            if d.get("version") != VERSION:
                return None
            saved = [Rect(*r) for r in d["screens"]]
            if screen_signature(saved) != screen_signature(screens):
                return None
            return GazeModel(Regressor(**d["head"]), Regressor(**d["full"]), saved,
                             [Sample(**s) for s in d["calib"]], [Sample(**s) for s in d["keyboard"]],
                             [Sample(**s) for s in d["learned"]], d.get("keyboard_line"),
                             d.get("median_width"), d.get("pose_std"),
                             Kernel(**d["kernel"]) if d.get("kernel") else None, d.get("error"))
        except (OSError, ValueError, KeyError, TypeError):
            return None


class OneEuro:
    """One Euro filter (Casiez et al.): heavy smoothing while still, little lag on fast moves."""

    def __init__(self, min_cutoff=0.8, beta=0.004):
        self.min_cutoff, self.beta = min_cutoff, beta
        self._last = None

    def reset(self):
        self._last = None

    def filter(self, p, t: float):
        if self._last is None:
            self._last = (p, (0.0, 0.0), t)
            return p
        lp, ld, lt = self._last
        dt = max(t - lt, 1e-3)

        def alpha(cutoff):
            r = 2 * math.pi * cutoff * dt
            return r / (r + 1)

        ad = alpha(1.0)
        d = tuple(ld[i] + ad * ((p[i] - lp[i]) / dt - ld[i]) for i in (0, 1))
        a = alpha(self.min_cutoff + self.beta * math.hypot(*d))
        out = tuple(lp[i] + a * (p[i] - lp[i]) for i in (0, 1))
        self._last = (out, d, t)
        return out
