"""X11 backend (Ubuntu on Xorg, and any EWMH window manager), pure ctypes on libX11."""
from __future__ import annotations

import ctypes
import ctypes.util
import os
from ctypes import POINTER, byref, c_char_p, c_int, c_long, c_uint, c_ulong, c_void_p

from ..geometry import Rect, Win
from . import BackendError

Atom = Window = c_ulong
SubstructureNotifyMask = 1 << 19
SubstructureRedirectMask = 1 << 20
ClientMessage = 33


class XClientMessageEvent(ctypes.Structure):
    _fields_ = [("type", c_int), ("serial", c_ulong), ("send_event", c_int), ("display", c_void_p),
                ("window", Window), ("message_type", Atom), ("format", c_int), ("data", c_long * 5)]


class XEvent(ctypes.Union):
    _fields_ = [("type", c_int), ("client", XClientMessageEvent), ("pad", c_long * 24)]


class XRRMonitorInfo(ctypes.Structure):
    _fields_ = [("name", Atom), ("primary", c_int), ("automatic", c_int), ("noutput", c_int),
                ("x", c_int), ("y", c_int), ("width", c_int), ("height", c_int),
                ("mwidth", c_int), ("mheight", c_int), ("outputs", c_void_p)]


class XScreenSaverInfo(ctypes.Structure):
    _fields_ = [("window", Window), ("state", c_int), ("kind", c_int), ("til_or_since", c_ulong),
                ("idle", c_ulong), ("eventMask", c_ulong)]


ERROR_HANDLER = ctypes.CFUNCTYPE(c_int, c_void_p, c_void_p)


def _load(name, soname):
    path = ctypes.util.find_library(name) or soname
    try:
        return ctypes.CDLL(path)
    except OSError:
        return None


def load_xlib():
    x = _load("X11", "libX11.so.6")
    if x is None:
        raise BackendError("libX11 not found. Install it with: sudo apt install libx11-6")
    P = lambda fn, res, *args: (setattr(fn, "restype", res), setattr(fn, "argtypes", args))
    P(x.XOpenDisplay, c_void_p, c_char_p)
    P(x.XDefaultRootWindow, Window, c_void_p)
    P(x.XInternAtom, Atom, c_void_p, c_char_p, c_int)
    P(x.XGetWindowProperty, c_int, c_void_p, Window, Atom, c_long, c_long, c_int, Atom,
      POINTER(Atom), POINTER(c_int), POINTER(c_ulong), POINTER(c_ulong), POINTER(c_void_p))
    P(x.XFree, c_int, c_void_p)
    P(x.XGetAtomName, c_void_p, c_void_p, Atom)
    P(x.XGetGeometry, c_int, c_void_p, Window, POINTER(Window), POINTER(c_int), POINTER(c_int),
      POINTER(c_uint), POINTER(c_uint), POINTER(c_uint), POINTER(c_uint))
    P(x.XTranslateCoordinates, c_int, c_void_p, Window, Window, c_int, c_int,
      POINTER(c_int), POINTER(c_int), POINTER(Window))
    P(x.XSendEvent, c_int, c_void_p, Window, c_int, c_long, POINTER(XEvent))
    P(x.XFlush, c_int, c_void_p)
    P(x.XSync, c_int, c_void_p, c_int)
    P(x.XWarpPointer, c_int, c_void_p, Window, Window, c_int, c_int, c_uint, c_uint, c_int, c_int)
    P(x.XDisplayWidth, c_int, c_void_p, c_int)
    P(x.XDisplayHeight, c_int, c_void_p, c_int)
    P(x.XDefaultScreen, c_int, c_void_p)
    P(x.XSetErrorHandler, c_void_p, ERROR_HANDLER)
    return x


@ERROR_HANDLER
def _ignore_errors(_dpy, _event):
    # windows vanish between listing and querying them; a BadWindow must not kill the process
    return 0


class X11Backend:
    name = "x11"

    def __init__(self, display: str | None = None):
        self.x = load_xlib()
        self.x.XSetErrorHandler(_ignore_errors)
        self.dpy = self.x.XOpenDisplay(display.encode() if display else None)
        if not self.dpy:
            raise BackendError("Cannot open the X display (is DISPLAY set?).")
        self.root = self.x.XDefaultRootWindow(self.dpy)
        self._atoms: dict = {}
        self._pid = os.getpid()
        self.xrr = _load("Xrandr", "libXrandr.so.2")
        if self.xrr is not None:
            self.xrr.XRRGetMonitors.restype = POINTER(XRRMonitorInfo)
            self.xrr.XRRGetMonitors.argtypes = (c_void_p, Window, c_int, POINTER(c_int))
            self.xrr.XRRFreeMonitors.argtypes = (POINTER(XRRMonitorInfo),)
        self.xss = _load("Xss", "libXss.so.1")
        self._xss_info = None
        if self.xss is not None:
            self.xss.XScreenSaverAllocInfo.restype = POINTER(XScreenSaverInfo)
            self.xss.XScreenSaverQueryInfo.argtypes = (c_void_p, Window, POINTER(XScreenSaverInfo))
            self._xss_info = self.xss.XScreenSaverAllocInfo()

    def atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = self.x.XInternAtom(self.dpy, name.encode(), False)
        return self._atoms[name]

    def _prop(self, win: int, name: str, limit: int = 4096):
        """A window property as a list of ints (format 32) or bytes (format 8); None if unset."""
        typ, fmt, n, after, data = Atom(), c_int(), c_ulong(), c_ulong(), c_void_p()
        st = self.x.XGetWindowProperty(self.dpy, win, self.atom(name), 0, limit, False, 0,
                                       byref(typ), byref(fmt), byref(n), byref(after), byref(data))
        if st != 0 or not data.value:
            return None
        try:
            if fmt.value == 32:     # Xlib hands format-32 data back as C longs, sign-extended on 64-bit
                if not n.value:
                    return []
                return [v & 0xFFFFFFFF for v in ctypes.cast(data, POINTER(c_ulong * n.value)).contents]
            if fmt.value == 8:
                return ctypes.string_at(data, n.value)
            return None
        finally:
            self.x.XFree(data)

    def supports(self, hint: str) -> bool:
        return self.atom(hint) in (self._prop(self.root, "_NET_SUPPORTED") or [])

    def screens(self) -> list:
        out = []
        if self.xrr is not None:
            n = c_int()
            mons = self.xrr.XRRGetMonitors(self.dpy, self.root, True, byref(n))
            if mons:
                out = [Rect(mons[i].x, mons[i].y, mons[i].width, mons[i].height) for i in range(n.value)]
                self.xrr.XRRFreeMonitors(mons)
        if not out:
            s = self.x.XDefaultScreen(self.dpy)
            out = [Rect(0, 0, self.x.XDisplayWidth(self.dpy, s), self.x.XDisplayHeight(self.dpy, s))]
        return sorted(set(out))

    def main_screen(self) -> Rect | None:
        """The built-in screen (a laptop's own panel) if there is one, else the primary screen."""
        if self.xrr is None:
            return None
        n, found = c_int(), {}
        mons = self.xrr.XRRGetMonitors(self.dpy, self.root, True, byref(n))
        if not mons:
            return None
        for i in range(n.value):
            m = mons[i]
            p = self.x.XGetAtomName(self.dpy, m.name)
            name = ctypes.string_at(p).decode(errors="replace") if p else ""
            if p:
                self.x.XFree(p)
            rect = Rect(m.x, m.y, m.width, m.height)
            if name.startswith(("eDP", "LVDS", "DSI")):     # how laptops name their own panel
                found.setdefault("builtin", rect)
            if m.primary:
                found.setdefault("primary", rect)
        self.xrr.XRRFreeMonitors(mons)
        return found.get("builtin") or found.get("primary")

    def _rect(self, win: int) -> Rect | None:
        root, x, y, w, h, bw, depth = Window(), c_int(), c_int(), c_uint(), c_uint(), c_uint(), c_uint()
        if not self.x.XGetGeometry(self.dpy, win, byref(root), byref(x), byref(y), byref(w), byref(h),
                                   byref(bw), byref(depth)):
            return None
        gx, gy, child = c_int(), c_int(), Window()
        if not self.x.XTranslateCoordinates(self.dpy, win, self.root, 0, 0, byref(gx), byref(gy), byref(child)):
            return None
        r = Rect(gx.value, gy.value, w.value, h.value)
        ext = self._prop(win, "_GTK_FRAME_EXTENTS")     # GTK windows include an invisible shadow margin
        if ext and len(ext) == 4:
            l, rt, t, b = (int(v) for v in ext)
            r = Rect(r.x + l, r.y + t, r.w - l - rt, r.h - t - b)
        return r

    def _win(self, win: int, desktop=None) -> Win | None:
        types = self._prop(win, "_NET_WM_WINDOW_TYPE")
        if types and self.atom("_NET_WM_WINDOW_TYPE_NORMAL") not in types \
                and self.atom("_NET_WM_WINDOW_TYPE_DIALOG") not in types:
            return None
        if self.atom("_NET_WM_STATE_HIDDEN") in (self._prop(win, "_NET_WM_STATE") or []):
            return None     # minimised
        if desktop is not None:
            d = self._prop(win, "_NET_WM_DESKTOP")
            if d and d[0] not in (desktop, 0xFFFFFFFF):
                return None     # on another workspace
        pid = self._prop(win, "_NET_WM_PID")
        pid = int(pid[0]) if pid else 0
        if pid == self._pid:
            return None
        r = self._rect(win)
        if r is None or r.w < 50 or r.h < 50:
            return None
        name = self._prop(win, "_NET_WM_NAME") or self._prop(win, "WM_NAME") or b""
        title = name.decode("utf-8", "replace") if isinstance(name, bytes) else ""
        return Win(int(win), r, pid, title)

    def _desktop(self):
        d = self._prop(self.root, "_NET_CURRENT_DESKTOP")
        return d[0] if d else None

    def windows(self) -> list:
        ids = self._prop(self.root, "_NET_CLIENT_LIST_STACKING")    # bottom to top
        if ids is None:
            ids = self._prop(self.root, "_NET_CLIENT_LIST") or []
        desktop = self._desktop()
        out = [self._win(w, desktop) for w in reversed(ids)]
        return [w for w in out if w]

    def active_window(self) -> Win | None:
        a = self._prop(self.root, "_NET_ACTIVE_WINDOW")
        return self._win(a[0]) if a and a[0] else None

    def focus(self, win_id: int) -> bool:
        ev = XEvent()
        ev.client.type = ClientMessage
        ev.client.send_event = 1
        ev.client.display = self.dpy
        ev.client.window = win_id
        ev.client.message_type = self.atom("_NET_ACTIVE_WINDOW")
        ev.client.format = 32
        ev.client.data[0] = 2       # source indication 2 = "pager": the user asked, don't just flash the taskbar
        ev.client.data[1] = 0       # timestamp: CurrentTime
        ok = self.x.XSendEvent(self.dpy, self.root, False, SubstructureNotifyMask | SubstructureRedirectMask,
                               byref(ev))
        self.x.XFlush(self.dpy)
        return bool(ok)

    def move_pointer(self, x: float, y: float) -> None:
        self.x.XWarpPointer(self.dpy, 0, self.root, 0, 0, 0, 0, int(x), int(y))
        self.x.XFlush(self.dpy)

    def idle_seconds(self) -> float | None:
        if self.xss is None or not self._xss_info:
            return None
        if not self.xss.XScreenSaverQueryInfo(self.dpy, self.root, self._xss_info):
            return None
        return self._xss_info.contents.idle / 1000.0
