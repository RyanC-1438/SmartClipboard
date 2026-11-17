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
from . import startup
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

WM_APP_REBIND = 0x0400 + 2  # GUI -> worker: re-register hotkeys
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL
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
        # Persist defaults on first run so the user has a config.json to edit,
        # but never write over a config.json we failed to read.
        if not self.config.load_error:
            config_mod.save(self.config)
        self.features = Features(self.config, notify=self._notify)
        self.command_queue: "queue.Queue[str]" = queue.Queue()
        self.window: MessageWindow | None = None
        self.tray: TrayIcon | None = None
        self.hotkeys: HotkeyManager | None = None
        self._worker_ready = threading.Event()
        self._rebind_requests: "queue.Queue[dict]" = queue.Queue()
        self._actions = {
            "backup_copy": self.features.backup_copy,
            "backup_paste": self.features.backup_paste,
            "replace_paste": self.features.replace_paste,
            "open_settings": lambda: self.command_queue.put("show"),
        }

    def _notify(self, title: str, text: str, warning: bool = False) -> None:
        if self.tray is not None:
            self.tray.notify(title, text, warning)

    # --- worker thread: hotkeys + tray + message loop ---
    def _worker(self) -> None:
        self.window = MessageWindow()
        self.hotkeys = HotkeyManager(self.window)
        self.window.on(WM_APP_REBIND, self._on_rebind)

        failures = []
        for action, callback in self._actions.items():
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

        if self.config.load_error:
            _message_box(self.config.load_error, flags=MB_ICONWARNING)
        if failures:
            _message_box(
                "These hotkeys could not be registered (already in use by "
                "another app). Change them in Settings → Change hotkeys…:\n\n"
                + "\n".join(failures),
                flags=MB_ICONWARNING,
            )

        self.window.run()  # blocks until stop()

        # Cleanup once the loop ends. Put the user's clipboard back if a
        # restore is still pending.
        self.features.flush_restore()
        if self.tray:
            self.tray.remove()
        if self.hotkeys:
            self.hotkeys.unregister_all()
        if self.window:
            self.window.destroy()

    # --- live hotkey changes from the Settings window ---
    def rebind_hotkeys(self, wanted: dict) -> list:
        """Called on the GUI thread. RegisterHotKey only works on the thread
        that owns the window, so hand the request to the worker and wait.
        Returns a list of (action, reason) for chords that could not be used;
        those actions keep their previous chord."""
        if self.window is None or not self.window.hwnd:
            return [(a, "app is not ready") for a in wanted]
        req = {"wanted": wanted, "done": threading.Event(), "failures": None}
        self._rebind_requests.put(req)
        _user32.PostMessageW(self.window.hwnd, WM_APP_REBIND, 0, 0)
        if not req["done"].wait(timeout=6):
            return [(a, "timed out") for a in wanted]
        return req["failures"]

    def _on_rebind(self, _wparam: int, _lparam: int) -> None:
        """Worker thread: re-register every hotkey, keeping the old chord for
        any action whose new chord is taken by another app."""
        while True:
            try:
                req = self._rebind_requests.get_nowait()
            except queue.Empty:
                return
            failures = []
            try:
                old = {a: dict(v) for a, v in self.config.hotkeys.items()}
                self.hotkeys.unregister_all()
                for action, callback in self._actions.items():
                    new = req["wanted"].get(action, old.get(action, {}))
                    if self.hotkeys.register(new.get("mods", []), new.get("key", ""), callback):
                        self.config.hotkeys[action] = new
                        continue
                    if new != old.get(action):
                        failures.append((action, f"{describe(new.get('mods', []), new.get('key', ''))} "
                                                 "is already used by another app"))
                    else:
                        failures.append((action, "could not be registered"))
                    prev = old.get(action, {})
                    self.hotkeys.register(prev.get("mods", []), prev.get("key", ""), callback)
            finally:
                req["failures"] = failures
                req["done"].set()

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
        startup.refresh_path()
        worker = threading.Thread(target=self._worker, daemon=True, name="sc-worker")
        worker.start()
        self._worker_ready.wait(timeout=5)
        # GUI loop runs on the main thread and blocks until quit.
        run_gui(self.config, self.command_queue, self._on_save, self._on_quit,
                rebind=self.rebind_hotkeys)
        worker.join(timeout=3)


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
