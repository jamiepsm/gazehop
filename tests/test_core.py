import math
import random

import numpy as np
import pytest

from gazehop import selftest
from gazehop.backends.gnome import parse_gvariant_string
from gazehop.calibration_plan import edge_points, plan
from gazehop.focuser import Focuser
from gazehop.geometry import Rect, Win
from gazehop.model import GazeModel
from gazehop.tracker import features, pose_angles

CFG = {"switch_delay_ms": 250, "window_delay_ms": 300, "head_turn": 50, "window_focus": True,
       "edge_margin": 120, "wait_typing": True, "typing_pause": 2.0, "mouse_hold": 1.5, "move_pointer": False}


def test_selftest_model():
    selftest.check_model()


def test_selftest_screens():
    selftest.check_screens()


def test_selftest_filter():
    selftest.check_filter()


def test_model_roundtrip(tmp_path):
    rng = random.Random(2)
    samples = [selftest._sample(rng) for _ in range(120)]
    m = GazeModel.fit(samples, selftest.SCREENS, keyboard=samples[:5])
    m.remember(selftest._sample(rng))
    path = str(tmp_path / "sub" / "model.json")
    m.save(path)
    again = GazeModel.load(path, selftest.SCREENS)
    assert again is not None and len(again.learned) == 1
    f = samples[3].f
    assert again.predict(f) == pytest.approx(m.predict(f))
    assert again.predict_head(f) == pytest.approx(m.predict_head(f))
    # a different monitor layout invalidates the calibration
    assert GazeModel.load(path, [Rect(0, 0, 1920, 1080)]) is None
    assert GazeModel.load(str(tmp_path / "nope.json"), selftest.SCREENS) is None


class FakeBackend:
    name = "fake"

    def __init__(self, screens, wins, active):
        self._screens, self.wins, self.active, self.focused = screens, wins, active, []

    def screens(self): return self._screens
    def main_screen(self): return self._screens[-1]
    def windows(self): return self.wins
    def active_window(self): return next((w for w in self.wins if w.id == self.active), None)
    def idle_seconds(self): return None
    def move_pointer(self, x, y): pass

    def focus(self, wid):
        self.focused.append(wid)
        self.active = wid
        return True


class FakeActivity:
    key = mouse = 1e9
    def key_ago(self): return self.key
    def mouse_ago(self): return self.mouse
    def ignore_mouse(self, s): pass


def make(two_screens=False):
    screens = [Rect(0, 0, 1000, 600)] + ([Rect(1000, 0, 1000, 600)] if two_screens else [])
    wins = [Win(1, Rect(0, 0, 480, 600), 10, "left"), Win(2, Rect(520, 0, 480, 600), 11, "right")]
    if two_screens:
        wins.append(Win(3, Rect(1100, 50, 800, 500), 12, "other screen"))
    b, a, t = FakeBackend(screens, wins, 1), FakeActivity(), [0.0]
    return b, a, t, Focuser(b, dict(CFG), a, clock=lambda: t[0])


def look(f, t, gaze, head=None, seconds=1.0, kb=None):
    for _ in range(int(seconds * 15)):
        t[0] += 1 / 15
        f.update(gaze, head or gaze, kb)


def test_window_switch_needs_dwell():
    b, a, t, f = make()
    look(f, t, (800, 300), seconds=0.3)
    assert b.focused == []              # a glance is not enough
    look(f, t, (800, 300), seconds=0.4)
    assert b.focused == [2]
    look(f, t, (800, 300), seconds=2)
    assert b.focused == [2]             # and it doesn't refocus what already has focus


def test_edge_margin_keeps_focus():
    b, a, t, f = make()
    look(f, t, (560, 300), seconds=3)   # just past the focused window's edge, inside the margin
    assert b.focused == []


def test_typing_and_mouse_hold_focus():
    b, a, t, f = make()
    a.key = 0.5
    look(f, t, (800, 300), seconds=3)
    assert b.focused == []
    a.key, a.mouse = 1e9, 0.5
    look(f, t, (800, 300), seconds=3)
    assert b.focused == []
    a.mouse = 1e9
    look(f, t, (800, 300), seconds=1)
    assert b.focused == [2]


def test_keyboard_line_holds_focus():
    b, a, t, f = make()
    look(f, t, (800, 300), head=(800, 900), seconds=3, kb=750)
    assert b.focused == [] and f.at_keyboard


def test_screen_switch_follows_head_not_eyes():
    b, a, t, f = make(two_screens=True)
    look(f, t, (1500, 300), head=(400, 300), seconds=2)     # eyes wander, head still faces screen 1
    assert 3 not in b.focused
    b.focused.clear(); b.active = 1
    look(f, t, (1500, 300), head=(1500, 300), seconds=1)
    assert b.focused == [3]
    # typing blocks window switches for 2 s but lets screens switch after 0.6 s
    b.focused.clear(); b.active = 1; a.key = 1.0
    look(f, t, (1500, 300), head=(1500, 300), seconds=1)
    assert b.focused == [3]


def test_returns_to_last_window_on_a_screen():
    b, a, t, f = make(two_screens=True)
    b.active = 2
    look(f, t, (800, 300), seconds=0.2)                     # remembers window 2 for screen 1
    b.active = 3
    look(f, t, (100, 300), head=(300, 300), seconds=1)      # looks back at screen 1, over window 1
    assert b.focused[0] == 2


def test_looking_away_does_nothing():
    b, a, t, f = make()
    look(f, t, (5000, 300), seconds=3)
    assert b.focused == [] and f.facing is None


def test_dry_run_never_focuses():
    b, a, t, f = make()
    f.dry_run, seen = True, []
    f.on_action = seen.append
    look(f, t, (800, 300), seconds=1)
    assert b.focused == [] and seen and seen[0].id == 2


def test_calibration_plan():
    a, b = Rect(0, 0, 1000, 600), Rect(1000, 0, 1000, 600)
    assert len(edge_points(a, b)) == 3 and all(p[0] == 950 for p in edge_points(a, b))
    assert edge_points(a, Rect(3000, 0, 100, 100)) == []
    blocks = plan([a, b])
    assert len(blocks) == 2 and len(blocks[0]) == 16 + 3 + 5 + 3
    assert all(a.contains(p) for p, _ in blocks[0]) and all(b.contains(p) for p, _ in blocks[1])


def test_gvariant_parse():
    out = """('[{"title":"it\\'s \\\\"x\\\\"","id":5}]',)\n"""
    import json
    assert json.loads(parse_gvariant_string(out)) == [{"title": 'it\'s "x"', "id": 5}]


def _face(shift=0.0):
    """478 fake landmarks on a 640x480 frame with the eye/iris points where features() expects them."""
    rng = np.random.default_rng(0)
    lm = rng.uniform([220, 140], [420, 380], size=(478, 2))
    lm[33], lm[133], lm[468] = (250, 220), (300, 220), (275 + shift, 220)
    lm[263], lm[362], lm[473] = (390, 220), (340, 220), (365 + shift, 220)
    return lm


def test_tracker_features():
    gray = np.random.default_rng(1).integers(0, 255, (480, 640), dtype=np.uint8)
    f = features(_face(), np.eye(4), gray)
    assert len(f) == 8 + 2 * 72
    assert f[:3] == [0, 0, 0] and f[6] == pytest.approx(0.5) and f[7] == pytest.approx(0.5)
    assert 0 < f[5] < 1
    # both irises moving right in the image must move the feature one way, not cancel out
    assert features(_face(10), np.eye(4), gray)[6] == pytest.approx(0.7)
    # eyes too small to read, or off the frame -> no reading
    assert features(_face() / 10, np.eye(4), gray) is None
    assert features(_face() + 400, np.eye(4), gray) is None


def test_pose_angles():
    a = 0.3
    yaw = np.array([[math.cos(a), 0, math.sin(a), 0], [0, 1, 0, 0], [-math.sin(a), 0, math.cos(a), 0], [0, 0, 0, 1]])
    assert pose_angles(yaw) == pytest.approx((a, 0, 0))
    assert pose_angles(yaw * 7.5)[0] == pytest.approx(a)    # scale-invariant


def test_dry_run_pretends_focus_moved():
    b, a, t, f = make()
    f.dry_run, seen = True, []
    f.on_action = lambda w: seen.append(w.id)
    look(f, t, (800, 300), seconds=3)
    assert seen == [2]                  # reported once, not over and over
    look(f, t, (200, 300), seconds=1)
    assert seen == [2, 1]               # and looking back is reported as a switch back
    b.active = 2                        # the user really changes focus: stop pretending
    look(f, t, (200, 300), seconds=1)
    assert seen == [2, 1, 1] and b.focused == []


def test_quick_glances_between_windows():
    b, a, t, f = make()
    for k in range(6):                  # 0.7 s on each window, back and forth
        look(f, t, (800, 300) if k % 2 == 0 else (200, 300), seconds=0.7)
    assert b.focused == [2, 1, 2, 1, 2, 1]


def test_screen_from_gaze_option():
    b, a, t, f = make(two_screens=True)
    f.cfg["screen_from_gaze"] = True
    look(f, t, (1500, 300), head=(400, 300), seconds=1)     # eyes on screen 2, head still on screen 1
    assert b.focused == [3]


def test_config_migration(tmp_path, monkeypatch):
    import json
    from gazehop import config
    monkeypatch.setattr(config, "data_dir", lambda: str(tmp_path))
    old = {**config.DEFAULTS, **config.OLD_DEFAULTS, "camera": 2, "window_delay_ms": 900}
    del old["config_version"], old["screen_from_gaze"]
    (tmp_path / "config.json").write_text(json.dumps(old))
    cfg = config.load()
    assert cfg["smoothing"] == 6 and cfg["switch_delay_ms"] == 250    # untouched old defaults move forward
    assert cfg["window_delay_ms"] == 900 and cfg["camera"] == 2       # the user's own values stay
    assert json.loads((tmp_path / "config.json").read_text())["config_version"] == 2
    assert config.load() == cfg


def test_overlay_and_ppm():
    """The camera view picture: right size, mirrored, and marked up only when a face is there."""
    import importlib.util
    from gazehop.tracker import PREVIEW_W, draw_overlay
    frame = np.full((480, 640, 3), 40, np.uint8)
    none = draw_overlay(frame, None, False)
    assert none.shape == (360, PREVIEW_W, 3) and none.dtype == np.uint8
    lm = _face() - (150, 0)             # left of centre in the camera frame
    marked = draw_overlay(frame, lm, True)
    assert marked.shape == none.shape
    changed = np.argwhere((marked != 40).any(axis=2))
    # the fake face sits left of centre in the frame, so mirrored it must be drawn right of centre
    assert lm[:468, 0].mean() < 320 and changed[changed[:, 0] > 60][:, 1].mean() > PREVIEW_W / 2
    if importlib.util.find_spec("tkinter"):
        from gazehop.ui import ppm
        data = ppm(marked)
        assert data.startswith(b"P6 480 360 255\n") and len(data) == 15 + 480 * 360 * 3


def test_update_check(monkeypatch):
    """Only a real, newer release of this project counts; offline or odd replies are ignored."""
    import io
    import json
    from gazehop import update
    page = update.RELEASES + "/tag/v0.10.0"
    assert update.parse("v0.10.0") == (0, 10, 0) and update.parse("v1.0-beta") is None
    assert update.newer({"tag_name": "v0.10.0", "html_url": page}, "0.9.9") == ("0.10.0", page)  # not string order
    assert update.newer({"tag_name": "v0.10.0", "html_url": page}, "0.10.0") is None
    assert update.newer({"tag_name": "v0.3.0", "html_url": page}, "0.10.0") is None
    assert update.newer({"tag_name": "v9.0.0", "html_url": "https://evil.example/x"}, "0.1.0") is None
    assert update.newer({}, "0.1.0") is None

    reply = json.dumps({"tag_name": "v99.0.0", "html_url": update.RELEASES + "/tag/v99.0.0"}).encode()
    monkeypatch.setattr(update.urllib.request, "urlopen", lambda req, timeout: io.BytesIO(reply))
    assert update.check() == ("99.0.0", update.RELEASES + "/tag/v99.0.0")

    def offline(req, timeout):
        raise OSError("no network")
    monkeypatch.setattr(update.urllib.request, "urlopen", offline)
    assert update.check() is None


def test_one_instance(tmp_path, monkeypatch):
    """A second gazehop of the same user is refused while the first runs, and allowed after."""
    from gazehop import __main__ as cli, config
    monkeypatch.setattr(config, "data_dir", lambda: str(tmp_path))
    first = cli.lock_instance()
    assert first is not None and cli.lock_instance() is None
    first.close()                       # the first one quits (or crashes: the OS drops the lock too)
    again = cli.lock_instance()
    assert again is not None
    again.close()


def test_gnome_skips_minimised_windows():
    """A minimised window is never a focus target: focusing it would bring it back on screen."""
    import json
    from gazehop.backends.gnome import GnomeBackend
    b = GnomeBackend.__new__(GnomeBackend)      # no D-Bus: answers come from the dict below
    b._pid, b._cache, b._rects = 1, (0.0, [], None), {}
    listed = [{"id": 1, "title": "editor", "pid": 5, "focus": True},
              {"id": 2, "title": "minimised video", "pid": 6}]
    details = {1: {"x": 0, "y": 0, "width": 800, "height": 600, "minimized": False},
               2: {"x": 0, "y": 0, "width": 1920, "height": 1080, "minimized": True}}
    reply = lambda obj: "('" + json.dumps(obj) + "',)"
    b._call = lambda method, *a: reply(listed) if method == "List" else reply(details[a[0]])
    assert [w.title for w in b.windows()] == ["editor"] and b.active_window().title == "editor"
