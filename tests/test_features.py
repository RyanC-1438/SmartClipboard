"""Tests for chain mode, clipboard restore, the palette/backup slot and config
persistence.

Windows clipboard, keystroke and foreground-window calls are replaced with
fakes, and APPDATA is pointed at a temp folder so the real config.json is never
touched.
"""

import os
import sys
import tempfile
import time as real_time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="sc_test_")

from smartclipboard import config as config_mod
from smartclipboard import features as features_mod
from smartclipboard.config import PALETTE_SIZE, Config
from smartclipboard.findreplace import Rule


class FakeSnapshot:
    def __init__(self, text, extra):
        self.text = text
        self.extra = extra


class FakeClipboard:
    """Text plus an opaque 'extra' standing in for non-text formats."""

    def __init__(self, text=""):
        self.text = text
        self.extra = None
        self.seq = 1
        self.history = []  # history flag of every write

    def get_text(self):
        return self.text

    def set_text(self, text, history=True):
        self.text = text
        self.extra = None
        self.seq += 1
        self.history.append(history)
        return True

    def snapshot(self):
        return FakeSnapshot(self.text, self.extra)

    def restore(self, snap, history=True):
        self.text, self.extra = snap.text, snap.extra
        self.seq += 1
        self.history.append(history)
        return True

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
        features_mod.time.sleep = lambda _s: None
        self.cfg = Config(rules=[Rule(find="Z1", replace="Z2"),
                                 Rule(find="Z2", replace="Z3")])
        self.cfg.restore_delay_ms = 60_000  # tests trigger restores explicitly
        self.notes = []
        self.f = features_mod.Features(
            self.cfg, notify=lambda t, m, w=False: self.notes.append((t, w)))

    def tearDown(self):
        self.f.flush_restore()
        (features_mod.clipboard, features_mod.keyboard_send,
         features_mod.foreground, features_mod.time.sleep) = self._orig

    def copy(self, text):
        """Simulate the user pressing Ctrl+C."""
        self.clip.set_text(text)


class TestChain(FeatureTestBase):
    def test_chain_off_repeats_same_paste(self):
        self.copy("Z1Status")
        for _ in range(3):
            self.f.replace_paste()
        self.f.flush_restore()
        self.assertEqual(self.kb.pasted, ["Z2Status"] * 3)
        self.assertEqual(self.clip.text, "Z1Status")  # clipboard restored

    def test_chain_steps_through_rules_then_stays(self):
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

    def test_timer_restores_on_its_own(self):
        self.cfg.restore_delay_ms = 50
        self.copy("Z1Status")
        self.f.replace_paste()
        deadline = real_time.monotonic() + 2
        while self.clip.text != "Z1Status" and real_time.monotonic() < deadline:
            pass  # busy-wait; time.sleep is patched out in the module
        self.assertEqual(self.clip.text, "Z1Status")


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
            self.assertNotIn("secret backup", fh.read())

    def test_broken_json_is_backed_up_not_lost(self):
        path = config_mod.config_path()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"rules": [{"find": "keep me"}], oops')
        cfg = config_mod.load()
        self.assertIn("could not be read", cfg.load_error)
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

    def test_old_config_without_new_keys(self):
        cfg = Config.from_dict({"rules": [{"find": "a", "replace": "b"}]})
        self.assertFalse(cfg.chain_paste)
        self.assertTrue(cfg.paste_results_in_history)
        self.assertEqual(cfg.palette, [""] * PALETTE_SIZE)

    def test_malformed_palette_is_sanitised(self):
        cfg = Config.from_dict({"palette": ["x", 5, None, "y", "z", "extra", "more"]})
        self.assertEqual(cfg.palette, ["", "", "", "y", "z"])
        self.assertEqual(Config.from_dict({"palette": "nope"}).palette, [""] * 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
