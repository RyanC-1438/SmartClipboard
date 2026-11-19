"""The clipboard features, wired to hotkey actions.

All features are careful to leave the user's MAIN clipboard (Ctrl+C / Ctrl+V)
as they found it -- every format, not just text.  They borrow the system
clipboard to drive a paste, then put the previous contents back.

The put-back happens in the background, ``config.restore_delay_ms`` after the
paste keystroke, so slow target apps (a busy IDE, an RDP session) have time to
read the pasted text. If the user copies something during that window we leave
their new clipboard alone. If another hotkey fires first, the pending restore
is not written at all: the clipboard still holds our pasted text, so the new
action simply takes over the saved snapshot as the user's clipboard.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from . import clipboard
from . import foreground
from . import keyboard_send
from .clipboard import Result
from .config import Config
from .findreplace import apply_rules_counted

Notify = Callable[[str, str, bool], None]

# The target app reads the clipboard some time after our injected Ctrl+V, so
# never write the clipboard again sooner than this after a paste (capped at
# restore_delay_ms, which already allows for slow apps).
MIN_PASTE_GAP_MS = 400
# Extra attempts when the clipboard is busy at restore time.
RESTORE_RETRIES = 3


def _wait_for_clipboard_change(previous_seq: int, timeout: float = 1.0) -> bool:
    """Spin briefly until a synthesized copy updates the clipboard."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if clipboard.sequence_number() != previous_seq:
            return True
        time.sleep(0.01)
    return False


def _one_line(text: str, limit: int = 120) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


class _Pending:
    """A restore waiting for its timer. ``our_seq`` is the clipboard sequence
    number right after we wrote, so any later change means the user copied."""

    def __init__(self, snap, our_seq: int, after: Optional[Callable[[], None]]) -> None:
        self.snap = snap
        self.our_seq = our_seq
        self.after = after
        self.token = 0
        self.retries = 0


class Features:
    RESTORE_RETRY_DELAY = 0.3  # seconds between restore attempts when busy

    def __init__(self, config: Config, notify: Optional[Notify] = None) -> None:
        self.config = config
        self._notify = notify or (lambda _t, _m, _w=False: None)
        # One hotkey action at a time, and never concurrently with a restore.
        self._lock = threading.RLock()
        self._pending: Optional[_Pending] = None
        self._timer: Optional[threading.Timer] = None
        self._token = 0  # generation of the latest armed restore
        self._last_paste: Optional[float] = None  # time.monotonic() of last Ctrl+V
        # Chain state for replace_paste: the clipboard text we restored after the
        # last replace-paste, the clipboard sequence number right after that
        # restore, and what we pasted. If the clipboard is still exactly that on
        # the next press, chain mode continues from the last pasted text.
        self._chain_source: Optional[str] = None
        self._chain_seq: Optional[int] = None
        self._chain_last: str = ""

    def _tell(self, title: str, text: str, problem: bool, warning: bool = False) -> None:
        """Notify, honouring config.notify_level ("all" / "problems" / "off")."""
        level = self.config.notify_level
        if level == "all" or (level == "problems" and problem):
            self._notify(title, text, warning)

    def _tell_busy(self) -> None:
        self._tell("Clipboard busy, try again",
                   "Another app is using the clipboard.", problem=True)

    # --- shared paste / restore machinery --------------------------------
    def flush_restore(self, token: Optional[int] = None) -> None:
        """Put the user's clipboard back now if a restore is pending.

        The timer passes the token of the restore it was armed for; if that
        restore has since been taken over or replaced, the call does nothing.
        Called without a token (e.g. at shutdown) it restores immediately,
        retrying in place if the clipboard is busy."""
        with self._lock:
            p = self._pending
            if p is None or (token is not None and token != p.token):
                return
            self._cancel_timer()
            self._pending = None
            self._wait_paste_gap()
            while True:
                result = clipboard.restore(p.snap, history=False, expected_seq=p.our_seq)
                if result is not Result.BUSY or p.retries >= RESTORE_RETRIES:
                    break
                p.retries += 1
                if token is not None:
                    self._arm(p, self.RESTORE_RETRY_DELAY)
                    return
                time.sleep(self.RESTORE_RETRY_DELAY)
            if result:
                if p.after is not None:
                    p.after()
            elif result is not Result.SKIPPED:  # SKIPPED: the user copied; keep it
                self._tell("Couldn't restore your clipboard",
                           "Another app kept the clipboard busy."
                           if result is Result.BUSY else
                           "Part of it could not be put back.",
                           problem=True, warning=True)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _arm(self, p: _Pending, delay: float) -> None:
        # A timer that already fired but is still waiting for the lock carries
        # the old token, so it can't run (or cancel) this newer restore early.
        self._cancel_timer()
        self._token += 1
        p.token = self._token
        self._pending = p
        self._timer = threading.Timer(delay, self.flush_restore, args=(p.token,))
        self._timer.daemon = True
        self._timer.start()

    def _schedule_restore(self, snap, our_seq: int,
                          after: Optional[Callable[[], None]] = None) -> None:
        self._arm(_Pending(snap, our_seq, after), self.config.restore_delay_ms / 1000.0)

    def _wait_paste_gap(self) -> None:
        if self._last_paste is None:
            return
        gap = min(self.config.restore_delay_ms, MIN_PASTE_GAP_MS) / 1000.0
        wait = self._last_paste + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _begin(self):
        """Start an action. Returns (snapshot of the user's clipboard, reused),
        where ``reused`` is the pending restore that was taken over, if any;
        or (None, None) after telling the user the clipboard is busy.

        If a restore is pending and the clipboard still holds the text we
        pasted, it is NOT written back: that Ctrl+V was only just injected and
        the target app reads the clipboard asynchronously, so it could paste
        the original instead. Its snapshot becomes this action's snapshot of
        the user's clipboard, and this action schedules the restore."""
        p, self._pending = self._pending, None
        self._cancel_timer()
        if p is not None and clipboard.sequence_number() == p.our_seq:
            if p.after is not None:
                p.after()  # e.g. chain bookkeeping, as if it had been restored
            return p.snap, p
        # No restore pending, or the user copied since and that's theirs now.
        snap = clipboard.snapshot()
        if snap is None:
            self._tell_busy()
            return None, None
        return snap, None

    def _give_back(self, reused: Optional[_Pending]) -> None:
        """The action stopped without writing the clipboard. A restore it took
        over is still needed (the clipboard holds the previous paste), so put
        it back as it was."""
        if reused is not None:
            reused.retries = 0
            self._arm(reused, self.config.restore_delay_ms / 1000.0)

    def _restore_now(self, snap, our_seq: int) -> None:
        self._cancel_timer()
        self._pending = _Pending(snap, our_seq, None)
        self.flush_restore()

    def _paste(self, text: str, snap, reused: Optional[_Pending], private: bool = False,
               after: Optional[Callable[[], None]] = None) -> bool:
        """Paste ``text`` into the focused window and schedule the restore of
        ``snap``. ``private`` keeps the result out of history and clipboard
        monitors. Returns False if it could not paste."""
        history = self.config.paste_results_in_history and not private
        self._wait_paste_gap()
        elevated = foreground.foreground_is_elevated()
        result = clipboard.set_text(text, history=history, private=private)
        if result is Result.BUSY:
            self._give_back(reused)
            self._tell_busy()
            return False
        if not result:
            # The clipboard may already be emptied: put the user's data back now.
            self._restore_now(snap, clipboard.sequence_number())
            self._tell("Paste failed", "Couldn't put the text on the clipboard.",
                       problem=True)
            return False
        if elevated:
            # Windows blocks simulated keystrokes into elevated apps, silently.
            # The result stays on the clipboard so a manual Ctrl+V works.
            self._tell("Paste blocked by Windows",
                       "The target app is running as administrator, so it "
                       "ignores simulated keys. Your original clipboard was "
                       "replaced by the result: press Ctrl+V.",
                       problem=True, warning=True)
            return False
        our_seq = clipboard.sequence_number()
        time.sleep(0.03)
        keyboard_send.send_paste()
        self._last_paste = time.monotonic()
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
            snap, reused = self._begin()
            if snap is None:
                return
            self._wait_paste_gap()
            seq = clipboard.sequence_number()
            keyboard_send.send_copy()
            if not _wait_for_clipboard_change(seq):
                # Nothing selected, or the app ignored Ctrl+C: keep the old
                # backup instead of capturing whatever was already there.
                self._give_back(reused)
                self._tell("Nothing copied",
                           "No selection, or the app didn't respond to Ctrl+C. "
                           "The backup clipboard is unchanged.", problem=True)
                return
            captured = clipboard.get_text()
            if captured:
                self.config.palette[0] = captured
            # Restore the main clipboard now so Ctrl+V is unaffected.
            self._restore_now(snap, clipboard.sequence_number())

    def backup_paste(self) -> None:
        """Paste the backup clipboard into the focused window, then restore the
        main clipboard."""
        self.paste_slot(1)

    def paste_slot(self, n: int) -> None:
        """Paste palette slot ``n`` (1-based; slot 1 is the backup clipboard)
        into the focused window, then restore the main clipboard."""
        with self._lock:
            text = self.config.palette[n - 1]
            if not text:
                if n == 1:
                    self._tell("Backup clipboard is empty",
                               "Use Ctrl+Shift+Win+C to fill it.", problem=True)
                else:
                    self._tell(f"Palette slot {n} is empty",
                               "Right-click the slot in Settings to fill it.", problem=True)
                return
            snap, reused = self._begin()
            if snap is None:
                return
            self._paste(text, snap, reused)

    # --- Transform the clipboard in place ---------------------------------
    def transform_clipboard(self) -> None:
        """Apply the rules to the clipboard and leave the result there (no
        paste, no restore): the user pastes it with Ctrl+V, as often as they
        like. This also works in apps that ignore simulated keys (elevated
        apps) and avoids any paste timing issues."""
        with self._lock:
            self.flush_restore()  # finish any pending paste first
            snap = clipboard.snapshot()
            if snap is None:
                self._tell_busy()
                return
            if not snap.text:
                self._tell("Nothing to transform", "The clipboard has no text.",
                           problem=True)
                return
            result, count = apply_rules_counted(snap.text, list(self.config.rules))
            if count == 0:
                self._tell("No matches", "Clipboard unchanged.", problem=True)
                return
            history = self.config.paste_results_in_history and not snap.private
            outcome = clipboard.set_text(result, history=history, private=snap.private)
            if outcome is Result.BUSY:
                self._tell_busy()
            elif not outcome:
                # Opened but failed partway: put the original back.
                self._restore_now(snap, clipboard.sequence_number())
                self._tell("Transform failed", "Couldn't update the clipboard.",
                           problem=True)
            else:
                self._tell(f"{count} replacement{'s' if count != 1 else ''}",
                           "Clipboard now: " + _one_line(result) + "  (Ctrl+V to paste)",
                           problem=False)

    # --- Feature 2: find & replace on paste -----------------------------
    def next_replace_text(self, original: str, seq: int):
        """What a replace-paste should paste for clipboard text ``original``.

        Normally the rules are applied to the clipboard text. In chain mode, if
        the clipboard is untouched since our last replace-paste, the rules are
        applied to the text we pasted last time instead, so each press advances
        one more step (Z1Status -> Z2Status -> Z3Status ...). Copying anything
        (even the same text again) bumps the sequence number and restarts.
        Returns (text, number of replacements).
        """
        source = original
        if (self.config.chain_paste
                and self._chain_seq == seq
                and self._chain_source == original):
            source = self._chain_last
        # A copy: the Settings window may replace the list while we work.
        return apply_rules_counted(source, list(self.config.rules))

    def replace_paste(self) -> None:
        """Read the main clipboard, apply find/replace rules, paste the result,
        then restore the original clipboard contents."""
        with self._lock:
            snap, reused = self._begin()
            if snap is None:
                return
            original = snap.text
            if original == "":
                self._give_back(reused)
                self._tell("Nothing to paste", "The clipboard has no text.",
                           problem=True)
                return
            transformed, count = self.next_replace_text(
                original, clipboard.sequence_number())

            def remember_chain() -> None:
                self._chain_source = original
                self._chain_last = transformed
                self._chain_seq = clipboard.sequence_number()

            # A result made from a password manager's clipboard stays as
            # private as the source was.
            if self._paste(transformed, snap, reused, private=snap.private,
                           after=remember_chain):
                if count == 0:
                    self._tell("No matches", "Pasted unchanged.", problem=True)
                else:
                    self._tell(f"{count} replacement{'s' if count != 1 else ''}",
                               "Pasted: " + _one_line(transformed), problem=False)
