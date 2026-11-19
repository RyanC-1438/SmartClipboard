"""SmartClipboard - a lightweight background clipboard macro manager for Windows.

Architecture is deliberately simple and dependency-free so the code is easy to
audit:

  - hotkeys.py        thin ctypes wrapper around the Win32 RegisterHotKey API
  - clipboard.py      snapshot/restore the Windows clipboard via ctypes
  - keyboard_send.py  synthesize Ctrl+C / Ctrl+V via the Win32 SendInput API
  - foreground.py     is the target window elevated? (query only)
  - findreplace.py    pure, side-effect-free find & replace logic (unit tested)
  - config.py         load/save settings as JSON under %APPDATA%
  - features.py       backup, replace-paste, transform, slot paste, chain mode
  - tray.py           system tray icon, menu and notifications (Shell_NotifyIcon)
  - win.py            hidden window + message loop for hotkeys and the tray
  - resources.py      paths to bundled assets (the icons)
  - startup.py        optional per-user "Start with Windows" entry
  - gui.py            tkinter settings window, palette and pins
  - app.py            entry point; ties everything together
"""

__version__ = "1.4.2"
APP_NAME = "SmartClipboard"
