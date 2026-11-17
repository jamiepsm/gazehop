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

    def subsample(self, *a):
        return self

    def bbox(self, *a):
        return (0, 0, 0, 0)

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
    mod.Entry = FakeTk
    mod._default_root = None
    monkeypatch.delitem(sys.modules, "gazehop.ui", raising=False)
    monkeypatch.delitem(sys.modules, "gazehop.tray", raising=False)
    monkeypatch.delitem(sys.modules, "gazehop.settings", raising=False)
    monkeypatch.setitem(sys.modules, "tkinter", mod)
    monkeypatch.delitem(sys.modules, "gazehop.calibration", raising=False)
    monkeypatch.delitem(sys.modules, "gazehop.app", raising=False)
    monkeypatch.setattr("gazehop.dot._x11", ())     # no real X windows from a test: the Tk dot, faked
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
    bullets = [c[2]["fill"] for c in cal.canvases[0].calls if c[0] == "create_text" and c[2].get("text") == "●"]
    assert bullets == ["#ff453a", "#0a84ff", "#30d158"]      # the intro's dots in the colours of the phases
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
    assert app.model is not None and appmod.GazeModel.load(app.model_file, [SCREEN]) is not None

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

    # docked at another desk: that layout has no calibration yet; back home, the first one returns
    home, work = app.model, [Rect(0, 0, 1600, 900), Rect(1600, 0, 1600, 900)]
    app._remember(appmod.Sample(list(face((300, 450), (300, 450), rng)), 300, 450))    # a click not saved yet
    backend._screens = work
    look((300, 450), (500, 450), 1.2)
    assert app.model is None and app.screens == work and app.status()[0] == "Not calibrated"
    assert len(appmod.GazeModel.load(config.model_path(layout="0,0,1600,900"), [SCREEN]).learned) == len(home.learned)
    backend._screens = [SCREEN]
    look((300, 450), (500, 450), 1.2)
    assert app.model is not None and len(app.model.calib) == len(home.calib)


def test_add_round_and_profiles(faketk, monkeypatch, tmp_path):
    """A second round (say, with glasses) is added to the first; profiles keep separate files."""
    from gazehop import config
    from gazehop.calibration import Calibration
    from gazehop.model import GazeModel
    monkeypatch.setattr(config, "data_dir", lambda: str(tmp_path))
    first, _ = calibrate(FakeTk(), [SCREEN])
    err = first.error
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
            made.update(menu=menu, icon=self, redraws=0)
            self.icon, self.title = image, title
        def run(self): pass
        def stop(self): made["stopped"] = True
        def update_menu(self): made["redraws"] += 1

    class Item:
        def __init__(self, text, action, **k): self.text, self.action, self.k = text, action, k

    fake = types.ModuleType("pystray")
    fake.Icon, fake.MenuItem = Icon, Item
    fake.Menu = type("Menu", (), {"SEPARATOR": None, "__init__": lambda self, *items: setattr(self, "items", items)})
    monkeypatch.setitem(sys.modules, "pystray", fake)
    from gazehop import tray

    app = types.SimpleNamespace(events=queue.Queue(), paused=False, dot_visible=False, update=None)
    t = tray.start(app)
    assert t is not None
    for it in made["menu"].items:
        if it is not None:
            it.action(None, it)
    got = []
    while not app.events.empty():
        got.append(app.events.get())
    assert got == [("window",), ("update_page",), ("pause",), ("calibrate", False), ("calibrate", True),
                   ("dot",), ("settings",), ("quit",)]
    update = made["menu"].items[1]
    app.update = None
    assert update.k["visible"](update) is False                # hidden until a newer version is known
    app.update = ("9.9.9", "https://github.com/jamiepsm/gazehop/releases/tag/v9.9.9")
    assert update.k["visible"](update) and update.text(update) == "Update to 9.9.9…"
    app.update = None
    pause = made["menu"].items[2]
    assert pause.text(pause) == "Pause"
    app.paused = True
    t.refresh()
    assert pause.text(pause) == "Resume" and made["icon"].title == "GazeHop (paused)"
    before = made["redraws"]
    app.dot_visible = True              # turned on from the app window: the tray's checkmark must follow
    t.refresh()
    assert made["redraws"] == before + 1
    t.refresh()
    assert made["redraws"] == before + 1    # and only when something changed
    t.stop()
    assert made["stopped"]

    monkeypatch.setitem(sys.modules, "pystray", None)       # not installed: the app carries on without
    assert tray.start(app) is None


def test_settings_window(faketk, monkeypatch, tmp_path):
    """The settings window offers every setting, applies changes to the live settings and saves
    only what was changed."""
    import json
    import queue
    from gazehop import config, tracker
    monkeypatch.setattr(config, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(tracker, "list_cameras", lambda skip=(): [(2, 640, 480)])
    from gazehop import settings
    from gazehop.ui import disc
    disc.cache_clear()

    offered = {row[0] for _name, rows in settings.PAGES for row in rows}
    assert offered == set(config.DEFAULTS) - {"config_version"}
    for _name, rows in settings.PAGES:      # every slider can show its default
        for key, _label, _hint, control in rows:
            if isinstance(control, tuple):
                assert control[0] <= config.DEFAULTS[key] <= control[1], key
    try:
        from pynput import keyboard     # needs a display on Linux; CI has none
    except Exception:
        keyboard = None
    if keyboard:                        # without pynput the app offers no shortcut to validate
        assert settings.valid_hotkey("<ctrl>+<alt>+g") and not settings.valid_hotkey("g")
        assert not settings.valid_hotkey("<nonsense>+g")

    cfg = config.load()
    cfg["camera"] = 4               # as if started with --camera 4: must not be saved by accident
    cfg["dot_color"] = "red"        # hand-edited into config.json: the window must still open
    app = types.SimpleNamespace(cfg=cfg, root=FakeTk(), events=queue.Queue(), settings=None,
                                tracker=types.SimpleNamespace(camera=4, error=None),
                                activity=types.SimpleNamespace(hooks=True))
    app.set_options = lambda changes: (cfg.update(changes), config.save(changes))
    win = settings.SettingsWindow(app)
    saved = lambda: json.loads((tmp_path / "config.json").read_text())

    assert set(win._show) == offered - {"camera"}    # every row but the camera can be reset
    win._set("smoothing", 9)
    assert cfg["smoothing"] == 9 and saved()["smoothing"] == 9 and saved()["camera"] == 0

    for _ in range(100):            # the camera search runs on a thread and reports through the queue
        if not app.events.empty():
            break
        time.sleep(0.02)
    assert app.events.get() == ("cameras", [2, 4])
    win.set_cameras([2, 4])
    win._pick_camera(2)
    assert saved()["camera"] == 2

    win.reset()
    assert cfg["smoothing"] == config.DEFAULTS["smoothing"] and saved()["smoothing"] == config.DEFAULTS["smoothing"]
    assert saved()["camera"] == 2   # reset leaves the camera alone
    win.close()
    assert app.settings is None


def test_dot_shape():
    """The gaze dot is round at rest and a longer, thinner drop of the same area when it moves."""
    import math
    from gazehop.dot import RADIUS, blob

    def area(pts):
        return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2

    rest = blob((1.0, 0.0), 0.0)
    assert all(abs(math.hypot(*p) - RADIUS) < 1e-6 for p in rest)
    moving = blob((1.0, 0.0), 0.5)
    xs, ys = [p[0] for p in moving], [p[1] for p in moving]
    assert max(xs) - min(xs) > 2 * RADIUS > max(ys) - min(ys)
    assert 0.75 * area(rest) < area(moving) <= area(rest) * 1.01       # the tail takes a little off
    up = blob((0.0, -1.0), 0.5)
    assert max(p[1] for p in up) - min(p[1] for p in up) > 2 * RADIUS   # stretched along the direction


    import numpy as np
    from gazehop.dot import DEFAULT_COLOUR, SIZE, colour_or_default, droplet
    assert colour_or_default("#FF00aa") == "#FF00aa"
    assert colour_or_default("red") == colour_or_default(None) == DEFAULT_COLOUR     # hand-edited config
    px = np.frombuffer(droplet((1.0, 0.0), 0.0, "#ff0000"), np.uint8).reshape(SIZE, SIZE, 4)
    assert px[SIZE // 2, SIZE // 2, 3] == 0                         # a ring: the middle is see-through
    ring = px[px[..., 3] > 200]
    assert len(ring) and (ring[:, 2] > 3 * ring[:, 1]).all()         # BGRA: red, in the chosen colour


def test_calibration_preview_and_escape(faketk):
    """The camera picture shows on the main screen without covering a dot; Esc works from any
    gazehop window, and stops working once calibration is over."""
    import numpy as np
    from gazehop.calibration import Calibration
    from gazehop.calibration_plan import plan, preview_spot
    root, result = FakeTk(), []
    second = Rect(1600, 0, 1920, 1080)
    picture = np.zeros((270, 480, 3), np.uint8)
    cal = Calibration(root, [SCREEN, second], result.append, main=1, preview=lambda: picture)
    cal.start()
    cal.add(face((400, 400), (400, 400), random.Random(1)))
    images = [c for c in cal.canvases[1].calls if c[0] == "create_image"]
    assert images and not [c for c in cal.canvases[0].calls if c[0] == "create_image"]
    x, y = images[0][1]
    for (px, py), _kind in plan([SCREEN, second])[1]:       # no dot under the picture
        assert not (x - 14 < px - second.x < x + 240 + 14 and y - 14 < py - second.y < y + 135 + 14)
    assert any(c[0] == "bind_all" and c[1][0] == "<Escape>" for c in root.calls)
    escape = next(c[1][1] for c in root.calls if c[0] == "bind_all")
    escape(None)
    assert result == [None] and any(c[0] == "unbind_all" for c in root.calls)


def test_preview_spot():
    from gazehop.calibration_plan import plan, preview_spot
    r = Rect(0, 0, 1920, 1080)
    above = Rect(0, -1440, 2560, 1440)      # a screen above: dots along the shared top edge
    for screens in ([r], [r, above]):
        points = [p for p, _kind in plan(screens)[0]]
        x, y = preview_spot(r, points, 240, 135)
        assert y < 300, "near the top when there is room"
        assert all(not (x - 44 < px < x + 240 + 44 and y - 44 < py < y + 135 + 44) for px, py in points)
    assert preview_spot(Rect(0, 0, 300, 200), [(150, 100)], 240, 135) is None     # no room: no picture


def test_dot_toggle_has_one_truth(faketk, monkeypatch, tmp_path):
    """However the dot is switched (button, tray, settings), what is on screen, the setting, the
    saved file and an open settings window agree."""
    import json
    from gazehop import config
    monkeypatch.setattr(config, "data_dir", lambda: str(tmp_path))
    import gazehop.app as appmod

    class Mini:         # just the dot part of the app, with the real methods
        dot_visible, toggle_dot, _sync_dot = appmod.App.dot_visible, appmod.App.toggle_dot, appmod.App._sync_dot
        set_options, _make_dot = appmod.App.set_options, appmod.App._make_dot

    app, shown = Mini(), []
    app.cfg, app.root, app._dot, app.calibration = {"show_dot": False, "dot_color": "#d0d0d6"}, FakeTk(), None, None
    app.settings = types.SimpleNamespace(reflect=lambda k, v: shown.append((k, v)))
    saved = lambda: json.loads((tmp_path / "config.json").read_text())["show_dot"]
    for expected in (True, False, True):
        app.toggle_dot()
        assert app.dot_visible is expected and app.cfg["show_dot"] is expected and saved() is expected
        assert shown[-1] == ("show_dot", expected)
    app.set_options({"show_dot": False})        # the settings switch takes the same path
    assert not app.dot_visible and saved() is False
