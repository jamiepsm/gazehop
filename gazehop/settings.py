"""The settings window: every option in config.json as a switch, a slider or a choice.

Changes apply right away (the app and the focuser read the same settings) and are saved as they
are made. Opened from the app window or the tray menu.
"""
from __future__ import annotations

import threading
import tkinter as tk

from . import config
from .ui import ACCENT, BG, CARD, FG, LINE, MUTED, OFF, WARN, button, disc, font, set_icon

RESTART = "Takes effect the next time gazehop starts."
# tab -> rows of (key, label, explanation, control). A control is "switch", "camera", "hotkey",
# or (lowest, highest, step, unit) for a slider.
PAGES = [
    ("Switching", [
        ("switch_delay_ms", "Screen delay", "How long you look at another screen before focus moves there.",
         (0, 1500, 50, "ms")),
        ("window_delay_ms", "Window delay", "How long you look at another window before it gets focus.",
         (0, 1500, 50, "ms")),
        ("head_turn", "Head turn", "How far toward another screen your head must turn.", (10, 90, 5, "%")),
        ("window_focus", "Switch between windows", "Off: focus only moves between screens.", "switch"),
        ("edge_margin", "Edge margin",
         "How far past the edge of the focused window you must look before a neighbour takes over.",
         (0, 400, 10, "px")),
        ("screen_from_gaze", "Eyes pick the screen",
         "Off: your head direction picks the screen, which is steadier.", "switch"),
    ]),
    ("Holding focus", [
        ("wait_typing", "Wait while typing", "Never move focus while you are typing.", "switch"),
        ("typing_pause", "Typing pause", "How long after the last key press windows may switch again.",
         (0, 5, 0.25, "s")),
        ("mouse_hold", "Mouse pause", "How long after you use the mouse anything may switch again.",
         (0, 5, 0.25, "s")),
    ]),
    ("Tracking", [
        ("camera", "Camera", RESTART, "camera"),
        ("smoothing", "Smoothing", "How many recent estimates are averaged. Lower is faster but jumpier.",
         (1, 20, 1, "")),
        ("learn", "Learn from clicks", "Every click teaches gazehop where you were looking.", "switch"),
    ]),
    ("Extras", [
        ("show_dot", "Gaze dot", "Show where gazehop thinks you are looking.", "switch"),
        ("move_pointer", "Move the pointer", "Also put the mouse pointer on the window that gets focus.",
         "switch"),
        ("hotkey", "Pause shortcut", "For example <ctrl>+<alt>+g. " + RESTART, "hotkey"),
    ]),
]


def valid_hotkey(text: str) -> bool:
    """A shortcut pynput understands, with at least one modifier: a bare letter as a global pause
    key would fire while typing."""
    try:
        from pynput import keyboard
        return len(keyboard.HotKey.parse(text)) > 1
    except Exception:
        return False


class Switch(tk.Canvas):
    """An on/off switch. Click, Space or Enter flips it; the knob slides across."""

    def __init__(self, parent, value, on_change):
        super().__init__(parent, width=44, height=26, bg=CARD, highlightthickness=0, bd=0, cursor="hand2",
                         takefocus=1)
        self.value, self.on_change = bool(value), on_change
        self._ring = self.create_rectangle(0, 0, 43, 25, outline=CARD)      # shows keyboard focus
        self._ends = [self.create_image(x, 3, anchor="nw") for x in (3, 21)]
        self._bar = self.create_rectangle(13, 3, 31, 23, width=0)
        self._knob = self.create_image(0, 5, anchor="nw")
        for seq in ("<Button-1>", "<space>", "<Return>"):
            self.bind(seq, self.toggle)
        self.bind("<FocusIn>", lambda _e: self.itemconfigure(self._ring, outline=ACCENT))
        self.bind("<FocusOut>", lambda _e: self.itemconfigure(self._ring, outline=CARD))
        self.set(value)

    def set(self, value) -> None:
        """Shows `value` without reporting a change."""
        self.value = bool(value)
        self._x = self._goal()
        self._paint()

    def toggle(self, _e=None) -> None:
        self.value = not self.value
        self.on_change(self.value)
        self._slide()

    def _goal(self) -> int:
        return 23 if self.value else 5

    def _paint(self) -> None:
        colour = ACCENT if self.value else OFF
        for end in self._ends:
            self.itemconfigure(end, image=disc(20, colour, CARD))
        self.itemconfigure(self._bar, fill=colour)
        self.itemconfigure(self._knob, image=disc(16, "#ffffff", colour))
        self.coords(self._knob, self._x, 5)

    def _slide(self) -> None:
        goal = self._goal()
        self._x += max(-4, min(4, goal - self._x))
        self._paint()
        if self._x != goal:
            self.after(15, self._slide)


class Slider(tk.Canvas):
    """A slider. `on_move(value)` while dragging, `on_commit(value)` when let go; arrow keys nudge."""
    W, PAD = 190, 11

    def __init__(self, parent, value, lo, hi, step, on_move, on_commit):
        super().__init__(parent, width=self.W, height=26, bg=CARD, highlightthickness=0, bd=0, cursor="hand2",
                         takefocus=1)
        self.lo, self.hi, self.step, self.on_move, self.on_commit = lo, hi, step, on_move, on_commit
        self._ring = self.create_rectangle(0, 0, self.W - 1, 25, outline=CARD)
        self.create_rectangle(self.PAD, 11, self.W - self.PAD, 15, fill=OFF, width=0)
        self._fill = self.create_rectangle(self.PAD, 11, self.PAD, 15, fill=ACCENT, width=0)
        self._knob = self.create_image(0, 5, anchor="nw", image=disc(16, "#ffffff", CARD))
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda _e: self.on_commit(self.value))
        self.bind("<Left>", lambda _e: self._nudge(-1))
        self.bind("<Right>", lambda _e: self._nudge(1))
        self.bind("<FocusIn>", lambda _e: self.itemconfigure(self._ring, outline=ACCENT))
        self.bind("<FocusOut>", lambda _e: self.itemconfigure(self._ring, outline=CARD))
        self.set(value)

    def set(self, value) -> None:
        """Shows `value` without reporting a change."""
        self.value = min(self.hi, max(self.lo, value))
        x = self.PAD + (self.W - 2 * self.PAD) * (self.value - self.lo) / (self.hi - self.lo)
        self.coords(self._fill, self.PAD, 11, x, 15)
        self.coords(self._knob, x - 8, 5)

    def _to(self, value) -> None:
        self.set(round(value, 2) if isinstance(self.step, float) else int(value))
        self.on_move(self.value)

    def _drag(self, e) -> None:
        part = min(1.0, max(0.0, (e.x - self.PAD) / (self.W - 2 * self.PAD)))
        self._to(self.lo + round(part * (self.hi - self.lo) / self.step) * self.step)

    def _nudge(self, direction: int) -> None:
        self._to(self.value + direction * self.step)
        self.on_commit(self.value)


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self._show = {}             # key -> function that puts a value into that row's control
        self._cameras, self._camera_buttons = [app.tracker.camera], []
        w = self.win = tk.Toplevel(app.root)
        w.title("gazehop settings")
        w.configure(bg=BG, padx=18, pady=16)
        w.resizable(False, False)
        set_icon(w)
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Escape>", lambda _e: self.close())
        tk.Label(w, text="Settings", bg=BG, fg=FG, font=font(16, "bold"), anchor="w").pack(fill="x")
        tk.Label(w, text="Changes apply right away and are saved for next time.", bg=BG, fg=MUTED,
                 font=font(10), anchor="w").pack(fill="x", pady=(0, 10))
        bar = tk.Frame(w, bg=BG)
        bar.pack(fill="x")
        tk.Frame(w, bg=LINE, height=1).pack(fill="x", pady=(0, 12))
        body = tk.Frame(w, bg=BG)
        body.pack(fill="both", expand=True)
        self._tabs = []
        for k, (name, rows) in enumerate(PAGES):
            page = tk.Frame(body, bg=BG)
            page.grid(row=0, column=0, sticky="nsew")       # all pages share one spot; the chosen one is on top
            card = tk.Frame(page, bg=CARD, padx=16, pady=4)
            card.pack(fill="x")
            for j, row in enumerate(rows):
                if j:
                    tk.Frame(card, bg=LINE, height=1).pack(fill="x")
                self._row(card, *row)
            cell = tk.Frame(bar, bg=BG)
            cell.pack(side="left")
            tab = tk.Label(cell, text=name, bg=BG, font=font(11), padx=12, pady=6, cursor="hand2", takefocus=1,
                           highlightthickness=1, highlightbackground=BG, highlightcolor=ACCENT)
            tab.pack()
            line = tk.Frame(cell, height=2, bg=BG)
            line.pack(fill="x")
            for seq in ("<Button-1>", "<Return>", "<space>"):
                tab.bind(seq, lambda _e, k=k: self.select(k))
            self._tabs.append((tab, line, page))
        foot = tk.Frame(w, bg=BG)
        foot.pack(fill="x", pady=(14, 0))
        button(foot, "Reset to defaults", self.reset).pack(side="left")
        button(foot, "Done", self.close, primary=True).pack(side="right")
        self.select(0)
        self._find_cameras()

    def _row(self, page, key, label, hint, control) -> None:
        row = tk.Frame(page, bg=CARD)
        row.pack(fill="x", pady=10)
        text = tk.Frame(row, bg=CARD)
        text.pack(side="left", fill="x", expand=True, padx=(0, 16))
        tk.Label(text, text=label, bg=CARD, fg=FG, font=font(11), anchor="w").pack(fill="x")
        note = tk.Label(text, text=hint, bg=CARD, fg=MUTED, font=font(9), anchor="w", justify="left", wraplength=300)
        note.pack(fill="x")
        value = self.app.cfg[key]
        if control == "switch":
            switch = Switch(row, value, lambda v: self._set(key, v))
            switch.pack(side="right")
            self._show[key] = switch.set
        elif control == "camera":
            self._camera_box = tk.Frame(row, bg=CARD)
            self._camera_box.pack(side="right")
            self.set_cameras(self._cameras)
        elif control == "hotkey":
            self._hotkey_row(row, note)
        else:
            lo, hi, step, unit = control
            shown = tk.Label(row, bg=CARD, fg=FG, font=font(10), width=8, anchor="e")
            show = lambda v: shown.configure(text=f"{v:g} {unit}".strip())
            slider = Slider(row, value, lo, hi, step, show, lambda v: self._set(key, v))
            slider.pack(side="right")
            shown.pack(side="right", padx=(0, 6))
            show(value)
            self._show[key] = lambda v: (slider.set(v), show(v))

    def _hotkey_row(self, row, note) -> None:
        box = tk.Frame(row, bg=BG, highlightthickness=1)     # the field's border
        box.pack(side="right")
        edge = lambda colour: box.configure(highlightbackground=colour, highlightcolor=colour)
        edge(LINE)
        e = tk.Entry(box, width=16, bg=BG, fg=FG, insertbackground=FG, relief="flat", bd=0, font=font(10),
                     highlightthickness=0, disabledbackground=BG, disabledforeground=MUTED)
        e.insert(0, self.app.cfg["hotkey"])
        e.pack(padx=8, pady=6)

        def show(value) -> None:
            e.delete(0, "end")
            e.insert(0, value)

        def commit(_e=None) -> None:
            text = (e.get() or "").strip().lower()
            if text == self.app.cfg["hotkey"] or valid_hotkey(text):
                show(text)
                edge(LINE)
                if text != self.app.cfg["hotkey"]:
                    self._set("hotkey", text)
            else:       # not saved: a shortcut that cannot be registered would switch all shortcuts off
                edge(WARN)

        if self.app.activity.hooks:
            e.bind("<FocusIn>", lambda _e: edge(ACCENT))
            e.bind("<Return>", commit)
            e.bind("<FocusOut>", commit)
            self._show["hotkey"] = show
        else:
            e.configure(state="disabled")
            note.configure(text="Not available in this session (Wayland has no global shortcuts).")

    # ---- cameras: looked for in the background, because opening one can take a second

    def _find_cameras(self) -> None:
        in_use = () if self.app.tracker.error else (self.app.tracker.camera,)

        def work():
            from .tracker import list_cameras
            found = [i for i, _w, _h in list_cameras(skip=in_use)]
            self.app.events.put(("cameras", sorted({*in_use, *found})))     # handled on the main loop

        threading.Thread(target=work, name="gazehop-cameras", daemon=True).start()

    def set_cameras(self, found) -> None:
        self._cameras = list(found)
        chosen = self.app.cfg["camera"]
        for b in self._camera_buttons:
            b.destroy()
        self._camera_buttons = [button(self._camera_box, str(i), lambda i=i: self._pick_camera(i),
                                       primary=i == chosen, on=CARD) for i in sorted({*self._cameras, chosen})]
        for b in self._camera_buttons:
            b.pack(side="left", padx=(6, 0))

    def _pick_camera(self, i: int) -> None:
        self._set("camera", i)
        self.set_cameras(self._cameras)

    # ---- actions

    def _set(self, key, value) -> None:
        self.app.set_options({key: value})

    def select(self, k: int) -> None:
        for j, (tab, line, page) in enumerate(self._tabs):
            tab.configure(fg=FG if j == k else MUTED)
            line.configure(bg=ACCENT if j == k else BG)
        self._tabs[k][2].tkraise()

    def reset(self) -> None:
        """Everything back to the defaults, except the camera: which one works is a fact about
        this machine, not a preference."""
        changes = {key: config.DEFAULTS[key] for key in self._show}
        self.app.set_options(changes)
        for key, value in changes.items():
            self._show[key](value)

    def show(self) -> None:
        self.win.deiconify()
        self.win.lift()

    def close(self) -> None:
        self.app.settings = None
        self.win.destroy()
