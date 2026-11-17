from __future__ import annotations

import json
import os
import sys
import zlib

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
    "dot_color": "#d0d0d6",   # the gaze dot's colour, #rrggbb
    "hotkey": "<ctrl>+<alt>+g",   # pause / resume
    "check_updates": True,    # ask GitHub once per start whether a newer version is out
    "start_at_login": False,  # open gazehop when you log in (see set_start_at_login)
    "camera_mode": "keep",    # keep | calls (free the camera while the microphone is in use) | share (Linux)
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


def login_command() -> list:
    """What the menu entry runs, as a command that works from any folder."""
    if getattr(sys, "frozen", False):           # the packaged app
        return [sys.executable, "--window"]
    exe = sys.executable
    if sys.platform == "win32":                 # pythonw: no console window
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        exe = w if os.path.exists(w) else exe
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [exe, os.path.join(here, "launcher.py"), "--window"]


def set_start_at_login(on: bool) -> None:
    """Adds or removes gazehop from what the desktop starts at login: an autostart entry
    (~/.config/autostart) on Linux, a Run value in the registry on Windows."""
    cmd = login_command()
    if sys.platform == "win32":
        import subprocess
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, "GazeHop", 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
            else:
                try:
                    winreg.DeleteValue(key, "GazeHop")
                except FileNotFoundError:
                    pass
        return
    path = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "autostart",
                        "gazehop.desktop")
    if not on:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # ponytail: paths are quoted, not escaped: a path containing " $ ` or \ would break the entry
    exec_line = " ".join(f'"{a}"' for a in cmd)
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"[Desktop Entry]\nType=Application\nName=GazeHop\nComment=Keyboard focus follows your eyes\n"
                 f"Exec={exec_line}\nIcon={icon}\nStartupWMClass=gazehop\nTerminal=false\n")


def config_path() -> str:
    return os.path.join(data_dir(), "config.json")


def model_path(profile: str | None = None, layout: str = "") -> str:
    """Each profile (e.g. "glasses") and each screen layout (`model.screen_signature`) keeps its own
    calibration. Without a layout: the file used before layouts had their own (model.json)."""
    safe = "".join(c for c in (profile or "") if c.isalnum() or c in "-_")
    tag = f"{zlib.crc32(layout.encode()):08x}" if layout else ""
    return os.path.join(data_dir(), "-".join(x for x in ("model", safe, tag) if x) + ".json")


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


def save(changes: dict) -> None:
    """Writes these settings into config.json and leaves the rest of the file as it is, so a
    --camera or --dot given on the command line for one run is not made permanent."""
    path = config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            user = json.load(fh)
    except (OSError, ValueError):
        user = dict(DEFAULTS)
    user.update(changes)
    os.makedirs(data_dir(), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(user, fh, indent=2)
    os.replace(path + ".tmp", path)
