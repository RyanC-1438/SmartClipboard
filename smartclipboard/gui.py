"""Tkinter settings window for editing find & replace rules.

Runs on the main thread. The background hotkey/tray thread asks it to appear by
pushing onto a queue that the Tk event loop polls.

Layout, top to bottom:
  * Palette: 5 quick clipboard slots. Left-click copies a slot to the clipboard,
    right-click stores the clipboard in it, middle-click clears it. Slot 1 is
    the backup clipboard used by Ctrl+Shift+Win+C / V.
  * Rules table: Find and Replace are edited inline, each row has a large On/Off
    toggle, and the remaining per-rule options live in a small popup.
  * Chain toggle and the hotkey list.
Every change auto-saves.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk
from typing import Callable, List

from . import clipboard
from . import config as config_mod
from . import hotkeys as hotkeys_mod
from .config import PALETTE_SIZE, Config
from .findreplace import Rule, rule_error

_ON_BG = "#2e7d32"
_OFF_BG = "#9e9e9e"
_MUTED = "#666"
_PREVIEW_CHARS = 26


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
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, font=("Consolas", 9),
                 wraplength=520, padx=4, pady=2).pack()

    def _hide(self, _e=None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def _preview(text: str, limit: int = _PREVIEW_CHARS) -> str:
    """One-line preview: whitespace collapsed, newlines shown as ⏎."""
    flat = " ⏎ ".join(line.strip() for line in text.strip().splitlines())
    flat = " ".join(flat.split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


class PaletteBar(ttk.Frame):
    """A row of quick clipboard slots backed by ``cfg.palette``."""

    def __init__(self, parent, cfg: Config, on_change: Callable[[], None]):
        super().__init__(parent)
        self.cfg = cfg
        self.on_change = on_change
        self._shown: List[str | None] = [None] * PALETTE_SIZE
        self._flashing = [False] * PALETTE_SIZE
        self.buttons: List[tk.Button] = []
        for i in range(PALETTE_SIZE):
            self.columnconfigure(i, weight=1, uniform="palette")
            caption = "1 · backup (C/V)" if i == 0 else str(i + 1)
            ttk.Label(self, text=caption, foreground=_MUTED,
                      font=("Segoe UI", 8)).grid(row=0, column=i, sticky="w", padx=2)
            # width=1 so long previews are clipped instead of widening the
            # column; the uniform grid columns share the window width equally.
            btn = tk.Button(self, font=("Segoe UI", 8), anchor="w", justify="left",
                            relief="groove", padx=4, pady=3, width=1,
                            command=lambda i=i: self._copy(i))
            btn.grid(row=1, column=i, sticky="ew", padx=2)
            btn.bind("<Button-3>", lambda _e, i=i: self._store(i))
            btn.bind("<Button-2>", lambda _e, i=i: self._clear(i))
            Tooltip(btn, lambda i=i: self._tip(i))
            self.buttons.append(btn)
        self.refresh()

    def _tip(self, i: int) -> str:
        text = self.cfg.palette[i]
        help_line = "Left-click: copy  ·  Right-click: store clipboard  ·  Middle-click: clear"
        if not text:
            return help_line
        body = text if len(text) <= 600 else text[:600] + "\n…"
        return f"{body}\n\n({len(text)} chars)  {help_line}"

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
                btn.config(text=_preview(text), fg="black")
            else:
                btn.config(text="(empty)", fg="#999")


class OptionsDialog(tk.Toplevel):
    """Small popup for the per-rule options that stay out of the table."""

    def __init__(self, parent, rule: Rule,
                 on_ok: Callable[[bool, bool, bool], None]):
        super().__init__(parent)
        self.title("Rule options")
        self.resizable(False, False)
        self.transient(parent)
        self._on_ok = on_ok
        self._protect = tk.BooleanVar(value=rule.protect_existing)
        self._case = tk.BooleanVar(value=rule.case_sensitive)
        self._regex = tk.BooleanVar(value=rule.regex)

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")
        self._protect_cb = ttk.Checkbutton(
            frm,
            text="Leave text that already matches the Replace value\n"
                 "(so 'Tunnel1'→'Tunnel12' ignores an existing 'Tunnel12')",
            variable=self._protect,
        )
        self._protect_cb.grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            frm, text="Case sensitive", variable=self._case,
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            frm, text="Regular expression", variable=self._regex,
            command=self._sync,
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Label(
            frm, foreground=_MUTED, justify="left",
            text="Find is a Python regex; ^ and $ match at each line.\n"
                 "In Replace, \\1 or \\g<name> inserts a group, \\n a newline.\n"
                 "e.g.  Find  Z(\\d+)Status   Replace  Zone\\1_Status\n"
                 "(The 'Leave text…' option does not apply to regex rules.)",
        ).grid(row=3, column=0, sticky="w", padx=(20, 0), pady=(2, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, pady=(14, 0), sticky="e")
        ttk.Button(btns, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(btns, text="OK", command=self._ok).grid(row=0, column=1)

        self._sync()
        self.grab_set()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())

    def _sync(self) -> None:
        self._protect_cb.state(["disabled"] if self._regex.get() else ["!disabled"])

    def _ok(self) -> None:
        self._on_ok(self._protect.get(), self._case.get(), self._regex.get())
        self.destroy()


class RuleRow:
    """Widgets and state for one editable rule in the table."""

    def __init__(self, parent, rule: Rule, on_change: Callable[[], None],
                 on_delete: Callable[["RuleRow"], None], on_options: Callable[["RuleRow"], None]):
        self.on_change = on_change
        self.find_var = tk.StringVar(value=rule.find)
        self.replace_var = tk.StringVar(value=rule.replace)
        self.enabled = rule.enabled
        self.protect_existing = rule.protect_existing
        self.case_sensitive = rule.case_sensitive
        self.regex = rule.regex

        self.toggle = tk.Button(
            parent, width=4, font=("Segoe UI", 10, "bold"),
            relief="raised", command=self._toggle,
        )
        self.find_entry = ttk.Entry(parent, textvariable=self.find_var, width=22)
        self.replace_entry = ttk.Entry(parent, textvariable=self.replace_var, width=22)
        self.opts_btn = ttk.Button(parent, width=11, command=lambda: on_options(self))
        self.del_btn = ttk.Button(parent, text="✕", width=3,
                                  command=lambda: on_delete(self))
        Tooltip(self.find_entry, lambda: rule_error(self.to_rule()) or "")

        for entry in (self.find_entry, self.replace_entry):
            entry.bind("<FocusOut>", lambda _e: self.on_change())
            entry.bind("<Return>", lambda _e: self.on_change())
        # Re-validate regex rules as the user types (no save until commit).
        self.find_var.trace_add("write", lambda *_a: self.refresh_style())
        self.replace_var.trace_add("write", lambda *_a: self.refresh_style())
        self._refresh_toggle()
        self.refresh_style()

    def _toggle(self) -> None:
        self.enabled = not self.enabled
        self._refresh_toggle()
        self.on_change()

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
        bad = rule_error(self.to_rule()) is not None
        style = "Bad.TEntry" if bad else "TEntry"
        for entry in (self.find_entry, self.replace_entry):
            entry.configure(font=font)
        self.find_entry.configure(style=style)
        self.replace_entry.configure(style=style)
        self.opts_btn.configure(text="Regex…" if self.regex else "Options…")

    def grid(self, row: int) -> None:
        self.toggle.grid(row=row, column=0, padx=(0, 8), pady=3, sticky="w")
        self.find_entry.grid(row=row, column=1, padx=4, pady=3, sticky="ew")
        self.replace_entry.grid(row=row, column=2, padx=4, pady=3, sticky="ew")
        self.opts_btn.grid(row=row, column=3, padx=4, pady=3)
        self.del_btn.grid(row=row, column=4, padx=(4, 0), pady=3)

    def destroy(self) -> None:
        for w in (self.toggle, self.find_entry, self.replace_entry,
                  self.opts_btn, self.del_btn):
            w.destroy()

    def to_rule(self) -> Rule:
        return Rule(
            find=self.find_var.get(),
            replace=self.replace_var.get(),
            enabled=self.enabled,
            protect_existing=self.protect_existing,
            case_sensitive=self.case_sensitive,
            regex=self.regex,
        )


class SettingsWindow:
    def __init__(self, root: tk.Tk, cfg: Config, on_save: Callable[[], None]):
        self.root = root
        self.cfg = cfg
        self.on_save = on_save
        self.rows: List[RuleRow] = []
        root.title("SmartClipboard — Settings")
        root.geometry("680x620")
        root.minsize(600, 520)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        ttk.Style(root).configure("Bad.TEntry", foreground="#c62828")

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)

        pal = ttk.LabelFrame(outer, text="Palette  (left-click copy · right-click "
                             "store clipboard · middle-click clear)", padding=(6, 2, 6, 6))
        pal.pack(fill="x", pady=(0, 10))
        self.palette = PaletteBar(pal, cfg, on_change=self._save)
        self.palette.pack(fill="x")

        ttk.Label(outer, text="Find & Replace rules (all applied together on "
                  "replace-paste):", font=("Segoe UI", 10, "bold")).pack(anchor="w")

        # Column headers, aligned with the scrollable grid below.
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(6, 0))
        for col, (text, w) in enumerate([("On", 6), ("Find", 24), ("Replace", 24),
                                         ("", 11), ("", 4)]):
            ttk.Label(header, text=text, font=("Segoe UI", 9, "bold"),
                      width=w, anchor="w").grid(row=0, column=col, padx=4)

        # Scrollable area holding one grid row per rule.
        canvas_wrap = ttk.Frame(outer)
        canvas_wrap.pack(fill="both", expand=True, pady=(2, 6))
        self.canvas = tk.Canvas(canvas_wrap, highlightthickness=0)
        vscroll = ttk.Scrollbar(canvas_wrap, orient="vertical",
                                command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.rows_frame = ttk.Frame(self.canvas)
        self._window_id = self.canvas.create_window((0, 0), window=self.rows_frame,
                                                    anchor="nw")
        self.rows_frame.columnconfigure(1, weight=1)
        self.rows_frame.columnconfigure(2, weight=1)
        self.rows_frame.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._window_id, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        btns = ttk.Frame(outer)
        btns.pack(fill="x")
        ttk.Button(btns, text="+ Add rule", command=self._add).pack(side="left")
        self._chain_var = tk.BooleanVar(value=cfg.chain_paste)
        chain_cb = ttk.Checkbutton(btns, text="Chain repeated replace-pastes",
                                   variable=self._chain_var, command=self._toggle_chain)
        chain_cb.pack(side="left", padx=(16, 0))
        Tooltip(chain_cb, lambda: (
            "Pressing replace-paste again without copying anything new applies the\n"
            "rules to what was pasted last, one step per press.\n"
            "Rules Z1→Z2 and Z2→Z3, clipboard 'Z1Status':\n"
            "  1st press pastes Z2Status, 2nd pastes Z3Status, then stays there.\n"
            "Copying anything starts over from the clipboard."))
        ttk.Label(btns, text="Changes save automatically.",
                  foreground=_MUTED).pack(side="right")

        hk = ttk.LabelFrame(outer, text="Hotkeys", padding=8)
        hk.pack(fill="x", pady=(12, 0))
        labels = {
            "backup_copy": "Copy selection to backup clipboard (palette slot 1)",
            "backup_paste": "Paste from backup clipboard (palette slot 1)",
            "replace_paste": "Paste with find & replace",
            "open_settings": "Open this Settings window",
        }
        for action, text in labels.items():
            hkc = cfg.hotkeys.get(action, {})
            combo = hotkeys_mod.describe(hkc.get("mods", []), hkc.get("key", ""))
            ttk.Label(hk, text=f"{combo:<22}  {text}",
                      font=("Consolas", 9)).pack(anchor="w")
        ttk.Label(hk, text="(Edit hotkeys in config.json, then restart.)",
                  foreground=_MUTED).pack(anchor="w", pady=(4, 0))

        self._build_rows()

    # --- mouse wheel scrolling ---
    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    # --- rows ---
    def _build_rows(self) -> None:
        for r in self.rows:
            r.destroy()
        self.rows = []
        for rule in self.cfg.rules:
            row = RuleRow(self.rows_frame, rule, on_change=self._commit,
                          on_delete=self._delete_row, on_options=self._edit_options)
            self.rows.append(row)
        self._regrid()

    def _regrid(self) -> None:
        for i, row in enumerate(self.rows):
            row.grid(i)

    def _add(self) -> None:
        rule = Rule(find="", replace="")
        row = RuleRow(self.rows_frame, rule, on_change=self._commit,
                      on_delete=self._delete_row, on_options=self._edit_options)
        self.rows.append(row)
        self._regrid()
        self._commit()
        row.find_entry.focus_set()

    def _delete_row(self, row: RuleRow) -> None:
        row.destroy()
        self.rows.remove(row)
        self._regrid()
        self._commit()

    def _edit_options(self, row: RuleRow) -> None:
        def apply(protect: bool, case: bool, regex: bool) -> None:
            row.protect_existing = protect
            row.case_sensitive = case
            row.regex = regex
            row.refresh_style()
            self._commit()
        OptionsDialog(self.root, row.to_rule(), apply)

    def _toggle_chain(self) -> None:
        self.cfg.chain_paste = bool(self._chain_var.get())
        self._save()

    def _commit(self) -> None:
        """Rebuild cfg.rules from the current widgets and persist."""
        self.cfg.rules[:] = [row.to_rule() for row in self.rows]
        self._save()

    def _save(self) -> None:
        config_mod.save(self.cfg)
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
            on_quit: Callable[[], None]) -> None:
    """Create the Tk root and poll the cross-thread command queue."""
    root = tk.Tk()
    win = SettingsWindow(root, cfg, on_save)
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
