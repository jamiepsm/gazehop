"""Checks that need no camera or display (ported from Eyeshift's --selftest). Also run by pytest."""
from __future__ import annotations

import random

from .focuser import pick_screen
from .geometry import Rect
from .model import PER_SITUATION, GazeModel, OneEuro, Sample

SCREENS = [Rect(0, 0, 1000, 800)]


def _sample(rng, shift=0.0) -> Sample:
    f = [rng.uniform(-1, 1) for _ in range(32)]
    f[5] = rng.uniform(0.15, 0.35)
    x = 500 + 300 * f[0] - 100 * f[6] + 80 * f[20] + shift
    y = 400 - 200 * f[1] + 50 * f[7]
    return Sample(f, x, y, x, y)


def check_model() -> None:
    rng = random.Random(1)
    samples = [_sample(rng) for _ in range(300)]
    assert GazeModel.fit(samples, SCREENS) is not None, "auto-regularised fit failed"
    m = GazeModel.fit(samples, SCREENS, lam=0.001)
    for s in samples[:20]:
        q = m.predict(s.f)
        assert abs(q[0] - s.x) < 1 and abs(q[1] - s.y) < 1, f"bad prediction {q} vs {(s.x, s.y)}"

    # distance compensation: x depends on yaw scaled by 1/faceWidth
    far = []
    for _ in range(200):
        f = [rng.uniform(-1, 1) for _ in range(8)]
        f[5] = rng.uniform(0.1, 0.4)
        x = 1000 + 60 * f[0] / f[5]
        far.append(Sample(f, x, 0, x, 0))
    d = GazeModel.fit(far, SCREENS, lam=0.001)
    for s in far[:20]:
        assert abs(d.predict_head(s.f)[0] - s.x) < 1, "distance compensation broken"

    # situation memory: many samples from one situation keep only the newest few
    mem = GazeModel.fit(samples, SCREENS, lam=0.001)
    one = _sample(rng)
    for _ in range(50):
        mem.remember(Sample(list(one.f), one.x, one.y, one.x, one.y))
    assert len(mem.learned) == PER_SITUATION, "situation memory not capped"

    # every click nudges immediately: one click moves ~25% toward it, a second click further
    nudge = GazeModel.fit(samples, SCREENS, lam=0.001)
    click = _sample(rng)
    click.x += 200
    base = nudge.predict(click.f)[0]
    nudge.remember(Sample(list(click.f), click.x, click.y))
    first = nudge.predict(click.f)[0] - base
    nudge.remember(Sample(list(click.f), click.x, click.y))
    second = nudge.predict(click.f)[0] - base
    assert 30 < first < 70, f"first click should nudge ~25% (50px of 200): {first}"
    assert first < second < 200, f"second click should nudge further: {second}"

    # local correction: sitting far right, the gaze lands 150px further right than the linear model
    # thinks. After learning that, that posture improves a lot while other postures stay put.
    def right_sample():
        s = _sample(rng)
        s.f[3] = rng.uniform(0.75, 0.95)
        s.x += 150
        s.hx = s.x
        return s

    smart = GazeModel.fit(samples, SCREENS, lam=0.001)
    for _ in range(150):
        smart.remember(right_sample())
    learnt = smart.refit()
    assert learnt is not None, "refit failed"
    probes = [right_sample() for _ in range(30)]
    before = sum(abs(m.predict(s.f)[0] - s.x) for s in probes)
    after = sum(abs(learnt.predict(s.f)[0] - s.x) for s in probes)
    assert after < before * 0.75, f"local correction didn't help: {after} vs {before}"
    left = _sample(rng)
    left.f[3] = -0.9
    assert abs(learnt.predict(left.f)[0] - m.predict(left.f)[0]) < 60, "correction leaked into other postures"


def check_screens() -> None:
    s = [Rect(0, 0, 1000, 600), Rect(1000, 0, 1000, 600)]
    pick = lambda x, cur: pick_screen((x, 300), s, cur, 0.5)
    assert pick(900, 0) == 0, "switched before the boundary"
    assert pick(1100, 0) == 1, "didn't switch past the boundary"
    assert pick(980, 1) == 1 and pick(900, 1) == 0, "hysteresis wrong"
    assert pick(3000, 0) is None, "looking away should be ignored"


def check_filter() -> None:
    f, out = OneEuro(), (0, 0)
    for i in range(60):     # jitter around (100,100) should settle near it
        out = f.filter((100 + (20 if i % 2 == 0 else -20), 100), i / 15)
    assert abs(out[0] - 100) < 12, f"one euro not smoothing: {out}"
    for i in range(60, 68):     # a big jump should be followed within half a second
        out = f.filter((1500, 100), i / 15)
    assert out[0] > 1200, f"one euro lags too much: {out}"


def run() -> None:
    check_model()
    check_screens()
    check_filter()
