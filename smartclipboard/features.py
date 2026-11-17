"""The clipboard features, wired to hotkey actions.

All features are careful to leave the user's MAIN clipboard (Ctrl+C / Ctrl+V)
as they found it -- every format, not just text.  They borrow the system
clipboard to drive a paste, then put the previous contents back.

The put-back happens in the background, ``config.restore_delay_ms`` after the
paste keystroke, so slow target apps (a busy IDE, an RDP session) have time to
read the pasted text. If the user copies something during that window we leave
their new clipboard alone, and if another hotkey fires first the pending
restore is done immediately before it runs.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import clipboard
from . import foreground
from . import keyboard_send
from .config import Config
from .findreplace import apply_rules

Notify = Callable[[str, str, bool], None]


def _wait_for_clipboard_change(previous_seq: int, timeout: float = 1.0) -> bool:
    """Spin briefly until a synthesized copy updates the clipboard."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if clipboard.sequence_number() != previous_seq:
            return True
        time.sleep(0.01)
    return False


class Features:
    def __init__(self, config: Config, notify: Optional[Notify] = None) -> None:
        self.config = config
        self._notify = notify or (lambda _t, _m, _w=False: None)
        # One hotkey action at a time, and never concurrently with a restore.
        self._lock = threading.RLock()
        self._pending = None  # (snapshot, our_seq, after_restore) awaiting restore
        self._timer: Optional[threading.Timer] = None
        # Chain state for replace_paste: the clipboard text we restored after the
        # last replace-paste, the clipboard sequence number right after that
        # restore, and what we pasted. If the clipboard is still exactly that on
        # the next press, chain mode continues from the last pasted text.
        self._chain_source: Optional[str] = None
        self._chain_seq: Optional[int] = None
        self._chain_last: str = ""

    # --- shared paste / restore machinery --------------------------------
    def flush_restore(self) -> None:
        """Put the user's clipboard back now if a restore is pending."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            pending, self._pending = self._pending, None
            if pending is None:
                return
            snap, our_seq, after = pending
            if clipboard.sequence_number() != our_seq:
                return  # the user copied something new meanwhile; keep it
            if clipboard.restore(snap, history=False) and after is not None:
                after()

    def _schedule_restore(self, snap, our_seq: int,
                          after: Optional[Callable[[], None]] = None) -> None:
        self._pending = (snap, our_seq, after)
        self._timer = threading.Timer(self.config.restore_delay_ms / 1000.0,
                                      self.flush_restore)
        self._timer.daemon = True
        self._timer.start()

    def _paste(self, text: str, snap, after: Optional[Callable[[], None]] = None) -> bool:
        """Paste ``text`` into the focused window and schedule the restore of
        ``snap``. Returns False if it could not paste."""
        if foreground.foreground_is_elevated():
            # Windows blocks simulated keystrokes into elevated apps, silently.
            # Leave the result on the clipboard so a manual Ctrl+V works.
            clipboard.set_text(text)
            self._notify("Paste blocked by Windows",
                         "The target app is running as administrator, so it "
                         "ignores simulated keys. The result is on your "
                         "clipboard: press Ctrl+V.", True)
            return False
        if not clipboard.set_text(text, history=self.config.paste_results_in_history):
            return False
        our_seq = clipboard.sequence_number()
        time.sleep(0.03)
        keyboard_send.send_paste()
        self._schedule_restore(snap, our_seq, after)
        return True

    # --- Feature 1: backup clipboard (= palette slot 1) -------------------
    # The backup lives in config.palette[0] so the Settings palette shows it.
    # Config.to_dict never writes that slot, so it stays memory-only.
    @property
    def backup(self) -> str:
        return self.config.palette[0]

    def backup_copy(self) -> None:
        """Copy the current selection into the in-memory backup clipboard
        WITHOUT disturbing the user's main clipboard."""
        with self._lock:
            self.flush_restore()
            snap = clipboard.snapshot()
            seq = clipboard.sequence_number()
            keyboard_send.send_copy()
            if not _wait_for_clipboard_change(seq):
                # Nothing selected, or the app ignored Ctrl+C: keep the old
                # backup instead of capturing whatever was already there.
                self._notify("Nothing copied",
                             "No selection, or the app didn't respond to Ctrl+C. "
                             "The backup clipboard is unchanged.", False)
                return
            captured = clipboard.get_text()
            if captured:
                self.config.palette[0] = captured
            # Restore the main clipboard so Ctrl+V is unaffected.
            clipboard.restore(snap, history=False)

    def backup_paste(self) -> None:
        """Paste the backup clipboard into the focused window, then restore the
        main clipboard."""
        with self._lock:
            self.flush_restore()
            backup = self.backup
            if not backup:
                self._notify("Backup clipboard is empty",
                             "Use Ctrl+Shift+Win+C to fill it.", False)
                return
            self._paste(backup, clipboard.snapshot())

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
        with self._lock:
            self.flush_restore()
            snap = clipboard.snapshot()
            original = snap.text
            if original == "":
                return
            transformed = self.next_replace_text(original, clipboard.sequence_number())

            def remember_chain() -> None:
                self._chain_source = original
                self._chain_last = transformed
                self._chain_seq = clipboard.sequence_number()

            self._paste(transformed, snap, after=remember_chain)
