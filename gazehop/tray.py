"""Tray icon (optional). Needs `pystray` and Pillow; on GNOME also the AppIndicator support that
Ubuntu ships. If any of it is missing the app simply runs without a tray icon.

Menu clicks arrive on the tray's own thread, so they are only queued here; the app acts on them
from its main loop.
"""
from __future__ import annotations

import os
import sys
import threading

from .ui import ASSETS


class Tray:
    def __init__(self, app):
        import pystray
        from PIL import Image
        self.app = app
        self._images = {False: Image.open(os.path.join(ASSETS, "tray.png")),
                        True: Image.open(os.path.join(ASSETS, "tray-paused.png"))}
        send = lambda *event: (lambda _icon=None, _item=None: app.events.put(event))
        item, menu = pystray.MenuItem, pystray.Menu
        self.icon = pystray.Icon("gazehop", self._images[False], "gazehop", menu(
            item("Open gazehop", send("window"), default=True),
            item(lambda _i: f"Update to {app.update[0]}…" if app.update else "", send("update_page"),
                 visible=lambda _i: app.update is not None),
            item(lambda _i: "Resume" if app.paused else "Pause", send("pause")),
            menu.SEPARATOR,
            item("Calibrate (start over)…", send("calibrate", False)),
            item("Add a calibration round…", send("calibrate", True)),
            item("Show gaze dot", send("dot"), checked=lambda _i: app.dot_visible),
            item("Settings…", send("settings")),
            menu.SEPARATOR,
            item("Quit", send("quit"))))
        self._shown = (False, None)     # (paused, update) the icon and menu were last drawn for
        self._thread = threading.Thread(target=self.icon.run, name="gazehop-tray", daemon=True)
        self._thread.start()

    def refresh(self) -> None:
        """Called from the main loop: keeps the icon and menu in step with the app."""
        now = (self.app.paused, self.app.update)
        if now != self._shown:
            self._shown = now
            try:
                self.icon.icon = self._images[now[0]]
                self.icon.title = "gazehop (paused)" if now[0] else "gazehop"
                self.icon.update_menu()
            except Exception:
                pass

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass


def start(app) -> "Tray | None":
    try:
        return Tray(app)
    except Exception as e:
        print(f"gazehop: no tray icon ({type(e).__name__}: {e}).", file=sys.stderr)
        return None
