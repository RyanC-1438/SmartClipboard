"""Is the window we are about to paste into running as administrator?

Windows (UIPI) silently drops synthesized keystrokes sent from a normal process
to an elevated one, and SendInput gives no error. So before pasting we check
the foreground window's process, purely to tell the user and fall back to
"result left on the clipboard, press Ctrl+V yourself".

Security note: this only *queries* the other process with the most limited
access right that exists (PROCESS_QUERY_LIMITED_INFORMATION + TOKEN_QUERY). It
never elevates SmartClipboard, never writes to the other process, and if
anything fails it simply answers "not elevated".
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION_CLASS = 20  # TOKEN_INFORMATION_CLASS.TokenElevation

_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]
_advapi32.OpenProcessToken.restype = wintypes.BOOL
_advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_advapi32.GetTokenInformation.restype = wintypes.BOOL


def _process_is_elevated(h_process) -> bool:
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(h_process, TOKEN_QUERY, ctypes.byref(token)):
        return False
    try:
        elevated = wintypes.DWORD()
        size = wintypes.DWORD()
        if not _advapi32.GetTokenInformation(token, TOKEN_ELEVATION_CLASS,
                                             ctypes.byref(elevated),
                                             ctypes.sizeof(elevated), ctypes.byref(size)):
            return False
        return bool(elevated.value)
    finally:
        _kernel32.CloseHandle(token)


def self_is_elevated() -> bool:
    return _process_is_elevated(_kernel32.GetCurrentProcess())


def foreground_is_elevated() -> bool:
    """True if the foreground window belongs to an elevated process (and we are
    not elevated ourselves, in which case pasting into it works fine)."""
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False
        h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h:
            return False
        try:
            return _process_is_elevated(h) and not self_is_elevated()
        finally:
            _kernel32.CloseHandle(h)
    except OSError:
        return False
