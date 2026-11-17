"""The two clipboard features, wired to hotkey actions.

Both features are careful to leave the user's MAIN clipboard (Ctrl+C / Ctrl+V)
exactly as they found it.  They borrow the system clipboard for a fraction of a
second to drive a paste, then restore the previous contents.
"""

from __future__ import annotations

import time

from . import clipboard
from . import keyboard_send
from .config import Config
from .findreplace import apply_rules


def _wait_for_clipboard_change(previous_seq: int, timeout: float = 0.5) -> None:
    """Spin briefly until a synthesized copy updates the clipboard."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if clipboard.sequence_number() != previous_seq:
            return
        time.sleep(0.01)


class Features:
    def __init__(self, config: Config) -> None:
        self.config = config
        # The backup clipboard lives only in memory.
        self._backup: str = ""

    # --- Feature 1: backup clipboard ------------------------------------
    def backup_copy(self) -> None:
        """Copy the current selection into the in-memory backup clipboard
        WITHOUT disturbing the user's main clipboard."""
        saved = clipboard.get_text()
        seq = clipboard.sequence_number()
        keyboard_send.send_copy()
        _wait_for_clipboard_change(seq)
        captured = clipboard.get_text()
        if captured:
            self._backup = captured
        # Restore the main clipboard so Ctrl+V is unaffected.
        clipboard.set_text(saved)

    def backup_paste(self) -> None:
        """Paste the backup clipboard into the focused window, then restore the
        main clipboard."""
        if not self._backup:
            return
        saved = clipboard.get_text()
        if not clipboard.set_text(self._backup):
            return
        time.sleep(0.03)
        keyboard_send.send_paste()
        time.sleep(0.15)  # let the target app consume the paste
        clipboard.set_text(saved)

    # --- Feature 2: find & replace on paste -----------------------------
    def replace_paste(self) -> None:
        """Read the main clipboard, apply find/replace rules, paste the result,
        then restore the original clipboard contents."""
        original = clipboard.get_text()
        if original == "":
            return
        transformed = apply_rules(original, self.config.rules)
        if not clipboard.set_text(transformed):
            return
        time.sleep(0.03)
        keyboard_send.send_paste()
        time.sleep(0.15)
        clipboard.set_text(original)
