"""Minimal Windows clipboard access via ctypes (Unicode text only).

Only two operations are exposed: read text and write text. We never register
clipboard listeners or read formats other than CF_UNICODETEXT.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE

_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalSize.restype = ctypes.c_size_t
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = wintypes.HGLOBAL

_user32.GetClipboardSequenceNumber.restype = wintypes.DWORD


def sequence_number() -> int:
    """A counter the OS bumps every time the clipboard changes. Useful to detect
    when a synthesized Ctrl+C has actually landed."""
    return int(_user32.GetClipboardSequenceNumber())


def _open(retries: int = 10, delay: float = 0.02) -> bool:
    """The clipboard is a shared resource; another app may hold it briefly."""
    import time

    for _ in range(retries):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def get_text() -> str:
    """Return clipboard text, or "" if the clipboard holds no Unicode text."""
    if not _open():
        return ""
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.c_wchar_p(ptr).value or ""
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def set_text(text: str) -> bool:
    """Replace the clipboard contents with ``text``. Returns success."""
    if text is None:
        text = ""
    if not _open():
        return False
    try:
        if not _user32.EmptyClipboard():
            return False
        # Size in bytes for a UTF-16-LE string plus the terminating NUL.
        data = text.encode("utf-16-le") + b"\x00\x00"
        h_mem = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h_mem:
            return False
        ptr = _kernel32.GlobalLock(h_mem)
        if not ptr:
            _kernel32.GlobalFree(h_mem)
            return False
        ctypes.memmove(ptr, data, len(data))
        _kernel32.GlobalUnlock(h_mem)
        # On success, the system owns h_mem; we must not free it. On failure we
        # still own it and must free it to avoid leaking the handle.
        if not _user32.SetClipboardData(CF_UNICODETEXT, h_mem):
            _kernel32.GlobalFree(h_mem)
            return False
        return True
    finally:
        _user32.CloseClipboard()
