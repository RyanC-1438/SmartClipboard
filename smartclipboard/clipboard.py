"""Minimal Windows clipboard access via ctypes.

Exposed operations: read text, write text, and snapshot/restore. A snapshot
copies every memory-backed clipboard format as opaque bytes, only so the exact
same data can be put back after the app has borrowed the clipboard to drive a
paste. Those bytes are never inspected, logged or stored on disk; the only
format ever decoded is CF_UNICODETEXT. We never register clipboard listeners.
"""

from __future__ import annotations

import ctypes
import enum
import time
from ctypes import wintypes
from typing import Optional

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
_EXCLUDE_MONITOR_NAME = "ExcludeClipboardContentFromMonitorProcessing"
# Our own history opt-out (restores, results with history off) also writes
# CanIncludeInClipboardHistory = 0. This marker says "that flag is ours, not a
# password manager's", so the next snapshot isn't wrongly treated as private.
# Dropped from snapshots like the history flags.
_OWN_OPT_OUT_NAME = "SmartClipboardHistoryOptOut"

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


class Result(enum.Enum):
    """Outcome of a clipboard write. Only OK is truthy, so callers that just
    want success/failure can keep testing the result as a bool."""
    OK = "ok"
    BUSY = "busy"        # could not open the clipboard; nothing was changed
    SKIPPED = "skipped"  # the clipboard changed since expected_seq; left alone
    FAILED = "failed"    # opened, but emptying it or setting a format failed

    def __bool__(self) -> bool:
        return self is Result.OK


class Snapshot:
    """Opaque copy of the clipboard: a list of (format_id, bytes) in the order
    the clipboard listed them. ``private`` is True when the source asked to be
    kept out of clipboard history or monitors (password managers do this)."""

    def __init__(self, formats: list[tuple[int, bytes]] | None = None,
                 private: bool = False) -> None:
        self.formats: list[tuple[int, bytes]] = list(formats or [])
        self.private = private

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


def _write(formats: list[tuple[int, bytes]], history: bool,
           expected_seq: Optional[int] = None, private: bool = False,
           exclude_monitors: bool = False) -> Result:
    """Replace the whole clipboard with ``formats`` in one Open/Empty/Close
    session. ``private`` says the content itself is private (see
    Snapshot.private); ``exclude_monitors`` adds the Exclude format. FAILED
    means the clipboard was taken but emptying it or setting some format
    failed (the other formats are still set)."""
    if not _open():
        return Result.BUSY
    try:
        # Checked only once we hold the clipboard: nobody else can change it
        # now, so a copy the user made while _open() was retrying is seen here.
        if expected_seq is not None and sequence_number() != expected_seq:
            return Result.SKIPPED
        if not _user32.EmptyClipboard():
            return Result.FAILED
        ok = True
        for fmt, data in formats:
            ok = _set_data(fmt, data) and ok
        zero = (0).to_bytes(4, "little")
        if not history:
            names = _HISTORY_FLAG_NAMES if private else _HISTORY_FLAG_NAMES + (_OWN_OPT_OUT_NAME,)
            for fmt in _registered(names):
                _set_data(fmt, zero)
        if exclude_monitors:
            for fmt in _registered((_EXCLUDE_MONITOR_NAME,)):
                _set_data(fmt, zero)
        return Result.OK if ok else Result.FAILED
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


def set_text(text: str, history: bool = True, private: bool = False) -> Result:
    """Replace the clipboard contents with ``text``. The result is truthy on
    success. With ``history=False`` the write is kept out of Win+V history and
    cloud sync; ``private=True`` also marks it with
    ExcludeClipboardContentFromMonitorProcessing for third-party monitors."""
    if text is None:
        text = ""
    # Size in bytes for a UTF-16-LE string plus the terminating NUL.
    data = text.encode("utf-16-le") + b"\x00\x00"
    return _write([(CF_UNICODETEXT, data)], history and not private,
                  private=private, exclude_monitors=private)


def _read_handle(handle, size: int) -> Optional[bytes]:
    if size == 0:
        return b""
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, size)
    finally:
        _kernel32.GlobalUnlock(handle)


def snapshot(max_bytes: int = 64 * 1024 * 1024) -> Optional[Snapshot]:
    """Copy every HGLOBAL-backed clipboard format so restore() can put it back.

    Once the running total would exceed ``max_bytes`` no further non-text
    formats are copied; CF_UNICODETEXT is always kept. That truncated result
    is still a normal snapshot.

    Returns None if the clipboard could not be opened or reading it failed
    partway. An empty Snapshot means the clipboard really was empty; None
    must never be restored, or it would wipe the user's clipboard.
    """
    try:
        if not _open():
            return None
        try:
            ids = []
            ctypes.set_last_error(0)
            fmt = _user32.EnumClipboardFormats(0)
            while fmt:
                ids.append(fmt)
                fmt = _user32.EnumClipboardFormats(fmt)
            if ctypes.get_last_error():
                return None  # the list ended on an error, not at its end

            history_flags = set(_registered(_HISTORY_FLAG_NAMES + (_OWN_OPT_OUT_NAME,)))
            can_include = _registered(("CanIncludeInClipboardHistory",))
            exclude = _registered((_EXCLUDE_MONITOR_NAME,))
            own = _registered((_OWN_OPT_OUT_NAME,))
            snap = Snapshot()
            # Recorded before the history flags are dropped below, so a paste
            # derived from a password stays out of history too.
            if exclude and exclude[0] in ids:
                snap.private = True
            if can_include and can_include[0] in ids and not (own and own[0] in ids):
                handle = _user32.GetClipboardData(can_include[0])
                value = _read_handle(handle, 4) if handle else None
                if value is not None and len(value) == 4 and not any(value):
                    snap.private = True

            skip = set(history_flags)
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
                data = _read_handle(handle, size)
                if data is None:
                    if fmt == CF_UNICODETEXT:
                        return None  # restoring without the text would lose it
                    continue
                snap.formats.append((fmt, data))
                total += size
            return snap
        finally:
            _user32.CloseClipboard()
    except Exception:
        return None


def restore(snap: Snapshot, history: bool = True,
            expected_seq: Optional[int] = None) -> Result:
    """Replace the clipboard with the formats saved in ``snap`` (an empty
    snapshot leaves the clipboard empty). The result is truthy only if every
    format was put back. With ``history=False`` the write is kept out of Win+V
    history and cloud sync.

    With ``expected_seq``, the clipboard is left alone (Result.SKIPPED) unless
    its sequence number still equals it once we hold the clipboard open, i.e.
    the user has not copied anything since."""
    return _write(snap.formats, history, expected_seq=expected_seq,
                  private=snap.private)
