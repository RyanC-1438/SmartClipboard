"""Persistent configuration, stored as JSON under %APPDATA%\\SmartClipboard.

We deliberately keep the config inside the per-user roaming profile and never
read/write anywhere else, so the app cannot touch system locations.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List

from .findreplace import Rule

APP_DIR_NAME = "SmartClipboard"
CONFIG_FILENAME = "config.json"

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
    # The most-recent backup clipboard value is intentionally NOT persisted to
    # disk -- it lives only in memory while the app runs.

    def to_dict(self) -> dict:
        return {
            "hotkeys": self.hotkeys,
            "rules": [r.to_dict() for r in self.rules],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        hotkeys = _clone(DEFAULT_HOTKEYS)
        for action, hk in (d.get("hotkeys") or {}).items():
            if action in hotkeys and isinstance(hk, dict):
                hotkeys[action] = {
                    "mods": [str(m).lower() for m in hk.get("mods", [])],
                    "key": str(hk.get("key", "")),
                }
        rules = [Rule.from_dict(r) for r in (d.get("rules") or []) if isinstance(r, dict)]
        return cls(hotkeys=hotkeys, rules=rules)


def _clone(d: Dict[str, Dict]) -> Dict[str, Dict]:
    return {k: {"mods": list(v["mods"]), "key": v["key"]} for k, v in d.items()}


def load() -> Config:
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return Config()
        return Config.from_dict(data)
    except FileNotFoundError:
        return Config()
    except (json.JSONDecodeError, OSError, ValueError):
        # Corrupt or unreadable config -> fall back to defaults rather than crash.
        return Config()


def save(cfg: Config) -> None:
    path = config_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg.to_dict(), fh, indent=2)
    os.replace(tmp, path)  # atomic on Windows
