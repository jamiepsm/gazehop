"""Runs the X11 backend against a real X server (Xvfb), with this test playing window manager."""
import ctypes
import os
import shutil
import subprocess
import sys
import time
from ctypes import byref, c_int, c_long, c_uint, c_ulong, c_void_p

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux") or not shutil.which("Xvfb"),
                                reason="needs Linux with Xvfb")
DISPLAY = ":97"
XA_ATOM, XA_CARDINAL, XA_WINDOW, PROP_REPLACE = 4, 6, 33, 0


@pytest.fixture(scope="module")
def xserver():
    proc = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", "1600x900x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    from gazehop.backends.x11 import X11Backend, XEvent, load_xlib
    x = load_xlib()
    x.XCreateSimpleWindow.restype = c_ulong
    x.XCreateSimpleWindow.argtypes = (c_void_p, c_ulong, c_int, c_int, c_uint, c_uint, c_uint, c_ulong, c_ulong)
    x.XChangeProperty.argtypes = (c_void_p, c_ulong, c_ulong, c_ulong, c_int, c_int, c_void_p, c_int)
    x.XMapWindow.argtypes = (c_void_p, c_ulong)
    x.XSelectInput.argtypes = (c_void_p, c_ulong, c_long)
    x.XNextEvent.argtypes = (c_void_p, ctypes.POINTER(XEvent))
    x.XPending.argtypes = (c_void_p,)
    x.XDestroyWindow.argtypes = (c_void_p, c_ulong)
    wm = None
    for _ in range(50):
        wm = x.XOpenDisplay(DISPLAY.encode())
        if wm:
            break
        time.sleep(0.1)
    assert wm, "Xvfb did not start"
    yield x, wm, X11Backend(DISPLAY)
    proc.terminate()


def set_prop(x, dpy, win, name, typ, values):
    atom = x.XInternAtom(dpy, name.encode(), False)
    if isinstance(values, bytes):
        x.XChangeProperty(dpy, win, atom, x.XInternAtom(dpy, b"UTF8_STRING", False), 8, PROP_REPLACE, values, len(values))
    else:
        arr = (c_ulong * len(values))(*values)
        x.XChangeProperty(dpy, win, atom, typ, 32, PROP_REPLACE, arr, len(values))
    x.XSync(dpy, False)


def test_x11_backend(xserver):
    x, wm, b = xserver
    root = x.XDefaultRootWindow(wm)
    atom = lambda n: x.XInternAtom(wm, n.encode(), False)

    assert [tuple(map(int, r)) for r in b.screens()] == [(0, 0, 1600, 900)]
    assert b.windows() == [] and b.active_window() is None

    def window(xp, yp, w, h, title, pid, **props):
        win = x.XCreateSimpleWindow(wm, root, xp, yp, w, h, 0, 0, 0)
        set_prop(x, wm, win, "_NET_WM_NAME", 0, title.encode())
        set_prop(x, wm, win, "_NET_WM_PID", XA_CARDINAL, [pid])
        for k, v in props.items():
            set_prop(x, wm, win, k, XA_ATOM if isinstance(v[0], str) else XA_CARDINAL,
                     [atom(i) if isinstance(i, str) else i for i in v])
        x.XMapWindow(wm, win)
        return win

    editor = window(0, 0, 800, 900, "editor — ünïcode", 111)
    term = window(800, 0, 800, 900, "terminal", 222)
    minimised = window(100, 100, 400, 400, "hidden", 333, _NET_WM_STATE=["_NET_WM_STATE_HIDDEN"])
    elsewhere = window(100, 100, 400, 400, "other workspace", 444, _NET_WM_DESKTOP=[3])
    dock = window(0, 850, 1600, 50, "panel", 555, _NET_WM_WINDOW_TYPE=["_NET_WM_WINDOW_TYPE_DOCK"])
    ours = window(200, 200, 300, 300, "gazehop overlay", os.getpid())
    gtk = window(300, 300, 500, 400, "gtk app", 666, _GTK_FRAME_EXTENTS=[20, 20, 10, 30])
    sticky = window(900, 100, 300, 300, "sticky", 777, _NET_WM_DESKTOP=[0xFFFFFFFF])

    everything = [editor, term, minimised, elsewhere, dock, ours, gtk, sticky]
    set_prop(x, wm, root, "_NET_CLIENT_LIST_STACKING", XA_WINDOW, everything)   # bottom to top
    set_prop(x, wm, root, "_NET_CURRENT_DESKTOP", XA_CARDINAL, [0])
    set_prop(x, wm, root, "_NET_ACTIVE_WINDOW", XA_WINDOW, [term])
    set_prop(x, wm, root, "_NET_SUPPORTED", XA_ATOM, [atom("_NET_ACTIVE_WINDOW")])

    wins = b.windows()
    assert [w.title for w in wins] == ["sticky", "gtk app", "terminal", "editor — ünïcode"]    # topmost first
    by = {w.title: w for w in wins}
    assert tuple(by["terminal"].rect) == (800, 0, 800, 900) and by["terminal"].pid == 222
    assert tuple(by["gtk app"].rect) == (320, 310, 460, 360)       # invisible shadow margin removed
    assert b.active_window().id == term
    assert b.supports("_NET_ACTIVE_WINDOW") and not b.supports("_NET_WM_STATE")

    # focus(): the window manager must receive a _NET_ACTIVE_WINDOW request for that window
    x.XSelectInput(wm, root, 1 << 20)   # SubstructureRedirectMask, as a real WM holds
    x.XSync(wm, False)
    assert b.focus(editor)
    x.XSync(wm, False)
    from gazehop.backends.x11 import XEvent
    got = None
    while x.XPending(wm):
        ev = XEvent()
        x.XNextEvent(wm, byref(ev))
        if ev.type == 33 and ev.client.message_type == atom("_NET_ACTIVE_WINDOW"):
            got = ev.client
    assert got is not None and got.window == editor and got.format == 32 and got.data[0] == 2

    # a window that vanished between listing and querying must not crash anything
    x.XDestroyWindow(wm, term)
    x.XSync(wm, False)
    assert "terminal" not in [w.title for w in b.windows()]
    assert b.active_window() is None

    idle = b.idle_seconds()
    assert idle is None or idle >= 0
    b.move_pointer(10, 10)
