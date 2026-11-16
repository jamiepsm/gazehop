"""The gaze dot: a thin see-through ring that floats where gazehop thinks you are looking.

It moves as if it had weight: it eases after the gaze without overshooting, and while it travels
it is drawn out into a teardrop that trails behind, then rounds off again as it slows down.
It never takes clicks or focus.

On Linux it is its own X window with an alpha channel, so the desktop's compositor blends every
pixel (GNOME on Wayland ignores cut-out window shapes, so nothing else gives a round, see-through
dot there). On Windows, and on Linux without a compositor, it is a Tk window instead: see-through
by colour key on Windows, cut to shape on X11.
"""
from __future__ import annotations

import ctypes
import math
import re
import sys
import time

SIZE = 96                           # the window; the ring lives in its middle
RADIUS = 15
DEFAULT_COLOUR = "#d0d0d6"          # light grey; changeable in the settings
SHADOW = "#101014"                  # a soft dark edge that keeps the ring visible on white
KEY = "#010203"                     # Windows: this colour is made invisible
ALPHA = 0.85
STIFFNESS = 340.0                   # how eagerly it follows the gaze; lower feels heavier
DAMPING = 2 * STIFFNESS ** 0.5      # exactly enough to arrive without overshooting: no wobble
STRETCH_RATE = 9.0                  # how quickly the teardrop forms and rounds off again (per second)
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


def colour_or_default(c) -> str:
    """`c` if it is a #rrggbb colour, else the default: config.json is edited by hand."""
    return c if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c) else DEFAULT_COLOUR


def droplet(direction, stretch: float, colour: str = DEFAULT_COLOUR) -> bytes:
    """The ring as SIZE x SIZE premultiplied BGRA pixels (what an X alpha window expects), with
    smooth edges: drawn 4x larger, then scaled down. Its middle stays fully see-through."""
    import numpy as np
    from PIL import Image, ImageDraw
    k, mid = 4, SIZE * 2
    rgb = lambda c, a: tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) + (a,)
    img = Image.new("RGBA", (SIZE * k, SIZE * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    outline = [(mid + x * k, mid + y * k) for x, y in blob(direction, stretch)]
    outline += outline[:2]      # overlap the ends, so the joint is rounded like every other one
    d.line(outline, fill=rgb(SHADOW, 90), width=6 * k, joint="curve")
    d.line(outline, fill=rgb(colour, round(255 * ALPHA)), width=3 * k, joint="curve")
    px = np.asarray(img.resize((SIZE, SIZE), Image.BOX), np.uint16)
    alpha = px[..., 3:]
    colour = (px[..., 2::-1] * alpha + 127) // 255      # RGB -> BGR, premultiplied
    return np.concatenate([colour, alpha], axis=2).astype(np.uint8).tobytes()


class _Dot:
    """The spring physics; subclasses put the droplet on screen."""

    def __init__(self, root, colour):
        self.root, self.colour = root, colour
        self._pos = self._target = None
        self._vel, self._dir = (0.0, 0.0), (1.0, 0.0)
        self._stretch = 0.0
        self._last, self._alive, self.hidden = time.monotonic(), True, False
        root.after(FRAME_MS, self._step)

    def move_to(self, p) -> None:
        self._target = p
        if self._pos is None:
            self._pos = p
            self._draw()

    def set_colour(self, colour: str) -> None:
        self.colour = colour_or_default(colour)
        self._recolour()
        if self._pos is not None:
            self._draw()

    def _recolour(self) -> None:
        pass

    def destroy(self) -> None:
        self._alive = False
        self._close()

    def _step(self) -> None:
        if not self._alive:
            return
        now = time.monotonic()
        dt, self._last = min(now - self._last, 0.05), now
        if self._pos is not None and not self.hidden:
            (x, y), (vx, vy), (tx, ty) = self._pos, self._vel, self._target
            vx += (STIFFNESS * (tx - x) - DAMPING * vx) * dt
            vy += (STIFFNESS * (ty - y) - DAMPING * vy) * dt
            x, y = x + vx * dt, y + vy * dt
            speed = math.hypot(vx, vy)
            if speed > 40:      # slower than this the direction is only noise: keep the last one
                self._dir = (vx / speed, vy / speed)
            # eases toward a teardrop that grows with speed, and back to round: never past it
            self._stretch += (min(speed / 3000, 0.45) - self._stretch) * (1 - math.exp(-STRETCH_RATE * dt))
            self._pos, self._vel = (x, y), (vx, vy)
            if speed > 2 or self._stretch > 0.004:
                self._draw()
        self.root.after(FRAME_MS, self._step)


class XPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]


class XVisualInfo(ctypes.Structure):
    _fields_ = [("visual", ctypes.c_void_p), ("visualid", ctypes.c_ulong), ("screen", ctypes.c_int),
                ("depth", ctypes.c_int), ("cls", ctypes.c_int), ("red_mask", ctypes.c_ulong),
                ("green_mask", ctypes.c_ulong), ("blue_mask", ctypes.c_ulong), ("colormap_size", ctypes.c_int),
                ("bits_per_rgb", ctypes.c_int)]


class XSetWindowAttributes(ctypes.Structure):
    _fields_ = [("background_pixmap", ctypes.c_ulong), ("background_pixel", ctypes.c_ulong),
                ("border_pixmap", ctypes.c_ulong), ("border_pixel", ctypes.c_ulong), ("bit_gravity", ctypes.c_int),
                ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
                ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int), ("event_mask", ctypes.c_long),
                ("do_not_propagate_mask", ctypes.c_long), ("override_redirect", ctypes.c_int),
                ("colormap", ctypes.c_ulong), ("cursor", ctypes.c_ulong)]


_x11 = None     # (libX11, libXext, display): one connection, shared by every dot


def _xlib():
    """Xlib handles for the dot, or None where X is not available."""
    global _x11
    if _x11 is None:
        from ctypes import POINTER, c_char_p, c_int, c_uint, c_ulong, c_void_p
        from .backends.x11 import _ignore_errors, _load, load_xlib
        try:
            x = load_xlib()
        except Exception:
            _x11 = ()
            return None
        ext = _load("Xext", "libXext.so.6")
        dpy = x.XOpenDisplay(None) if ext is not None else None
        if not dpy:
            _x11 = ()
            return None
        x.XSetErrorHandler(_ignore_errors)      # a dot closed mid-frame must not end the process
        P = lambda fn, res, *args: (setattr(fn, "restype", res), setattr(fn, "argtypes", args))
        P(x.XMatchVisualInfo, c_int, c_void_p, c_int, c_int, c_int, POINTER(XVisualInfo))
        P(x.XCreateColormap, c_ulong, c_void_p, c_ulong, c_void_p, c_int)
        P(x.XCreateWindow, c_ulong, c_void_p, c_ulong, c_int, c_int, c_uint, c_uint, c_uint, c_int, c_uint,
          c_void_p, c_ulong, POINTER(XSetWindowAttributes))
        P(x.XCreateGC, c_void_p, c_void_p, c_ulong, c_ulong, c_void_p)
        P(x.XCreateImage, c_void_p, c_void_p, c_void_p, c_uint, c_int, c_int, c_char_p, c_uint, c_uint, c_int, c_int)
        P(x.XPutImage, c_int, c_void_p, c_ulong, c_void_p, c_void_p, c_int, c_int, c_int, c_int, c_uint, c_uint)
        P(x.XMoveWindow, c_int, c_void_p, c_ulong, c_int, c_int)
        for fn in (x.XMapRaised, x.XUnmapWindow, x.XRaiseWindow, x.XDestroyWindow):
            P(fn, c_int, c_void_p, c_ulong)
        P(x.XFreeColormap, c_int, c_void_p, c_ulong)
        P(x.XFreeGC, c_int, c_void_p, c_void_p)
        P(x.XStoreName, c_int, c_void_p, c_ulong, c_char_p)
        P(x.XGetSelectionOwner, c_ulong, c_void_p, c_ulong)
        P(x.XPolygonRegion, c_void_p, POINTER(XPoint), c_int, c_int)
        P(x.XDestroyRegion, c_int, c_void_p)
        P(ext.XShapeCombineRegion, None, c_void_p, c_ulong, c_int, c_int, c_int, c_void_p, c_int)
        P(ext.XShapeCombineRectangles, None, c_void_p, c_ulong, c_int, c_int, c_int, c_void_p, c_int, c_int, c_int)
        _x11 = (x, ext, dpy)
    return _x11 or None


def _compositing() -> bool:
    """Is a compositor running? Only then do alpha windows look see-through."""
    x, _ext, dpy = _xlib()
    return bool(x.XGetSelectionOwner(dpy, x.XInternAtom(dpy, b"_NET_WM_CM_S%d" % x.XDefaultScreen(dpy), False)))


class _AlphaDot(_Dot):
    """Linux: an override-redirect X window with a 32-bit visual, painted with premultiplied alpha."""

    def __init__(self, root, colour):
        x, ext, dpy = _xlib()
        info = XVisualInfo()
        root_win = x.XDefaultRootWindow(dpy)
        if not x.XMatchVisualInfo(dpy, x.XDefaultScreen(dpy), 32, 4, ctypes.byref(info)):     # 4 = TrueColor
            raise OSError("no 32-bit visual")
        self._x, self._dpy = x, dpy
        self._cmap = x.XCreateColormap(dpy, root_win, info.visual, 0)
        attrs = XSetWindowAttributes(border_pixel=0, background_pixel=0, override_redirect=1, colormap=self._cmap)
        # CWBackPixel | CWBorderPixel | CWOverrideRedirect | CWColormap
        self._win = x.XCreateWindow(dpy, root_win, -500, -500, SIZE, SIZE, 0, 32, 1, info.visual,
                                    (1 << 1) | (1 << 3) | (1 << 9) | (1 << 13), ctypes.byref(attrs))
        x.XStoreName(dpy, self._win, b"gazehop dot")
        ext.XShapeCombineRectangles(dpy, self._win, 2, 0, 0, None, 0, 0, 0)     # empty input shape: no clicks
        self._gc = x.XCreateGC(dpy, self._win, 0, None)
        self._pixels = ctypes.create_string_buffer(SIZE * SIZE * 4)
        self._image = x.XCreateImage(dpy, info.visual, 32, 2, 0, self._pixels, SIZE, SIZE, 32, 0)     # 2 = ZPixmap
        x.XMapRaised(dpy, self._win)
        x.XFlush(dpy)
        self._frames = 0
        super().__init__(root, colour)

    def _draw(self) -> None:
        if self.hidden:
            return
        x, dpy = self._x, self._dpy
        ctypes.memmove(self._pixels, droplet(self._dir, self._stretch, self.colour), SIZE * SIZE * 4)
        x.XPutImage(dpy, self._win, self._gc, self._image, 0, 0, 0, 0, SIZE, SIZE)
        if self._pos is not None:
            x.XMoveWindow(dpy, self._win, round(self._pos[0] - SIZE / 2), round(self._pos[1] - SIZE / 2))
        self._frames += 1
        if self._frames % 30 == 0:      # stay above windows opened since
            x.XRaiseWindow(dpy, self._win)
        x.XFlush(dpy)

    def hide(self) -> None:
        self.hidden = True
        self._x.XUnmapWindow(self._dpy, self._win)
        self._x.XFlush(self._dpy)

    def show(self) -> None:
        self.hidden = False
        self._x.XMapRaised(self._dpy, self._win)
        self._draw()

    def _close(self) -> None:
        x, dpy = self._x, self._dpy
        x.XDestroyWindow(dpy, self._win)
        x.XFreeGC(dpy, self._gc)
        x.XFreeColormap(dpy, self._cmap)
        x.XFlush(dpy)
        # ponytail: the XImage header (it points into our own buffer) is left for the process to free


class _TkDot(_Dot):
    """Windows, or Linux without a compositor: a Tk window, keyed or cut to the droplet's shape."""

    def __init__(self, root, colour):
        import tkinter as tk
        w = self.win = tk.Toplevel(root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        bg = KEY if sys.platform == "win32" else colour
        w.configure(bg=bg)
        w.geometry(f"{SIZE}x{SIZE}+-500+-500")
        c = self.canvas = tk.Canvas(w, width=SIZE, height=SIZE, bg=bg, highlightthickness=0, bd=0)
        c.pack()
        self._body = c.create_polygon(0, 0, 0, 0, 0, 0, fill="", outline=colour, width=3, smooth=True)
        self._xwin = None
        try:
            self._platform_setup()
        except Exception:
            pass        # without it the dot is a plain square that takes clicks: ugly, still useful
        super().__init__(root, colour)
        self._draw()

    def _recolour(self) -> None:
        self.canvas.itemconfigure(self._body, outline=self.colour)
        if sys.platform != "win32":
            self.win.configure(bg=self.colour)
            self.canvas.configure(bg=self.colour)

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
        elif _xlib():
            x, ext, dpy = _xlib()
            ext.XShapeCombineRectangles(dpy, frame, 2, 0, 0, None, 0, 0, 0)     # empty input shape
            x.XFlush(dpy)
            self._xwin = frame

    def _draw(self) -> None:
        if self.hidden:
            return
        mid, s = SIZE / 2, self._stretch
        self.canvas.coords(self._body, *[mid + v for p in blob(self._dir, s) for v in p])
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

    def hide(self) -> None:
        self.hidden = True
        self.win.withdraw()

    def show(self) -> None:
        self.hidden = False
        self.win.deiconify()
        self._draw()

    def _close(self) -> None:
        self.win.destroy()


def GazeDot(root, colour=DEFAULT_COLOUR):
    """The best dot this desktop can show."""
    colour = colour_or_default(colour)
    if sys.platform != "win32" and _xlib() and _compositing():
        try:
            return _AlphaDot(root, colour)
        except Exception as e:
            print(f"gazehop: plain gaze dot ({e})", file=sys.stderr)
    return _TkDot(root, colour)
