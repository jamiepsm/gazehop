"""Platform backends: list screens and windows, read the active window, focus a window.

Every backend offers:
    name                    short label
    screens()   -> [Rect]   monitors in global pixels
    windows()   -> [Win]    normal visible windows, topmost first, without gazehop's own
    active_window() -> Win | None
    focus(win_id) -> bool
    move_pointer(x, y)
    idle_seconds() -> float | None    time since the last keyboard/mouse input, if known
"""
from __future__ import annotations

import os
import sys


class BackendError(RuntimeError):
    pass


def get_backend():
    if sys.platform == "win32":
        from .windows import WindowsBackend
        return WindowsBackend()
    if sys.platform.startswith("linux"):
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            desktop = (os.environ.get("XDG_CURRENT_DESKTOP", "") + os.environ.get("DESKTOP_SESSION", "")).lower()
            if "gnome" in desktop or "ubuntu" in desktop:
                from .gnome import GnomeBackend
                return GnomeBackend()
            raise BackendError(
                "This is a Wayland session on a desktop gazehop has no backend for.\n"
                "Wayland does not let ordinary apps list or focus other apps' windows.\n"
                "Log in with an X11/Xorg session instead, or use GNOME (see the README).")
        from .x11 import X11Backend
        return X11Backend()
    raise BackendError(f"Unsupported platform: {sys.platform} (gazehop runs on Linux and Windows; "
                       "on macOS use Eyeshift itself).")
