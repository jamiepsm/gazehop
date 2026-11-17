"""GNOME on Wayland backend (EXPERIMENTAL).

Wayland gives ordinary apps no way to list or focus other apps' windows, so this talks to
GNOME Shell through the "Window Calls" extension (window-calls@domandoman.xyz) over D-Bus:
in-process through PyGObject when it is there (well under a millisecond per call), else with the
`gdbus` tool that ships with GNOME (a process per call, 7 ms or more, blocking the main loop; the
packaged app has no PyGObject). Monitors come from XWayland's XRandR.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

from ..geometry import Rect, Win
from . import BackendError

SHELL, PATH, IFACE = "org.gnome.Shell", "/org/gnome/Shell/Extensions/Windows", "org.gnome.Shell.Extensions.Windows"
IDLE = ("org.gnome.Mutter.IdleMonitor", "/org/gnome/Mutter/IdleMonitor/Core", "org.gnome.Mutter.IdleMonitor")
INSTALL_HINT = (
    "On GNOME Wayland gazehop needs the 'Window Calls' GNOME Shell extension:\n"
    "  https://extensions.gnome.org/extension/4724/window-calls/\n"
    "Install and enable it, then log out and back in. (Or log in with 'Ubuntu on Xorg' if your\n"
    "Ubuntu version still offers it — the X11 backend needs no extension.)")


def parse_gvariant_string(out: str) -> str:
    """Pulls the string out of gdbus output like ('...',) and undoes GVariant's escaping."""
    m = re.match(r"""^\(\s*(['"])(.*)\1\s*,?\s*\)\s*$""", out.strip(), re.S)
    if not m:
        raise ValueError(f"unexpected gdbus output: {out[:80]!r}")
    return re.sub(r"\\(.)", lambda e: {"n": "\n", "t": "\t"}.get(e.group(1), e.group(1)), m.group(2))


def session_bus():
    """The session D-Bus through PyGObject, or None without it."""
    try:
        from gi.repository import Gio
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception:
        return None


class GnomeBackend:
    name = "gnome-wayland"
    CACHE = 0.25    # seconds; don't ask the shell on every camera frame

    def __init__(self):
        self._bus = session_bus()
        if self._bus is None and not shutil.which("gdbus"):
            raise BackendError("gdbus not found (sudo apt install libglib2.0-bin).")
        self._pid = os.getpid()
        self._cache = (0.0, [], None)
        self._rects: dict = {}
        self._idle = (-1e9, None)
        self._x11 = None
        try:
            self._call("List")
        except Exception as e:
            raise BackendError(f"{INSTALL_HINT}\n(details: {e})")
        if os.environ.get("DISPLAY"):
            try:
                from .x11 import X11Backend
                self._x11 = X11Backend()
            except Exception:
                self._x11 = None

    def _dbus(self, dest, path, iface, method, args=(), timeout=2.0) -> tuple:
        """Calls a D-Bus method whose arguments are all uint32 (window ids); returns its results."""
        if self._bus is not None:
            from gi.repository import Gio, GLib
            params = GLib.Variant("(" + "u" * len(args) + ")", tuple(int(a) for a in args)) if args else None
            return self._bus.call_sync(dest, path, iface, method, params, None, Gio.DBusCallFlags.NONE,
                                       int(timeout * 1000), None).unpack()
        r = subprocess.run(["gdbus", "call", "--session", "--dest", dest, "--object-path", path,
                            "--method", f"{iface}.{method}", *map(str, args)],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "gdbus failed")
        out = r.stdout.strip()
        if out == "()":
            return ()
        m = re.fullmatch(r"\(\s*(?:uint64\s+)?(\d+)\s*,?\s*\)", out)     # a number, e.g. the idle time
        return (int(m.group(1)),) if m else (parse_gvariant_string(out),)

    def _call(self, method: str, *args) -> str:
        """A Window Calls method; returns its string result (JSON), or "" if it has none."""
        out = self._dbus(SHELL, PATH, IFACE, method, args)
        return out[0] if out else ""

    def screens(self) -> list:
        if self._x11 is not None:
            return self._x11.screens()
        raise BackendError("Cannot read the monitor layout (XWayland / DISPLAY is not available).")

    def main_screen(self) -> Rect | None:
        return self._x11.main_screen() if self._x11 is not None else None

    def _refresh(self):
        now = time.monotonic()
        if now - self._cache[0] < self.CACHE:
            return
        try:
            raw = json.loads(self._call("List"))
        except Exception:
            self._cache = (now, [], None)
            return
        wins, active, seen = [], None, set()
        for w in reversed(raw):     # the shell lists windows bottom to top
            if not w.get("in_current_workspace", True) or w.get("window_type", 0) != 0:
                continue
            wid = int(w["id"])
            seen.add(wid)
            rect, minimized = self._frame(wid, w)
            # a minimised window must stay minimised: focusing it would bring it back
            if rect is None or minimized or rect.w < 50 or rect.h < 50 or w.get("pid") == self._pid:
                continue
            win = Win(wid, rect, int(w.get("pid") or 0), w.get("title") or "")
            wins.append(win)
            if w.get("focus"):
                active = win
        self._rects = {k: v for k, v in self._rects.items() if k in seen}
        self._cache = (now, wins, active)

    def _frame(self, wid: int, w: dict) -> tuple:
        """(Rect | None, minimised?) for one window. List leaves both out in most extension
        versions; Details has them (older versions only have GetFrameRect). Briefly cached."""
        if "minimized" in w and all(isinstance(w.get(k), (int, float)) for k in ("x", "y", "width", "height")):
            return Rect(w["x"], w["y"], w["width"], w["height"]), bool(w["minimized"])
        hit = self._rects.get(wid)
        if hit and time.monotonic() - hit[0] < 1.0:
            return hit[1]
        try:
            f = json.loads(self._call("Details", wid))
        except Exception:
            try:
                f = json.loads(self._call("GetFrameRect", wid))
            except Exception:
                return None, False
        found = Rect(f["x"], f["y"], f["width"], f["height"]), bool(f.get("minimized"))
        self._rects[wid] = (time.monotonic(), found)
        return found

    def windows(self) -> list:
        self._refresh()
        return self._cache[1]

    def active_window(self) -> Win | None:
        self._refresh()
        return self._cache[2]

    def focus(self, win_id: int) -> bool:
        try:
            self._call("Activate", win_id)
        except Exception:
            return False
        self._cache = (0.0, [], None)
        return True

    def move_pointer(self, x: float, y: float) -> None:
        pass    # not possible for a normal app on Wayland

    def idle_seconds(self) -> float | None:
        now = time.monotonic()
        if now - self._idle[0] >= self.CACHE:
            self._idle = (now, self._query_idle())
        return self._idle[1]

    def _query_idle(self) -> float | None:
        try:
            return self._dbus(*IDLE, "GetIdletime", timeout=1.0)[0] / 1000.0
        except Exception:
            return None
