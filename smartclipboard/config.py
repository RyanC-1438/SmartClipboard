"""Persistent configuration, stored as JSON under %APPDATA%\\SmartClipboard.

We deliberately keep the config inside the per-user roaming profile and never
read/write anywhere else, so the app cannot touch system locations.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Dict, List

from .findreplace import Rule

APP_DIR_NAME = "SmartClipboard"
CONFIG_FILENAME = "config.json"
PALETTE_SIZE = 5
PINS_PER_PAGE = 6
PIN_PAGES = 5
PIN_COUNT = PINS_PER_PAGE * PIN_PAGES
LABEL_MAX = 24
NOTIFY_LEVELS = ("all", "problems", "off")

# Default hotkeys. Modifier strings are a subset of {"win","ctrl","alt","shift"}
# and the key is a single character or a named key understood by hotkeys.py.
# Ctrl+Shift+Win+<key>: a triple-modifier namespace that nothing else uses, and
# which deliberately avoids combining Ctrl+Alt (that pair is AltGr on many
# keyboard layouts). Win+Alt was avoided because Xbox Game Bar owns it.
DEFAULT_HOTKEYS: Dict[str, Dict] = {
    "backup_copy": {"mods": ["ctrl", "shift", "win"], "key": "C"},
    "backup_paste": {"mods": ["ctrl", "shift", "win"], "key": "V"},
    "replace_paste": {"mods": ["ctrl", "shift", "win"], "key": "R"},
    "open_settings": {"mods": ["ctrl", "shift", "win"], "key": "S"},
}
_MOD_NAMES = {"ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "win": "win"}


def config_dir() -> str:
    """Return the per-user config directory, creating it if needed."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def config_path() -> str:
    return os.path.join(config_dir(), CONFIG_FILENAME)


@dataclass
class Config:
    hotkeys: Dict[str, Dict] = field(default_factory=lambda: _clone(DEFAULT_HOTKEYS))
    rules: List[Rule] = field(default_factory=list)
    # Repeated replace-pastes (without re-copying) advance the previous result
    # one more step through the rules instead of pasting the same text again.
    chain_paste: bool = False
    # Palette slots shown at the top of the Settings window. Slot 0 IS the
    # backup clipboard (Ctrl+Shift+Win+C/V); like before it is intentionally
    # never written to disk. Slots 1..4 are pinned on purpose and are saved.
    palette: List[str] = field(default_factory=lambda: [""] * PALETTE_SIZE)
    # How long the pasted text stays on the clipboard before the user's own
    # clipboard is put back. Too short and a slow target app (busy IDE, RDP)
    # reads the restored original instead. The restore happens in the
    # background, so a longer value does not slow the hotkey down.
    restore_delay_ms: int = 800
    # Whether the temporarily pasted result is allowed into Win+V clipboard
    # history. The restore of the user's own clipboard is always kept out of it
    # (it is already there from their copy, so it would only be a duplicate).
    paste_results_in_history: bool = True
    # Tray notifications: "all" (every paste), "problems" (no matches, blocked
    # paste, nothing copied...) or "off".
    notify_level: str = "all"
    # Whether the Settings window shows the General box (hotkeys, startup...).
    show_general: bool = True
    # Keep the Settings window above other windows (a plain window flag; no
    # admin rights involved).
    always_on_top: bool = False
    # The small "pins" row under the palette: PIN_COUNT slots of
    # {"text": ..., "label": ...}, shown PINS_PER_PAGE at a time. Saved.
    pins: List[Dict[str, str]] = field(
        default_factory=lambda: [{"text": "", "label": ""} for _ in range(PIN_COUNT)])
    show_pins: bool = False
    pin_page: int = 0
    # Set by load() when config.json could not be read (not saved to disk).
    load_error: str = field(default="", compare=False)
    # Set by load() when config.json was unreadable AND no backup copy could
    # be made: save() then refuses to overwrite it (not saved to disk).
    save_blocked: bool = field(default=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "hotkeys": self.hotkeys,
            "rules": [r.to_dict() for r in self.rules],
            "chain_paste": self.chain_paste,
            "palette": [""] + list(self.palette[1:]),
            "restore_delay_ms": self.restore_delay_ms,
            "paste_results_in_history": self.paste_results_in_history,
            "notify_level": self.notify_level,
            "show_general": self.show_general,
            "always_on_top": self.always_on_top,
            "pins": [dict(p) for p in self.pins],
            "show_pins": self.show_pins,
            "pin_page": self.pin_page,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        hotkeys = _clone(DEFAULT_HOTKEYS)
        raw_hotkeys = d.get("hotkeys")
        for action, hk in (raw_hotkeys if isinstance(raw_hotkeys, dict) else {}).items():
            if action in hotkeys and isinstance(hk, dict):
                mods = _parse_mods(hk.get("mods"))
                if mods:
                    hotkeys[action] = {"mods": mods, "key": str(hk.get("key", ""))}
        raw_rules = d.get("rules")
        rules = [Rule.from_dict(r) for r in (raw_rules if isinstance(raw_rules, list) else [])
                 if isinstance(r, dict)]
        raw = d.get("palette")
        raw = raw if isinstance(raw, list) else []
        palette = [str(v) if isinstance(v, str) else "" for v in raw[:PALETTE_SIZE]]
        palette += [""] * (PALETTE_SIZE - len(palette))
        palette[0] = ""  # the backup slot is never restored from disk
        try:
            delay = int(d.get("restore_delay_ms", 800))
        except (TypeError, ValueError, OverflowError):  # json.load accepts Infinity
            delay = 800
        return cls(hotkeys=hotkeys, rules=rules,
                   chain_paste=_bool(d.get("chain_paste"), False), palette=palette,
                   restore_delay_ms=max(100, min(delay, 5000)),
                   paste_results_in_history=_bool(d.get("paste_results_in_history"), True),
                   notify_level=(d.get("notify_level") if d.get("notify_level")
                                 in NOTIFY_LEVELS else "all"),
                   show_general=_bool(d.get("show_general"), True),
                   always_on_top=_bool(d.get("always_on_top"), False),
                   pins=_parse_pins(d.get("pins")),
                   show_pins=_bool(d.get("show_pins"), False),
                   pin_page=_int_in(d.get("pin_page"), 0, PIN_PAGES - 1, 0))


def _clone(d: Dict[str, Dict]) -> Dict[str, Dict]:
    return {k: {"mods": list(v["mods"]), "key": v["key"]} for k, v in d.items()}


def _parse_pins(raw) -> List[Dict[str, str]]:
    pins = []
    for item in (raw if isinstance(raw, list) else [])[:PIN_COUNT]:
        if isinstance(item, dict):
            text = item.get("text")
            label = item.get("label")
            pins.append({"text": text if isinstance(text, str) else "",
                         "label": (label if isinstance(label, str) else "")[:LABEL_MAX]})
        else:
            pins.append({"text": "", "label": ""})
    pins += [{"text": "", "label": ""} for _ in range(PIN_COUNT - len(pins))]
    return pins


def _int_in(value, lo: int, hi: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(lo, min(value, hi))


def _bool(value, default: bool) -> bool:
    """Only real JSON true/false count; "false" or 0 would be ambiguous."""
    return value if isinstance(value, bool) else default


def _parse_mods(raw) -> List[str]:
    """The known modifiers in ``raw``, or [] if it isn't a list or has none
    that keep the chord off normal typing (the caller then keeps the default:
    a bare-letter or Shift-only hotkey would steal typing system-wide)."""
    if not isinstance(raw, list):
        return []
    mods: List[str] = []
    for m in raw:
        name = _MOD_NAMES.get(m.lower()) if isinstance(m, str) else None
        if name and name not in mods:
            mods.append(name)
    return [] if mods in ([], ["shift"]) else mods


def _read_json(path: str):
    # OneDrive sync or a virus scanner can lock the file for a moment.
    for attempt in range(4):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.2)


def load() -> Config:
    """Load config.json. A missing file gives defaults. A file that can't be
    parsed (e.g. a typo while hand-editing hotkeys) is first copied to
    config.json.broken-<timestamp> so it is never lost, and the
    returned defaults carry ``load_error`` so the app can tell the user."""
    path = config_path()
    try:
        data = _read_json(path)
        if not isinstance(data, dict):
            raise ValueError("top level is not a JSON object")
        return Config.from_dict(data)
    except FileNotFoundError:
        return Config()
    except Exception as exc:  # corrupt / unreadable: keep a copy, don't crash
        backup = f"{path}.broken-{time.strftime('%Y%m%d-%H%M%S')}"
        cfg = Config()
        try:
            shutil.copy2(path, backup)
            where = f"A copy was saved as:\n{backup}"
        except OSError:
            where = ("WARNING: a backup copy could not be made, so settings "
                     "changes will not be saved over it.")
            cfg.save_blocked = True
        cfg.load_error = (f"config.json could not be read ({exc}).\n\n{where}\n\n"
                          "SmartClipboard started with default settings. Fix the "
                          "file and copy it back over config.json, then restart.")
        return cfg


def save(cfg: Config) -> None:
    """Write config.json. Raises OSError on failure, and also when load()
    could neither read the file nor back it up: it may hold the user's only
    copy of their settings."""
    path = config_path()
    if cfg.save_blocked:
        raise OSError(f"{path} could not be read when SmartClipboard started and "
                      "no backup copy could be made, so it was not overwritten. "
                      "Fix or move the file, then restart SmartClipboard.")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg.to_dict(), fh, indent=2)
    os.replace(tmp, path)  # atomic on Windows
