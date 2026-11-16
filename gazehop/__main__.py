from __future__ import annotations

import argparse
import sys

from . import __version__, config


def _backend():
    from .backends import BackendError, get_backend
    try:
        return get_backend()
    except BackendError as e:
        sys.exit(f"gazehop: {e}")


class _Tee:
    """Writes to the log file and, if there is one, the terminal."""

    def __init__(self, log, stream):
        self.log, self.stream = log, stream

    def write(self, text):
        self.log.write(text)
        self.log.flush()
        if self.stream is not None:
            self.stream.write(text)

    def flush(self):
        if self.stream is not None:
            self.stream.flush()


def _log_to_file() -> None:
    """Started from an icon there is no terminal: keep the messages in gazehop.log instead."""
    import os
    try:
        os.makedirs(config.data_dir(), exist_ok=True)
        log = open(os.path.join(config.data_dir(), "gazehop.log"), "w", encoding="utf-8", errors="replace")
    except OSError:
        return
    sys.stdout, sys.stderr = _Tee(log, sys.stdout), _Tee(log, sys.stderr)


def lock_instance():
    """A lock held for as long as this process lives (the OS drops it even after a crash), or None
    if another gazehop of this user already holds it: two would fight over the camera and focus."""
    import os
    os.makedirs(config.data_dir(), exist_ok=True)
    fh = open(os.path.join(config.data_dir(), "gazehop.lock"), "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


ALREADY = "gazehop is already running. Open it from its tray icon, or quit it there first."


def _detach(argv) -> int:
    """Starts this same command again, cut loose from the terminal, and returns: closing the
    terminal then leaves gazehop running. Its messages go to gazehop.log."""
    import os
    import subprocess
    cmd = [sys.executable, "-m", "gazehop", *argv]
    kw = {}
    if sys.platform == "win32":
        quiet = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")     # no console window
        if os.path.exists(quiet):
            cmd[0] = quiet
        kw["creationflags"] = 0x08 | 0x200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True          # no hang-up signal when the terminal closes
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    print("gazehop: running in the background; quit it from its window or tray icon.\n"
          f"gazehop: messages go to {os.path.join(config.data_dir(), 'gazehop.log')}")
    return 0


def cmd_run(args, calibrate_only=False) -> int:
    lock = lock_instance()      # held until exit
    if lock is None:
        sys.exit(f"gazehop: {ALREADY}")
    cfg = config.load()
    if args.camera is not None:
        cfg["camera"] = args.camera
    if args.dot:
        cfg["show_dot"] = True
    backend = _backend()
    try:
        from .app import App
        app = App(backend, cfg, dry_run=args.dry_run, force_calibrate=calibrate_only or args.recalibrate,
                  add_round=args.add, profile=args.profile,
                  # the tray icon is there whenever gazehop is at work; `calibrate` quits when done
                  window=args.window, tray=not (args.no_tray or calibrate_only))
    except ImportError as e:
        hint = "sudo apt install python3-tk" if "tkinter" in str(e) else "pip install -r requirements.txt"
        sys.exit(f"gazehop: missing dependency ({e}). Try: {hint}")
    except RuntimeError as e:
        sys.exit(f"gazehop: {e}")
    app.exit_after_calibration = calibrate_only
    return app.run()


def cmd_cameras(_args) -> int:
    from .tracker import list_cameras
    found = list_cameras()
    for i, w, h in found:
        print(f"camera {i}: {w}x{h}")
    if not found:
        print("No working camera found.")
    return 0 if found else 1


def cmd_doctor(_args) -> int:
    """Shows what gazehop sees, without touching the camera or changing focus."""
    import os
    print(f"gazehop {__version__} on {sys.platform}, Python {sys.version.split()[0]}")
    print(f"session: {os.environ.get('XDG_SESSION_TYPE', '-')} / {os.environ.get('XDG_CURRENT_DESKTOP', '-')}")
    for mod in ("cv2", "mediapipe", "numpy", "tkinter", "pynput"):
        try:
            __import__(mod)
            print(f"  ok       {mod}")
        except Exception as e:
            print(f"  MISSING  {mod}  ({e})")
    b = _backend()
    print(f"backend: {b.name}")
    if b.name == "x11" and not b.supports("_NET_ACTIVE_WINDOW"):
        print("  WARNING: the window manager does not advertise _NET_ACTIVE_WINDOW; focusing may not work.")
    for k, r in enumerate(b.screens()):
        print(f"screen {k + 1}: {int(r.w)}x{int(r.h)} at ({int(r.x)}, {int(r.y)})")
    active = b.active_window()
    print(f"idle: {b.idle_seconds()} s")
    print("windows, topmost first (* = has keyboard focus):")
    for w in b.windows():
        mark = "*" if active and active.id == w.id else " "
        print(f" {mark} {int(w.rect.w)}x{int(w.rect.h)} at ({int(w.rect.x)}, {int(w.rect.y)})  {w.title[:60]}")
    print(f"config: {config.config_path()}")
    return 0


def cmd_selftest(_args) -> int:
    from . import selftest
    selftest.run()
    print("selftest ok")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="gazehop", description="Keyboard focus follows your eyes.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("command", nargs="?", default="run",
                   choices=["run", "calibrate", "cameras", "doctor", "selftest"],
                   help="run (default) | calibrate | cameras | doctor | selftest")
    p.add_argument("--camera", type=int, help="webcam index (default: from config)")
    p.add_argument("--dot", action="store_true", help="show where gazehop thinks you are looking")
    p.add_argument("--dry-run", action="store_true", help="print focus changes instead of making them")
    p.add_argument("--recalibrate", action="store_true", help="calibrate again before running")
    p.add_argument("--add", action="store_true",
                   help="with calibrate: add a round to the existing calibration instead of replacing it")
    p.add_argument("--window", action="store_true",
                   help="open the app window (camera view, buttons); runs in the background")
    p.add_argument("--no-tray", action="store_true", help="no tray icon")
    p.add_argument("--foreground", action="store_true",
                   help="with --window: stay attached to the terminal (Ctrl+C quits) instead of running in the background")
    p.add_argument("--profile", metavar="NAME",
                   help="use a separate named calibration, e.g. --profile glasses")
    args = p.parse_args(argv)
    # the app (with its window) outlives the terminal it was started from; terminal-only mode
    # does not, because Ctrl+C is the only way to quit it
    if args.window and not args.foreground and args.command in ("run", "calibrate") \
            and sys.stdout is not None and sys.stdout.isatty():
        lock = lock_instance()  # checked here so the message lands in this terminal; the copy takes it
        if lock is None:
            sys.exit(f"gazehop: {ALREADY}")
        lock.close()
        return _detach(sys.argv[1:] if argv is None else argv)
    if sys.stdout is None or sys.stderr is None or args.window:
        _log_to_file()
    if args.command == "cameras":
        return cmd_cameras(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "selftest":
        return cmd_selftest(args)
    if args.add and args.command != "calibrate":
        p.error("--add goes with the calibrate command:  gazehop calibrate --add")
    try:
        return cmd_run(args, calibrate_only=args.command == "calibrate")
    except SystemExit as e:
        if args.window and isinstance(e.code, str):     # started from an icon: say why it could not start
            try:
                from .ui import show_error
                show_error(e.code)
            except ImportError:
                pass
        raise


if __name__ == "__main__":
    sys.exit(main())
