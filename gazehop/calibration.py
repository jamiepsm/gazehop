"""Calibration overlay (Tk). Per screen, three short phases, as in Eyeshift:
red = eyes only over a 4x4 grid (+ shared screen edges); blue = point your nose at the dot (teaches
the head model); green = keep looking at the dot while moving your head (teaches the gaze model to
cancel head movement). Then one look at the keyboard. Esc cancels.

Live rings show where a calibration thinks you are looking: grey for the previous calibration (while
the dots run), and at the end a review step with the new one in green next to the previous one,
where you keep or discard the result.
"""
from __future__ import annotations

import sys
import time
import tkinter as tk

from .calibration_plan import EYES, MOVE, NOSE, plan, preview_spot
from .model import GazeModel, OneEuro, Sample
from .ui import toplevel

PREVIOUS, NEW = "#9a9aa0", "#30d158"
FAINT = "#4a4a52"       # the previous calibration's ring while the dots run: there, not distracting

PHASE = {   # kind: (settle s, collect s, colour, hint)
    EYES: (0.6, 1.0, "#ff453a", "Head still — follow the red dot with your eyes"),
    NOSE: (1.0, 1.0, "#0a84ff", "Point your nose at the blue dot"),
    MOVE: (0.5, 4.0, "#30d158", "Eyes on the green dot — slowly move your head around"),
}



class Calibration:
    def __init__(self, root: tk.Misc, screens, on_done, base=None, previous=None, main=0, preview=None):
        """`on_done(model | None)`: None if cancelled, discarded, or too little was seen.
        `base`: an existing model whose calibration this round is added to instead of replacing it.
        `previous`: the calibration in use until now, shown as a grey live ring for comparison.
        `main`, `preview`: the screen (index) that shows the camera picture, and a function that
        returns the latest picture (RGB array) or None."""
        self.root, self.screens, self.on_done, self.base = root, list(screens), on_done, base
        self.main, self.preview = min(main, len(self.screens) - 1), preview
        self._picture = self._shown = None
        self.previous = previous or base
        self.review = None              # the freshly fitted model, while the user checks it
        self._filters = {PREVIOUS: OneEuro(), NEW: OneEuro()}
        self.blocks = plan(self.screens)
        self.samples: list = []
        self.keyboard: list = []
        self.b = self.i = 0
        self.target = None              # (point, kind, screen centre) while sampling
        self.collecting_keyboard = False
        self.waiting = True             # for Space
        self.keyboard_step = False
        self.done = False
        self.wins, self.canvases = [], []
        for r in self.screens:
            w = toplevel(root)
            w.configure(bg="black")
            if sys.platform == "win32":
                w.overrideredirect(True)
            self._place(w, r)
            c = tk.Canvas(w, bg="black", highlightthickness=0)
            c.pack(fill="both", expand=True)
            for seq, fn in (("<space>", self._space), ("<Return>", self._space), ("<Button-1>", self._space),
                            ("<Escape>", self._cancel)):
                w.bind(seq, fn)
            c.tag_bind("cancel", "<Button-1>", self._cancel)
            c.tag_bind("cancel", "<Enter>", lambda _e, c=c: c.itemconfigure("cancel-text", fill="white"))
            c.tag_bind("cancel", "<Leave>", lambda _e, c=c: c.itemconfigure("cancel-text", fill="#9a9aa0"))
            self.wins.append(w)
            self.canvases.append(c)
        # Esc cancels from any gazehop window: the desktop may leave keyboard focus on the app window
        root.bind_all("<Escape>", self._cancel)
        self.wins[0].focus_force()
        root.after(700, lambda: self._check_placement(3))

    @staticmethod
    def _place(w, r) -> None:
        w.geometry(f"{int(r.w)}x{int(r.h)}+{int(r.x)}+{int(r.y)}")
        if sys.platform != "win32":
            # X11 window managers refuse keyboard focus to override-redirect windows;
            # asking for fullscreen on the monitor the window sits on works everywhere
            w.update_idletasks()
            w.attributes("-fullscreen", True)
        w.attributes("-topmost", True)

    def _check_placement(self, tries: int) -> None:
        """Every overlay must cover its own screen. With the app window open, GNOME sometimes
        opens one on another monitor (seen with a portrait monitor; cause unknown): check, put back
        what is wrong, and say so in the log."""
        if self.done:
            return
        wrong = False
        for k, (w, r) in enumerate(zip(self.wins, self.screens)):
            try:
                got = (w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height())
                if max(abs(a - b) for a, b in zip(got, (r.x, r.y, r.w, r.h))) <= 2 and w.winfo_viewable():
                    continue
            except Exception:
                continue        # a window being torn down
            wrong = True
            print(f"gazehop: calibration screen {k + 1} is at {got[2]}x{got[3]}+{got[0]}+{got[1]}, not "
                  f"{int(r.w)}x{int(r.h)}+{int(r.x)}+{int(r.y)}: placing it again", file=sys.stderr)
            w.attributes("-fullscreen", False)
            self._place(w, r)
            w.lift()
        if wrong and tries > 1:
            self.root.after(700, lambda: self._check_placement(tries - 1))

    def start(self) -> None:
        self._prompt(None)

    def add(self, f) -> None:
        """Feed every face reading here while calibration runs."""
        self._live(f)
        self._show_preview()
        if self.target:
            p, kind, centre = self.target
            # the head model learns where the head points: the screen centre while only the eyes move,
            # the dot itself when the nose points at it, and nothing while the head is wandering
            head = centre if kind == EYES else p if kind == NOSE else None
            self.samples.append(Sample(list(f), p[0], p[1], head[0] if head else None, head[1] if head else None))
        if self.collecting_keyboard:
            self.keyboard.append(Sample(list(f), 0, 0))

    def _live(self, f) -> None:
        """Moves the live rings: where the previous / the new calibration puts your gaze right now."""
        if self.done:
            return
        now, rings = time.monotonic(), []
        for colour, model in ((PREVIOUS, self.previous), (NEW, self.review)):
            if model is not None:
                rings.append((colour, self._filters[colour].filter(model.predict(f), now)))
        subtle = self.review is None        # while the dots run, the ring must not pull the eyes
        for c, r in zip(self.canvases, self.screens):
            c.delete("live")
            for colour, p in rings:
                if r.contains(p):
                    x, y = p[0] - r.x, p[1] - r.y
                    rad, width = (12, 2) if subtle else (22, 4) if colour == NEW else (16, 4)
                    c.create_oval(x - rad, y - rad, x + rad, y + rad, outline=FAINT if subtle else colour,
                                  width=width, tags="live")

    def _show_preview(self) -> None:
        """The camera picture on the main screen, where it covers no calibration dot."""
        rgb = self.preview() if self.preview and not self.done else None
        if rgb is None or rgb is self._shown:
            return
        from .ui import ppm
        self._shown = rgb
        self._picture = tk.PhotoImage(data=ppm(rgb), format="PPM").subsample(2)    # held: Tk forgets unheld images
        c = self.canvases[self.main]
        if c.find_withtag("preview"):
            c.itemconfigure("preview", image=self._picture)
        else:
            self._draw_preview(self.main)

    def _draw_preview(self, k: int) -> None:
        r = self.screens[k]
        if k != self.main or self._picture is None:
            return
        w, h = self._shown.shape[1] / 2, self._shown.shape[0] / 2
        spot = preview_spot(r, [p for p, _kind in self.blocks[k]], w, h)
        if spot:
            c, (x, y) = self.canvases[k], spot
            c.create_rectangle(x - 2, y - 2, x + w + 1, y + h + 1, outline="#333a50", width=2)
            c.create_image(x, y, anchor="nw", image=self._picture, tags="preview")

    # ---- drawing

    def _draw(self, k: int, message=None, hint=None, dot=None, colour="#ff453a") -> None:
        c, r = self.canvases[k], self.screens[k]
        c.delete("all")
        size = max(14, int(r.h / 45))
        if message:
            self._text(c, r, message, size)
        if hint:
            c.create_text(r.w / 2, 50, text=hint, fill="#bbbbbb", font=("Helvetica", size))
        if dot:
            x, y = dot[0] - r.x, dot[1] - r.y
            c.create_oval(x - 14, y - 14, x + 14, y + 14, fill=colour, outline="")
            c.create_oval(x - 3, y - 3, x + 3, y + 3, fill="white", outline="")
        # always a way out, for the mouse: the keyboard focus may be somewhere else
        c.create_rectangle(r.w - 190, 20, r.w - 20, 56, outline="#333a50", width=1, fill="black", tags="cancel")
        c.create_text(r.w - 105, 38, text="Cancel   Esc", fill="#9a9aa0", font=("Helvetica", 12),
                      tags=("cancel", "cancel-text"))
        self._draw_preview(k)

    @staticmethod
    def _text(c, r, lines, size) -> None:
        """Lines centred on the screen, one canvas item each, so a `(colour, text)` line can get a
        dot in that colour in front of it. Long lines wrap; each line is stacked under the last."""
        y, font = 0, ("Helvetica", size)
        for line in lines.split("\n") if isinstance(lines, str) else lines:
            colour, line = line if isinstance(line, tuple) else (None, line)
            item = c.create_text(r.w / 2, y, text=line or " ", anchor="n", fill="white", font=font,
                                 justify="center", width=r.w - 80 - 2 * size, tags="message")
            box = c.bbox(item)
            if colour:
                c.create_text(box[0] - size / 2, box[1], text="●", anchor="ne", fill=colour, font=font, tags="message")
            y = box[3]
        c.move("message", 0, (r.h - y) / 2)

    def _prompt(self, done_name) -> None:
        for k in range(len(self.screens)):
            if k == self.b:
                done = [f"✓ Screen {done_name} calibrated.", ""] if done_name else []
                self._draw(k, done + [
                    f"Press Space (or click) to calibrate screen {k + 1} — about 45 seconds.", "",
                    (PHASE[EYES][2], "Red dot: face the middle of the screen, follow it with your eyes only."),
                    (PHASE[NOSE][2], "Blue dot: point your nose at it."),
                    (PHASE[MOVE][2], "Green dot: keep your eyes on it and slowly move your head around —"),
                    "left, right, up, down, closer and farther.", ""]
                    + (["The grey ring shows where your previous calibration thinks you are looking.", ""]
                       if self.previous else []) + ["Esc cancels."])
            else:
                self._draw(k, "Look at the other screen.")
        self.wins[self.b].focus_force()
        self.waiting = True

    # ---- flow

    def _space(self, _e=None) -> None:
        if not self.waiting or self.done:
            return
        self.waiting = False
        if self.review is not None:
            self._finish(self.review)
        elif self.keyboard_step:
            self._record_keyboard()
        else:
            self._next()

    def _next(self) -> None:
        if self.done:
            return
        steps = self.blocks[self.b]
        if self.i >= len(steps):
            name = self.b + 1
            self.b, self.i = self.b + 1, 0
            if self.b < len(self.blocks):
                self._prompt(name)
            else:
                self._keyboard_prompt(name)
            return
        p, kind = steps[self.i]
        settle, collect, colour, hint = PHASE[kind]
        # a new phase gets a moment for the instruction to sink in
        lead = 1.5 if self.i > 0 and steps[self.i - 1][1] != kind else 0.0
        for k in range(len(self.screens)):
            if k == self.b:
                self._draw(k, hint=hint, dot=p, colour=colour)
            else:
                self._draw(k)
        centre = self.screens[self.b].mid
        # let the eyes (or head) land, then sample
        self.root.after(int((lead + settle) * 1000), lambda: self._set_target((p, kind, centre)))
        self.root.after(int((lead + settle + collect) * 1000), self._step_done)

    def _set_target(self, t) -> None:
        if not self.done:
            self.target = t

    def _step_done(self) -> None:
        self.target = None
        self.i += 1
        self._next()

    def _keyboard_prompt(self, done_name) -> None:
        """Last step: learn what "looking at the keyboard" looks like, so typing heads-down never moves focus."""
        lowest = max(range(len(self.screens)), key=lambda k: self.screens[k].bottom)
        for k in range(len(self.screens)):
            self._draw(k, f"✓ Screen {done_name} calibrated.\n\nLast step: look down at your keyboard the way "
                       "you do while typing,\nand press Space. Keep looking at it for 3 seconds."
                       if k == lowest else None)
        self.wins[lowest].focus_force()
        self.keyboard_step = True
        self.waiting = True

    def _record_keyboard(self) -> None:
        for k in range(len(self.screens)):
            self._draw(k, "Keep looking at your keyboard…")
        self.root.after(600, lambda: setattr(self, "collecting_keyboard", not self.done))
        self.root.after(2800, self._fit)

    def _fit(self) -> None:
        if self.done:
            return
        self.collecting_keyboard = False
        b = self.base
        if b is not None:
            model = GazeModel.fit(b.calib + self.samples, self.screens, learned=b.learned,
                                  keyboard=b.keyboard + self.keyboard)
        else:
            model = GazeModel.fit(self.samples, self.screens, keyboard=self.keyboard)
        if model is None:
            self._finish(None)
        else:
            self._review(model)

    def _review(self, model) -> None:
        """Last screen: both calibrations follow your eyes live, so you can judge before keeping."""
        self.review = model
        new, old = model.error, self.previous.error if self.previous else None
        lines = ["Done — now check it.", "", "Look at a few things on your screen" + ("s." if len(self.screens) > 1 else "."),
                 "The green ring is where the new calibration thinks you are looking."]
        if self.previous:
            lines.append("The grey ring is the previous calibration.")
        if new is not None:
            lines += ["", f"Typical error: about {new:.0f} px" + (f"   (previous: {old:.0f} px)" if old is not None else "")]
        lines += ["", "Space (or click) = keep it      Esc = discard"
                  + (" and keep the previous one" if self.previous else "")]
        for k in range(len(self.screens)):
            self._draw(k, "\n".join(lines) if k == 0 else None)
        self.wins[0].focus_force()
        self.keyboard_step = False
        self.waiting = True

    def _cancel(self, _e=None) -> None:
        if not self.done:
            self._finish(None)

    def _finish(self, model) -> None:
        self.done, self.target, self.collecting_keyboard = True, None, False
        self.root.unbind_all("<Escape>")
        for w in self.wins:
            w.destroy()
        self.on_done(model)
