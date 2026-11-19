"""Locate bundled data files (icons) from source and from the PyInstaller exe.

From source, files live in <project>/assets/. In the PyInstaller onefile
build, build.bat adds them with ``--add-data "assets\\<file>;assets"``, so at
run time they are unpacked to ``sys._MEIPASS/assets/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ICON = "smartclipboard.ico"
ICON_PAUSED = "smartclipboard_paused.ico"


def _base_dir() -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle)
    return Path(__file__).resolve().parent.parent


def resource_path(name: str) -> str:
    """Absolute path of ``assets/<name>``. The file may not exist (e.g. a
    build without the assets); callers should fall back gracefully."""
    return str(_base_dir() / "assets" / name)
