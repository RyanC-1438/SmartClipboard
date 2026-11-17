"""A hidden message-only window plus a Win32 message loop.

The hotkey listener and the tray icon both attach to this single window, which
runs its GetMessage loop on a dedicated background thread.  All Win32 calls are
explicitly typed so ctypes marshals 64-bit handles correctly.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable, Dict

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_APP_STOP = 0x0400 + 100  # custom: ask the loop to terminate

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hWnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


_user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.DefWindowProcW.restype = ctypes.c_ssize_t
_user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEX)]
_user32.RegisterClassExW.restype = wintypes.ATOM
_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.GetMessageW.restype = ctypes.c_int
_user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
_user32.DispatchMessageW.restype = ctypes.c_ssize_t
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL
_user32.PostQuitMessage.argtypes = [ctypes.c_int]
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class MessageWindow:
    """A hidden window that dispatches messages to registered handlers."""

    def __init__(self, class_name: str = "SmartClipboardHidden") -> None:
        self._class_name = class_name
        self._handlers: Dict[int, Callable[[int, int], None]] = {}
        self.hwnd: int | None = None
        self._hinstance = _kernel32.GetModuleHandleW(None)
        # Keep a strong reference to the WNDPROC so it is never GC'd while in use.
        self._wndproc = WNDPROC(self._dispatch)
        self._register_class()
        self._create_window()

    def on(self, message: int, handler: Callable[[int, int], None]) -> None:
        """Register ``handler(wParam, lParam)`` for a window message id."""
        self._handlers[message] = handler

    def _register_class(self) -> None:
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.style = 0
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = self._hinstance
        wc.lpszClassName = self._class_name
        if not _user32.RegisterClassExW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())

    def _create_window(self) -> None:
        self.hwnd = _user32.CreateWindowExW(
            0, self._class_name, self._class_name, 0,
            0, 0, 0, 0, None, None, self._hinstance, None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

    def _dispatch(self, hwnd, msg, wparam, lparam):
        handler = self._handlers.get(msg)
        if handler is not None:
            try:
                handler(wparam, lparam)
            except Exception:  # never let a handler crash the message loop
                pass
            return 0
        if msg == WM_APP_STOP:
            _user32.PostQuitMessage(0)
            return 0
        if msg == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def run(self) -> None:
        """Block on the message loop until stop() is called."""
        msg = MSG()
        while True:
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:  # WM_QUIT or error
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self) -> None:
        if self.hwnd:
            _user32.PostMessageW(self.hwnd, WM_APP_STOP, 0, 0)

    def destroy(self) -> None:
        if self.hwnd:
            _user32.DestroyWindow(self.hwnd)
            self.hwnd = None
