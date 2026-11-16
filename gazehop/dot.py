"""The gaze dot: a small see-through droplet that floats where gazehop thinks you are looking.

It chases the gaze on a spring, stretches into a teardrop while it travels and wobbles like jelly
when it stops. It is its own borderless window; clicks pass straight through it.
"""
from __future__ import annotations

import ctypes
import math
import sys
import time

SIZE = 96                           # the window; the droplet lives in its middle
RADIUS = 15
FILL, RIM, SHINE = "#48d696", "#c9f7e2", "#ffffff"
KEY = "#010203"                     # Windows: this colour is made invisible
ALPHA = 0.6
STIFFNESS, DAMPING = 170.0, 19.0    # how eagerly it chases the gaze / how quickly it calms down
WOBBLE_K, WOBBLE_C = 260.0, 11.0    # the stretch has its own, bouncier spring: that is the jelly
FRAME_MS = 16


def blob(direction, stretch: float, grow: float = 0.0) -> list:
    """Outline of the droplet around (0, 0). Round at rest; with `stretch` > 0 it is longer along
    `direction` (a unit vector) with a narrower tail, with `stretch` < 0 it is squashed instead.
    Its area stays about the same, as a drop of liquid's would."""
    a, b = (RADIUS + grow) * (1 + stretch), (RADIUS + grow) / (1 + stretch)
    taper = max(0.0, min(0.45, stretch))
    (ca, sa), pts = direction, []
    for i in range(40):
        t = 2 * math.pi * i / 40
        x, y = a * math.cos(t), b * math.sin(t) * (1 - taper * (1 - math.cos(t)) / 2)
        pts.append((x * ca - y * sa, x * sa + y * ca))
    return pts


class XPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]


_x11 = None     # (libX11, libXext, display): one connection, shared by every dot


def _x11_shape():
    """Xlib handles for shaping windows, or None where that is not possible."""
    global _x11
    if _x11 is None:
        from ctypes import POINTER, c_int, c_ulong, c_void_p
        from .backends.x11 import _ignore_errors, _load, load_xlib
        x, ext = load_xlib(), _load("Xext", "libXext.so.6")
        dpy = x.XOpenDisplay(None) if ext is not None else None
        if not dpy:
            _x11 = ()
            return None
        x.XSetErrorHandler(_ignore_errors)      # a dot closed mid-frame must not end the process
        x.XPolygonRegion.restype = c_void_p
        x.XPolygonRegion.argtypes = (POINTER(XPoint), c_int, c_int)
        x.XDestroyRegion.argtypes = (c_void_p,)
        ext.XShapeCombineRegion.argtypes = (c_void_p, c_ulong, c_int, c_int, c_int, c_void_p, c_int)
        ext.XShapeCombineRectangles.argtypes = (c_void_p, c_ulong, c_int, c_int, c_int, c_void_p, c_int, c_int, c_int)
        _x11 = (x, ext, dpy)
    return _x11 or None


class GazeDot:
    def __init__(self, root):
        import tkinter as tk
        self.root = root
        w = self.win = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        # Windows cuts the droplet out with a see-through colour; X11 clips the window to its outline
        bg = KEY if sys.platform == "win32" else FILL
        w.configure(bg=bg)
        w.geometry(f"{SIZE}x{SIZE}+-500+-500")
        c = self.canvas = tk.Canvas(w, width=SIZE, height=SIZE, bg=bg, highlightthickness=0, bd=0)
        c.pack()
        self._body = c.create_polygon(0, 0, 0, 0, 0, 0, fill=FILL, outline=RIM, width=2, smooth=True)
        self._shine = c.create_oval(0, 0, 0, 0, fill=SHINE, outline="")
        self._pos = self._target = None
        self._vel, self._dir = (0.0, 0.0), (1.0, 0.0)
        self._stretch = self._stretch_v = 0.0
        self._last, self._alive = time.monotonic(), True
        self._xwin = None
        try:
            self._platform_setup()
        except Exception:
            pass        # without it the dot is a plain square that takes clicks: ugly, still useful
        self._draw()
        root.after(FRAME_MS, self._step)

    def _platform_setup(self) -> None:
        w = self.win
        w.update_idletasks()
        if sys.platform == "win32":
            w.attributes("-transparentcolor", KEY)
        w.attributes("-alpha", ALPHA)
        frame = int(w.wm_frame(), 16)       # the real top-level window around Tk's own
        if sys.platform == "win32":
            from ctypes import wintypes
            u = ctypes.WinDLL("user32")
            u.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
            u.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
            # layered + transparent = clicks fall through; no-activate + tool window = never takes
            # focus and stays out of Alt+Tab
            u.SetWindowLongW(frame, -20, u.GetWindowLongW(frame, -20) | 0x80000 | 0x20 | 0x08000000 | 0x80)
        elif _x11_shape():
            x, ext, dpy = _x11_shape()
            ext.XShapeCombineRectangles(dpy, frame, 2, 0, 0, None, 0, 0, 0)     # empty input shape
            x.XFlush(dpy)
            self._xwin = frame

    def move_to(self, p) -> None:
        self._target = p
        if self._pos is None:
            self._pos = p
            self._draw()

    def destroy(self) -> None:
        self._alive = False
        self.win.destroy()

    def _step(self) -> None:
        if not self._alive:
            return
        now = time.monotonic()
        dt, self._last = min(now - self._last, 0.05), now
        if self._pos is not None:
            (x, y), (vx, vy), (tx, ty) = self._pos, self._vel, self._target
            vx += (STIFFNESS * (tx - x) - DAMPING * vx) * dt
            vy += (STIFFNESS * (ty - y) - DAMPING * vy) * dt
            x, y = x + vx * dt, y + vy * dt
            speed = math.hypot(vx, vy)
            if speed > 40:      # slower than this the direction is only noise: keep the last one
                self._dir = (vx / speed, vy / speed)
            self._stretch_v += (WOBBLE_K * (min(speed / 3000, 0.5) - self._stretch) - WOBBLE_C * self._stretch_v) * dt
            self._stretch = max(-0.25, min(0.6, self._stretch + self._stretch_v * dt))
            self._pos, self._vel = (x, y), (vx, vy)
            if speed > 2 or abs(self._stretch) > 0.004 or abs(self._stretch_v) > 0.05:
                self._draw()
        self.root.after(FRAME_MS, self._step)

    def _draw(self) -> None:
        mid, s = SIZE / 2, self._stretch
        flat = [mid + v for p in blob(self._dir, s) for v in p]
        self.canvas.coords(self._body, *flat)
        # the highlight stays top-left (the light does not move) and shrinks with the thin side
        m = RADIUS / (1 + abs(s)) * (1 - max(0.0, min(0.45, s)) / 2)
        self.canvas.coords(self._shine, mid - 0.62 * m, mid - 0.62 * m, mid - 0.14 * m, mid - 0.14 * m)
        if self._xwin is not None:
            x, ext, dpy = _x11
            pts = blob(self._dir, s, grow=1.5)
            arr = (XPoint * len(pts))(*[(round(mid + px), round(mid + py)) for px, py in pts])
            region = x.XPolygonRegion(arr, len(pts), 1)
            ext.XShapeCombineRegion(dpy, self._xwin, 0, 0, 0, region, 0)
            x.XDestroyRegion(region)
            x.XFlush(dpy)
        if self._pos is not None:
            self.win.geometry(f"+{round(self._pos[0] - mid)}+{round(self._pos[1] - mid)}")
