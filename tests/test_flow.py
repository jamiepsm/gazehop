"""End-to-end without hardware: a fake Tk, a simulated face, and a fake desktop.

Runs the real calibration flow and the real app loop: a simulated user follows the calibration
dots, then looks at another window, and focus must follow.
"""
import random
import sys
import time
import types

import pytest

from gazehop.geometry import Rect, Win
from tests.test_core import CFG, FakeBackend

SCREEN = Rect(0, 0, 1600, 900)


class FakeTk:
    """Just enough of tkinter: widgets swallow drawing calls, `after` queues on a simulated clock."""
    now = 0.0
    queue: list = []

    def __init__(self, *a, **k):
        self.bindings, self.calls, self.options = {}, [], dict(k)

    def after(self, ms, fn):
        FakeTk.queue.append((FakeTk.now + ms / 1000, fn))

    def bind(self, seq, fn):
        self.bindings[seq] = fn

    def configure(self, **k):
        self.options.update(k)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: self.calls.append((name, a, k))

    @classmethod
    def advance(cls, dt):
        cls.now += dt
        due = sorted((x for x in cls.queue if x[0] <= cls.now), key=lambda x: x[0])
        cls.queue = [x for x in cls.queue if x[0] > cls.now]
        for _, fn in due:
            fn()


@pytest.fixture
def faketk(monkeypatch):
    FakeTk.now, FakeTk.queue = 0.0, []
    mod = types.ModuleType("tkinter")
    mod.Tk = mod.Toplevel = mod.Canvas = mod.Misc = mod.Label = mod.Button = mod.Frame = mod.PhotoImage = FakeTk
    mod._default_root = None
    monkeypatch.delitem(sys.modules, "gazehop.ui", raising=False)
    monkeypatch.delitem(sys.modules, "gazehop.tray", raising=False)
    monkeypatch.setitem(sys.modules, "tkinter", mod)
    monkeypatch.delitem(sys.modules, "gazehop.calibration", raising=False)
    monkeypatch.delitem(sys.modules, "gazehop.app", raising=False)
    return mod


def face(gaze, head, rng):
    """What the tracker would report for someone whose head points at `head` and eyes at `gaze`."""
    n = lambda s: rng.gauss(0, s)
    yaw, pitch = (head[0] - 800) / 2000 + n(0.004), (head[1] - 450) / 2000 + n(0.004)
    ix, iy = 0.5 + (gaze[0] - head[0]) / 3000 + n(0.004), 0.5 + (gaze[1] - head[1]) / 3000 + n(0.004)
    return [yaw, pitch, n(0.01), 0.5 + n(0.01), 0.5 + n(0.01), 0.25 + n(0.003), ix, iy] + [n(1) for _ in range(144)]


def calibrate(root, screens, keep=True, **kw):
    from gazehop.calibration import EYES, NOSE, Calibration
    rng, result = random.Random(3), []
    cal = Calibration(root, screens, result.append, **kw)
    cal.start()
    cal.wins[0].bindings["<space>"]()
    for _ in range(4000):
        FakeTk.advance(1 / 15)
        if result:
            break
        if cal.waiting and cal.review is not None:      # the review step: look around, then decide
            for g in ((300, 300), (1200, 600)):
                cal.add(face(g, g, rng))
            rings = [c for c in cal.canvases[0].calls if c[0] == "create_oval" and c[2].get("tags") == "live"]
            assert rings, "no live ring during review"
            cal.wins[0].bindings["<space>" if keep else "<Escape>"]()
        elif cal.waiting:               # the "look at your keyboard" prompt
            assert cal.keyboard_step
            cal.wins[0].bindings["<space>"]()
        elif cal.target:
            p, kind, centre = cal.target
            head = centre if kind == EYES else p if kind == NOSE else (p[0] + rng.uniform(-300, 300), p[1] + rng.uniform(-200, 200))
            cal.add(face(p, head, rng))
        elif cal.collecting_keyboard:
            cal.add(face((800, 1500), (800, 1500), rng))
    assert result, "calibration never finished"
    return result[0], FakeTk.now


def test_calibration_flow(faketk):
    model, took = calibrate(FakeTk(), [SCREEN])
    assert model is not None and 40 < took < 90      # "about 45 seconds" plus prompts
    assert model.keyboard_line is not None and model.keyboard_line > SCREEN.bottom
    rng, errs = random.Random(9), []
    for _ in range(200):
        g = (rng.uniform(100, 1500), rng.uniform(100, 800))
        h = (g[0] + rng.uniform(-250, 250), g[1] + rng.uniform(-150, 150))
        p = model.predict(face(g, h, rng))
        errs.append(((p[0] - g[0]) ** 2 + (p[1] - g[1]) ** 2) ** 0.5)
    assert sorted(errs)[100] < 120, f"median error {sorted(errs)[100]:.0f}px"


def test_calibration_cancel(faketk):
    from gazehop.calibration import Calibration
    result = []
    cal = Calibration(FakeTk(), [SCREEN], result.append)
    cal.start()
    cal.wins[0].bindings["<space>"]()
    FakeTk.advance(3)
    cal.wins[0].bindings["<Escape>"]()
    FakeTk.advance(60)                  # timers still pending must not revive it
    assert result == [None]


def test_app_focus_follows_gaze(faketk, monkeypatch, tmp_path):
    from gazehop import config
    monkeypatch.setattr(config, "data_dir", lambda: str(tmp_path))
    import gazehop.app as appmod

    class FakeTracker:
        error, frames, faces = None, 0, 0
        def __init__(self, *a): self.seq, self.f = 0, None
        def start(self): pass
        def stop(self): pass
        preview, picture = False, None
        def latest_preview(self): return self.picture
        def latest(self):
            self.seq += 1
            return (self.seq, self.f, time.monotonic())

    class FakeActivity:
        hooks, on_click, on_hotkey = True, None, None
        def __init__(self, *a, **k): self.key = 1e9
        def start(self, hotkey=None): pass
        def stop(self): pass
        def key_ago(self): return self.key
        def mouse_ago(self): return 1e9
        def ignore_mouse(self, s): pass

    monkeypatch.setattr(appmod, "Tracker", FakeTracker)
    monkeypatch.setattr(appmod, "Activity", FakeActivity)
    monkeypatch.setattr(appmod, "ensure_model", lambda d: "unused")
    wins = [Win(1, Rect(0, 0, 780, 900), 10, "editor"), Win(2, Rect(820, 0, 780, 900), 11, "terminal")]
    backend = FakeBackend([SCREEN], wins, 1)
    cfg = dict(CFG, camera=0, learn=True, smoothing=3, show_dot=False, hotkey="<ctrl>+<alt>+g")

    app = appmod.App(backend, cfg)
    assert app.model is None            # nothing calibrated yet
    assert app.status()[0] == "Not calibrated"
    app.calibrate()
    assert app.status()[0] == "Calibrating…"
    app.calibration._finish(calibrate(FakeTk(), [SCREEN])[0])
    assert app.model is not None and appmod.GazeModel.load(config.model_path(), [SCREEN]) is not None

    rng = random.Random(5)

    def look(gaze, head, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.tracker.f = face(gaze, head, rng)
            app._tick()
            time.sleep(1 / 30)

    look((300, 450), (500, 450), 0.5)
    assert backend.focused == []                        # looking at the window that already has focus
    look((1300, 450), (900, 450), 1.0)                  # eyes move to the terminal, head barely turns
    assert backend.focused == [2]
    look((800, 1500), (800, 1500), 1.0)                 # down at the keyboard: nothing moves
    assert backend.focused == [2] and app.focuser.at_keyboard

    # the user clicks back in the editor right away: that's a correction, learned with weight 3
    app._clicked((300, 450), time.monotonic())
    assert any(s.w == 3.0 for s in app.model.learned)

    # the app window: camera picture, status and buttons follow the app
    import numpy as np
    app.tracker.picture = np.zeros((270, 480, 3), np.uint8)
    app.open_window()
    look((300, 450), (500, 450), 0.2)
    win = app.window
    assert app.tracker.preview and win.status.options["text"].startswith("Tracking")
    assert win.buttons["pause"].options["text"] == "Pause" and win.buttons["add"].options["state"] == "normal"
    win.buttons["dot"].options["command"]()
    assert app.dot_visible
    app.events.put(("dot",))
    backend.focused.clear(); backend.active = 2

    app.events.put(("pause",))                          # hotkey / tray / button
    look((300, 450), (500, 450), 1.0)
    assert win.status.options["text"] == "Paused" and win.buttons["pause"].options["text"] == "Resume"
    assert not app.dot_visible
    backend.focused.append(2)
    assert app.paused and backend.focused == [2]
    app.events.put(("pause",))
    look((300, 450), (500, 450), 1.0)
    assert backend.focused == [2, 1]


def test_add_round_and_profiles(faketk, monkeypatch, tmp_path):
    """A second round (say, with glasses) is added to the first; profiles keep separate files."""
    from gazehop import config
    from gazehop.calibration import Calibration
    from gazehop.model import GazeModel
    monkeypatch.setattr(config, "data_dir", lambda: str(tmp_path))
    first, _ = calibrate(FakeTk(), [SCREEN])
    err = first.typical_error()
    assert err is not None and 0 < err < 150

    # the same person with glasses: the eyes read differently (shifted iris reading)
    rng, result = random.Random(4), []
    cal = Calibration(FakeTk(), [SCREEN], result.append, base=first)
    cal.start()
    cal.wins[0].bindings["<space>"]()
    glasses = lambda g, h: (lambda f: f[:6] + [f[6] + 0.08, f[7] - 0.05] + f[8:])(face(g, h, rng))
    for _ in range(4000):
        FakeTk.advance(1 / 15)
        if result:
            break
        if cal.waiting:
            cal.wins[0].bindings["<space>"]()
        elif cal.target:
            p, kind, centre = cal.target
            cal.add(glasses(p, centre if kind == "eyes" else p))
        elif cal.collecting_keyboard:
            cal.add(glasses((800, 1500), (800, 1500)))
    both = result[0]
    assert both is not None and len(both.calib) > 1.8 * len(first.calib)
    assert len(both.keyboard) > len(first.keyboard)

    assert config.model_path() != config.model_path("glasses") == str(tmp_path / "model-glasses.json")
    assert config.model_path("../../etc/x") == str(tmp_path / "model-etcx.json")    # no path tricks
    both.save(config.model_path("glasses"))
    assert GazeModel.load(config.model_path("glasses"), [SCREEN]) is not None
    assert GazeModel.load(config.model_path(), [SCREEN]) is None
    size = (tmp_path / "model-glasses.json").stat().st_size
    assert size < 6_000_000, size


def test_review_discard_keeps_previous(faketk):
    first, _ = calibrate(FakeTk(), [SCREEN])
    again, _ = calibrate(FakeTk(), [SCREEN], keep=False, previous=first)
    assert first is not None and again is None


def test_previous_ring_shown_while_calibrating(faketk):
    from gazehop.calibration import Calibration
    first, _ = calibrate(FakeTk(), [SCREEN])
    cal = Calibration(FakeTk(), [SCREEN], lambda m: None, previous=first)
    cal.start()
    cal.add(face((400, 400), (400, 400), random.Random(1)))
    rings = [c for c in cal.canvases[0].calls if c[0] == "create_oval" and c[2].get("tags") == "live"]
    assert len(rings) == 1 and abs(rings[0][1][0] + 16 - 400) < 200     # roughly where the "user" looks


def test_tray_menu_queues_events(faketk, monkeypatch):
    """The tray only queues events (its clicks arrive on another thread); the app acts on them."""
    import queue
    made = {}

    class Icon:
        def __init__(self, name, image, title, menu):
            made.update(menu=menu, icon=self)
            self.icon, self.title = image, title
        def run(self): pass
        def stop(self): made["stopped"] = True
        def update_menu(self): pass

    class Item:
        def __init__(self, text, action, **k): self.text, self.action, self.k = text, action, k

    fake = types.ModuleType("pystray")
    fake.Icon, fake.MenuItem = Icon, Item
    fake.Menu = type("Menu", (), {"SEPARATOR": None, "__init__": lambda self, *items: setattr(self, "items", items)})
    monkeypatch.setitem(sys.modules, "pystray", fake)
    from gazehop import tray

    app = types.SimpleNamespace(events=queue.Queue(), paused=False, dot_visible=False)
    t = tray.start(app)
    assert t is not None
    for it in made["menu"].items:
        if it is not None:
            it.action(None, it)
    got = []
    while not app.events.empty():
        got.append(app.events.get())
    assert got == [("window",), ("pause",), ("calibrate", False), ("calibrate", True), ("dot",), ("quit",)]
    pause = made["menu"].items[1]
    assert pause.text(pause) == "Pause"
    app.paused = True
    t.refresh()
    assert pause.text(pause) == "Resume" and made["icon"].title == "gazehop (paused)"
    t.stop()
    assert made["stopped"]

    monkeypatch.setitem(sys.modules, "pystray", None)       # not installed: the app carries on without
    assert tray.start(app) is None
