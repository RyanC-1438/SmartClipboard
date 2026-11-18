"""Tkinter settings window.

Runs on the main thread. The background hotkey/tray thread asks it to appear by
pushing onto a queue that the Tk event loop polls.

Layout, top to bottom:
  * Palette: 5 quick clipboard slots. Left-click copies a slot to the clipboard,
    right-click stores the clipboard in it, middle-click clears it. Slot 1 is
    the backup clipboard used by Ctrl+Shift+Win+C / V.
  * Rules table: Find and Replace are edited inline; each row has an On/Off
    toggle, a swap-direction button, move up/down, and an Options popup.
  * Rule tools (add, swap all, preview) and the chain toggle.
  * General: hotkeys (editable), start with Windows, notifications, restore
    delay.
Every change auto-saves.

The window never reads the clipboard on its own: only a palette right-click
does, and the Preview box only shows text you type or paste into it.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional

from . import clipboard
from . import config as config_mod
from . import hotkeys as hotkeys_mod
from . import startup
from .config import PALETTE_SIZE, Config
from .findreplace import Rule, apply_rules_spans, rule_error

_ON_BG = "#2e7d32"
_OFF_BG = "#9e9e9e"
_MUTED = "#666"
_STATUS_IDLE = "Auto-saved"

_HOTKEY_LABELS = {
    "backup_copy": "Copy selection to backup clipboard (palette slot 1)",
    "backup_paste": "Paste from backup clipboard (palette slot 1)",
    "replace_paste": "Paste with find & replace",
    "open_settings": "Open this Settings window",
}
_NOTIFY_CHOICES = {"all": "Every paste", "problems": "Problems only", "off": "Off"}

Rebind = Callable[[Dict[str, Dict]], list]


class Tooltip:
    """Minimal hover tooltip. ``text_fn`` is called each time it appears, so the
    tip can describe live state; an empty string means "no tooltip"."""

    def __init__(self, widget: tk.Widget, text_fn: Callable[[], str]) -> None:
        self.widget = widget
        self.text_fn = text_fn
        self._tip: tk.Toplevel | None = None
        self._after: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None) -> None:
        self._cancel()
        self._after = self.widget.after(450, self._show)

    def _cancel(self) -> None:
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self) -> None:
        self._after = None
        text = self.text_fn()
        if not text:
            return
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        # Topmost, or it opens behind the Settings window when that window is
        # pinned "always on top" (e.g. with PowerToys) and is never seen.
        tw.wm_attributes("-topmost", True)
        tk.Label(tw, text=text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, font=("Consolas", 9),
                 wraplength=640, padx=4, pady=2).pack()
        # Below the widget, kept on screen (above it if there's no room).
        tw.update_idletasks()
        w, h = tw.winfo_reqwidth(), tw.winfo_reqheight()
        sw, sh = tw.winfo_screenwidth(), tw.winfo_screenheight()
        x = min(self.widget.winfo_rootx() + 8, sw - w - 4)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        if y + h > sh:
            y = max(0, self.widget.winfo_rooty() - h - 4)
        tw.wm_geometry(f"+{max(0, x)}+{y}")

    def _hide(self, _e=None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def _flatten(text: str) -> str:
    """Whitespace collapsed, newlines shown as ⏎."""
    flat = " ⏎ ".join(line.strip() for line in text.strip().splitlines())
    return " ".join(flat.split())


def _fit_lines(text: str, font: tkfont.Font, px: int, lines: int = 2) -> str:
    """Break ``text`` (flattened) into at most ``lines`` lines that each fit
    in ``px`` pixels, ending with … if it doesn't all fit. Breaks mid-word, so
    long PLC identifiers still fill both lines."""
    flat = _flatten(text)
    out, pos = [], 0
    for n in range(lines):
        if pos >= len(flat):
            break
        end = pos
        while end < len(flat) and font.measure(flat[pos:end + 1]) <= px:
            end += 1
        end = max(end, pos + 1)
        if n == lines - 1 and end < len(flat):
            while end > pos + 1 and font.measure(flat[pos:end] + "…") > px:
                end -= 1
            out.append(flat[pos:end] + "…")
        else:
            out.append(flat[pos:end])
        pos = end
        while pos < len(flat) and flat[pos] == " ":
            pos += 1  # don't start a line with a space
    return "\n".join(out)


_TIP_MAX_LINES = 40
_TIP_MAX_CHARS = 4000


def _tip_body(text: str) -> str:
    """The full slot text for a tooltip, trimmed only if it's huge."""
    lines = text[:_TIP_MAX_CHARS].splitlines()[:_TIP_MAX_LINES]
    body = "\n".join(lines)
    if body != text.rstrip("\n"):
        rest = len(text.splitlines()) - len(lines)
        body += f"\n… (+{rest} more lines)" if rest > 0 else "…"
    return body


class PaletteBar(ttk.Frame):
    """A row of quick clipboard slots backed by ``cfg.palette``."""

    def __init__(self, parent, cfg: Config, on_change: Callable[[], None]):
        super().__init__(parent)
        self.cfg = cfg
        self.on_change = on_change
        self._shown: List[str | None] = [None] * PALETTE_SIZE
        self._flashing = [False] * PALETTE_SIZE
        self.buttons: List[tk.Button] = []
        self._font = tkfont.Font(family="Segoe UI", size=8)
        for i in range(PALETTE_SIZE):
            self.columnconfigure(i, weight=1, uniform="palette")
            caption = "1 · backup (C/V)" if i == 0 else str(i + 1)
            ttk.Label(self, text=caption, foreground=_MUTED,
                      font=("Segoe UI", 8)).grid(row=0, column=i, sticky="w", padx=2)
            # width=1 so long previews are clipped instead of widening the
            # column; the uniform grid columns share the window width equally.
            # height=2: two lines of preview, filled by _fit_lines.
            btn = tk.Button(self, font=self._font, anchor="w", justify="left",
                            relief="groove", padx=4, pady=2, width=1, height=2,
                            command=lambda i=i: self._copy(i))
            btn.grid(row=1, column=i, sticky="ew", padx=2)
            btn.bind("<Button-3>", lambda _e, i=i: self._store(i))
            btn.bind("<Button-2>", lambda _e, i=i: self._clear(i))
            btn.bind("<Configure>", lambda _e, i=i: self._resized(i))
            Tooltip(btn, lambda i=i: self._tip(i))
            self.buttons.append(btn)
        self.refresh()

    def _resized(self, i: int) -> None:
        self._shown[i] = None  # re-fit the preview to the new width
        self.refresh()

    def _tip(self, i: int) -> str:
        text = self.cfg.palette[i]
        help_line = "Left-click: copy  ·  Right-click: store clipboard  ·  Middle-click: clear"
        if not text:
            return help_line
        return f"{_tip_body(text)}\n\n({len(text)} chars)  {help_line}"

    def _copy(self, i: int) -> None:
        text = self.cfg.palette[i]
        if text and clipboard.set_text(text):
            self._flash(i, "Copied ✓")

    def _store(self, i: int) -> None:
        text = clipboard.get_text()
        if not text:
            self._flash(i, "Clipboard empty")
            return
        self.cfg.palette[i] = text
        self.on_change()
        self._flash(i, "Stored ✓")

    def _clear(self, i: int) -> None:
        self.cfg.palette[i] = ""
        self.on_change()
        self.refresh()

    def _flash(self, i: int, msg: str) -> None:
        self._flashing[i] = True
        self.buttons[i].config(text=msg, fg=_ON_BG)

        def done() -> None:
            self._flashing[i] = False
            self._shown[i] = None  # force a redraw
            self.refresh()
        self.after(700, done)

    def refresh(self) -> None:
        """Redraw any slot whose contents changed (slot 1 changes from the
        hotkey thread, so this is also called from the GUI poll loop)."""
        for i, btn in enumerate(self.buttons):
            text = self.cfg.palette[i]
            if self._flashing[i] or self._shown[i] == text:
                continue
            self._shown[i] = text
            if text:
                px = max(btn.winfo_width() - 14, 40)  # minus padding + border
                btn.config(text=_fit_lines(text, self._font, px), fg="black")
            else:
                btn.config(text="(empty)", fg="#999")


class OptionsDialog(tk.Toplevel):
    """Small popup for the per-rule options that stay out of the table."""

    def __init__(self, parent, rule: Rule, on_ok: Callable[[Rule], None]):
        super().__init__(parent)
        self.title("Rule options")
        self.resizable(False, False)
        self.transient(parent)
        self._rule = rule
        self._on_ok = on_ok
        self._protect = tk.BooleanVar(value=rule.protect_existing)
        self._numbers = tk.BooleanVar(value=rule.keep_numbers_whole)
        self._case = tk.BooleanVar(value=rule.case_sensitive)
        self._regex = tk.BooleanVar(value=rule.regex)

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")
        self._numbers_cb = ttk.Checkbutton(
            frm, variable=self._numbers,
            text="Keep numbers whole\n"
                 "(so 'Z1'→'Z2' changes Z1Status but leaves Z10 and Z12 alone)")
        self._numbers_cb.grid(row=0, column=0, sticky="w")
        self._protect_cb = ttk.Checkbutton(
            frm, variable=self._protect,
            text="Leave text that already matches the Replace value\n"
                 "(so 'Tunnel1'→'Tunnel12' ignores an existing 'Tunnel12')")
        self._protect_cb.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            frm, text="Case sensitive", variable=self._case,
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            frm, text="Regular expression", variable=self._regex,
            command=self._sync,
        ).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Label(
            frm, foreground=_MUTED, justify="left",
            text="Find is a Python regex; ^ and $ match at each line.\n"
                 "In Replace, \\1 or \\g<name> inserts a group, \\n a newline.\n"
                 "e.g.  Find  Z(\\d+)Status   Replace  Zone\\1_Status\n"
                 "The two options above don't apply to regex rules\n"
                 "(use \\b or (?<!\\d) in the pattern instead).",
        ).grid(row=4, column=0, sticky="w", padx=(20, 0), pady=(2, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, pady=(14, 0), sticky="e")
        ttk.Button(btns, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(btns, text="OK", command=self._ok).grid(row=0, column=1)

        self._sync()
        self.grab_set()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())

    def _sync(self) -> None:
        state = ["disabled"] if self._regex.get() else ["!disabled"]
        self._protect_cb.state(state)
        self._numbers_cb.state(state)

    def _ok(self) -> None:
        r = self._rule
        self._on_ok(Rule(find=r.find, replace=r.replace, enabled=r.enabled,
                         protect_existing=self._protect.get(),
                         case_sensitive=self._case.get(), regex=self._regex.get(),
                         keep_numbers_whole=self._numbers.get()))
        self.destroy()


class RuleRow:
    """Widgets and state for one editable rule in the table."""

    def __init__(self, parent, rule: Rule, on_change: Callable[[], None],
                 on_edit: Callable[[], None],
                 on_delete: Callable[["RuleRow"], None],
                 on_options: Callable[["RuleRow"], None],
                 on_move: Callable[["RuleRow", int], None]):
        self.on_change = on_change
        self.find_var = tk.StringVar(value=rule.find)
        self.replace_var = tk.StringVar(value=rule.replace)
        self.enabled = rule.enabled
        self.protect_existing = rule.protect_existing
        self.case_sensitive = rule.case_sensitive
        self.regex = rule.regex
        self.keep_numbers_whole = rule.keep_numbers_whole

        self.toggle = tk.Button(
            parent, width=4, font=("Segoe UI", 10, "bold"),
            relief="raised", command=self._toggle,
        )
        self.find_entry = ttk.Entry(parent, textvariable=self.find_var, width=20)
        self.swap_btn = ttk.Button(parent, text="↔", width=3, command=self.swap)
        self.replace_entry = ttk.Entry(parent, textvariable=self.replace_var, width=20)
        self.opts_btn = ttk.Button(parent, width=9, command=lambda: on_options(self))
        self.up_btn = ttk.Button(parent, text="▲", width=2,
                                 command=lambda: on_move(self, -1))
        self.down_btn = ttk.Button(parent, text="▼", width=2,
                                   command=lambda: on_move(self, 1))
        self.del_btn = ttk.Button(parent, text="✕", width=3,
                                  command=lambda: on_delete(self))
        Tooltip(self.find_entry, lambda: rule_error(self.to_rule()) or "")
        Tooltip(self.swap_btn, lambda: (
            "Swap Find and Replace (reverse this rule)" if self.to_rule().can_swap()
            else "Can't swap a regex rule or one with an empty Replace"))

        for entry in (self.find_entry, self.replace_entry):
            entry.bind("<FocusOut>", lambda _e: self.on_change())
            entry.bind("<Return>", lambda _e: self.on_change())

        def edited(*_a) -> None:  # validate + live preview, no save yet
            self.refresh_style()
            on_edit()
        self.find_var.trace_add("write", edited)
        self.replace_var.trace_add("write", edited)
        self._refresh_toggle()
        self.refresh_style()

    def widgets(self) -> list:
        return [self.toggle, self.find_entry, self.swap_btn, self.replace_entry,
                self.opts_btn, self.up_btn, self.down_btn, self.del_btn]

    def _toggle(self) -> None:
        self.enabled = not self.enabled
        self._refresh_toggle()
        self.on_change()

    def swap(self, commit: bool = True) -> bool:
        if not self.to_rule().can_swap():
            return False
        f, r = self.find_var.get(), self.replace_var.get()
        self.find_var.set(r)
        self.replace_var.set(f)
        if commit:
            self.on_change()
        return True

    def _refresh_toggle(self) -> None:
        if self.enabled:
            self.toggle.config(text="On", bg=_ON_BG, fg="white",
                               activebackground=_ON_BG, activeforeground="white")
        else:
            self.toggle.config(text="Off", bg=_OFF_BG, fg="white",
                               activebackground=_OFF_BG, activeforeground="white")

    def refresh_style(self) -> None:
        """Regex rules use a monospace font; an invalid one shows in red (hover
        the Find box for the error)."""
        font = ("Consolas", 9) if self.regex else ("Segoe UI", 9)
        rule = self.to_rule()
        style = "Bad.TEntry" if rule_error(rule) is not None else "TEntry"
        for entry in (self.find_entry, self.replace_entry):
            entry.configure(font=font, style=style)
        self.opts_btn.configure(text="Regex…" if self.regex else "Options…")
        self.swap_btn.state(["!disabled"] if rule.can_swap() else ["disabled"])

    def grid(self, row: int, first: bool, last: bool) -> None:
        pads = [(0, 6), 4, 0, 4, 4, (4, 0), 0, (4, 0)]
        for col, (w, padx) in enumerate(zip(self.widgets(), pads)):
            w.grid(row=row, column=col, padx=padx, pady=3,
                   sticky="ew" if col in (1, 3) else "w")
        self.up_btn.state(["disabled"] if first else ["!disabled"])
        self.down_btn.state(["disabled"] if last else ["!disabled"])

    def destroy(self) -> None:
        for w in self.widgets():
            w.destroy()

    def to_rule(self) -> Rule:
        return Rule(
            find=self.find_var.get(),
            replace=self.replace_var.get(),
            enabled=self.enabled,
            protect_existing=self.protect_existing,
            case_sensitive=self.case_sensitive,
            regex=self.regex,
            keep_numbers_whole=self.keep_numbers_whole,
        )


class HotkeyDialog(tk.Toplevel):
    """Edit the four hotkey chords with modifier checkboxes + a key list."""

    _MODS = (("ctrl", "Ctrl"), ("shift", "Shift"), ("alt", "Alt"), ("win", "Win"))

    def __init__(self, parent, hotkeys: Dict[str, Dict],
                 on_apply: Callable[[Dict[str, Dict]], bool]):
        super().__init__(parent)
        self.title("Change hotkeys")
        self.resizable(False, False)
        self.transient(parent)
        self._on_apply = on_apply
        self._vars: Dict[str, tuple] = {}

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")
        for row, (action, label) in enumerate(_HOTKEY_LABELS.items()):
            hk = hotkeys.get(action, {})
            mods = {"ctrl" if m == "control" else m for m in hk.get("mods", [])}
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            mod_vars = {}
            for col, (name, text) in enumerate(self._MODS, start=1):
                v = tk.BooleanVar(value=name in mods)
                ttk.Checkbutton(frm, text=text, variable=v).grid(row=row, column=col, padx=2)
                mod_vars[name] = v
            key_var = tk.StringVar(value=str(hk.get("key", "")).upper()
                                   if len(str(hk.get("key", ""))) == 1
                                   else str(hk.get("key", "")).capitalize())
            ttk.Combobox(frm, textvariable=key_var, values=hotkeys_mod.KEY_CHOICES,
                         width=7, state="readonly").grid(row=row, column=5, padx=(6, 0))
            self._vars[action] = (mod_vars, key_var)

        ttk.Label(frm, foreground=_MUTED,
                  text="Tip: keep Ctrl+Shift+Win, which nothing else uses. "
                       "Avoid Win+number and Win+Ctrl+Shift+B.").grid(
            row=10, column=0, columnspan=6, sticky="w", pady=(10, 0))
        btns = ttk.Frame(frm)
        btns.grid(row=11, column=0, columnspan=6, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(btns, text="Apply", command=self._apply).grid(row=0, column=1)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())

    def wanted(self) -> Dict[str, Dict]:
        out = {}
        for action, (mod_vars, key_var) in self._vars.items():
            mods = [name for name, _t in self._MODS if mod_vars[name].get()]
            out[action] = {"mods": mods, "key": key_var.get()}
        return out

    def problems(self, wanted: Dict[str, Dict]) -> List[str]:
        msgs, seen = [], {}
        for action, hk in wanted.items():
            name = _HOTKEY_LABELS[action]
            reason = hotkeys_mod.reserved_reason(hk["mods"], hk["key"])
            if reason:
                msgs.append(f"{name}: {reason}")
            chord = (frozenset(hk["mods"]), hk["key"].lower())
            if chord in seen:
                msgs.append(f"{name}: same chord as \"{_HOTKEY_LABELS[seen[chord]]}\".")
            seen[chord] = action
        return msgs

    def _apply(self) -> None:
        wanted = self.wanted()
        msgs = self.problems(wanted)
        if msgs:
            messagebox.showwarning("Change hotkeys", "\n\n".join(msgs), parent=self)
            return
        if self._on_apply(wanted):
            self.destroy()


class PreviewWindow(tk.Toplevel):
    """Type or paste sample text and see what replace-paste would produce.
    Nothing is read from the clipboard automatically."""

    def __init__(self, parent, rules_fn: Callable[[], List[Rule]],
                 on_close: Callable[[], None]):
        super().__init__(parent)
        self.title("Preview rules")
        self.geometry("560x420")
        self.transient(parent)  # stays above Settings, even when it's pinned
        self.rules_fn = rules_fn
        self._on_close = on_close
        self.protocol("WM_DELETE_WINDOW", self.close)

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Sample text (type or paste with Ctrl+V):").pack(anchor="w")
        self.inp = tk.Text(frm, height=7, wrap="word", font=("Consolas", 9), undo=True)
        self.inp.pack(fill="both", expand=True, pady=(2, 8))
        self.result_label = ttk.Label(frm, text="Result:")
        self.result_label.pack(anchor="w")
        self.out = tk.Text(frm, height=7, wrap="word", font=("Consolas", 9),
                           background="#f6f6f6", state="disabled")
        self.out.tag_configure("hit", background="#fff59d")
        self.out.pack(fill="both", expand=True, pady=(2, 0))
        self.inp.bind("<<Modified>>", self._on_modified)
        self.inp.focus_set()
        self.refresh()

    def _on_modified(self, _e=None) -> None:
        if self.inp.edit_modified():
            self.inp.edit_modified(False)
            self.refresh()

    def refresh(self) -> None:
        text = self.inp.get("1.0", "end-1c")
        out, spans = apply_rules_spans(text, self.rules_fn())
        n = len(spans)
        self.result_label.configure(
            text=f"Result:  {n} replacement{'s' if n != 1 else ''} (highlighted)")
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("1.0", out)
        for a, b in spans:
            if b > a:
                self.out.tag_add("hit", f"1.0+{a}c", f"1.0+{b}c")
        self.out.configure(state="disabled")

    def close(self) -> None:
        self._on_close()
        self.destroy()


class SettingsWindow:
    def __init__(self, root: tk.Tk, cfg: Config, on_save: Callable[[], None],
                 rebind: Optional[Rebind] = None):
        self.root = root
        self.cfg = cfg
        self.on_save = on_save
        self.rebind = rebind
        self.rows: List[RuleRow] = []
        self.preview: Optional[PreviewWindow] = None
        self._status_after: Optional[str] = None
        root.title("SmartClipboard — Settings")
        root.geometry("720x680")
        # Small minimum so the window can be shrunk to just the top part when
        # pinned on top. Everything is packed top-down and Tk squeezes the
        # last-packed widgets first, so shrinking cuts off the General box,
        # then the rule list; the palette and rule tools stay visible.
        root.minsize(560, 150)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        ttk.Style(root).configure("Bad.TEntry", foreground="#c62828")
        root.attributes("-topmost", cfg.always_on_top)

        outer = ttk.Frame(root, padding=(8, 6, 8, 8))
        outer.pack(fill="both", expand=True)

        self.palette = PaletteBar(outer, cfg, on_change=self._save)
        self.palette.pack(fill="x")

        self._build_rule_tools(outer)

        # Scrollable area holding a header row plus one grid row per rule.
        # height=60 keeps its *requested* size small; expand fills the rest.
        canvas_wrap = ttk.Frame(outer)
        canvas_wrap.pack(fill="both", expand=True, pady=(2, 0))
        self.canvas = tk.Canvas(canvas_wrap, highlightthickness=0, height=60)
        vscroll = ttk.Scrollbar(canvas_wrap, orient="vertical",
                                command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.rows_frame = ttk.Frame(self.canvas)
        self._window_id = self.canvas.create_window((0, 0), window=self.rows_frame,
                                                    anchor="nw")
        self.rows_frame.columnconfigure(1, weight=1)
        self.rows_frame.columnconfigure(3, weight=1)
        for col, text in ((0, "On"), (1, "Find"), (3, "Replace")):
            ttk.Label(self.rows_frame, text=text, font=("Segoe UI", 9, "bold")).grid(
                row=0, column=col, sticky="w", padx=4)
        self.rows_frame.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._window_id, width=e.width))
        # Scroll the rules only while the mouse is over them.
        self.canvas.bind("<Enter>", lambda _e: self.canvas.bind_all(
            "<MouseWheel>", self._on_mousewheel))
        self.canvas.bind("<Leave>", lambda _e: self.canvas.unbind_all("<MouseWheel>"))

        self._build_general(outer)
        self._build_rows()

    # --- layout pieces ---
    def _build_rule_tools(self, outer) -> None:
        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="+ Add rule", command=self._add).pack(side="left")
        swap_all = ttk.Button(btns, text="↔ Swap all", command=self._swap_all)
        swap_all.pack(side="left", padx=(6, 0))
        Tooltip(swap_all, lambda: "Reverse every rule (Find ↔ Replace), e.g. to undo "
                "a Z1→Z2 rename.\nRegex rules and rules with an empty Replace are skipped.")
        ttk.Button(btns, text="Preview…", command=self._open_preview).pack(
            side="left", padx=(6, 0))
        self._chain_var = tk.BooleanVar(value=self.cfg.chain_paste)
        chain_cb = ttk.Checkbutton(btns, text="Chain repeated replace-pastes",
                                   variable=self._chain_var, command=self._toggle_chain)
        chain_cb.pack(side="left", padx=(14, 0))
        Tooltip(chain_cb, lambda: (
            "Pressing replace-paste again without copying anything new applies the\n"
            "rules to what was pasted last, one step per press.\n"
            "Rules Z1→Z2 and Z2→Z3, clipboard 'Z1Status':\n"
            "  1st press pastes Z2Status, 2nd pastes Z3Status, then stays there.\n"
            "Copying anything starts over from the clipboard."))
        self._gear = ttk.Button(btns, width=5, command=self._toggle_general)
        self._gear.pack(side="right")
        self._pin_var = tk.BooleanVar(value=self.cfg.always_on_top)
        self._pin_btn = ttk.Button(btns, width=6, command=self._click_pin)
        self._pin_btn.pack(side="right", padx=(0, 4))
        self._refresh_pin_btn()
        Tooltip(self._pin_btn, lambda: "Keep this window on top of other windows.")
        Tooltip(self._gear, lambda: ("Hide" if self.cfg.show_general else "Show")
                + " the General box (hotkeys, start with Windows, notifications,\n"
                "restore delay). Hide it to fit more rules when the window is small.")
        self.status = ttk.Label(btns, text=_STATUS_IDLE, foreground=_MUTED)
        self.status.pack(side="right", padx=(0, 6))
        Tooltip(self.status, lambda: "All rules are applied together in one pass; "
                "when two match at the same spot,\nthe higher rule wins. "
                "Changes save automatically.")

    def _build_general(self, outer) -> None:
        self.general = gen = ttk.LabelFrame(outer, text="General", padding=8)
        gen.columnconfigure(0, weight=1)
        self._apply_general_visibility()
        self.hotkey_label = ttk.Label(gen, font=("Consolas", 9), justify="left")
        self.hotkey_label.grid(row=0, column=0, sticky="w")
        ttk.Button(gen, text="Change hotkeys…", command=self._edit_hotkeys).grid(
            row=0, column=1, sticky="ne")
        self._refresh_hotkey_label()

        opts = ttk.Frame(gen)
        opts.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._startup_var = tk.BooleanVar(value=startup.is_enabled())
        start_cb = ttk.Checkbutton(opts, text="Start with Windows",
                                   variable=self._startup_var, command=self._toggle_startup)
        start_cb.pack(side="left")
        if not startup.available():
            start_cb.state(["disabled"])
            Tooltip(start_cb, lambda: "Only available in the built SmartClipboard.exe.")
        else:
            Tooltip(start_cb, lambda: "Adds SmartClipboard to your own (per-user) startup "
                    "list. No admin needed;\nuntick to remove it again.")

        ttk.Label(opts, text="Notifications:").pack(side="left", padx=(18, 4))
        self._notify_var = tk.StringVar(value=_NOTIFY_CHOICES.get(self.cfg.notify_level,
                                                                   "Every paste"))
        notify_cb = ttk.Combobox(opts, textvariable=self._notify_var, width=13,
                                 state="readonly", values=list(_NOTIFY_CHOICES.values()))
        notify_cb.pack(side="left")
        notify_cb.bind("<<ComboboxSelected>>", lambda _e: self._set_notify())
        Tooltip(notify_cb, lambda: "Every paste: show what was pasted and how many "
                "replacements.\nProblems only: no matches, blocked paste, nothing copied…")

        ttk.Label(opts, text="Restore delay:").pack(side="left", padx=(18, 4))
        self._delay_var = tk.StringVar(value=str(self.cfg.restore_delay_ms))
        delay = ttk.Spinbox(opts, from_=100, to=5000, increment=100, width=6,
                            textvariable=self._delay_var, command=self._set_delay)
        delay.pack(side="left")
        delay.bind("<FocusOut>", lambda _e: self._set_delay())
        delay.bind("<Return>", lambda _e: self._set_delay())
        ttk.Label(opts, text="ms").pack(side="left", padx=(2, 0))
        Tooltip(delay, lambda: "How long the pasted text stays on the clipboard before "
                "yours is put back.\nRaise it if a slow app sometimes pastes the original "
                "text instead.")

    def _apply_general_visibility(self) -> None:
        if self.cfg.show_general:
            self.general.pack(fill="x", pady=(8, 0))
            self._gear.configure(text="⚙▴")
        else:
            self.general.pack_forget()
            self._gear.configure(text="⚙▾")

    def _click_pin(self) -> None:
        self._pin_var.set(not self._pin_var.get())
        self._toggle_pin()

    def _refresh_pin_btn(self) -> None:
        self._pin_btn.configure(text="Unpin" if self._pin_var.get() else "Pin")

    def _toggle_pin(self) -> None:
        self.cfg.always_on_top = bool(self._pin_var.get())
        self.root.attributes("-topmost", self.cfg.always_on_top)
        self._refresh_pin_btn()
        self._save()

    def _toggle_general(self) -> None:
        self.cfg.show_general = not self.cfg.show_general
        self._apply_general_visibility()
        self._save()

    def _refresh_hotkey_label(self) -> None:
        lines = []
        for action, text in _HOTKEY_LABELS.items():
            hkc = self.cfg.hotkeys.get(action, {})
            combo = hotkeys_mod.describe(hkc.get("mods", []), hkc.get("key", ""))
            lines.append(f"{combo:<22}  {text}")
        self.hotkey_label.configure(text="\n".join(lines))

    def _say(self, msg: str) -> None:
        self.status.configure(text=msg, foreground=_ON_BG)
        if self._status_after:
            self.root.after_cancel(self._status_after)
        self._status_after = self.root.after(
            3000, lambda: self.status.configure(text=_STATUS_IDLE, foreground=_MUTED))

    # --- mouse wheel scrolling ---
    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    # --- rows ---
    def _make_row(self, rule: Rule) -> RuleRow:
        return RuleRow(self.rows_frame, rule, on_change=self._commit,
                       on_edit=self._refresh_preview, on_delete=self._delete_row,
                       on_options=self._edit_options, on_move=self._move_row)

    def _build_rows(self) -> None:
        for r in self.rows:
            r.destroy()
        self.rows = [self._make_row(rule) for rule in self.cfg.rules]
        self._regrid()

    def _regrid(self) -> None:
        last = len(self.rows) - 1
        for i, row in enumerate(self.rows):
            row.grid(i + 1, first=(i == 0), last=(i == last))  # row 0 = header

    def _add(self) -> None:
        row = self._make_row(Rule(find="", replace=""))
        self.rows.append(row)
        self._regrid()
        self._commit()
        row.find_entry.focus_set()

    def _delete_row(self, row: RuleRow) -> None:
        row.destroy()
        self.rows.remove(row)
        self._regrid()
        self._commit()

    def _move_row(self, row: RuleRow, delta: int) -> None:
        i = self.rows.index(row)
        j = i + delta
        if 0 <= j < len(self.rows):
            self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
            self._regrid()
            self._commit()

    def _swap_all(self) -> None:
        swapped = sum(1 for row in self.rows if row.swap(commit=False))
        skipped = len(self.rows) - swapped
        self._commit()
        self._say(f"Swapped {swapped} rule{'s' if swapped != 1 else ''}"
                  + (f", skipped {skipped}" if skipped else "") + ".")

    def _edit_options(self, row: RuleRow) -> None:
        def apply(rule: Rule) -> None:
            row.protect_existing = rule.protect_existing
            row.case_sensitive = rule.case_sensitive
            row.regex = rule.regex
            row.keep_numbers_whole = rule.keep_numbers_whole
            row.refresh_style()
            self._commit()
        OptionsDialog(self.root, row.to_rule(), apply)

    # --- preview ---
    def _current_rules(self) -> List[Rule]:
        return [row.to_rule() for row in self.rows]

    def _open_preview(self) -> None:
        if self.preview is not None:
            self.preview.lift()
            return
        self.preview = PreviewWindow(self.root, self._current_rules,
                                     on_close=lambda: setattr(self, "preview", None))

    def _refresh_preview(self) -> None:
        if self.preview is not None:
            self.preview.refresh()

    # --- general options ---
    def _toggle_chain(self) -> None:
        self.cfg.chain_paste = bool(self._chain_var.get())
        self._save()

    def _toggle_startup(self) -> None:
        want = bool(self._startup_var.get())
        try:
            startup.set_enabled(want)
        except OSError as exc:
            self._startup_var.set(not want)
            messagebox.showerror("Start with Windows", f"Could not change it:\n{exc}",
                                 parent=self.root)

    def _set_notify(self) -> None:
        label = self._notify_var.get()
        for level, text in _NOTIFY_CHOICES.items():
            if text == label:
                self.cfg.notify_level = level
        self._save()

    def _set_delay(self) -> None:
        try:
            ms = max(100, min(int(float(self._delay_var.get())), 5000))
        except ValueError:
            ms = self.cfg.restore_delay_ms
        self._delay_var.set(str(ms))
        if ms != self.cfg.restore_delay_ms:
            self.cfg.restore_delay_ms = ms
            self._save()

    def _edit_hotkeys(self) -> None:
        def apply(wanted: Dict[str, Dict]) -> bool:
            if self.rebind is None:
                self.cfg.hotkeys.update(wanted)
                failures = []
            else:
                failures = self.rebind(wanted)  # updates cfg.hotkeys for successes
            self._save()
            self._refresh_hotkey_label()
            if failures:
                messagebox.showwarning(
                    "Change hotkeys",
                    "Some hotkeys kept their old chord:\n\n" + "\n".join(
                        f"• {_HOTKEY_LABELS.get(a, a)}: {why}" for a, why in failures),
                    parent=self.root)
                return False
            self._say("Hotkeys updated.")
            return True
        HotkeyDialog(self.root, self.cfg.hotkeys, apply)

    # --- saving ---
    def _commit(self) -> None:
        """Rebuild cfg.rules from the current widgets and persist."""
        self.cfg.rules = self._current_rules()  # one atomic swap for the hotkey thread
        self._save()
        self._refresh_preview()

    def _save(self) -> None:
        try:
            config_mod.save(self.cfg)
        except OSError as exc:
            messagebox.showerror("SmartClipboard", f"Could not save settings:\n{exc}",
                                 parent=self.root)
            return
        self.on_save()

    # --- visibility ---
    def show(self) -> None:
        self.palette.refresh()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self) -> None:
        self.root.withdraw()


def run_gui(cfg: Config, command_queue: "queue.Queue", on_save: Callable[[], None],
            on_quit: Callable[[], None], rebind: Optional[Rebind] = None) -> None:
    """Create the Tk root and poll the cross-thread command queue."""
    root = tk.Tk()
    win = SettingsWindow(root, cfg, on_save, rebind)
    root.withdraw()  # start hidden in the tray

    def poll() -> None:
        try:
            while True:
                cmd = command_queue.get_nowait()
                if cmd == "show":
                    win.show()
                elif cmd == "quit":
                    on_quit()
                    root.quit()
                    return
        except queue.Empty:
            pass
        # Slot 1 (backup clipboard) is written by the hotkey thread.
        win.palette.refresh()
        root.after(120, poll)

    root.after(120, poll)
    root.mainloop()
    try:
        root.destroy()
    except tk.TclError:
        pass
