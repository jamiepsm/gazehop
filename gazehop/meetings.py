"""Sharing the camera with calls: knowing when one is on (the microphone is in use)."""
from __future__ import annotations

import glob
import sys

MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def mic_in_use() -> bool:
    """Whether an app records from a microphone right now: the sign of a call.
    Linux: the kernel's state of every capture device (PipeWire and PulseAudio close it a few
    seconds after the last app stops recording). Windows: the privacy settings' record of which
    apps use the microphone (an app in use has a start time and no stop time yet)."""
    if sys.platform == "win32":
        return _windows_mic(MIC_KEY)
    for path in glob.glob("/proc/asound/card*/pcm*c/sub*/status"):
        try:
            with open(path, encoding="ascii", errors="replace") as fh:
                if fh.readline().startswith("state: RUNNING"):
                    return True
        except OSError:
            pass
    return False


def _windows_mic(path: str) -> bool:
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return False
    with key:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
            except OSError:
                return False
            i += 1
            if name == "NonPackaged":       # desktop apps (Zoom, Teams...) sit one level deeper
                if _windows_mic(path + "\\" + name):
                    return True
                continue
            try:
                with winreg.OpenKey(key, name) as app:
                    if winreg.QueryValueEx(app, "LastUsedTimeStart")[0] and \
                            not winreg.QueryValueEx(app, "LastUsedTimeStop")[0]:
                        return True
            except OSError:
                pass
