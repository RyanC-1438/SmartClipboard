"""Reliable middle-click for Tk buttons on Windows.

Tk only reports a mouse button when it sees Windows' "button is down" key
state change (GetKeyState). Many mouse drivers (e.g. ones that remap the wheel
button) and precision touchpads deliver WM_MBUTTONDOWN without updating that
state, so Tk silently drops the click -- it only notices once the pointer moves
while the button is held. To make middle-click dependable, we look at the
window messages of the widget itself: the button's window procedure is
subclassed and WM_MBUTTONDOWN is recorded directly.

The window procedure runs while Tk is dispatching a message, when calling back
into tkinter is not allowed (it crashes the interpreter). So it only appends
the callback to a queue; a short Tk ``after`` loop runs queued callbacks.

Only the widgets we pass in are touched, and only that one message is acted on;
everything else goes to Tk's own window procedure unchanged.
"""

from __future__ import annotations

import ctypes
from collections import deque
from ctypes import wintypes
from typing import Callable, Deque, Dict

_user32 = ctypes.WinDLL("user32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)
GWLP_WNDPROC = -4
WM_NCDESTROY = 0x0082
WM_MBUTTONDOWN = 0x0207
_POLL_MS = 30

_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_void_p
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
_user32.SetWindowLongPtrW.restype = ctypes.c_void_p
_user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
_user32.CallWindowProcW.restype = LRESULT

# hwnd -> (our WNDPROC, Tk's original proc). Keeping the WNDPROC object here
# stops it being garbage-collected while Windows still calls it.
_hooks: Dict[int, tuple] = {}
_pending: Deque[Callable[[], None]] = deque()
_pollers: set = set()  # Tk interpreters with a running drain loop


def _start_drain(widget) -> None:
    root = widget.winfo_toplevel()
    key = str(root.tk)
    if key in _pollers:
        return
    _pollers.add(key)

    def drain() -> None:
        while _pending:
            cb = _pending.popleft()
            try:
                cb()
            except Exception:
                pass
        try:
            root.after(_POLL_MS, drain)
        except Exception:
            _pollers.discard(key)  # root destroyed
    root.after(_POLL_MS, drain)


def on_middle_click(widget, callback: Callable[[], None]) -> bool:
    """Run ``callback`` shortly after every middle-button press on
    ``widget``. Returns False if the widget's window could not be
    subclassed; the caller should then fall back to a Tk <Button-2> binding."""
    try:
        widget.update_idletasks()  # make sure the native window exists
        hwnd = int(widget.winfo_id())
    except Exception:
        return False
    if not hwnd or hwnd in _hooks:
        return False
    old = _user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC)
    if not old:
        return False

    def proc(h, msg, wparam, lparam):
        # No tkinter calls in here (see module docstring).
        if msg == WM_MBUTTONDOWN:
            _pending.append(callback)
            return 0
        if msg == WM_NCDESTROY:
            _user32.SetWindowLongPtrW(h, GWLP_WNDPROC, old)
            _hooks.pop(int(h), None)
        return _user32.CallWindowProcW(old, h, msg, wparam, lparam)

    new = WNDPROC(proc)
    _hooks[hwnd] = (new, old)
    if not _user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC,
                                     ctypes.cast(new, ctypes.c_void_p).value):
        _hooks.pop(hwnd, None)
        return False
    _start_drain(widget)
    return True
