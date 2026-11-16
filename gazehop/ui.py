"""The app window: live camera view with what gazehop reads marked on it, a status line, buttons."""
from __future__ import annotations

import functools
import math
import os
import tkinter as tk

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
BG, FG, MUTED = "#1c2030", "#f5f7fa", "#9aa0b0"
CARD, CARD_HI, LINE, OFF = "#262b3d", "#343a52", "#333a50", "#4a516b"
ACCENT, ACCENT_HI, WARN = "#48d696", "#72e3b0", "#ff6b6b"
_family = None


def font(size: int, weight: str = "normal") -> tuple:
    """The desktop's own interface font (Segoe UI on Windows, the system sans on Linux)."""
    global _family
    if _family is None:
        try:
            _family = str(tk._default_root.tk.call("font", "actual", "TkDefaultFont", "-family"))
        except Exception:
            _family = "Helvetica"
    return (_family, size, weight)


def button(parent, text, command, primary=False, on=BG):
    """A flat button that lights up under the pointer. `on`: the colour of the surface it sits on."""
    bg, hover, fg = (ACCENT, ACCENT_HI, BG) if primary else (CARD_HI if on == CARD else CARD, OFF, FG)
    b = tk.Button(parent, text=text, command=command, font=font(10), relief="flat", bd=0, padx=12, pady=6,
                  cursor="hand2", bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
                  disabledforeground=MUTED, highlightthickness=1, highlightbackground=on, highlightcolor=ACCENT)
    b.bind("<Enter>", lambda _e: str(b["state"]) != "disabled" and b.configure(bg=hover))
    b.bind("<Leave>", lambda _e: b.configure(bg=bg))
    return b


@functools.lru_cache(maxsize=None)
def disc(d: int, colour: str, bg: str):
    """A filled circle with smooth edges, as an image. Tk draws circles jagged, so the edge pixels
    are mixed with the background colour here."""
    rgb = lambda c: [int(c[i:i + 2], 16) for i in (1, 3, 5)]
    fg, back, r, rows = rgb(colour), rgb(bg), d / 2, []
    for y in range(d):
        cover = [max(0.0, min(1.0, r - math.hypot(x + 0.5 - r, y + 0.5 - r))) for x in range(d)]
        rows.append("{" + " ".join("#%02x%02x%02x" % tuple(round(b + (f - b) * a) for f, b in zip(fg, back))
                                   for a in cover) + "}")
    img = tk.PhotoImage(width=d, height=d)
    img.put(" ".join(rows))
    return img


def ppm(rgb) -> bytes:
    """An RGB array as a PPM image, the one picture format Tk reads from memory without Pillow."""
    h, w = rgb.shape[:2]
    return b"P6 %d %d 255\n" % (w, h) + rgb.tobytes()


def set_icon(window) -> None:
    try:
        img = tk.PhotoImage(file=os.path.join(ASSETS, "icon.png"))
        window.iconphoto(True, img)
        window._gazehop_icon = img      # Tk drops images nobody holds on to
    except Exception:
        pass


def show_error(message: str) -> None:
    """A message box, for when there is no terminal to print to."""
    try:
        from tkinter import messagebox
        root = tk._default_root or tk.Tk()
        if tk._default_root is root and not root.winfo_viewable():
            root.withdraw()
        messagebox.showerror("gazehop", message)
    except Exception:
        pass


class ControlWindow:
    def __init__(self, app):
        self.app = app
        w = self.win = tk.Toplevel(app.root)
        w.title("gazehop")
        w.configure(bg=BG)
        w.resizable(False, False)
        set_icon(w)
        w.protocol("WM_DELETE_WINDOW", self.close)
        self._image = tk.PhotoImage(width=480, height=270)     # blank until the first camera picture
        self.picture = tk.Label(w, bg="black", image=self._image, bd=0)
        self.picture.pack(padx=12, pady=(12, 6))
        self.status = tk.Label(w, bg=BG, fg=FG, font=font(12, "bold"), anchor="w")
        self.status.pack(fill="x", padx=14)
        self.detail = tk.Label(w, bg=BG, fg=MUTED, font=font(10), anchor="w", justify="left")
        self.detail.pack(fill="x", padx=14, pady=(0, 6))
        legend = tk.Label(w, bg=BG, fg=MUTED, font=font(9), anchor="w", justify="left",
                          text="Green outline: face and eyes found.  White circles: irises.\n"
                               "White boxes: the eye pixels the model reads.  Orange arrow: where your head points.")
        legend.pack(fill="x", padx=14, pady=(0, 8))
        row = self._row = tk.Frame(w, bg=BG)
        row.pack(fill="x", padx=10, pady=(0, 12))
        self._update_bar = None
        self.buttons = {}
        for key, text, action, side in (("pause", "Pause", app.toggle_pause, "left"),
                                        ("calibrate", "Calibrate", lambda: app.request_calibration(add=False), "left"),
                                        ("add", "Add round", lambda: app.request_calibration(add=True), "left"),
                                        ("dot", "Gaze dot", app.toggle_dot, "left"),
                                        ("quit", "Quit", app.quit, "right"),
                                        ("settings", "Settings", app.open_settings, "right")):
            b = button(row, text, action)
            b.pack(side=side, padx=4)
            self.buttons[key] = b
        self._shown = None
        self.visible = True
        app.tracker.preview = True

    def show(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.visible = True
        self.app.tracker.preview = True

    def close(self) -> None:
        """With a tray icon, closing only hides the window; without one it quits the app."""
        if self.app.tray is not None:
            self.win.withdraw()
            self.visible = False
            self.app.tracker.preview = False    # no point drawing pictures nobody sees
        else:
            self.app.quit()

    def refresh(self) -> None:
        if not self.visible:
            return
        app = self.app
        rgb = app.tracker.latest_preview()
        if rgb is not None and rgb is not self._shown:
            self._shown = rgb
            self._image = tk.PhotoImage(data=ppm(rgb), format="PPM")
            self.picture.configure(image=self._image, width=rgb.shape[1], height=rgb.shape[0])
        title, detail = app.status()
        self.status.configure(text=title)
        self.detail.configure(text=detail)
        self.buttons["pause"].configure(text="Resume" if app.paused else "Pause")
        self.buttons["dot"].configure(text="Hide gaze dot" if app.dot_visible else "Show gaze dot")
        self.buttons["add"].configure(state="normal" if app.model is not None else "disabled")
        if app.update and self._update_bar is None:
            self._show_update(*app.update)

    def _show_update(self, version: str, _page: str) -> None:
        from .update import how_to_update
        bar = self._update_bar = tk.Frame(self.win, bg=CARD, padx=12, pady=8)
        bar.pack(fill="x", padx=12, pady=(0, 10), before=self._row)
        text = tk.Frame(bar, bg=CARD)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=f"gazehop {version} is available", bg=CARD, fg=FG, font=font(10, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(text, text=how_to_update(), bg=CARD, fg=MUTED, font=font(9), anchor="w").pack(fill="x")
        button(bar, "What's new", lambda: self.app.events.put(("update_page",)), primary=True, on=CARD).pack(side="right")
