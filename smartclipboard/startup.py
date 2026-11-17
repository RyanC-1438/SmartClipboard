"""Optional "Start with Windows", via the per-user Run registry key.

This is the one place SmartClipboard writes outside its config.json, and only
when the user ticks the box: a single value named "SmartClipboard" under
HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run (no admin
needed). Unticking deletes it again. Only offered for the built .exe; running
from source has no stable executable to point at.
"""

from __future__ import annotations

import sys
import winreg

from . import APP_NAME

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def available() -> bool:
    return bool(getattr(sys, "frozen", False))


def _command() -> str:
    return f'"{sys.executable}"'


def _read() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _type = winreg.QueryValueEx(key, APP_NAME)
            return str(value)
    except OSError:
        return None


def is_enabled() -> bool:
    return _read() is not None


def set_enabled(enabled: bool) -> None:
    """Raises OSError if the registry can't be written."""
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def refresh_path() -> None:
    """If the user opted in and then moved the .exe, point the entry at the
    copy that is running now. Does nothing if they never opted in."""
    if available() and is_enabled() and _read() != _command():
        try:
            set_enabled(True)
        except OSError:
            pass
