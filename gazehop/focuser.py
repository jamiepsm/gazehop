"""Turns a stream of gaze points into focus changes: screen first, then window.

Follows the decision logic of Eyeshift's Focuser (MIT, (c) 2026 Sarthak Chhabra),
without its macOS-only split-pane support.
"""
from __future__ import annotations

import time

from .geometry import Rect, Win

SCREEN_TYPING_PAUSE = 0.6   # screens may switch this soon after typing; windows wait the full typing pause
HYSTERESIS = 0.05           # going back across a screen boundary needs this much extra turn
OFF_SCREEN = 0.2            # further than this (fraction of width) from every screen = looking away
COOLDOWN = 0.3              # seconds between two focus changes


def pick_screen(h, screens, current, turn, hysteresis=HYSTERESIS, off_screen=OFF_SCREEN):
    """Which screen the head points at. Leaving the current screen means turning `turn` of the way
    from its centre toward another screen's centre (plus hysteresis), so boundaries don't flicker."""
    if not screens:
        return None
    d = [r.distance(h) for r in screens]
    nearest = min(range(len(screens)), key=d.__getitem__)
    if d[nearest] >= off_screen * screens[nearest].w:
        return None
    if current is None or current >= len(screens):
        return nearest
    cx, cy = screens[current].mid
    best, best_t = current, turn + hysteresis
    for k, r in enumerate(screens):
        if k == current:
            continue
        vx, vy = r.mid[0] - cx, r.mid[1] - cy
        t = ((h[0] - cx) * vx + (h[1] - cy) * vy) / (vx * vx + vy * vy)
        if t > best_t:
            best, best_t = k, t
    return best


def screen_of(rect: Rect, screens):
    for k, r in enumerate(screens):
        if r.contains(rect.mid):
            return k
    return None


class Focuser:
    def __init__(self, backend, cfg: dict, activity, clock=time.monotonic, dry_run=False):
        self.backend, self.cfg, self.activity, self.clock, self.dry_run = backend, cfg, activity, clock, dry_run
        self.facing = None          # screen the head is turned toward; None = looking away
        self.at_keyboard = False
        self.on_action = None       # called with (Win) after every focus change we make
        self._last_on_screen: dict = {}
        self._candidate = None      # (key, since)
        self._last_action = -1e9
        self._pretend = None        # dry run: (window we "focused", real active window at that moment)
        self._screens = (-1e9, [])  # monitors rarely move; don't re-enumerate on every frame

    def screens_now(self, now: float):
        if now - self._screens[0] > 1.0:    # 1 s TTL still catches a monitor hotplug quickly
            self._screens = (now, self.backend.screens())
        return self._screens[1]

    def update(self, gaze, head, keyboard_line=None) -> None:
        """`gaze` (head + eyes) picks the window; `head` (head pose only) picks the screen."""
        cfg, now = self.cfg, self.clock()
        screens = self.screens_now(now)
        # looking down at the keyboard (head or just eyes): keep focus exactly where it is
        self.at_keyboard = keyboard_line is not None and (head[1] > keyboard_line or gaze[1] > keyboard_line)
        if self.at_keyboard:
            self._candidate = None
            return
        front = self.backend.active_window()
        if self._pretend:
            # dry run: act as if our last switch happened, until the user really changes focus
            if (front.id if front else None) != self._pretend[1]:
                self._pretend = None
            else:
                front = next((w for w in self.backend.windows() if w.id == self._pretend[0]), front)
        current = screen_of(front.rect, screens) if front else None
        if front and current is not None:
            self._last_on_screen[current] = front.id
        # the head normally decides the screen (steadier); screen_from_gaze lets the eyes alone do it
        pointer = gaze if cfg.get("screen_from_gaze") else head
        self.facing = pick_screen(pointer, screens, current, cfg["head_turn"] / 100)

        if self.activity.mouse_ago() < cfg["mouse_hold"] or now - self._last_action < COOLDOWN or self.facing is None:
            self._candidate = None
            return
        key_ago = self.activity.key_ago()
        s = self.facing

        if s != current:
            if cfg["wait_typing"] and key_ago <= SCREEN_TYPING_PAUSE:
                self._candidate = None
                return
            wins = [w for w in self.backend.windows() if screen_of(w.rect, screens) == s]
            # prefer the window you last used there, then the one under your gaze, then the topmost
            target = next((w for w in wins if w.id == self._last_on_screen.get(s)), None) \
                or next((w for w in wins if w.rect.contains(gaze)), None) \
                or (wins[0] if wins else None)
            if target:
                self._act(f"screen:{s}", cfg["switch_delay_ms"] / 1000, target, now)
            else:
                self._candidate = None
            return

        if not cfg["window_focus"] or (cfg["wait_typing"] and key_ago <= cfg["typing_pause"]):
            self._candidate = None
            return
        # reading near the edge of the focused window must not hand focus to its neighbour:
        # stay until the gaze is clearly past the edge
        if front and front.rect.inset(-cfg["edge_margin"]).contains(gaze):
            self._candidate = None
            return
        # only windows on the screen you face: the head decides screens, so the eyes alone must not
        # pull focus across (it would bounce straight back)
        target = next((w for w in self.backend.windows()
                       if w.rect.contains(gaze) and screen_of(w.rect, screens) == s), None)
        if target is None or (front and target.id == front.id):
            self._candidate = None
            return
        self._act(f"win:{target.id}", cfg["window_delay_ms"] / 1000, target, now)

    def _act(self, key: str, dwell: float, target: Win, now: float) -> None:
        """Focuses `target` once the same target has been looked at for `dwell` seconds."""
        if self._candidate is None or self._candidate[0] != key:
            self._candidate = (key, now)
            return
        if now - self._candidate[1] < dwell:
            return
        self._candidate = None
        self._last_action = now
        if self.dry_run:
            real = self.backend.active_window()
            self._pretend = (target.id, real.id if real else None)
        else:
            self.backend.focus(target.id)
            if self.cfg["move_pointer"]:
                self.activity.ignore_mouse(0.3)     # our own pointer move is not the user using the mouse
                self.backend.move_pointer(*target.rect.mid)
        if self.on_action:
            self.on_action(target)
