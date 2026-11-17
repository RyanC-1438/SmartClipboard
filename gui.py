"""Tkinter settings window for editing find & replace rules.

Runs on the main thread. The background hotkey/tray thread asks it to appear by
pushing onto a queue that the Tk event loop polls.

The rules are shown as an editable table: Find and Replace are edited inline,
each row has a large On/Off toggle, and the remaining per-rule options live in a
small popup. Every change auto-saves.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk
from typing import Callable, List

from . import config as config_mod
from . import hotkeys as hotkeys_mod
from .config import Config
from .findreplace import Rule

_ON_BG = "#2e7d32"
_OFF_BG = "#9e9e9e"


class OptionsDialog(tk.Toplevel):
    """Small popup for the per-rule options that stay out of the table."""

    def __init__(self, parent, rule: Rule, on_ok: Callable[[bool, bool], None]):
        super().__init__(parent)
        self.title("Rule options")
        self.resizable(False, False)
        self.transient(parent)
        self._on_ok = on_ok
        self._protect = tk.BooleanVar(value=rule.protect_existing)
        self._case = tk.BooleanVar(value=rule.case_sensitive)

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")
        ttk.Checkbutton(
            frm,
            text="Leave text that already matches the Replace value\n"
                 "(so 'Tunnel1'→'Tunnel12' ignores an existing 'Tunnel12')",
            variable=self._protect,
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            frm, text="Case sensitive", variable=self._case,
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, pady=(14, 0), sticky="e")
        ttk.Button(btns, text="Cancel", command=self.destroy).grid(row=0, column=0, padx=4)
        ttk.Button(btns, text="OK", command=self._ok).grid(row=0, column=1)

        self.grab_set()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())

    def _ok(self) -> None:
        self._on_ok(self._protect.get(), self._case.get())
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

        self.toggle = tk.Button(
            parent, width=4, font=("Segoe UI", 10, "bold"),
            relief="raised", command=self._toggle,
        )
        self.find_entry = ttk.Entry(parent, textvariable=self.find_var, width=22)
        self.replace_entry = ttk.Entry(parent, textvariable=self.replace_var, width=22)
        self.opts_btn = ttk.Button(parent, text="Options…", width=9,
                                   command=lambda: on_options(self))
        self.del_btn = ttk.Button(parent, text="✕", width=3,
                                  command=lambda: on_delete(self))

        for entry in (self.find_entry, self.replace_entry):
            entry.bind("<FocusOut>", lambda _e: self.on_change())
            entry.bind("<Return>", lambda _e: self.on_change())
        self._refresh_toggle()

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
        )


class SettingsWindow:
    def __init__(self, root: tk.Tk, cfg: Config, on_save: Callable[[], None]):
        self.root = root
        self.cfg = cfg
        self.on_save = on_save
        self.rows: List[RuleRow] = []
        root.title("SmartClipboard — Settings")
        root.geometry("640x460")
        root.minsize(560, 380)
        root.protocol("WM_DELETE_WINDOW", self.hide)

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)

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
        ttk.Label(btns, text="Changes save automatically.",
                  foreground="#666").pack(side="right")

        hk = ttk.LabelFrame(outer, text="Hotkeys", padding=8)
        hk.pack(fill="x", pady=(12, 0))
        labels = {
            "backup_copy": "Copy selection to backup clipboard",
            "backup_paste": "Paste from backup clipboard",
            "replace_paste": "Paste with find & replace",
            "open_settings": "Open this Settings window",
        }
        for action, text in labels.items():
            hkc = cfg.hotkeys.get(action, {})
            combo = hotkeys_mod.describe(hkc.get("mods", []), hkc.get("key", ""))
            ttk.Label(hk, text=f"{combo:<22}  {text}",
                      font=("Consolas", 9)).pack(anchor="w")
        ttk.Label(hk, text="(Edit hotkeys in config.json, then restart.)",
                  foreground="#666").pack(anchor="w", pady=(4, 0))

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
        def apply(protect: bool, case: bool) -> None:
            row.protect_existing = protect
            row.case_sensitive = case
            self._commit()
        OptionsDialog(self.root, row.to_rule(), apply)

    def _commit(self) -> None:
        """Rebuild cfg.rules from the current widgets and persist."""
        self.cfg.rules[:] = [row.to_rule() for row in self.rows]
        config_mod.save(self.cfg)
        self.on_save()

    # --- visibility ---
    def show(self) -> None:
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
        root.after(120, poll)

    root.after(120, poll)
    root.mainloop()
    try:
        root.destroy()
    except tk.TclError:
        pass
