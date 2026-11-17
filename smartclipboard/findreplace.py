"""Pure find & replace logic for clipboard text.

Design rules (from requirements):

  * No accidental regex.  By default a rule's *find* term and the clipboard text
    are treated as literal strings.  A rule is only interpreted as a regular
    expression when the user explicitly ticks its "Regular expression" option.
  * Skip-existing-replace (optional, on by default, literal rules only).  When
    scanning for the find term, ignore any match that sits inside text already
    matching the *replace* term exactly.  This is what stops "Tunnel1" ->
    "Tunnel12" from touching an existing "Tunnel12" (which would make
    "Tunnel122"); the result is "Tunnel12 and Tunnel12".  Because the guard is
    the replace text itself, it only ever matters when the replace value
    contains the find value -- e.g. with find "test1" / replace "X", "X" never
    appears, so "test1 test12 test1!" fully becomes "X X2 X!".  The check
    honours the rule's case sensitivity, and is disabled when the replace term
    is empty (a deletion, where there is nothing to protect).
  * Replacing with an empty string is allowed (i.e. delete the term).

Literal rules: we use the ``re`` module internally, but only on a
``re.escape``-d copy of the find term, so none of the user's characters carry
special meaning, and the replacement is emitted verbatim (backslashes / "\\1"
are never interpreted).

Regex rules: the find term is a Python regular expression compiled with
MULTILINE (so ``^``/``$`` match at every line) and IGNORECASE unless the rule
is case sensitive.  The replacement is a Python template: ``\\1`` or
``\\g<name>`` insert groups, ``\\n`` is a newline.  A rule whose pattern or
template is invalid is skipped (``rule_error`` reports why, for the GUI).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Pattern


@dataclass
class Rule:
    """A single find/replace rule."""

    find: str
    replace: str = ""
    enabled: bool = True
    protect_existing: bool = True
    case_sensitive: bool = False
    regex: bool = False

    def to_dict(self) -> dict:
        return {
            "find": self.find,
            "replace": self.replace,
            "enabled": self.enabled,
            "protect_existing": self.protect_existing,
            "case_sensitive": self.case_sensitive,
            "regex": self.regex,
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
            regex=bool(d.get("regex", False)),
        )


def compile_rule(rule: Rule) -> Pattern:
    """Compile a rule's find term. Raises ``re.error`` for a bad regex, or for a
    regex rule whose replacement template is invalid."""
    flags = 0 if rule.case_sensitive else re.IGNORECASE
    if not rule.regex:
        return re.compile(re.escape(rule.find), flags)
    pattern = re.compile(rule.find, flags | re.MULTILINE)
    # Substituting into "" forces Python to parse the template up front, which
    # surfaces bad group references / escapes without needing a match.
    try:
        pattern.sub(rule.replace, "")
    except IndexError as exc:  # unknown group *name* raises IndexError
        raise re.error(str(exc)) from None
    return pattern


def rule_error(rule: Rule) -> Optional[str]:
    """Human-readable problem with a rule, or None if it is usable."""
    if not rule.regex or rule.find == "":
        return None
    try:
        compile_rule(rule)
    except re.error as exc:
        return str(exc)
    return None


def apply_rule(text: str, rule: Rule) -> str:
    """Apply a single rule to ``text``. An empty ``find`` is a no-op."""
    return apply_rules(text, [rule])


def apply_rules(text: str, rules: List[Rule]) -> str:
    """Apply all enabled rules in a SINGLE simultaneous pass.

    Every rule is matched against the *original* text, and a replacement we emit
    is never fed back into another rule. This is what makes chained rules behave
    as independent steps rather than compounding: with rules "1"->"2" and
    "2"->"3", the text "Num1" becomes "Num2" (not "Num3"), and "Num2" becomes
    "Num3". Scanning left to right, at each position the first enabled rule (in
    list order) whose find term matches there wins; its replacement is emitted
    and scanning resumes after the matched text.

    Each literal rule's own "protect_existing" guard still applies: a rule will
    not rewrite text that already matches that rule's replace value.
    """
    active = []  # (rule, compiled pattern, protected spans)
    for r in rules:
        if not r.enabled or r.find == "":
            continue
        try:
            pat = compile_rule(r)
        except re.error:
            continue  # invalid regex rule: skip it rather than fail the paste
        protected = []
        if not r.regex and r.protect_existing and r.replace != "":
            flags = 0 if r.case_sensitive else re.IGNORECASE
            protected = [(m.start(), m.end())
                         for m in re.finditer(re.escape(r.replace), text, flags=flags)]
        active.append((r, pat, protected))
    if not active:
        return text

    n = len(text)

    def next_match(i: int, start: int):
        """First match of rule ``i`` at or after ``start`` that is not inside
        one of that rule's protected spans; False when there are no more."""
        _rule, pat, protected = active[i]
        p = start
        while p <= n:
            m = pat.search(text, p)
            if m is None:
                return False
            s, e = m.span()
            if not any(s < pe and e > ps for ps, pe in protected):
                return m
            p = s + 1
        return False

    # Per-rule cache of its next acceptable match (None = not yet searched,
    # False = exhausted). A cached match stays valid while it starts at or after
    # the scan position, so each rule is searched only O(matches) times.
    cache: list = [None] * len(active)
    out: List[str] = []
    pos = 0
    while pos <= n:
        best = None
        best_i = -1
        for i in range(len(active)):
            m = cache[i]
            if m is False:
                continue
            if m is None or m.start() < pos:
                m = cache[i] = next_match(i, pos)
                if m is False:
                    continue
            # Strict "<" keeps the earlier rule when two match at one position.
            if best is None or m.start() < best.start():
                best, best_i = m, i
        if best is None:
            break
        s, e = best.span()
        rule = active[best_i][0]
        out.append(text[pos:s])
        out.append(best.expand(rule.replace) if rule.regex else rule.replace)
        if e == s:
            # Zero-width regex match (e.g. "^"): keep the next character and
            # step past it so the scan always advances.
            if s < n:
                out.append(text[s])
            pos = s + 1
        else:
            pos = e
    out.append(text[pos:])
    return "".join(out)
