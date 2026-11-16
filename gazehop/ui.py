"""The app window: live camera view with what gazehop reads marked on it, a status line, buttons."""
from __future__ import annotations

import os
import tkinter as tk

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
BG, FG, MUTED = "#1c2030", "#f5f7fa", "#9aa0b0"


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
        self.status = tk.Label(w, bg=BG, fg=FG, font=("Helvetica", 12, "bold"), anchor="w")
        self.status.pack(fill="x", padx=14)
        self.detail = tk.Label(w, bg=BG, fg=MUTED, font=("Helvetica", 10), anchor="w", justify="left")
        self.detail.pack(fill="x", padx=14, pady=(0, 6))
        legend = tk.Label(w, bg=BG, fg=MUTED, font=("Helvetica", 9), anchor="w", justify="left",
                          text="Green outline: face and eyes found.  White circles: irises.\n"
                               "White boxes: the eye pixels the model reads.  Orange arrow: where your head points.")
        legend.pack(fill="x", padx=14, pady=(0, 8))
        row = tk.Frame(w, bg=BG)
        row.pack(fill="x", padx=10, pady=(0, 12))
        self.buttons = {}
        for key, text, action in (("pause", "Pause", app.toggle_pause),
                                  ("calibrate", "Calibrate", lambda: app.request_calibration(add=False)),
                                  ("add", "Add round", lambda: app.request_calibration(add=True)),
                                  ("dot", "Gaze dot", app.toggle_dot),
                                  ("quit", "Quit", app.quit)):
            b = tk.Button(row, text=text, command=action, relief="flat", padx=10, pady=4)
            b.pack(side="left", padx=4)
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
