"""Entry point: wires hotkeys, tray, features and the settings GUI together.

Threading model:
  * Main thread  -> tkinter GUI event loop (window starts hidden).
  * Worker thread -> hidden Win32 window running the GetMessage loop, which owns
    the global hotkeys and the tray icon.
The worker talks to the GUI through a thread-safe queue the Tk loop polls.
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes

from . import APP_NAME, __version__
from . import config as config_mod
from .features import Features
from .gui import run_gui
from .hotkeys import HotkeyManager, describe
from .tray import TrayIcon
from .win import MessageWindow

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ERROR_ALREADY_EXISTS = 183
MB_OK = 0x0
MB_ICONWARNING = 0x30

_user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
_user32.MessageBoxW.restype = ctypes.c_int
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE


def _message_box(text: str, title: str = APP_NAME, flags: int = MB_OK) -> None:
    _user32.MessageBoxW(None, text, title, flags)


def _acquire_single_instance() -> bool:
    """Return True if we are the only instance (and keep the mutex alive)."""
    _kernel32.CreateMutexW(None, True, "SmartClipboard_SingleInstance_Mutex")
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


class App:
    def __init__(self) -> None:
        self.config = config_mod.load()
        # Persist defaults on first run so the user has a config.json to edit.
        config_mod.save(self.config)
        self.features = Features(self.config)
        self.command_queue: "queue.Queue[str]" = queue.Queue()
        self.window: MessageWindow | None = None
        self.tray: TrayIcon | None = None
        self.hotkeys: HotkeyManager | None = None
        self._worker_ready = threading.Event()

    # --- worker thread: hotkeys + tray + message loop ---
    def _worker(self) -> None:
        self.window = MessageWindow()
        self.hotkeys = HotkeyManager(self.window)

        actions = {
            "backup_copy": self.features.backup_copy,
            "backup_paste": self.features.backup_paste,
            "replace_paste": self.features.replace_paste,
            "open_settings": lambda: self.command_queue.put("show"),
        }
        failures = []
        for action, callback in actions.items():
            hk = self.config.hotkeys.get(action, {})
            mods, key = hk.get("mods", []), hk.get("key", "")
            if not self.hotkeys.register(mods, key, callback):
                failures.append(f"  • {describe(mods, key)}  ({action})")

        self.tray = TrayIcon(
            self.window, f"{APP_NAME} {__version__}",
            on_settings=lambda: self.command_queue.put("show"),
            on_quit=lambda: self.command_queue.put("quit"),
        )
        self._worker_ready.set()

        if failures:
            _message_box(
                "These hotkeys could not be registered (already in use by "
                "another app). Edit config.json to change them:\n\n"
                + "\n".join(failures),
                flags=MB_ICONWARNING,
            )

        self.window.run()  # blocks until stop()

        # Cleanup once the loop ends.
        if self.tray:
            self.tray.remove()
        if self.hotkeys:
            self.hotkeys.unregister_all()
        if self.window:
            self.window.destroy()

    def _on_save(self) -> None:
        # Features reads self.config.rules live, so nothing else is needed.
        pass

    def _on_quit(self) -> None:
        if self.window:
            self.window.stop()

    def run(self) -> None:
        if not _acquire_single_instance():
            _message_box(f"{APP_NAME} is already running.", flags=MB_ICONWARNING)
            return
        worker = threading.Thread(target=self._worker, daemon=True, name="sc-worker")
        worker.start()
        self._worker_ready.wait(timeout=5)
        # GUI loop runs on the main thread and blocks until quit.
        run_gui(self.config, self.command_queue, self._on_save, self._on_quit)
        worker.join(timeout=3)


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
