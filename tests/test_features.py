"""Tests for chain mode, clipboard restore, the palette/backup slot and config
persistence.

Windows clipboard, keystroke and foreground-window calls are replaced with
fakes, and APPDATA is pointed at a temp folder so the real config.json is never
touched.
"""

import json
import os
import sys
import tempfile
import time as real_time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="sc_test_")

from smartclipboard import config as config_mod
from smartclipboard import features as features_mod
from smartclipboard.clipboard import Result
from smartclipboard.config import DEFAULT_HOTKEYS, PALETTE_SIZE, Config
from smartclipboard.findreplace import Rule

REAL_SLEEP = real_time.sleep


class FakeSnapshot:
    def __init__(self, text, extra, private=False):
        self.text = text
        self.extra = extra
        self.private = private


class FakeClipboard:
    """Text plus an opaque 'extra' standing in for non-text formats."""

    def __init__(self, text=""):
        self.text = text
        self.extra = None
        self.private = False  # set by a password manager's copy
        self.seq = 1
        self.history = []  # history flag of every write
        self.writes = []   # (kind, text, history, private) of every write
        self.restore_calls = []  # expected_seq of every restore attempt
        self.busy = 0            # the next N writes fail with Result.BUSY
        self.restore_busy = 0    # the next N restores fail with Result.BUSY
        self.fail_set = False    # set_text empties the clipboard, then fails
        self.snapshot_fails = False
        self.on_open = None      # run once when a write has opened the clipboard

    def _opened(self):
        hook, self.on_open = self.on_open, None
        if hook:
            hook()

    def get_text(self):
        return self.text

    def set_text(self, text, history=True, private=False):
        if self.busy:
            self.busy -= 1
            return Result.BUSY
        self._opened()
        self.seq += 1
        if self.fail_set:
            self.text, self.extra = "", None
            return Result.FAILED
        self.text, self.extra, self.private = text, None, private
        self.history.append(history)
        self.writes.append(("set", text, history, private))
        return Result.OK

    def snapshot(self):
        if self.snapshot_fails:
            return None
        return FakeSnapshot(self.text, self.extra, self.private)

    def restore(self, snap, history=True, expected_seq=None):
        self.restore_calls.append(expected_seq)
        if self.restore_busy:
            self.restore_busy -= 1
            return Result.BUSY
        self._opened()
        if expected_seq is not None and self.seq != expected_seq:
            return Result.SKIPPED
        self.text, self.extra, self.private = snap.text, snap.extra, snap.private
        self.seq += 1
        self.history.append(history)
        self.writes.append(("restore", snap.text, history, snap.private))
        return Result.OK

    def sequence_number(self):
        return self.seq


class FakeForeground:
    elevated = False

    def foreground_is_elevated(self):
        return self.elevated


class FakeKeyboard:
    def __init__(self, clip, selection=""):
        self.clip = clip
        self.selection = selection  # None = the app ignores Ctrl+C
        self.pasted = []

    def send_paste(self):
        self.pasted.append(self.clip.text)

    def send_copy(self):
        if self.selection is not None:
            self.clip.set_text(self.selection)


def wait_until(cond, timeout=3.0):
    deadline = real_time.monotonic() + timeout
    while not cond() and real_time.monotonic() < deadline:
        pass  # busy-wait; time.sleep is patched out in the module
    return cond()


class FeatureTestBase(unittest.TestCase):
    def setUp(self):
        self.clip = FakeClipboard()
        self.kb = FakeKeyboard(self.clip)
        self.fg = FakeForeground()
        self._orig = (features_mod.clipboard, features_mod.keyboard_send,
                      features_mod.foreground, features_mod.time.sleep)
        features_mod.clipboard = self.clip
        features_mod.keyboard_send = self.kb
        features_mod.foreground = self.fg
        self.sleeps = []
        features_mod.time.sleep = self.sleeps.append
        self.cfg = Config(rules=[Rule(find="Z1", replace="Z2"),
                                 Rule(find="Z2", replace="Z3")])
        self.cfg.restore_delay_ms = 60_000  # tests trigger restores explicitly
        self.notes = []
        self.f = features_mod.Features(
            self.cfg, notify=lambda t, m, w=False: self.notes.append((t, w)))
        self.f.RESTORE_RETRY_DELAY = 0.01

    def tearDown(self):
        self.f.flush_restore()
        (features_mod.clipboard, features_mod.keyboard_send,
         features_mod.foreground, features_mod.time.sleep) = self._orig

    def copy(self, text):
        """Simulate the user pressing Ctrl+C."""
        self.clip.set_text(text)

    def titles(self):
        return [t for t, _w in self.notes]


class TestChain(FeatureTestBase):
    def test_chain_off_repeats_same_paste(self):
        self.copy("Z1Status")
        for _ in range(3):
            self.f.replace_paste()
        self.f.flush_restore()
        self.assertEqual(self.kb.pasted, ["Z2Status"] * 3)
        self.assertEqual(self.clip.text, "Z1Status")  # clipboard restored

    def test_chain_steps_through_rules_then_stays(self):
        # Presses before the timer: each reuses the pending snapshot.
        self.cfg.chain_paste = True
        self.copy("Z1Status")
        for _ in range(4):
            self.f.replace_paste()
        self.f.flush_restore()
        self.assertEqual(self.kb.pasted,
                         ["Z2Status", "Z3Status", "Z3Status", "Z3Status"])
        self.assertEqual(self.clip.text, "Z1Status")

    def test_chain_continues_after_timer_restore(self):
        # The background restore fires between presses (the normal case).
        self.cfg.chain_paste = True
        self.copy("Z1Status")
        self.f.replace_paste()
        self.f.flush_restore()
        self.assertEqual(self.clip.text, "Z1Status")
        self.f.replace_paste()
        self.assertEqual(self.kb.pasted, ["Z2Status", "Z3Status"])

    def test_chain_mixes_timer_restores_and_reused_snapshots(self):
        self.cfg.chain_paste = True
        self.cfg.rules.append(Rule(find="Z3", replace="Z4"))
        self.copy("Z1Status")
        self.f.replace_paste()   # Z2
        self.f.flush_restore()   # timer restore
        self.f.replace_paste()   # Z3, before its timer
        self.f.replace_paste()   # Z4, reusing the pending snapshot
        self.f.flush_restore()
        self.assertEqual(self.kb.pasted, ["Z2Status", "Z3Status", "Z4Status"])
        self.assertEqual(self.clip.text, "Z1Status")

    def test_chain_with_real_timer(self):
        self.cfg.chain_paste = True
        self.cfg.restore_delay_ms = 100
        self.copy("Z1Status")
        self.f.replace_paste()
        self.assertTrue(wait_until(lambda: self.clip.text == "Z1Status"))
        self.cfg.restore_delay_ms = 60_000
        self.f.replace_paste()   # after the timer restore
        self.f.replace_paste()   # before the timer
        self.assertEqual(self.kb.pasted, ["Z2Status", "Z3Status", "Z3Status"])
        self.f.flush_restore()
        self.assertEqual(self.clip.text, "Z1Status")

    def test_new_copy_restarts_chain(self):
        self.cfg.chain_paste = True
        self.copy("Z1Status")
        self.f.replace_paste()
        self.f.replace_paste()
        self.f.flush_restore()
        self.copy("Z1Status")  # same text copied again still restarts
        self.f.replace_paste()
        self.assertEqual(self.kb.pasted, ["Z2Status", "Z3Status", "Z2Status"])

    def test_backup_paste_in_between_restarts_chain(self):
        self.cfg.chain_paste = True
        self.cfg.palette[0] = "B"
        self.copy("Z1Status")
        self.f.replace_paste()
        self.f.backup_paste()
        self.f.replace_paste()
        self.assertEqual(self.kb.pasted, ["Z2Status", "B", "Z2Status"])

    def test_rules_are_copied_once_per_action(self):
        seen = []
        orig = features_mod.apply_rules_counted

        def spy(text, rules):
            seen.append(rules)
            return orig(text, rules)

        features_mod.apply_rules_counted = spy
        try:
            self.copy("Z1Status")
            self.f.replace_paste()
        finally:
            features_mod.apply_rules_counted = orig
        self.assertEqual(len(seen), 1)
        self.assertIsNot(seen[0], self.cfg.rules)
        self.assertEqual(seen[0], self.cfg.rules)


class TestBackToBack(FeatureTestBase):
    """Two hotkey presses queued before the first restore timer fires."""

    def test_second_press_does_not_restore_between_pastes(self):
        self.copy("Z1Status")
        self.clip.writes.clear()
        self.f.replace_paste()
        self.f.replace_paste()
        # The original was NOT written back between the two pastes, so the
        # target app can't read it for the first Ctrl+V.
        self.assertEqual([w[:2] for w in self.clip.writes],
                         [("set", "Z2Status"), ("set", "Z2Status")])
        self.f.flush_restore()
        self.assertEqual(self.clip.writes[-1][:2], ("restore", "Z1Status"))
        self.assertEqual(self.kb.pasted, ["Z2Status", "Z2Status"])
        self.assertEqual(self.clip.text, "Z1Status")

    def test_original_kept_through_different_actions(self):
        self.copy("Z1Status")
        self.clip.extra = "an image"
        self.cfg.palette[0] = "B"
        self.f.replace_paste()
        self.f.backup_paste()
        self.f.replace_paste()  # uses the snapshot, not the pasted "B"
        self.f.flush_restore()
        self.assertEqual(self.kb.pasted, ["Z2Status", "B", "Z2Status"])
        self.assertEqual((self.clip.text, self.clip.extra), ("Z1Status", "an image"))

    def test_minimum_gap_between_paste_and_next_write(self):
        self.copy("Z1Status")
        self.f.replace_paste()
        self.assertNotIn(True, [s > 0.1 for s in self.sleeps])  # first press: no wait
        self.sleeps.clear()
        self.f.replace_paste()
        gap = [s for s in self.sleeps if s > 0.1]
        self.assertEqual(len(gap), 1)
        self.assertGreater(gap[0], 0.3)
        self.assertLessEqual(gap[0], 0.4)

    def test_gap_is_capped_by_restore_delay(self):
        self.cfg.restore_delay_ms = 150
        self.copy("Z1Status")
        self.f.replace_paste()
        self.sleeps.clear()
        self.f.replace_paste()
        self.assertTrue(self.sleeps and max(self.sleeps) <= 0.15)

    def test_user_copy_between_presses_drops_pending_restore(self):
        self.copy("Z1Status")
        self.f.replace_paste()
        self.copy("new Z1")
        self.f.replace_paste()
        self.assertEqual(self.kb.pasted, ["Z2Status", "new Z2"])
        self.f.flush_restore()
        self.assertEqual(self.clip.text, "new Z1")
        self.assertNotIn(("restore", "Z1Status"), [w[:2] for w in self.clip.writes])

    def test_backup_copy_after_paste_restores_original(self):
        self.copy("Z1Status")
        self.f.replace_paste()
        self.kb.selection = "selected"
        self.f.backup_copy()
        self.assertEqual(self.cfg.palette[0], "selected")
        self.assertEqual(self.clip.text, "Z1Status")

    def test_aborted_action_still_restores_reused_snapshot(self):
        self.clip.text, self.clip.extra = "", "an image"  # no text on clipboard
        self.cfg.palette[0] = "B"
        self.f.backup_paste()
        self.f.replace_paste()  # "Nothing to paste"
        self.assertEqual(self.titles()[-1], "Nothing to paste")
        self.f.flush_restore()
        self.assertEqual((self.clip.text, self.clip.extra), ("", "an image"))


class TestRestore(FeatureTestBase):
    def test_restore_waits_for_flush_and_keeps_other_formats(self):
        self.copy("Z1Status")
        self.clip.extra = "an image"
        self.f.replace_paste()
        self.assertEqual(self.clip.text, "Z2Status")  # pasted text still there
        self.f.flush_restore()
        self.assertEqual((self.clip.text, self.clip.extra), ("Z1Status", "an image"))

    def test_user_copy_during_delay_is_not_overwritten(self):
        self.copy("Z1Status")
        self.f.replace_paste()
        self.copy("something new")
        self.f.flush_restore()
        self.assertEqual(self.clip.text, "something new")

    def test_user_copy_while_restore_opens_clipboard_is_kept(self):
        # The copy lands after the restore started but before it holds the
        # clipboard; the restore must compare against our_seq at that point.
        self.copy("Z1Status")
        self.f.replace_paste()
        our_seq = self.clip.seq
        self.clip.on_open = lambda: self.copy("copied meanwhile")
        self.f.flush_restore()
        self.assertEqual(self.clip.restore_calls, [our_seq])
        self.assertEqual(self.clip.text, "copied meanwhile")
        self.assertNotIn("Couldn't restore your clipboard", self.titles())

    def test_restore_is_kept_out_of_history(self):
        self.copy("Z1Status")
        self.clip.history.clear()
        self.f.replace_paste()
        self.f.flush_restore()
        self.assertEqual(self.clip.history, [True, False])  # paste, restore
        self.cfg.paste_results_in_history = False
        self.clip.history.clear()
        self.f.replace_paste()
        self.f.flush_restore()
        self.assertEqual(self.clip.history, [False, False])

    def test_elevated_target_leaves_result_on_clipboard(self):
        self.fg.elevated = True
        self.copy("Z1Status")
        self.f.replace_paste()
        self.f.flush_restore()
        self.assertEqual(self.kb.pasted, [])
        self.assertEqual(self.clip.text, "Z2Status")
        self.assertTrue(self.notes and self.notes[-1][1])  # shown as a warning

    def test_elevated_target_respects_history_setting(self):
        self.fg.elevated = True
        self.cfg.paste_results_in_history = False
        self.copy("Z1Status")
        self.f.replace_paste()
        self.assertEqual(self.clip.writes[-1], ("set", "Z2Status", False, False))

    def test_elevated_after_paste_says_original_replaced(self):
        captured = []
        self.f._notify = lambda t, m, w=False: captured.append(m)
        self.copy("Z1Status")
        self.f.replace_paste()
        self.fg.elevated = True
        self.f.replace_paste()
        self.assertIn("original clipboard was replaced", captured[-1])
        self.assertIsNone(self.f._pending)  # nothing left to restore over it
        self.assertEqual(self.clip.text, "Z2Status")

    def test_timer_restores_on_its_own(self):
        self.cfg.restore_delay_ms = 50
        self.copy("Z1Status")
        self.f.replace_paste()
        self.assertTrue(wait_until(lambda: self.clip.text == "Z1Status"))

    def test_stale_timer_token_is_ignored(self):
        self.copy("Z1Status")
        self.f.replace_paste()
        stale = self.f._pending.token
        self.f.replace_paste()  # takes over the pending restore, new token
        self.f.flush_restore(stale)  # what a late first timer would call
        self.assertEqual(self.clip.text, "Z2Status")  # not restored early
        self.assertIsNotNone(self.f._pending)
        self.f.flush_restore(self.f._pending.token)
        self.assertEqual(self.clip.text, "Z1Status")

    def test_busy_restore_is_retried_by_timer(self):
        self.cfg.restore_delay_ms = 50
        self.clip.restore_busy = 2
        self.copy("Z1Status")
        self.f.replace_paste()
        self.assertTrue(wait_until(lambda: self.clip.text == "Z1Status"))
        self.assertEqual(len(self.clip.restore_calls), 3)
        self.assertNotIn("Couldn't restore your clipboard", self.titles())

    def test_busy_restore_gives_up_after_3_retries_and_notifies_once(self):
        self.cfg.restore_delay_ms = 50
        self.clip.restore_busy = 100
        self.copy("Z1Status")
        self.f.replace_paste()
        self.assertTrue(wait_until(
            lambda: "Couldn't restore your clipboard" in self.titles()))
        REAL_SLEEP(0.1)
        self.assertEqual(len(self.clip.restore_calls), 4)
        self.assertEqual(self.titles().count("Couldn't restore your clipboard"), 1)
        self.assertIsNone(self.f._pending)

    def test_direct_flush_retries_in_place(self):
        self.clip.restore_busy = 2
        self.copy("Z1Status")
        self.f.replace_paste()
        self.f.flush_restore()  # e.g. at shutdown
        self.assertEqual(self.clip.text, "Z1Status")
        self.assertEqual(len(self.clip.restore_calls), 3)

    def test_failed_set_text_restores_snapshot_now(self):
        self.copy("Z1Status")
        self.clip.extra = "an image"
        self.clip.fail_set = True
        self.f.replace_paste()
        self.assertEqual(self.kb.pasted, [])
        self.assertEqual((self.clip.text, self.clip.extra), ("Z1Status", "an image"))
        self.assertIsNone(self.f._pending)

    def test_busy_set_text_keeps_pending_restore(self):
        self.copy("Z1Status")
        self.f.replace_paste()
        self.clip.busy = 1
        self.f.replace_paste()
        self.assertEqual(self.titles()[-1], "Clipboard busy, try again")
        self.assertEqual(self.kb.pasted, ["Z2Status"])
        self.f.flush_restore()
        self.assertEqual(self.clip.text, "Z1Status")


class TestSnapshotFailure(FeatureTestBase):
    def test_failed_snapshot_aborts_without_touching_clipboard(self):
        self.copy("main")
        self.clip.extra = "an image"
        self.cfg.palette[0] = "B"
        self.kb.selection = "sel"
        self.clip.snapshot_fails = True
        writes = len(self.clip.writes)
        for action in (self.f.replace_paste, self.f.backup_paste, self.f.backup_copy):
            action()
            self.assertEqual(self.titles()[-1], "Clipboard busy, try again")
        self.f.flush_restore()
        self.assertEqual(len(self.clip.writes), writes)
        self.assertEqual(self.kb.pasted, [])
        self.assertEqual(self.cfg.palette[0], "B")
        self.assertEqual((self.clip.text, self.clip.extra), ("main", "an image"))


class TestPrivacy(FeatureTestBase):
    def test_private_source_result_not_in_history(self):
        self.copy("Z1secret")
        self.clip.private = True  # e.g. a password manager's copy
        self.f.replace_paste()
        self.assertEqual(self.clip.writes[-1], ("set", "Z2secret", False, True))
        self.f.flush_restore()
        self.assertEqual(self.clip.writes[-1], ("restore", "Z1secret", False, True))

    def test_private_source_elevated_path(self):
        self.fg.elevated = True
        self.copy("Z1secret")
        self.clip.private = True
        self.f.replace_paste()
        self.assertEqual(self.clip.writes[-1], ("set", "Z2secret", False, True))

    def test_normal_source_keeps_history_setting(self):
        self.copy("Z1Status")
        self.f.replace_paste()
        self.assertEqual(self.clip.writes[-1], ("set", "Z2Status", True, False))


class TestBackupIsPaletteSlot1(FeatureTestBase):
    def test_backup_copy_fills_slot_1_and_restores_clipboard(self):
        self.copy("main")
        self.kb.selection = "selected text"
        self.f.backup_copy()
        self.assertEqual(self.cfg.palette[0], "selected text")
        self.assertEqual(self.clip.text, "main")

    def test_backup_paste_uses_slot_1(self):
        self.copy("main")
        self.cfg.palette[0] = "from palette"
        self.f.backup_paste()
        self.f.flush_restore()
        self.assertEqual(self.kb.pasted, ["from palette"])
        self.assertEqual(self.clip.text, "main")

    def test_empty_slot_1_pastes_nothing(self):
        self.f.backup_paste()
        self.assertEqual(self.kb.pasted, [])

    def test_failed_copy_keeps_old_backup(self):
        self.copy("main clipboard")
        self.cfg.palette[0] = "old backup"
        self.kb.selection = None  # Ctrl+C does nothing
        self.f.backup_copy()
        self.assertEqual(self.cfg.palette[0], "old backup")
        self.assertEqual(self.clip.text, "main clipboard")
        self.assertEqual(self.notes[-1][0], "Nothing copied")


class TestConfig(unittest.TestCase):
    def tearDown(self):
        try:
            os.remove(config_mod.config_path())
        except FileNotFoundError:
            pass

    def test_roundtrip_keeps_palette_2_to_5_but_not_backup(self):
        cfg = Config(chain_paste=True, restore_delay_ms=1200,
                     paste_results_in_history=False)
        cfg.palette[:] = ["secret backup", "two", "", "four", "five"]
        cfg.rules.append(Rule(find="a(b)", replace=r"\1", regex=True))
        config_mod.save(cfg)
        loaded = config_mod.load()
        self.assertTrue(loaded.chain_paste)
        self.assertEqual(loaded.restore_delay_ms, 1200)
        self.assertFalse(loaded.paste_results_in_history)
        self.assertEqual(loaded.palette, ["", "two", "", "four", "five"])
        self.assertTrue(loaded.rules[0].regex)
        with open(config_mod.config_path(), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("secret backup", text)
        self.assertNotIn("save_blocked", text)
        self.assertNotIn("load_error", text)

    def test_broken_json_is_backed_up_not_lost(self):
        path = config_mod.config_path()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"rules": [{"find": "keep me"}], oops')
        cfg = config_mod.load()
        self.assertIn("could not be read", cfg.load_error)
        self.assertFalse(cfg.save_blocked)  # the backup exists, saving is fine
        folder = os.path.dirname(path)
        backups = [n for n in os.listdir(folder) if n.startswith("config.json.broken-")]
        self.assertTrue(backups)
        with open(os.path.join(folder, backups[-1]), encoding="utf-8") as fh:
            self.assertIn("keep me", fh.read())

    def test_wrong_types_do_not_crash(self):
        cfg = Config.from_dict({"hotkeys": [], "rules": "x", "restore_delay_ms": "abc"})
        self.assertEqual(cfg.rules, [])
        self.assertEqual(cfg.restore_delay_ms, 800)
        self.assertEqual(Config.from_dict({"restore_delay_ms": 1}).restore_delay_ms, 100)

    def test_infinite_or_nan_delay_uses_default(self):
        for raw in ('{"restore_delay_ms": Infinity}', '{"restore_delay_ms": -Infinity}',
                    '{"restore_delay_ms": NaN}'):
            self.assertEqual(Config.from_dict(json.loads(raw)).restore_delay_ms, 800, raw)

    def test_bad_hotkey_mods_fall_back_to_default(self):
        default = DEFAULT_HOTKEYS["replace_paste"]
        for mods in ("ctrl+shift", None, 5, {"ctrl": 1}, [], ["bogus"], [None, 3],
                     ["shift"]):
            cfg = Config.from_dict({"hotkeys": {"replace_paste": {"mods": mods, "key": "Q"}}})
            self.assertEqual(cfg.hotkeys["replace_paste"], default, repr(mods))
        cfg = Config.from_dict({"hotkeys": {"replace_paste": {"key": "Q"}}})
        self.assertEqual(cfg.hotkeys["replace_paste"], default)

    def test_good_hotkey_mods_are_kept_and_normalised(self):
        cfg = Config.from_dict({"hotkeys": {
            "replace_paste": {"mods": ["Control", "WIN", "bogus", "ctrl"], "key": "Q"},
            "backup_copy": {"mods": ["alt", "shift"], "key": "C"}}})
        self.assertEqual(cfg.hotkeys["replace_paste"], {"mods": ["ctrl", "win"], "key": "Q"})
        self.assertEqual(cfg.hotkeys["backup_copy"]["mods"], ["alt", "shift"])

    def test_booleans_must_be_real_json_booleans(self):
        for raw in ("false", "true", 0, 1, None, [], "yes"):
            cfg = Config.from_dict({"chain_paste": raw, "paste_results_in_history": raw})
            self.assertFalse(cfg.chain_paste, repr(raw))
            self.assertTrue(cfg.paste_results_in_history, repr(raw))
        cfg = Config.from_dict({"chain_paste": True, "paste_results_in_history": False})
        self.assertTrue(cfg.chain_paste)
        self.assertFalse(cfg.paste_results_in_history)

    def test_old_config_without_new_keys(self):
        cfg = Config.from_dict({"rules": [{"find": "a", "replace": "b"}]})
        self.assertFalse(cfg.chain_paste)
        self.assertTrue(cfg.paste_results_in_history)
        self.assertEqual(cfg.palette, [""] * PALETTE_SIZE)

    def test_malformed_palette_is_sanitised(self):
        cfg = Config.from_dict({"palette": ["x", 5, None, "y", "z", "extra", "more"]})
        self.assertEqual(cfg.palette, ["", "", "", "y", "z"])
        self.assertEqual(Config.from_dict({"palette": "nope"}).palette, [""] * 5)


class TestConfigLockedFile(unittest.TestCase):
    """config.json held by OneDrive / a virus scanner."""

    def setUp(self):
        self.path = config_mod.config_path()
        config_mod.save(Config(chain_paste=True))
        self.sleeps = []
        self._orig = (config_mod.time.sleep, config_mod.shutil.copy2)
        config_mod.time.sleep = self.sleeps.append
        self.denials = 0

    def tearDown(self):
        config_mod.time.sleep, config_mod.shutil.copy2 = self._orig
        config_mod.__dict__.pop("open", None)
        os.remove(self.path)

    def lock_reads(self, times):
        """The next ``times`` opens of config.json for reading are denied."""
        def fake_open(file, mode="r", *args, **kwargs):
            if file == self.path and "r" in mode and self.denials < times:
                self.denials += 1
                raise PermissionError(13, "locked", file)
            return open(file, mode, *args, **kwargs)
        config_mod.open = fake_open

    def test_brief_lock_is_retried(self):
        self.lock_reads(2)
        cfg = config_mod.load()
        self.assertEqual(cfg.load_error, "")
        self.assertTrue(cfg.chain_paste)
        self.assertEqual(self.sleeps, [0.2, 0.2])

    def test_unreadable_and_no_backup_blocks_save(self):
        self.lock_reads(100)

        def no_copy(*_a, **_k):
            raise PermissionError(13, "locked")

        config_mod.shutil.copy2 = no_copy
        cfg = config_mod.load()
        self.assertIn("could not be read", cfg.load_error)
        self.assertTrue(cfg.save_blocked)
        self.assertEqual(self.denials, 4)  # first try + 3 retries
        cfg.chain_paste = False
        with self.assertRaises(OSError) as ctx:
            config_mod.save(cfg)
        self.assertIn("not overwritten", str(ctx.exception))
        config_mod.__dict__.pop("open", None)
        self.assertTrue(config_mod.load().chain_paste)  # file untouched


if __name__ == "__main__":
    unittest.main(verbosity=2)
