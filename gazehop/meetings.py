"""Sharing the camera with calls: knowing when one is on (the microphone is in use), and passing
the camera picture on to a virtual camera that call apps can use (Linux, v4l2loopback)."""
from __future__ import annotations

import glob
import os
import struct
import sys

LABEL = "GazeHop Camera"    # the v4l2loopback device's card_label (README: Sharing the camera)
VIDIOC_S_FMT = 0xC0D05605   # _IOWR('V', 5, struct v4l2_format), 208 bytes on 64-bit Linux
OUTPUT, FIELD_NONE, SRGB = 2, 1, 8
YU12 = int.from_bytes(b"YU12", "little")    # planar 4:2:0, what OpenCV's BGR2YUV_I420 makes
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


def virtual_camera_path() -> str | None:
    """The device of the virtual camera made for gazehop, if there is one."""
    for d in sorted(glob.glob("/sys/class/video4linux/video*")):
        try:
            with open(os.path.join(d, "name"), encoding="utf-8") as fh:
                if fh.read().strip() == LABEL:
                    return "/dev/" + os.path.basename(d)
        except OSError:
            pass
    return None


class VirtualCamera:
    """Passes camera frames on to a v4l2loopback device: call apps see it as "GazeHop Camera"."""

    def __init__(self, path: str):
        self.path, self.size = path, None
        self.fd = os.open(path, os.O_RDWR)

    def write(self, frame) -> None:
        """Called on the camera thread with every BGR frame. A failure ends the sharing, never
        the tracking."""
        if self.fd is None:
            return
        import cv2
        import fcntl
        try:
            h, w = frame.shape[:2]
            if self.size != (w, h):
                fmt = struct.pack("I4x12I", OUTPUT, w, h, YU12, FIELD_NONE, w, w * h * 3 // 2, SRGB,
                                  0, 0, 0, 0, 0).ljust(208, b"\0")
                fcntl.ioctl(self.fd, VIDIOC_S_FMT, fmt)
                self.size = (w, h)
            os.write(self.fd, cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420).tobytes())
        except OSError as e:
            if self.fd is not None:     # not just closed from the main thread meanwhile
                print(f"gazehop: sharing the camera stopped ({self.path}: {e})", file=sys.stderr)
                self.close()

    def close(self) -> None:
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)
