"""The clipboard features, wired to hotkey actions.

All features are careful to leave the user's MAIN clipboard (Ctrl+C / Ctrl+V)
exactly as they found it.  They borrow the system clipboard for a fraction of a
second to drive a paste, then restore the previous contents.
"""

from __future__ import annotations

import time
from typing import Optional

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
        # Chain state for replace_paste: the clipboard text we restored after the
        # last replace-paste, the clipboard sequence number right after that
        # restore, and what we pasted. If the clipboard is still exactly that on
        # the next press, chain mode continues from the last pasted text.
        self._chain_source: Optional[str] = None
        self._chain_seq: Optional[int] = None
        self._chain_last: str = ""

    # --- Feature 1: backup clipboard (= palette slot 1) -------------------
    # The backup lives in config.palette[0] so the Settings palette shows it.
    # Config.to_dict never writes that slot, so it stays memory-only.
    @property
    def backup(self) -> str:
        return self.config.palette[0]

    def backup_copy(self) -> None:
        """Copy the current selection into the in-memory backup clipboard
        WITHOUT disturbing the user's main clipboard."""
        saved = clipboard.get_text()
        seq = clipboard.sequence_number()
        keyboard_send.send_copy()
        _wait_for_clipboard_change(seq)
        captured = clipboard.get_text()
        if captured:
            self.config.palette[0] = captured
        # Restore the main clipboard so Ctrl+V is unaffected.
        clipboard.set_text(saved)

    def backup_paste(self) -> None:
        """Paste the backup clipboard into the focused window, then restore the
        main clipboard."""
        backup = self.backup
        if not backup:
            return
        saved = clipboard.get_text()
        if not clipboard.set_text(backup):
            return
        time.sleep(0.03)
        keyboard_send.send_paste()
        time.sleep(0.15)  # let the target app consume the paste
        clipboard.set_text(saved)

    # --- Feature 2: find & replace on paste -----------------------------
    def next_replace_text(self, original: str, seq: int) -> str:
        """What a replace-paste should paste for clipboard text ``original``.

        Normally the rules are applied to the clipboard text. In chain mode, if
        the clipboard is untouched since our last replace-paste, the rules are
        applied to the text we pasted last time instead, so each press advances
        one more step (Z1Status -> Z2Status -> Z3Status ...). Copying anything
        (even the same text again) bumps the sequence number and restarts.
        """
        source = original
        if (self.config.chain_paste
                and self._chain_seq == seq
                and self._chain_source == original):
            source = self._chain_last
        return apply_rules(source, self.config.rules)

    def replace_paste(self) -> None:
        """Read the main clipboard, apply find/replace rules, paste the result,
        then restore the original clipboard contents."""
        original = clipboard.get_text()
        if original == "":
            return
        transformed = self.next_replace_text(original, clipboard.sequence_number())
        if not clipboard.set_text(transformed):
            return
        time.sleep(0.03)
        keyboard_send.send_paste()
        time.sleep(0.15)
        clipboard.set_text(original)
        self._chain_source = original
        self._chain_last = transformed
        self._chain_seq = clipboard.sequence_number()
