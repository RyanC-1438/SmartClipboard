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
    # Set by load() when config.json could not be read (not saved to disk).
    load_error: str = field(default="", compare=False)

    def to_dict(self) -> dict:
        return {
            "hotkeys": self.hotkeys,
            "rules": [r.to_dict() for r in self.rules],
            "chain_paste": self.chain_paste,
            "palette": [""] + list(self.palette[1:]),
            "restore_delay_ms": self.restore_delay_ms,
            "paste_results_in_history": self.paste_results_in_history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        hotkeys = _clone(DEFAULT_HOTKEYS)
        raw_hotkeys = d.get("hotkeys")
        for action, hk in (raw_hotkeys if isinstance(raw_hotkeys, dict) else {}).items():
            if action in hotkeys and isinstance(hk, dict):
                hotkeys[action] = {
                    "mods": [str(m).lower() for m in hk.get("mods", [])],
                    "key": str(hk.get("key", "")),
                }
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
        except (TypeError, ValueError):
            delay = 800
        return cls(hotkeys=hotkeys, rules=rules,
                   chain_paste=bool(d.get("chain_paste", False)), palette=palette,
                   restore_delay_ms=max(100, min(delay, 5000)),
                   paste_results_in_history=bool(d.get("paste_results_in_history", True)))


def _clone(d: Dict[str, Dict]) -> Dict[str, Dict]:
    return {k: {"mods": list(v["mods"]), "key": v["key"]} for k, v in d.items()}


def load() -> Config:
    """Load config.json. A missing file gives defaults. A file that can't be
    parsed (e.g. a typo while hand-editing hotkeys) is first copied to
    config.json.broken-<timestamp> so it is never lost, and the
    returned defaults carry ``load_error`` so the app can tell the user."""
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("top level is not a JSON object")
        return Config.from_dict(data)
    except FileNotFoundError:
        return Config()
    except Exception as exc:  # corrupt / unreadable: keep a copy, don't crash
        backup = f"{path}.broken-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            shutil.copy2(path, backup)
            where = f"A copy was saved as:\n{backup}"
        except OSError:
            where = "WARNING: a backup copy could not be made."
        cfg = Config()
        cfg.load_error = (f"config.json could not be read ({exc}).\n\n{where}\n\n"
                          "SmartClipboard started with default settings. Fix the "
                          "file and copy it back over config.json, then restart.")
        return cfg


def save(cfg: Config) -> None:
    path = config_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg.to_dict(), fh, indent=2)
    os.replace(tmp, path)  # atomic on Windows
