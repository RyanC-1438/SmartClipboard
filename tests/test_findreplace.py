"""Unit tests for the pure find & replace logic (no Windows APIs touched)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smartclipboard.findreplace import Rule, apply_rule, apply_rules, rule_error


class TestFindReplace(unittest.TestCase):
    def test_basic_replace(self):
        r = Rule(find="cat", replace="dog", protect_existing=False)
        self.assertEqual(apply_rule("a cat sat", r), "a dog sat")

    def test_no_regex_interpretation_in_find(self):
        # The find term contains regex metacharacters; they must be literal.
        r = Rule(find="a.b", replace="X", protect_existing=False)
        self.assertEqual(apply_rule("a.b axb aXb", r), "X axb aXb")

    def test_no_regex_interpretation_in_replace(self):
        # Backreference-like text in the replacement must stay literal.
        r = Rule(find="x", replace=r"\1\g<0>$&", protect_existing=False)
        self.assertEqual(apply_rule("x", r), r"\1\g<0>$&")

    # --- the headline "skip existing replace text" behaviour ---
    def test_tunnel_leaves_existing_replace_text(self):
        # Tunnel1 -> Tunnel12 must not touch an existing Tunnel12.
        r = Rule(find="Tunnel1", replace="Tunnel12", protect_existing=True)
        self.assertEqual(apply_rule("Tunnel1 and Tunnel12", r), "Tunnel12 and Tunnel12")

    def test_no_runaway(self):
        r = Rule(find="Tunnel1", replace="Tunnel12", protect_existing=True)
        self.assertNotIn("Tunnel122", apply_rule("Tunnel12", r))

    def test_replace_text_absent_means_no_protection(self):
        # With replace "X", X never appears, so every test1 is replaced -> X X2 X!
        r = Rule(find="test1", replace="X", protect_existing=True)
        self.assertEqual(apply_rule("test1 test12 test1!", r), "X X2 X!")

    def test_matches_inside_word_between_letters(self):
        # No occurrence of the replace text here, so the match still happens.
        r = Rule(find="test1", replace="test12", protect_existing=True)
        self.assertEqual(apply_rule("thisisatest1ABC", r), "thisisatest12ABC")

    def test_protection_only_skips_overlapping_occurrences(self):
        # The standalone test1 is replaced; the one inside test12 is left alone.
        r = Rule(find="test1", replace="test12", protect_existing=True)
        self.assertEqual(apply_rule("test1 test12 test1", r),
                         "test12 test12 test12")

    def test_protect_off_matches_substring(self):
        r = Rule(find="test1", replace="X", protect_existing=False)
        self.assertEqual(apply_rule("test12", r), "X2")

    def test_protection_honors_case_insensitive(self):
        # Case-insensitive: an existing 'tunnel12' also counts as replace text.
        r = Rule(find="Tunnel1", replace="Tunnel12", protect_existing=True,
                 case_sensitive=False)
        self.assertEqual(apply_rule("Tunnel1 and tunnel12", r),
                         "Tunnel12 and tunnel12")

    def test_replace_with_nothing_deletes(self):
        # Empty replace -> protection is moot; deletion works.
        r = Rule(find="REMOVE_ME", replace="", protect_existing=True)
        self.assertEqual(apply_rule("keep REMOVE_ME keep", r), "keep  keep")

    def test_empty_find_is_noop(self):
        r = Rule(find="", replace="X")
        self.assertEqual(apply_rule("unchanged", r), "unchanged")

    def test_disabled_rule_is_noop(self):
        r = Rule(find="a", replace="b", enabled=False, protect_existing=False)
        self.assertEqual(apply_rule("aaa", r), "aaa")

    def test_case_insensitive(self):
        r = Rule(find="error", replace="OK", protect_existing=True, case_sensitive=False)
        self.assertEqual(apply_rule("ERROR error Error", r), "OK OK OK")

    def test_ignore_case_is_the_default(self):
        # Default is now case-insensitive.
        r = Rule(find="error", replace="OK", protect_existing=True)
        self.assertEqual(apply_rule("ERROR error", r), "OK OK")
        self.assertFalse(Rule(find="x").case_sensitive)

    def test_explicit_case_sensitive(self):
        r = Rule(find="error", replace="OK", protect_existing=True, case_sensitive=True)
        self.assertEqual(apply_rule("ERROR error", r), "ERROR OK")

    def test_legacy_keys_still_load(self):
        # Old configs used "whole_word"/"protect_numbers"; map onto protect_existing.
        r1 = Rule.from_dict({"find": "a", "replace": "b", "whole_word": False})
        r2 = Rule.from_dict({"find": "a", "replace": "b", "protect_numbers": False})
        self.assertFalse(r1.protect_existing)
        self.assertFalse(r2.protect_existing)

    def test_rules_do_not_chain(self):
        # Simultaneous single pass: a->b does NOT then feed into b->c.
        rules = [
            Rule(find="a", replace="b", protect_existing=False),
            Rule(find="b", replace="c", protect_existing=False),
        ]
        self.assertEqual(apply_rules("a", rules), "b")
        self.assertEqual(apply_rules("b", rules), "c")

    def test_sequence_num_example(self):
        # The user's scenario: rules 1->2 and 2->3, one paste each.
        rules = [Rule(find="1", replace="2"), Rule(find="2", replace="3")]
        self.assertEqual(apply_rules("Num1", rules), "Num2")
        self.assertEqual(apply_rules("Num2", rules), "Num3")

    def test_sequence_both_present_each_advances_once(self):
        rules = [Rule(find="1", replace="2"), Rule(find="2", replace="3")]
        # In "1 2", the 1 becomes 2 and the (original) 2 becomes 3 -- no chaining.
        self.assertEqual(apply_rules("1 2", rules), "2 3")

    def test_first_matching_rule_wins_at_a_position(self):
        rules = [Rule(find="ab", replace="X"), Rule(find="abc", replace="Y")]
        self.assertEqual(apply_rules("abc", rules), "Xc")

    def test_apply_rules_single_rule_matches_apply_rule(self):
        r = Rule(find="Tunnel1", replace="Tunnel12", protect_existing=True)
        text = "Tunnel1 and Tunnel12"
        self.assertEqual(apply_rules(text, [r]), apply_rule(text, r))

    def test_multiline_text(self):
        r = Rule(find="foo", replace="bar", protect_existing=True)
        self.assertEqual(apply_rule("foo\nfoo qux\nfoofoo", r), "bar\nbar qux\nbarbar")


class TestRegexRules(unittest.TestCase):
    def test_regex_off_by_default(self):
        self.assertFalse(Rule(find="x").regex)
        self.assertEqual(apply_rules("a.b axb", [Rule(find="a.b", replace="X")]), "X axb")

    def test_regex_with_group(self):
        r = Rule(find=r"Z(\d+)Status", replace=r"Zone\1", regex=True)
        self.assertEqual(apply_rules("Z1Status Z12Status Zstatus", [r]),
                         "Zone1 Zone12 Zstatus")

    def test_regex_case_insensitive_by_default(self):
        r = Rule(find=r"err(or)?", replace="OK", regex=True)
        self.assertEqual(apply_rules("ERROR Err", [r]), "OK OK")

    def test_regex_case_sensitive(self):
        r = Rule(find=r"err", replace="OK", regex=True, case_sensitive=True)
        self.assertEqual(apply_rules("ERR err", [r]), "ERR OK")

    def test_regex_line_anchors(self):
        r = Rule(find="^", replace="// ", regex=True)
        self.assertEqual(apply_rules("a\nb", [r]), "// a\n// b")
        r2 = Rule(find="$", replace=";", regex=True)
        self.assertEqual(apply_rules("a\nb", [r2]), "a;\nb;")

    def test_regex_empty_matches_match_re_sub(self):
        import re
        r = Rule(find="x*", replace="-", regex=True)
        self.assertEqual(apply_rules("abxd", [r]), re.sub("x*", "-", "abxd"))

    def test_regex_named_group_and_newline_escape(self):
        r = Rule(find=r"(?P<k>\w+)=(?P<v>\w+)", replace=r"\g<v>\n\g<k>", regex=True)
        self.assertEqual(apply_rules("a=b", [r]), "b\na")

    def test_regex_ignores_protect_existing(self):
        r = Rule(find="T1", replace="T12", regex=True, protect_existing=True)
        self.assertEqual(apply_rules("T12", [r]), "T122")

    def test_invalid_regex_is_skipped_and_reported(self):
        bad = Rule(find="(", replace="x", regex=True)
        good = Rule(find="a", replace="b")
        self.assertEqual(apply_rules("(a", [bad, good]), "(b")
        self.assertIsNotNone(rule_error(bad))

    def test_invalid_template_is_skipped_and_reported(self):
        for repl in (r"\2", r"\g<nope>", r"\d"):
            r = Rule(find="(a)", replace=repl, regex=True)
            self.assertEqual(apply_rules("a", [r]), "a")
            self.assertIsNotNone(rule_error(r), repl)

    def test_valid_rules_report_no_error(self):
        self.assertIsNone(rule_error(Rule(find="(", replace=r"\1")))  # literal
        self.assertIsNone(rule_error(Rule(find=r"(a)", replace=r"\1", regex=True)))

    def test_regex_and_literal_rules_mix_in_one_pass(self):
        rules = [Rule(find="1", replace="2"),
                 Rule(find=r"Z(\d)", replace=r"Y\1", regex=True)]
        # At position 0 only the regex rule matches; its output is not re-fed.
        self.assertEqual(apply_rules("Z1 1", rules), "Y1 2")

    def test_regex_roundtrips_through_dict(self):
        r = Rule(find="a+", replace="b", regex=True)
        self.assertTrue(Rule.from_dict(r.to_dict()).regex)
        self.assertFalse(Rule.from_dict({"find": "a"}).regex)

    def test_large_text_is_fast(self):
        import time
        text = "Z1Status foo bar " * 20000
        rules = [Rule(find=f"x{i}", replace="y") for i in range(30)]
        rules.append(Rule(find="Z1", replace="Z2"))
        t = time.perf_counter()
        out = apply_rules(text, rules)
        self.assertLess(time.perf_counter() - t, 2.0)
        self.assertEqual(out.count("Z2Status"), 20000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
