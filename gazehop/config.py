from __future__ import annotations

import json
import os
import sys

DEFAULTS = {
    "camera": 0,              # webcam index (see `gazehop cameras`)
    "switch_delay_ms": 250,   # look at another screen this long before focus moves there
    "window_delay_ms": 300,   # look at another window this long before it gets focus
    "head_turn": 50,          # % of the way toward another screen your head must turn
    "window_focus": True,     # also switch between windows on the same screen
    "edge_margin": 120,       # px past the focused window's edge before a neighbour can take focus
    "wait_typing": True,      # never move focus while you type
    "typing_pause": 2.0,      # seconds after the last key before windows may switch
    "mouse_hold": 1.5,        # seconds after mouse use before anything may switch
    "learn": True,            # keep learning from your clicks
    "smoothing": 6,           # how many recent estimates to average (lower = faster, jumpier)
    "screen_from_gaze": False,    # True: eyes alone pick the screen; False: your head direction does
    "move_pointer": False,    # also move the mouse pointer to the newly focused window
    "show_dot": False,        # draw where gazehop thinks you are looking
    "hotkey": "<ctrl>+<alt>+g",   # pause / resume
    "config_version": 2,
}
# defaults of config version 1; a value still equal to one of these was never changed by the user
OLD_DEFAULTS = {"switch_delay_ms": 300, "window_delay_ms": 400, "smoothing": 10}


def data_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "gazehop")


def config_path() -> str:
    return os.path.join(data_dir(), "config.json")


def model_path(profile: str | None = None) -> str:
    """Each profile (e.g. "glasses") keeps its own calibration; the default one is model.json."""
    safe = "".join(c for c in (profile or "") if c.isalnum() or c in "-_")
    return os.path.join(data_dir(), f"model-{safe}.json" if safe else "model.json")


def load() -> dict:
    """Defaults overlaid with the user's config.json; the file is created on first run."""
    cfg = dict(DEFAULTS)
    path = config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            user = json.load(fh)
        cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
        if user.get("config_version", 1) < 2:
            for k, old in OLD_DEFAULTS.items():
                if cfg[k] == old:
                    cfg[k] = DEFAULTS[k]
            cfg["config_version"] = 2
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2)
    except FileNotFoundError:
        os.makedirs(data_dir(), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(DEFAULTS, fh, indent=2)
    except (OSError, ValueError) as e:
        print(f"gazehop: ignoring unreadable {path}: {e}", file=sys.stderr)
    return cfg
