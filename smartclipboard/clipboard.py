"""Minimal Windows clipboard access via ctypes.

Exposed operations: read text, write text, and snapshot/restore. A snapshot
copies every memory-backed clipboard format as opaque bytes, only so the exact
same data can be put back after the app has borrowed the clipboard to drive a
paste. Those bytes are never inspected, logged or stored on disk; the only
format ever decoded is CF_UNICODETEXT. We never register clipboard listeners.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

CF_TEXT = 1
CF_OEMTEXT = 7
CF_UNICODETEXT = 13
CF_LOCALE = 16
GMEM_MOVEABLE = 0x0002

# Formats whose clipboard handle is a GDI or other non-HGLOBAL handle, so their
# data cannot be copied as bytes. CF_DIB / CF_DIBV5 still carry any bitmap.
_HANDLE_FORMATS = frozenset({
    2,     # CF_BITMAP
    3,     # CF_METAFILEPICT (HGLOBAL wrapping an HMETAFILE)
    9,     # CF_PALETTE
    14,    # CF_ENHMETAFILE
    0x80,  # CF_OWNERDISPLAY
    0x82,  # CF_DSPBITMAP
    0x83,  # CF_DSPMETAFILEPICT
    0x8E,  # CF_DSPENHMETAFILE
})
_PRIVATE_FIRST, _GDIOBJ_LAST = 0x200, 0x3FF  # CF_PRIVATEFIRST .. CF_GDIOBJLAST

# Windows re-synthesizes these from CF_UNICODETEXT.
_SYNTHESIZED_FROM_UNICODE = frozenset({CF_TEXT, CF_OEMTEXT, CF_LOCALE})

# Registered formats that tell Win+V history and cloud clipboard to ignore a
# write. Dropped from snapshots so restore()'s history argument alone decides.
# "ExcludeClipboardContentFromMonitorProcessing" is deliberately NOT dropped:
# password managers set it, and a restored password must stay hidden from
# third-party clipboard managers too.
_HISTORY_FLAG_NAMES = ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard")
_EXCLUDE_NAMES = _HISTORY_FLAG_NAMES

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
_user32.EnumClipboardFormats.restype = wintypes.UINT
_user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterClipboardFormatW.restype = wintypes.UINT
_user32.GetClipboardSequenceNumber.argtypes = []
_user32.GetClipboardSequenceNumber.restype = wintypes.DWORD

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


class Snapshot:
    """Opaque copy of the clipboard: a list of (format_id, bytes) in the order
    the clipboard listed them."""

    def __init__(self, formats: list[tuple[int, bytes]] | None = None) -> None:
        self.formats: list[tuple[int, bytes]] = list(formats or [])

    @property
    def text(self) -> str:
        """The CF_UNICODETEXT content, or "" if there is none."""
        for fmt, data in self.formats:
            if fmt == CF_UNICODETEXT:
                text = data[: len(data) - len(data) % 2].decode("utf-16-le", "replace")
                return text.split("\x00", 1)[0]
        return ""

    @property
    def is_empty(self) -> bool:
        return not self.formats


def sequence_number() -> int:
    """A counter the OS bumps every time the clipboard changes. Useful to detect
    when a synthesized Ctrl+C has actually landed."""
    return int(_user32.GetClipboardSequenceNumber())


def _open(retries: int = 10, delay: float = 0.02) -> bool:
    """The clipboard is a shared resource; another app may hold it briefly."""
    for _ in range(retries):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def _registered(names: tuple[str, ...]) -> list[int]:
    ids = (_user32.RegisterClipboardFormatW(name) for name in names)
    return [i for i in ids if i]


def _set_data(fmt: int, data: bytes) -> bool:
    """SetClipboardData from a fresh HGLOBAL copy of ``data``. The clipboard
    must already be open and emptied."""
    h_mem = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h_mem:
        return False
    if data:  # a zero-byte block cannot be locked, and needs no copy
        ptr = _kernel32.GlobalLock(h_mem)
        if not ptr:
            _kernel32.GlobalFree(h_mem)
            return False
        ctypes.memmove(ptr, data, len(data))
        _kernel32.GlobalUnlock(h_mem)
    # On success, the system owns h_mem; we must not free it. On failure we
    # still own it and must free it to avoid leaking the handle.
    if not _user32.SetClipboardData(fmt, h_mem):
        _kernel32.GlobalFree(h_mem)
        return False
    return True


def _write(formats: list[tuple[int, bytes]], history: bool) -> bool:
    """Replace the whole clipboard with ``formats`` in one Open/Empty/Close
    session. Returns False if the clipboard could not be taken or any format
    failed to be set (the others are still set)."""
    if not _open():
        return False
    try:
        if not _user32.EmptyClipboard():
            return False
        ok = True
        for fmt, data in formats:
            ok = _set_data(fmt, data) and ok
        if not history:
            zero = (0).to_bytes(4, "little")
            for fmt in _registered(_HISTORY_FLAG_NAMES):
                _set_data(fmt, zero)
        return ok
    finally:
        _user32.CloseClipboard()


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


def set_text(text: str, history: bool = True) -> bool:
    """Replace the clipboard contents with ``text``. Returns success. With
    ``history=False`` the write is kept out of Win+V history and cloud sync."""
    if text is None:
        text = ""
    # Size in bytes for a UTF-16-LE string plus the terminating NUL.
    data = text.encode("utf-16-le") + b"\x00\x00"
    return _write([(CF_UNICODETEXT, data)], history)


def snapshot(max_bytes: int = 64 * 1024 * 1024) -> Snapshot:
    """Copy every HGLOBAL-backed clipboard format so restore() can put it back.

    Once the running total would exceed ``max_bytes`` no further non-text
    formats are copied; CF_UNICODETEXT is always kept. Never raises: on any
    failure the formats gathered so far are returned.
    """
    snap = Snapshot()
    try:
        if not _open():
            return snap
        try:
            ids = []
            fmt = _user32.EnumClipboardFormats(0)
            while fmt:
                ids.append(fmt)
                fmt = _user32.EnumClipboardFormats(fmt)

            skip = set(_registered(_EXCLUDE_NAMES))
            if CF_UNICODETEXT in ids:
                skip |= _SYNTHESIZED_FROM_UNICODE
            total = 0
            capped = False
            for fmt in ids:
                if (fmt in skip or fmt in _HANDLE_FORMATS
                        or _PRIVATE_FIRST <= fmt <= _GDIOBJ_LAST):
                    continue
                if capped and fmt != CF_UNICODETEXT:
                    continue
                # NULL here means e.g. a delayed-render owner that has exited.
                handle = _user32.GetClipboardData(fmt)
                if not handle:
                    continue
                ctypes.set_last_error(0)
                size = _kernel32.GlobalSize(handle)
                if size == 0 and ctypes.get_last_error():
                    continue  # not an HGLOBAL after all
                if fmt != CF_UNICODETEXT and total + size > max_bytes:
                    capped = True
                    continue
                if size == 0:
                    data = b""
                else:
                    ptr = _kernel32.GlobalLock(handle)
                    if not ptr:
                        continue
                    try:
                        data = ctypes.string_at(ptr, size)
                    finally:
                        _kernel32.GlobalUnlock(handle)
                snap.formats.append((fmt, data))
                total += size
        finally:
            _user32.CloseClipboard()
    except Exception:
        pass
    return snap


def restore(snap: Snapshot, history: bool = True) -> bool:
    """Replace the clipboard with the formats saved in ``snap`` (an empty
    snapshot leaves the clipboard empty). Returns True if every format was put
    back. With ``history=False`` the write is kept out of Win+V history and
    cloud sync."""
    return _write(snap.formats, history)
