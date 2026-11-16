"""Ties it together: camera thread -> gaze model -> focuser, on a hidden Tk main loop."""
from __future__ import annotations

import math
import queue
import sys
import threading
import time

from . import config
from .activity import Activity
from .focuser import Focuser
from .model import GazeModel, OneEuro, Sample
from .tracker import Tracker, ensure_model

FRESH = 0.25    # a face reading older than this is stale


class App:
    def __init__(self, backend, cfg: dict, dry_run=False, force_calibrate=False, add_round=False, profile=None,
                 window=False, tray=False):
        import tkinter as tk
        self.backend, self.cfg, self.dry_run = backend, cfg, dry_run
        from .ui import APP_CLASS, set_icon
        self.root = tk.Tk(className=APP_CLASS)
        self.root.withdraw()
        set_icon(self.root)     # every window after this gets the icon
        self.screens = backend.screens()
        self.model_file = config.model_path(profile)
        self.profile = profile
        saved = GazeModel.load(self.model_file, self.screens)
        self.add_round = add_round and saved is not None
        if add_round and saved is None:
            print("gazehop: nothing to add to (no calibration for this screen layout) — starting a fresh one.")
        # an added round builds on the saved model; a plain recalibration starts over
        self.model = saved if (self.add_round or not force_calibrate) else None
        # with the app window open, the user starts calibration from its button once the camera looks right
        self._calibrate_first = force_calibrate or add_round or (saved is None and not window)
        self._cal_last = 0.0
        self.activity = Activity(backend, use_hooks=backend.name != "gnome-wayland")
        self.activity.on_click = lambda x, y: self.events.put(("click", x, y, time.monotonic()))
        self.activity.on_hotkey = lambda: self.events.put(("pause",))
        self.focuser = Focuser(backend, cfg, self.activity, dry_run=dry_run)
        self.focuser.on_action = self._switched
        self.tracker = Tracker(cfg["camera"], ensure_model(config.data_dir()))
        self.events: queue.Queue = queue.Queue()
        self.calibration = None
        self.paused = False
        self.exit_after_calibration = False
        self._seq = 0
        self._reading = None            # (features, time) of the latest face reading
        self._gaze_filter, self._head_filter = OneEuro(), OneEuro()
        self._recent_gaze, self._recent_head = [], []
        self._gaze = None
        self._last_switch = None        # (time, target rect, features, gaze) — to learn if it was right
        self._unfitted = 0
        self._dot = None
        self._report = None             # (time, frames, faces) when running started
        self._want_window, self._want_tray = window, tray
        self.window = None              # the app window (camera view), if open
        self.settings = None            # the settings window, if open
        self.update = None              # (version, release page) once a newer version is known
        self.tray = None

    # ---- lifecycle

    def run(self) -> int:
        import signal
        # a plain kill, or logging out, ends the app the normal way: learned clicks get saved
        for name in ("SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: self.root.after(0, self.quit))
        self.tracker.start()
        self.activity.start(self.cfg["hotkey"])
        if self._calibrate_first:
            if self.add_round:
                print(f"gazehop: adding a calibration round to the {len(self.model.calib)} samples already there.")
            elif self.model is None:
                print("gazehop: starting calibration.")
            self.root.after(800, self.calibrate)
        elif self.model is None:
            print("gazehop: not calibrated yet — press Calibrate in the window.")
        else:
            self._announce()
        self._sync_dot()
        if self._want_tray:
            from . import tray
            self.tray = tray.start(self)
        if self._want_window:
            self.open_window()
        if self.cfg["check_updates"]:
            from . import update
            threading.Thread(target=lambda: self.events.put(("update", update.check())),
                             name="gazehop-update", daemon=True).start()
        self.root.after(30, self._pump)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            self.tracker.stop()
            self.activity.stop()
            if self.tray:
                self.tray.stop()
            if self.model and self._unfitted:
                self.model.save(self.model_file)
        return 1 if self.tracker.error else 0

    def _announce(self) -> None:
        mode = "DRY RUN (not changing focus)" if self.dry_run else "running"
        hot = f"{self.cfg['hotkey']} pauses, " if self.activity.hooks else ""
        print(f"gazehop: {mode} on {self.backend.name}, {len(self.screens)} screen(s). {hot}Ctrl+C quits.")
        self._report = (time.monotonic(), self.tracker.frames, self.tracker.faces)

    def calibrate(self) -> None:
        from .calibration import Calibration
        if self.calibration:
            return
        main = self.backend.main_screen()
        self.tracker.preview = True             # the camera picture is shown during calibration too
        if self._dot:
            self._dot.hide()                    # it would only pull the eyes away from the dots
        self.calibration = Calibration(self.root, self.screens, self._calibrated,
                                       base=self.model if self.add_round else None, previous=self.model,
                                       main=self.screens.index(main) if main in self.screens else 0,
                                       preview=self.tracker.latest_preview)
        self.calibration.start()

    def _calibrated(self, model) -> None:
        self.calibration = None
        self.tracker.preview = bool(self.window and self.window.visible)
        if self._dot:
            self._dot.show()
        if model is None:
            self.add_round = False
            print("gazehop: calibration cancelled, discarded, or the camera saw too little of your face.")
            if self.model is None or self.exit_after_calibration:
                self.root.quit()
            return
        self.model = model
        model.save(self.model_file)
        self._reset_filters()
        self.add_round = False
        print(f"gazehop: calibrated ({len(model.calib)} samples). Saved to {self.model_file}")
        err = model.typical_error()
        if err is not None:
            width = min(r.w for r in self.screens)
            verdict = "good" if err < 0.08 * width else "usable" if err < 0.15 * width else "rough — try better light, or add a round"
            print(f"gazehop: typical error about {err:.0f} px ({100 * err / width:.0f}% of a screen's width): {verdict}")
        if self.exit_after_calibration:
            self.root.quit()
        else:
            self._announce()

    def _reset_filters(self) -> None:
        self._gaze_filter.reset()
        self._head_filter.reset()
        self._recent_gaze, self._recent_head, self._gaze = [], [], None

    # ---- main loop

    def _pump(self) -> None:
        if self.tracker.error:
            print(f"gazehop: {self.tracker.error}", file=sys.stderr)
            if self._want_window:
                from .ui import show_error
                show_error(self.tracker.error)
            self.root.quit()
            return
        try:
            self._tick()
        except Exception as e:      # a vanished window or a flaky query must not end the session
            print(f"gazehop: {type(e).__name__}: {e}", file=sys.stderr)
        self.root.after(30, self._pump)

    def _tick(self) -> None:
        now = time.monotonic()
        latest = self.tracker.latest()
        new = latest is not None and latest[0] != self._seq
        if new:
            self._seq = latest[0]
            self._reading = (latest[1], latest[2]) if latest[1] is not None else None
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                break
            if ev[0] == "pause":
                self.toggle_pause()
            elif ev[0] == "click":
                self._clicked((ev[1], ev[2]), ev[3])
            elif ev[0] == "calibrate":
                self.request_calibration(add=ev[1])
            elif ev[0] == "window":
                self.open_window()
            elif ev[0] == "dot":
                self.toggle_dot()
            elif ev[0] == "settings":
                self.open_settings()
            elif ev[0] == "cameras" and self.settings:
                self.settings.set_cameras(ev[1])
            elif ev[0] == "update" and ev[1]:
                from .update import how_to_update
                self.update = ev[1]
                print(f"gazehop: version {ev[1][0]} is out: {ev[1][1]}  {how_to_update()}")
            elif ev[0] == "update_page" and self.update:
                import webbrowser
                webbrowser.open(self.update[1])
            elif ev[0] == "quit":
                self.quit()
        if self.tray:
            self.tray.refresh()
        if self.window:
            self.window.refresh()
        if self.calibration:
            if new and latest[1] is not None and latest[2] - self._cal_last >= 0.07:
                self._cal_last = latest[2]
                self.calibration.add(latest[1])
            return
        if self._report and now - self._report[0] > 5:
            self._speed_report(now)
        if self._last_switch and now - self._last_switch[0] > 5:
            self._settle_last_switch()
        if not new or self.paused or self.model is None or self._reading is None:
            return
        f = self._reading[0]
        n = max(1, int(self.cfg["smoothing"]))
        # averaging the last few estimates calms the jitter the filter lets through
        self._recent_gaze = (self._recent_gaze + [self._gaze_filter.filter(self.model.predict(f), now)])[-n:]
        self._recent_head = (self._recent_head + [self._head_filter.filter(self.model.predict_head(f), now)])[-n:]
        self._gaze = _mean(self._recent_gaze)
        head = _mean(self._recent_head)
        if self._dot:
            self._dot.move_to(self._gaze)
        self.focuser.update(self._gaze, head, self.model.keyboard_line)

    def _speed_report(self, now: float) -> None:
        t, frames, faces = self._report
        self._report = None
        n = self.tracker.frames - frames
        fps = n / (now - t)
        seen = 100 * (self.tracker.faces - faces) / max(n, 1)
        note = ""
        if fps < 12:
            note = " — slow: switching will feel laggy; close other camera apps or lower `smoothing`"
        elif seen < 80:
            note = " — your eyes are often unreadable: check lighting and camera position"
        print(f"gazehop: tracking at {fps:.0f} frames/s, face readable in {seen:.0f}% of them{note}")

    # ---- controls (window buttons, tray menu, hotkey)

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self._reset_filters()
        print("gazehop: paused" if self.paused else "gazehop: resumed")

    def request_calibration(self, add: bool) -> None:
        if self.calibration:
            return
        self.add_round = add and self.model is not None
        self.calibrate()

    def open_window(self) -> None:
        if self.window is None:
            from .ui import ControlWindow
            self.window = ControlWindow(self)
        else:
            self.window.show()

    def open_settings(self) -> None:
        if self.settings is None:
            from .settings import SettingsWindow
            self.settings = SettingsWindow(self)
        else:
            self.settings.show()

    def set_options(self, changes: dict) -> None:
        """From the settings window: applies now (the focuser reads this same dict) and saves."""
        self.cfg.update(changes)
        self._sync_dot()
        if "dot_color" in changes and self._dot:
            self._dot.set_colour(changes["dot_color"])
        config.save(changes)

    @property
    def dot_visible(self) -> bool:
        return self._dot is not None

    def toggle_dot(self) -> None:
        """Button, tray and hotkey flip what is really on screen; the setting and the settings
        window follow, so nothing can show the opposite."""
        self.set_options({"show_dot": not self.dot_visible})
        if self.settings:
            self.settings.reflect("show_dot", self.cfg["show_dot"])

    def _sync_dot(self) -> None:
        """Makes the screen match the show_dot setting: the one place the dot is made or removed."""
        if self.cfg["show_dot"] and self._dot is None:
            self._make_dot()
        elif not self.cfg["show_dot"] and self._dot is not None:
            self._dot.destroy()
            self._dot = None

    def quit(self) -> None:
        self.root.quit()

    def status(self) -> tuple[str, str]:
        """(headline, detail) for the window."""
        mode = "Dry run — focus is not changed. " if self.dry_run else ""
        if self.calibration:
            return "Calibrating…", "Follow the instructions on the dimmed screen."
        if self.model is None:
            return "Not calibrated", "Press Calibrate to start (about 45 seconds per screen)."
        if self.paused:
            return "Paused", "Focus stays where it is until you resume."
        if self._fresh() is None:
            return "No readable face", "Sit in front of the camera with your face evenly lit."
        if self.focuser.at_keyboard:
            return "Looking at the keyboard", mode + "Focus is held while you look down."
        if self.focuser.facing is None:
            return "Looking away", mode + "Focus is held while you look off-screen."
        return f"Tracking — facing screen {self.focuser.facing + 1} of {len(self.screens)}", \
            mode + f"Calibration: {len(self.model.calib)} samples, {len(self.model.learned)} learned from clicks."

    def _fresh(self, at: float | None = None):
        r = self._reading
        return r[0] if r and abs((at or time.monotonic()) - r[1]) < FRESH else None

    # ---- learning (from Eyeshift): clicks are where you were almost certainly looking

    def _switched(self, target) -> None:
        print(f"gazehop: {'would focus' if self.dry_run else 'focus'} -> {target.title[:60] or target.id}")
        self._settle_last_switch()
        f = self._fresh()
        if f is not None and self._gaze:
            self._last_switch = (time.monotonic(), target.rect, f, self._gaze)

    def _clicked(self, click, at: float) -> None:
        f = self._fresh(at)
        if self.paused or self.calibration or self.model is None or not self.cfg["learn"] or f is None:
            return
        screen = next((r for r in self.screens if r.contains(click)), None)
        if screen is None:
            return
        # clicking somewhere else right after we moved focus = we got it wrong, and the click is the
        # right answer. Learn from what the face looked like when we decided, and weigh it heavily.
        ls = self._last_switch
        if ls and at - ls[0] < 4 and not ls[1].contains(click):
            self._last_switch = None
            self._remember(Sample(list(ls[2]), click[0], click[1], click[0], click[1], w=3.0))
        p = self.model.predict(f)
        if math.hypot(p[0] - click[0], p[1] - click[1]) >= 0.4 * screen.w:
            return      # clicked while clearly looking elsewhere
        self._remember(Sample(list(f), click[0], click[1], click[0], click[1]))

    def _settle_last_switch(self) -> None:
        """No click-away within 5 s and you typed into what we focused = we got it right. Reinforce
        gently, with the gaze pulled inside the focused window."""
        ls, self._last_switch = self._last_switch, None
        if not ls or not self.cfg["learn"] or self.model is None:
            return
        if self.activity.key_ago() >= time.monotonic() - ls[0]:
            return
        t = ls[1].inset(min(40, ls[1].w / 4, ls[1].h / 4))
        p = t.clamp(ls[3])
        self._remember(Sample(list(ls[2]), p[0], p[1], p[0], p[1], w=0.5))

    def _remember(self, s: Sample) -> None:
        self.model.remember(s)
        self._unfitted += 1
        if self._unfitted >= 10:
            fitted = self.model.refit()
            if fitted:
                self.model, self._unfitted = fitted, 0
                fitted.save(self.model_file)

    # ---- gaze dot

    def _make_dot(self) -> None:
        from .dot import GazeDot
        self._dot = GazeDot(self.root, self.cfg.get("dot_color"))
        if self.calibration:
            self._dot.hide()


def _mean(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
