"""Windows 10/11 backend, pure ctypes (user32 + dwmapi)."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from ..geometry import Rect, Win

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                 "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow"}

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                     ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def _proto(fn, restype, *argtypes):
    fn.restype, fn.argtypes = restype, argtypes


_proto(user32.EnumWindows, wintypes.BOOL, WNDENUMPROC, wintypes.LPARAM)
_proto(user32.EnumDisplayMonitors, wintypes.BOOL, wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM)
_proto(user32.IsWindowVisible, wintypes.BOOL, wintypes.HWND)
_proto(user32.IsIconic, wintypes.BOOL, wintypes.HWND)
_proto(user32.IsWindow, wintypes.BOOL, wintypes.HWND)
_proto(user32.GetWindowTextLengthW, ctypes.c_int, wintypes.HWND)
_proto(user32.GetWindowTextW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_proto(user32.GetClassNameW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_proto(user32.GetWindowLongW, wintypes.LONG, wintypes.HWND, ctypes.c_int)
_proto(user32.GetWindowRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_proto(user32.GetWindowThreadProcessId, wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_proto(user32.GetForegroundWindow, wintypes.HWND)
_proto(user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
_proto(user32.BringWindowToTop, wintypes.BOOL, wintypes.HWND)
_proto(user32.AttachThreadInput, wintypes.BOOL, wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
_proto(user32.SetCursorPos, wintypes.BOOL, ctypes.c_int, ctypes.c_int)
_proto(user32.GetLastInputInfo, wintypes.BOOL, ctypes.POINTER(LASTINPUTINFO))
_proto(user32.keybd_event, None, wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p)
_proto(kernel32.GetCurrentThreadId, wintypes.DWORD)
_proto(kernel32.GetTickCount, wintypes.DWORD)
_proto(dwmapi.DwmGetWindowAttribute, ctypes.c_long, wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)


class WindowsBackend:
    name = "windows"

    def __init__(self):
        # per-monitor DPI awareness, so every API (and Tk) speaks real pixels on scaled displays;
        # must happen before the first window is created
        try:
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            try:
                ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                pass
        self._pid = os.getpid()

    def screens(self) -> list:
        out = []

        @MONITORENUMPROC
        def cb(_mon, _dc, rect, _lp):
            r = rect.contents
            out.append(Rect(r.left, r.top, r.right - r.left, r.bottom - r.top))
            return True

        user32.EnumDisplayMonitors(None, None, cb, 0)
        return sorted(out)

    def main_screen(self) -> Rect | None:
        """The primary screen: Windows always puts it at (0, 0)."""
        # ponytail: built-in panel detection needs QueryDisplayConfig; add it if laptop users ask
        return next((r for r in self.screens() if (r.x, r.y) == (0, 0)), None)

    def _win(self, hwnd) -> Win | None:
        if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return None
        if user32.GetWindowTextLengthW(hwnd) == 0:
            return None
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
            return None
        cloaked = wintypes.DWORD(0)     # e.g. windows on another virtual desktop, suspended Store apps
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return None
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in SHELL_CLASSES:
            return None
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == self._pid:
            return None
        r = wintypes.RECT()     # the visible frame, without the invisible resize borders
        if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return None
        if r.right - r.left < 50 or r.bottom - r.top < 50:
            return None
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        return Win(int(hwnd), Rect(r.left, r.top, r.right - r.left, r.bottom - r.top), pid.value, title.value)

    def windows(self) -> list:
        out = []

        @WNDENUMPROC
        def cb(hwnd, _lp):      # EnumWindows walks the z-order top to bottom
            w = self._win(hwnd)
            if w:
                out.append(w)
            return True

        user32.EnumWindows(cb, 0)
        return out

    def active_window(self) -> Win | None:
        return self._win(user32.GetForegroundWindow())

    def focus(self, win_id: int) -> bool:
        hwnd = wintypes.HWND(win_id)
        if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
            return False
        # Windows only lets the foreground thread hand over the foreground; borrow its input queue
        fg = user32.GetForegroundWindow()
        me = kernel32.GetCurrentThreadId()
        other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = bool(other and other != me and user32.AttachThreadInput(me, other, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if attached:
            user32.AttachThreadInput(me, other, False)
        if user32.GetForegroundWindow() != win_id:
            # fallback: a synthetic Alt tap makes this process eligible to set the foreground
            user32.keybd_event(VK_MENU, 0, 0, None)
            user32.SetForegroundWindow(hwnd)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, None)
        return user32.GetForegroundWindow() == win_id

    def move_pointer(self, x: float, y: float) -> None:
        user32.SetCursorPos(int(x), int(y))

    def idle_seconds(self) -> float | None:
        info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
