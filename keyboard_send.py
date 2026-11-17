"""Synthesize Ctrl+C / Ctrl+V keystrokes via the Win32 SendInput API.

This is how the app pastes into the currently focused window. We only ever send
the two specific chords below -- we never type arbitrary text and never inject
anything else.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)

VK_CONTROL = 0x11
VK_C = 0x43
VK_V = 0x56

KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

# Every modifier key that might be physically held when our hotkey fires. We
# inject key-up for all of them before sending our own chord, otherwise (e.g.)
# a held Ctrl+Shift+Win turns our injected Ctrl+V into Ctrl+Shift+Win+V, which
# the target app does not recognise as "paste". Sending key-up for a key that
# is not down is harmless.
_MODIFIER_VKS = (
    0x5B, 0x5C,        # LWIN, RWIN
    0x10, 0xA0, 0xA1,  # SHIFT, LSHIFT, RSHIFT
    0x11, 0xA2, 0xA3,  # CONTROL, LCONTROL, RCONTROL
    0x12, 0xA4, 0xA5,  # MENU(ALT), LMENU, RMENU
)


# ULONG_PTR is pointer-sized (8 bytes on x64). Using the right width here is
# what makes the overall INPUT struct match the size the OS expects.
_ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT(ctypes.Structure):
    # The union must include MOUSEINPUT (its largest member) so sizeof(_INPUT)
    # equals the OS INPUT size. SendInput rejects any call whose cbSize does not
    # match exactly -- the cause of the "nothing happens" bug.
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT


def _key(vk: int, up: bool) -> _INPUT:
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = _KEYBDINPUT(
        wVk=vk,
        wScan=0,
        dwFlags=KEYEVENTF_KEYUP if up else 0,
        time=0,
        dwExtraInfo=0,
    )
    return inp


def _send(inputs) -> None:
    arr = (_INPUT * len(inputs))(*inputs)
    _user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _release_held_modifiers() -> None:
    """Inject key-up for all modifier keys so the hotkey's own modifiers (which
    the user is still physically holding) don't contaminate the chord we send."""
    _send([_key(vk, True) for vk in _MODIFIER_VKS])
    time.sleep(0.02)  # let the target app process the releases first


def _chord(vk: int) -> None:
    """Release any held modifiers, then press Ctrl + <vk> and release both."""
    _release_held_modifiers()
    _send([_key(VK_CONTROL, False), _key(vk, False)])
    time.sleep(0.01)
    _send([_key(vk, True), _key(VK_CONTROL, True)])


def send_copy() -> None:
    _chord(VK_C)


def send_paste() -> None:
    _chord(VK_V)
