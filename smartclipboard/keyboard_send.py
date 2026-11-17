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

# Every modifier key that might be physically held when our hotkey fires. If
# they were still down, our injected Ctrl+V would become Ctrl+Shift+Win+V, which
# the target app does not recognise as "paste", so we wait for their release.
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


_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short


def _held_modifiers() -> list:
    return [vk for vk in _MODIFIER_VKS if _user32.GetAsyncKeyState(vk) & 0x8000]


def wait_for_modifiers_released(timeout: float = 2.0) -> bool:
    """Wait until the user lets go of the hotkey's modifiers. Returns True if
    they were released in time.

    Sending our chord only after a real release means we never have to fake a
    key-up for a key the user is still holding. Faking it desynchronises
    Windows' idea of the keyboard: a second tap of the hotkey letter while the
    modifiers are still held would then type that letter instead of firing the
    hotkey again. Now a second tap simply queues a second hotkey message.
    """
    deadline = time.monotonic() + timeout
    while _held_modifiers():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


def _release_held_modifiers() -> None:
    """Fallback if the user is still holding modifiers after the timeout:
    inject key-up for the ones that are down so they don't contaminate the
    chord we send (Ctrl+Shift+Win+V is not "paste")."""
    held = _held_modifiers()
    if held:
        _send([_key(vk, True) for vk in held])
        time.sleep(0.02)  # let the target app process the releases first


def _chord(vk: int) -> None:
    """Wait for (or force) the modifiers up, then press Ctrl + <vk>."""
    wait_for_modifiers_released()
    _release_held_modifiers()
    _send([_key(VK_CONTROL, False), _key(vk, False)])
    time.sleep(0.01)
    _send([_key(vk, True), _key(VK_CONTROL, True)])


def send_copy() -> None:
    _chord(VK_C)


def send_paste() -> None:
    _chord(VK_V)
