"""Global hotkeys via the Win32 RegisterHotKey API.

This is the security-critical design choice: RegisterHotKey asks the OS to
deliver a WM_HOTKEY message ONLY when an exact registered chord is pressed.  The
process never receives, sees, or can record any other keystroke -- it is not a
keyboard hook and structurally cannot act as a keylogger or interfere with
normal typing.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable, Dict, List

from .win import MessageWindow

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MOD_MAP = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
            "shift": MOD_SHIFT, "win": MOD_WIN}

# Named virtual-key codes we support in addition to single characters.
_NAMED_VK = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},  # F1..F24 -> 0x70..0x87
}


# Keys offered in the Settings hotkey editor, in display order.
KEY_CHOICES = ([chr(c) for c in range(ord("A"), ord("Z") + 1)]
               + [str(d) for d in range(10)]
               + [f"F{i}" for i in range(1, 25)]
               + ["Space", "Enter", "Tab", "Esc"])


def reserved_reason(mods: List[str], key: str) -> str:
    """Why a chord is a bad idea even if RegisterHotKey would accept it ("" if
    it's fine)."""
    m = {x.lower() for x in mods}
    k = key.upper()
    if not m:
        return "Needs at least one modifier, or it would block normal typing."
    if m == {"shift"}:
        return "Shift alone would block typing that character."
    if {"ctrl", "shift", "win"} <= m and k == "B":
        return "Win+Ctrl+Shift+B is the Windows graphics-driver reset."
    if "win" in m and len(k) == 1 and k.isdigit():
        return "Win+number chords open taskbar apps in Windows."
    return ""


def mods_to_flags(mods: List[str]) -> int:
    flags = MOD_NOREPEAT
    for m in mods:
        flags |= _MOD_MAP.get(str(m).lower(), 0)
    return flags


def key_to_vk(key: str) -> int:
    """Map a configured key string to a virtual-key code (0 if invalid)."""
    if not key:
        return 0
    k = key.lower()
    if k in _NAMED_VK:
        return _NAMED_VK[k]
    if len(key) == 1:
        ch = key.upper()
        if "A" <= ch <= "Z" or "0" <= ch <= "9":
            return ord(ch)
    return 0


def describe(mods: List[str], key: str) -> str:
    order = ["win", "ctrl", "alt", "shift"]
    given = ["ctrl" if x.lower() == "control" else x.lower() for x in mods]
    parts = [m.capitalize() for m in order if m in given]
    parts.append(key.upper())
    return "+".join(parts)


class HotkeyManager:
    """Registers chords against a MessageWindow and dispatches WM_HOTKEY."""

    def __init__(self, window: MessageWindow) -> None:
        self._window = window
        self._callbacks: Dict[int, Callable[[], None]] = {}
        self._registered: List[int] = []
        self._next_id = 1
        window.on(WM_HOTKEY, self._on_hotkey)

    def register(self, mods: List[str], key: str, callback: Callable[[], None]) -> bool:
        """Register one chord. Returns False if the key is invalid or taken."""
        vk = key_to_vk(key)
        if vk == 0:
            return False
        hotkey_id = self._next_id
        self._next_id += 1
        if not _user32.RegisterHotKey(self._window.hwnd, hotkey_id, mods_to_flags(mods), vk):
            return False
        self._callbacks[hotkey_id] = callback
        self._registered.append(hotkey_id)
        return True

    def _on_hotkey(self, wparam: int, lparam: int) -> None:
        cb = self._callbacks.get(int(wparam))
        if cb is not None:
            cb()

    def unregister_all(self) -> None:
        for hotkey_id in self._registered:
            _user32.UnregisterHotKey(self._window.hwnd, hotkey_id)
        self._registered.clear()
        self._callbacks.clear()
