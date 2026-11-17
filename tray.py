"""System tray icon via ctypes Shell_NotifyIcon.

Provides a right/left-click menu with "Settings" and "Quit". Uses the Windows
stock application icon so we don't have to bundle an .ico file.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable

from .win import MessageWindow

_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_TRAY = 0x0400 + 1  # WM_APP + 1
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04

IDI_APPLICATION = 32512
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800

ID_SETTINGS = 1
ID_QUIT = 2


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
_user32.CreatePopupMenu.restype = wintypes.HMENU
_user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
_user32.DestroyMenu.argtypes = [wintypes.HMENU]
_user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, ctypes.c_void_p,
]
_user32.TrackPopupMenu.restype = wintypes.BOOL
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


class TrayIcon:
    def __init__(self, window: MessageWindow, tooltip: str,
                 on_settings: Callable[[], None], on_quit: Callable[[], None]) -> None:
        self._window = window
        self._tooltip = tooltip
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._nid = NOTIFYICONDATA()
        window.on(WM_TRAY, self._on_tray)
        self._add()

    def _add(self) -> None:
        nid = self._nid
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self._window.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = _user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))
        nid.szTip = self._tooltip[:127]
        _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _on_tray(self, wparam: int, lparam: int) -> None:
        event = lparam & 0xFFFF
        if event in (WM_RBUTTONUP, WM_LBUTTONUP, WM_LBUTTONDBLCLK):
            self._show_menu()

    def _show_menu(self) -> None:
        menu = _user32.CreatePopupMenu()
        _user32.AppendMenuW(menu, MF_STRING, ID_SETTINGS, "Settings…")
        _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        _user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "Quit")
        pt = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        # Required so the menu dismisses correctly when clicking elsewhere.
        _user32.SetForegroundWindow(self._window.hwnd)
        cmd = _user32.TrackPopupMenu(
            menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, self._window.hwnd, None
        )
        _user32.DestroyMenu(menu)
        if cmd == ID_SETTINGS:
            self._on_settings()
        elif cmd == ID_QUIT:
            self._on_quit()

    def remove(self) -> None:
        _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
