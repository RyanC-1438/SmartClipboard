"""System tray icon via ctypes Shell_NotifyIcon.

Left-click (or double-click) opens Settings. Right-click shows a menu with
"Settings…", a checkable "Pause hotkeys" and "Quit". The icon comes from
assets/smartclipboard.ico (assets/smartclipboard_paused.ico while paused); if
that can't be loaded the stock Windows application icon is used instead.

Threading: the tray belongs to the worker thread's hidden window, so mouse
events, the menu and the callbacks run on that thread. ``notify`` may also be
called from other threads (paste timers); every Shell_NotifyIconW call builds
its own NOTIFYICONDATA and runs under one lock.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable, List, Optional

from .resources import ICON, ICON_PAUSED, resource_path
from .win import MessageWindow

_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

# How long a notification stays up before we take it down ourselves.
NOTIFY_SECONDS = 3.0

WM_NULL = 0x0000
WM_CONTEXTMENU = 0x007B
WM_TRAY = 0x0400 + 1  # WM_APP + 1
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
NIN_KEYSELECT = 0x0400 + 1  # Enter/Space on the focused tray icon

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10
NIIF_INFO = 0x01
NIIF_WARNING = 0x02
NIIF_NOSOUND = 0x10

IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXSMICON = 49
SM_CYSMICON = 50

TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
MF_STRING = 0x0000
MF_CHECKED = 0x0008
MF_SEPARATOR = 0x0800

ID_SETTINGS = 1
ID_QUIT = 2
ID_PAUSE = 3

TRAY_UID = 1


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


_shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)]
_shell32.Shell_NotifyIconW.restype = wintypes.BOOL
_user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
_user32.LoadIconW.restype = wintypes.HICON
_user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
_user32.LoadImageW.restype = wintypes.HANDLE
_user32.DestroyIcon.argtypes = [wintypes.HICON]
_user32.DestroyIcon.restype = wintypes.BOOL
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterWindowMessageW.restype = wintypes.UINT
_user32.CreatePopupMenu.argtypes = []
_user32.CreatePopupMenu.restype = wintypes.HMENU
_user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
_user32.AppendMenuW.restype = wintypes.BOOL
_user32.DestroyMenu.argtypes = [wintypes.HMENU]
_user32.DestroyMenu.restype = wintypes.BOOL
_user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, ctypes.c_void_p,
]
_user32.TrackPopupMenu.restype = wintypes.BOOL
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL


def _load_small_icon(name: str) -> Optional[int]:
    """Load assets/<name> at the system small-icon size, or None."""
    cx = _user32.GetSystemMetrics(SM_CXSMICON) or 16
    cy = _user32.GetSystemMetrics(SM_CYSMICON) or 16
    try:
        path = resource_path(name)
    except Exception:
        return None
    return _user32.LoadImageW(None, path, IMAGE_ICON, cx, cy, LR_LOADFROMFILE) or None


class TrayIcon:
    def __init__(self, window: MessageWindow, tooltip: str,
                 on_settings: Callable[[], None], on_quit: Callable[[], None],
                 on_pause: Optional[Callable[[bool], None]] = None) -> None:
        self._window = window
        self._tooltip = tooltip
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._on_pause = on_pause
        self._lock = threading.Lock()
        self._paused = False
        self._removed = False
        self._gen = 0  # bumped by every notify(); a timer only clears its own
        self._timer: Optional[threading.Timer] = None

        # Icons we loaded from file and must DestroyIcon; the stock icon is
        # shared and must not be destroyed.
        self._owned_icons: List[int] = []
        stock = _user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))
        normal = _load_small_icon(ICON)
        paused = _load_small_icon(ICON_PAUSED)
        self._owned_icons = [h for h in (normal, paused) if h]
        self._icon = normal or stock
        self._icon_paused = paused or self._icon

        window.on(WM_TRAY, self._on_tray)
        # Explorer broadcasts this when the taskbar is (re)created, e.g. after
        # Explorer crashes or restarts; all tray icons must then be re-added.
        self._msg_taskbar_created = _user32.RegisterWindowMessageW("TaskbarCreated")
        if self._msg_taskbar_created:
            window.on(self._msg_taskbar_created, self._on_taskbar_created)
            window.allow_message(self._msg_taskbar_created)
        with self._lock:
            self._add()

    @property
    def paused(self) -> bool:
        return self._paused

    # --- Shell_NotifyIcon plumbing (call with self._lock held) ---
    def _nid(self, flags: int) -> NOTIFYICONDATA:
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self._window.hwnd
        nid.uID = TRAY_UID
        nid.uFlags = flags
        return nid

    def _fill_icon_and_tip(self, nid: NOTIFYICONDATA) -> None:
        nid.hIcon = self._icon_paused if self._paused else self._icon
        tip = self._tooltip + (" (paused)" if self._paused else "")
        nid.szTip = tip[:127]

    def _add(self) -> None:
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        nid.uCallbackMessage = WM_TRAY
        self._fill_icon_and_tip(nid)
        _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _on_taskbar_created(self, _wparam: int, _lparam: int) -> None:
        with self._lock:
            if not self._removed:
                self._add()

    # --- mouse ---
    def _on_tray(self, wparam: int, lparam: int) -> None:
        event = lparam & 0xFFFF
        if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK, NIN_KEYSELECT):
            self._on_settings()
        elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
            self._show_menu()

    def _show_menu(self) -> None:
        hwnd = self._window.hwnd
        menu = _user32.CreatePopupMenu()
        if not menu:
            return
        try:
            _user32.AppendMenuW(menu, MF_STRING, ID_SETTINGS, "Settings…")
            _user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if self._paused else 0),
                                ID_PAUSE, "Pause hotkeys")
            _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            _user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "Quit")
            pt = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(pt))
            # Required so the menu dismisses correctly when clicking elsewhere.
            _user32.SetForegroundWindow(hwnd)
            cmd = _user32.TrackPopupMenu(
                menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, hwnd, None
            )
            # Documented fix (KB135788) so the menu doesn't need two clicks to
            # close: force a task switch to our window.
            _user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        finally:
            _user32.DestroyMenu(menu)
        if cmd == ID_SETTINGS:
            self._on_settings()
        elif cmd == ID_PAUSE:
            want = not self._paused
            if self._on_pause is not None:
                self._on_pause(want)  # the owner calls set_paused()
            else:
                self.set_paused(want)
        elif cmd == ID_QUIT:
            self._on_quit()

    # --- public API ---
    def set_paused(self, paused: bool) -> None:
        """Switch between the normal and the greyed icon and tooltip."""
        with self._lock:
            self._paused = bool(paused)
            if self._removed:
                return
            nid = self._nid(NIF_ICON | NIF_TIP)
            self._fill_icon_and_tip(nid)
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def notify(self, title: str, text: str, warning: bool = False) -> None:
        """Show a silent tray balloon (a toast on Windows 10/11) and take it
        down again after NOTIFY_SECONDS. Safe to call from any thread."""
        with self._lock:
            if self._removed:
                return
            self._gen += 1
            gen = self._gen
            if self._timer is not None:
                self._timer.cancel()
            nid = self._nid(NIF_INFO)
            nid.szInfoTitle = title[:63]
            nid.szInfo = text[:255]
            nid.dwInfoFlags = (NIIF_WARNING if warning else NIIF_INFO) | NIIF_NOSOUND
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            timer = threading.Timer(NOTIFY_SECONDS, self._clear_notification, args=(gen,))
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _clear_notification(self, gen: int) -> None:
        """Timer thread. Remove the balloon, unless a newer one replaced it.
        NIF_INFO with an empty szInfo is Microsoft's documented way to take a
        balloon down without deleting the icon."""
        with self._lock:
            if self._removed or gen != self._gen:
                return
            self._timer = None
            nid = self._nid(NIF_INFO)  # szInfo / szInfoTitle are empty
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def remove(self) -> None:
        with self._lock:
            if self._removed:
                return
            self._removed = True
            self._gen += 1
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            nid = self._nid(0)
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            for h in self._owned_icons:
                _user32.DestroyIcon(h)
            self._owned_icons = []
