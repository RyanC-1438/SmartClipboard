"""SmartClipboard - a lightweight background clipboard macro manager for Windows.

Architecture is deliberately simple and dependency-free so the code is easy to
audit:

  - hotkeys.py        thin ctypes wrapper around the Win32 RegisterHotKey API
  - clipboard.py      get/set the Windows clipboard via ctypes
  - keyboard_send.py  synthesize Ctrl+C / Ctrl+V via the Win32 SendInput API
  - findreplace.py    pure, side-effect-free find & replace logic (unit tested)
  - config.py         load/save settings as JSON under %APPDATA%
  - features.py       wires the two clipboard features to hotkey actions
  - tray.py           system tray icon (ctypes Shell_NotifyIcon)
  - gui.py            tkinter settings window
  - app.py            entry point; ties everything together
"""

__version__ = "1.0.0"
APP_NAME = "SmartClipboard"
