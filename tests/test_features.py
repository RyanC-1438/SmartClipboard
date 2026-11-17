"""Tests for chain mode, the palette/backup slot and config persistence.

Windows clipboard and keystroke calls are replaced with fakes, and APPDATA is
pointed at a temp folder so the real config.json is never touched.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="sc_test_")

from smartclipboard import config as config_mod
from smartclipboard import features as features_mod
from smartclipboard.config import PALETTE_SIZE, Config
from smartclipboard.findreplace import Rule


class FakeClipboard:
    def __init__(self, text=""):
        self.text = text
        self.seq = 1

    def get_text(self):
        return self.text

    def set_text(self, text):
        self.text = text
        self.seq += 1
        return True

    def sequence_number(self):
        return self.seq


class FakeKeyboard:
    def __init__(self, clip, selection=""):
        self.clip = clip
        self.selection = selection
        self.pasted = []

    def send_paste(self):
        self.pasted.append(self.clip.text)

    def send_copy(self):
        self.clip.set_text(self.selection)


class FeatureTestBase(unittest.TestCase):
    def setUp(self):
        self.clip = FakeClipboard()
        self.kb = FakeKeyboard(self.clip)
        self._orig = (features_mod.clipboard, features_mod.keyboard_send,
                      features_mod.time.sleep)
        features_mod.clipboard = self.clip
        features_mod.keyboard_send = self.kb
        features_mod.time.sleep = lambda _s: None
        self.cfg = Config(rules=[Rule(find="Z1", replace="Z2"),
                                 Rule(find="Z2", replace="Z3")])
        self.f = features_mod.Features(self.cfg)

    def tearDown(self):
        (features_mod.clipboard, features_mod.keyboard_send,
         features_mod.time.sleep) = self._orig

    def copy(self, text):
        """Simulate the user pressing Ctrl+C."""
        self.clip.set_text(text)


class TestChain(FeatureTestBase):
    def test_chain_off_repeats_same_paste(self):
        self.copy("Z1Status")
        for _ in range(3):
            self.f.replace_paste()
        self.assertEqual(self.kb.pasted, ["Z2Status"] * 3)
        self.assertEqual(self.clip.text, "Z1Status")  # clipboard restored

    def test_chain_steps_through_rules_then_stays(self):
        self.cfg.chain_paste = True
        self.copy("Z1Status")
        for _ in range(4):
            self.f.replace_paste()
        self.assertEqual(self.kb.pasted,
                         ["Z2Status", "Z3Status", "Z3Status", "Z3Status"])
        self.assertEqual(self.clip.text, "Z1Status")

    def test_new_copy_restarts_chain(self):
        self.cfg.chain_paste = True
        self.copy("Z1Status")
        self.f.replace_paste()
        self.f.replace_paste()
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
        self.assertEqual(self.kb.pasted, ["from palette"])
        self.assertEqual(self.clip.text, "main")

    def test_empty_slot_1_pastes_nothing(self):
        self.f.backup_paste()
        self.assertEqual(self.kb.pasted, [])


class TestConfig(unittest.TestCase):
    def test_roundtrip_keeps_palette_2_to_5_but_not_backup(self):
        cfg = Config(chain_paste=True)
        cfg.palette[:] = ["secret backup", "two", "", "four", "five"]
        cfg.rules.append(Rule(find="a(b)", replace=r"\1", regex=True))
        config_mod.save(cfg)
        loaded = config_mod.load()
        self.assertTrue(loaded.chain_paste)
        self.assertEqual(loaded.palette, ["", "two", "", "four", "five"])
        self.assertTrue(loaded.rules[0].regex)
        with open(config_mod.config_path(), encoding="utf-8") as fh:
            self.assertNotIn("secret backup", fh.read())

    def test_old_config_without_new_keys(self):
        cfg = Config.from_dict({"rules": [{"find": "a", "replace": "b"}]})
        self.assertFalse(cfg.chain_paste)
        self.assertEqual(cfg.palette, [""] * PALETTE_SIZE)

    def test_malformed_palette_is_sanitised(self):
        cfg = Config.from_dict({"palette": ["x", 5, None, "y", "z", "extra", "more"]})
        self.assertEqual(cfg.palette, ["", "", "", "y", "z"])
        self.assertEqual(Config.from_dict({"palette": "nope"}).palette, [""] * 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
