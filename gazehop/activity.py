"""When did the user last type or use the mouse? Focus never moves under active hands.

With pynput (X11, Windows) keyboard and mouse are tracked separately and clicks are reported
for learning. Without it (or on Wayland, where global input hooks don't exist) it falls back
to the desktop's combined idle time.
"""
from __future__ import annotations

import sys
import time


class Activity:
    def __init__(self, backend, use_hooks=True):
        self.backend = backend
        self.on_click = None        # (x, y) — called from a listener thread
        self.on_hotkey = None       # called from a listener thread
        self._key = self._mouse = -1e9
        self._ignore_mouse_until = -1e9
        self._listeners = []
        self.hooks = False
        self._use_hooks = use_hooks

    def start(self, hotkey: str | None = None) -> None:
        if not self._use_hooks:
            return
        try:
            from pynput import keyboard, mouse
        except Exception as e:      # ImportError, or no usable display
            print(f"gazehop: pynput unavailable ({e}); using idle time only — no click learning, no hotkey.",
                  file=sys.stderr)
            return

        def on_key(_key):
            self._key = time.monotonic()

        def on_mouse(*_a):
            now = time.monotonic()
            if now > self._ignore_mouse_until:
                self._mouse = now

        def on_click(x, y, _button, pressed):
            on_mouse()
            if pressed and self.on_click and time.monotonic() > self._ignore_mouse_until:
                self.on_click(x, y)

        try:
            self._listeners = [keyboard.Listener(on_press=on_key),
                               mouse.Listener(on_move=on_mouse, on_click=on_click, on_scroll=on_mouse)]
            if hotkey and self.on_hotkey:
                self._listeners.append(keyboard.GlobalHotKeys({hotkey: lambda: self.on_hotkey()}))
            for l in self._listeners:
                l.daemon = True
                l.start()
            self.hooks = True
        except Exception as e:
            print(f"gazehop: could not start input listeners ({e}); using idle time only.", file=sys.stderr)

    def stop(self) -> None:
        for l in self._listeners:
            try:
                l.stop()
            except Exception:
                pass

    def ignore_mouse(self, seconds: float) -> None:
        self._ignore_mouse_until = time.monotonic() + seconds

    def _idle(self) -> float:
        idle = self.backend.idle_seconds()
        return idle if idle is not None else 1e9

    def key_ago(self) -> float:
        return time.monotonic() - self._key if self.hooks else self._idle()

    def mouse_ago(self) -> float:
        if self.hooks:
            return time.monotonic() - self._mouse
        return 1e9 if time.monotonic() < self._ignore_mouse_until else self._idle()
