"""Pure find & replace logic for clipboard text.

Design rules (from requirements):

  * No accidental regex.  The user's *find* term and the clipboard text are
    treated as literal strings.  We never interpret the user's input as a
    regular expression, and we never interpret the clipboard content as one.
  * Skip-existing-replace (optional, on by default).  When scanning for the
    find term, ignore any match that sits inside text already matching the
    *replace* term exactly.  This is what stops "Tunnel1" -> "Tunnel12" from
    touching an existing "Tunnel12" (which would make "Tunnel122"); the result
    is "Tunnel12 and Tunnel12".  Because the guard is the replace text itself,
    it only ever matters when the replace value contains the find value -- e.g.
    with find "test1" / replace "X", "X" never appears, so "test1 test12 test1!"
    fully becomes "X X2 X!".  The check honours the rule's case sensitivity, and
    is disabled when the replace term is empty (a deletion, where there is
    nothing to protect).
  * Replacing with an empty string is allowed (i.e. delete the term).

Implementation note: we DO use the ``re`` module internally, but only on a
``re.escape``-d copy of the find term, so none of the user's characters carry
special meaning.  The replacement is supplied as a plain function returning the
literal replacement string, so backslashes / "\\1" style sequences in the
replacement are never interpreted either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class Rule:
    """A single find/replace rule."""

    find: str
    replace: str = ""
    enabled: bool = True
    protect_existing: bool = True
    case_sensitive: bool = False

    def to_dict(self) -> dict:
        return {
            "find": self.find,
            "replace": self.replace,
            "enabled": self.enabled,
            "protect_existing": self.protect_existing,
            "case_sensitive": self.case_sensitive,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        # Accept the older "protect_numbers"/"whole_word" keys from past configs.
        protect = d.get("protect_existing",
                        d.get("protect_numbers", d.get("whole_word", True)))
        return cls(
            find=str(d.get("find", "")),
            replace=str(d.get("replace", "")),
            enabled=bool(d.get("enabled", True)),
            protect_existing=bool(protect),
            case_sensitive=bool(d.get("case_sensitive", False)),
        )


def apply_rule(text: str, rule: Rule) -> str:
    """Apply a single rule to ``text`` and return the result.

    A rule with an empty ``find`` is a no-op (we never want to "match the empty
    string", which would otherwise splice the replacement between every
    character).
    """
    if not rule.enabled or rule.find == "":
        return text

    flags = 0 if rule.case_sensitive else re.IGNORECASE
    find_pat = re.escape(rule.find)

    # Without protection (or when there is no replace text to protect), this is a
    # plain literal find/replace. The replacement is supplied via a function so
    # it is treated 100% literally (no backreference / escape interpretation).
    if not rule.protect_existing or rule.replace == "":
        return re.sub(find_pat, lambda _m: rule.replace, text, flags=flags)

    # Protection on: find the spans already occupied by an exact occurrence of
    # the replace text, and skip any find-match that overlaps one of them.
    protected = [(m.start(), m.end())
                 for m in re.finditer(re.escape(rule.replace), text, flags=flags)]

    def _overlaps_protected(start: int, end: int) -> bool:
        return any(start < pe and end > ps for ps, pe in protected)

    out: List[str] = []
    last = 0
    for m in re.finditer(find_pat, text, flags=flags):
        if _overlaps_protected(m.start(), m.end()):
            continue  # leave this occurrence untouched
        out.append(text[last:m.start()])
        out.append(rule.replace)
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def apply_rules(text: str, rules: List[Rule]) -> str:
    """Apply all enabled rules in a SINGLE simultaneous pass.

    Every rule is matched against the *original* text, and a replacement we emit
    is never fed back into another rule. This is what makes chained rules behave
    as independent steps rather than compounding: with rules "1"->"2" and
    "2"->"3", the text "Num1" becomes "Num2" (not "Num3"), and "Num2" becomes
    "Num3". Scanning left to right, at each position the first enabled rule (in
    list order) whose find term matches there wins; its replacement is emitted
    and scanning resumes after the matched text.

    Each rule's own "protect_existing" guard still applies: a rule will not
    rewrite text that already matches that rule's replace value.
    """
    active = [r for r in rules if r.enabled and r.find != ""]
    if not active:
        return text

    # Precompute, per rule, the spans already occupied by an exact occurrence of
    # that rule's replace text, so the rule won't rewrite its own output form.
    protected: List[List[tuple]] = []
    for r in active:
        if r.protect_existing and r.replace != "":
            flags = 0 if r.case_sensitive else re.IGNORECASE
            spans = [(m.start(), m.end())
                     for m in re.finditer(re.escape(r.replace), text, flags=flags)]
        else:
            spans = []
        protected.append(spans)

    # Lower-cased find terms for case-insensitive comparison (computed once).
    folded = [r.find if r.case_sensitive else r.find.lower() for r in active]

    out: List[str] = []
    pos = 0
    n = len(text)
    while pos < n:
        emitted = False
        for idx, r in enumerate(active):
            flen = len(r.find)
            segment = text[pos:pos + flen]
            if len(segment) < flen:
                continue
            if r.case_sensitive:
                if segment != r.find:
                    continue
            elif segment.lower() != folded[idx]:
                continue
            end = pos + flen
            if any(pos < pe and end > ps for ps, pe in protected[idx]):
                continue  # this rule leaves an existing replacement untouched
            out.append(r.replace)
            pos = end
            emitted = True
            break
        if not emitted:
            out.append(text[pos])
            pos += 1
    return "".join(out)
