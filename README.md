# gazehop

**Keyboard focus follows your eyes — on Ubuntu and Windows 11.** gazehop watches your webcam and
gives keyboard focus to the screen or window you are looking at, so you can just start typing
without clicking first.

- **Screens** — turn your head toward another monitor and focus moves there.
- **Windows** — look at another window on the same screen and it gets focus.
- **Stays out of your way** — never moves focus while you type, use the mouse, or look down at
  your keyboard.
- **Keeps learning** — every click is a hint of where you were looking; a click elsewhere right
  after a switch counts as a correction.
- **Private** — frames are processed in memory and dropped. Nothing is recorded or sent anywhere.
  The only network use is a one-time download of the face model (about 4 MB, from Google) and,
  once per start, a check on GitHub for a newer version (nothing about you is sent; switch it off
  in Settings).

Webcam eye tracking is good to roughly a thumbnail-sized area. That is enough to pick a window; it
is **not** precise enough to replace the mouse pointer.

> **Early version.** gazehop works on Ubuntu 24.04 (GNOME on Wayland). Windows support is new and
> has not been tried on a real PC yet, so expect a bug or two there. Start with `--dry-run` (below),
> which shows what gazehop would do without changing anything.

## Install

Needs Python 3.10 or newer (one that `mediapipe` has a build for) and a webcam.

### Windows 11

Double-click **`install.bat`**, then **`run.bat`**. The installer finds Python (and offers to
install it with winget if it is missing), creates a private environment in `.venv`, installs the
packages, and adds gazehop to the Desktop and the Start menu.

Windows asks for camera access the first time. gazehop cannot focus windows of programs running
*as administrator* unless it is started as administrator too.

### Ubuntu

```sh
cd gazehop
./install.sh
./run.sh
```

`install.sh` checks that the machine is compatible (Python, webcam, session type, GNOME version),
installs `python3-tk` / `python3-venv` if missing (it asks before using sudo), installs the Python
packages into `.venv`, adds gazehop to the applications menu, and on GNOME Wayland sets up the
Window Calls extension.

By hand instead: `python -m venv .venv`, activate it, `pip install -r requirements.txt`,
`python -m gazehop` (on Ubuntu first `sudo apt install python3-venv python3-tk`).

Which session you are in matters (`echo $XDG_SESSION_TYPE`):

- **x11** ("Ubuntu on Xorg" on the login screen, offered up to about Ubuntu 24.04): works with no
  extra setup, and click learning and the pause hotkey work.
- **wayland** (the default, and the only choice on recent Ubuntu releases): Wayland does not let
  apps focus other apps' windows, so gazehop goes through GNOME Shell with the
  [Window Calls](https://extensions.gnome.org/extension/4724/window-calls/) extension, which
  `install.sh` sets up (GNOME shows a dialog: click Install). In this mode there is no click
  learning and no pause hotkey, minimised windows may be picked, and fractional display scaling
  may put the gaze point slightly off.

## The app

Start **gazehop** from the applications menu (Linux) or the Desktop / Start menu icon (Windows).
From a terminal it is `./run.sh --window` (`run.bat --window`).

- **Camera view** — the live picture, mirrored, with what gazehop reads drawn on it: a green
  outline around the face and eyes, white circles on the irises, white boxes around the eye pixels
  it reads, and an orange arrow for where your head points. If the outline turns amber or
  disappears, gazehop has nothing to work with: fix the light or the camera first.
- **Status line** — tracking, paused, looking at the keyboard, looking away, no readable face.
- **Buttons** — Pause, Calibrate (start over), Add round, Gaze dot, Settings, Quit.
- **Gaze dot** — a small see-through green drop that floats where gazehop thinks you are looking.
  It stretches as it moves and settles when your eyes rest. Clicks go straight through it.
- **Tray icon** — the same actions in a menu; closing the window keeps gazehop running in the
  tray. Without tray support (see the installer's notes) closing the window quits.

## Use from a terminal

```sh
./run.sh                    # run; calibrates first if needed
./run.sh --dry-run --dot    # show the gaze dot and print what it would focus, change nothing
./run.sh calibrate          # recalibrate (after moving a screen or the camera)
./run.sh cameras            # list webcam indices; pick one with --camera N
./run.sh doctor             # show screens and windows as gazehop sees them
```

On Windows use `run.bat` in place of `./run.sh`. Start with `--dry-run --dot` to judge accuracy
before letting it move focus.

**Ctrl+Alt+G** pauses and resumes (not on GNOME Wayland). Ctrl+C in the terminal quits.

**Screens follow your head, windows follow your eyes.** To move to another monitor, turn your head
toward it (point your nose at it); eyes alone only switch between windows on the monitor you face.
Set `screen_from_gaze` to true if you would rather not turn your head — it is less steady.

## Calibration

Takes about 45 seconds per screen: follow the red dot with your eyes only, point your nose at the
blue dot, keep looking at the green dot while moving your head, then look at your keyboard once.
Space (or a click) starts each part; Esc cancels. Sit the way you normally do, with your face
evenly lit.

While the dots run, a **grey ring** shows live where your previous calibration thinks you are
looking. At the end comes a **review screen**: a **green ring** follows your eyes using the new
calibration next to the grey ring of the previous one, with the *typical error* of both. Look
around, then press Space to keep the new calibration or Esc to discard it.

```sh
./run.sh calibrate                      # start over (replaces the calibration)
./run.sh calibrate --add                # add another round to what is already there
./run.sh calibrate --profile glasses    # a separate calibration kept under its own name
./run.sh --profile glasses              # run with that one
```

- **Add rounds for the ways you really sit**: one with glasses and one without, one leaning back,
  one in evening light. One calibration that has seen them all copes with all of them.
- **Use a profile instead** when two setups are very different (for example thick glasses with
  strong reflections): each profile is tuned to one setup, but you choose it at start.
- Light your face evenly from the front; a window or lamp behind you hurts the most.
- Put the camera at the top centre of the screen you use most, and do not move it afterwards.
- During the red dots keep your head still; during the green dots move it more than feels natural.
- Everyday clicks keep refining the calibration on top of whatever you calibrated.

## Settings

Press **Settings** in the app window, or choose **Settings…** in the tray menu. Changes apply right
away and are saved for next time; only the camera and the pause shortcut wait for the next start.

The same settings live in `config.json` (`%APPDATA%\gazehop\` on Windows, `~/.config/gazehop/` on
Linux) if you prefer to edit them by hand; restart afterwards.

| Key | Default | Meaning |
|---|---|---|
| `camera` | 0 | Webcam index |
| `switch_delay_ms` | 250 | Look at another screen this long before focus moves |
| `window_delay_ms` | 300 | Look at another window this long before it gets focus |
| `head_turn` | 50 | How far (%) toward another screen your head must turn |
| `window_focus` | true | Switch between windows on one screen (false = screens only) |
| `edge_margin` | 120 | Pixels past the focused window's edge before a neighbour can take over |
| `wait_typing` / `typing_pause` | true / 2.0 | Hold focus for this many seconds after a key press |
| `mouse_hold` | 1.5 | Hold focus for this many seconds after mouse use |
| `learn` | true | Learn from clicks |
| `smoothing` | 6 | Recent estimates averaged; lower is faster but jumpier |
| `screen_from_gaze` | false | true: your eyes alone pick the screen; false: your head direction does |
| `move_pointer` | false | Also move the mouse pointer to the focused window |
| `show_dot` | false | Always draw the gaze dot |
| `hotkey` | `<ctrl>+<alt>+g` | Pause/resume |
| `check_updates` | true | Check once per start whether a newer version is out |

## Updating

When a newer version is out, the app window and the tray menu say so. If you downloaded gazehop
from the Releases page, download the new version and replace your copy (your settings and
calibration are kept: they live in the folder above, not next to the app). If you installed it
from a copy of the source, run `git pull` and then the installer again.

## Something not working?

1. Run `./run.sh doctor` (`run.bat doctor`): it shows whether everything is installed and which
   screens and windows gazehop can see.
2. Run `./run.sh --dry-run --dot` and watch the dot and the printed messages.
3. Started from an icon there is no terminal; messages go to `gazehop.log` in the same folder as
   `config.json`.

When reporting a problem on the project's GitHub page, include the output of `doctor` and the
contents of `gazehop.log`.

## Credits and license

MIT. A Python port of [Eyeshift](https://github.com/itssarthak/eyeshift) by Sarthak Chhabra (MIT,
macOS only): the gaze model, focus rules and calibration design come from it.
