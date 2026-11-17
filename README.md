# SmartClipboard

A lightweight background clipboard macro manager for Windows. It sits in the
system tray and does **nothing** until you press one of its specific hotkeys.

## Features

### 1. Backup clipboard
A second, independent clipboard that lives only in the app's memory.

| Hotkey | Action |
| --- | --- |
| `Ctrl+Shift+Win+C` | Copy the current selection into the backup clipboard (your normal `Ctrl+C`/`Ctrl+V` clipboard is left untouched) |
| `Ctrl+Shift+Win+V` | Paste from the backup clipboard |

### 2. Find & replace on paste
| Hotkey | Action |
| --- | --- |
| `Ctrl+Shift+Win+R` | Read the clipboard, apply your find/replace rules, and paste the result |

Copy normally with `Ctrl+C`, then press `Ctrl+Shift+Win+R` to paste the transformed
text. Your original clipboard contents are restored afterward.

Rules follow these guarantees:
- **No regex unless you ask for it.** By default your find term and the
  clipboard text are treated as literal strings. Tick **Regular expression** in
  a rule's Options to make that one rule a Python regex (see below).
- **All rules apply together in one pass** (no chaining). Every rule matches
  against the *original* clipboard text, and a replacement is never re-fed into
  another rule. So with rules `1`→`2` and `2`→`3`, copying `Num1` pastes `Num2`
  and copying `Num2` pastes `Num3` — it never jumps `Num1`→`Num3`. When two rules
  could match at the same spot, the one higher in the list wins.
- **Ignore case by default** (per-rule toggle in Options). New rules are
  case-insensitive; turn on "Case sensitive" for exact-case matching.
- **Leave existing Replace text** (toggle per rule, in Options). With it on, a
  Find match is skipped when it sits inside text that already matches that
  rule's Replace value. So `Tunnel1` → `Tunnel12` turns `Tunnel1 and Tunnel12`
  into `Tunnel12 and Tunnel12` (never `Tunnel122`). Because the guard is the
  replace text itself, it only matters when Replace contains Find — e.g. with
  `test1` → `X`, `X` never appears, so `test1 test12 test1!` fully becomes
  `X X2 X!`. Ignored when Replace is empty (a deletion).
- **Delete by replacing with nothing.** Leave the Replace box empty.

#### Regex rules (opt-in, per rule)
- Find is a Python regular expression. `^` and `$` match at the start/end of
  every line; case is ignored unless the rule is set to Case sensitive.
- In Replace, `\1` or `\g<name>` inserts a captured group and `\n` a newline.
  Example: Find `Z(\d+)Status`, Replace `Zone\1_Status`.
- "Leave existing Replace text" does not apply to regex rules.
- An invalid pattern or replacement shows in red (hover the Find box for the
  reason) and that rule is skipped; the other rules still apply.

#### Chain repeated replace-pastes (checkbox under the rules)
With this on, pressing `Ctrl+Shift+Win+R` again **without copying anything
new** applies the rules to what was pasted last, one step per press. With rules
`Z1`→`Z2` and `Z2`→`Z3` and `Z1Status` on the clipboard: the 1st press pastes
`Z2Status`, the 2nd `Z3Status`, and further presses repeat `Z3Status`. Copying
anything (even the same text again) starts over. With it off, every press
pastes the same one-step result.

### Palette
Five quick slots across the top of the Settings window, each showing a short
preview (hover for the full text):

| Mouse | Action |
| --- | --- |
| Left-click | Copy the slot to the clipboard |
| Right-click | Store the current clipboard text in the slot |
| Middle-click | Clear the slot |

Slot 1 *is* the backup clipboard: `Ctrl+Shift+Win+C` fills it and
`Ctrl+Shift+Win+V` pastes it. Like before, slot 1 lives only in memory. Slots
2–5 are saved in `config.json` so they survive a restart.

### Settings
`Ctrl+Shift+Win+S` (or the tray icon → *Settings*) opens the rules table. Edit
**Find** and **Replace** directly in the row, flip each rule On/Off with its
toggle button, and use **Options…** for the per-rule case / protect / regex
settings. Changes save automatically.

## Why it can't interfere with your typing / isn't a keylogger

It registers global hotkeys through the Windows `RegisterHotKey` API. Windows
only notifies the app when one of those *exact* chords is pressed — the process
never receives, sees, or can record any other keystroke. It is **not** a
low-level keyboard hook. It also:

- requires **no administrator rights** (no UAC elevation),
- makes **no network connections**,
- only reads/writes a single `config.json` (rules, settings, palette slots 2–5) under
  `%APPDATA%\SmartClipboard`,
- contains no `eval`/`exec`/shell execution.

## Hotkey choice

`Ctrl+Shift+Win+<key>` was chosen because this triple-modifier namespace is
effectively unused by Windows and other apps, and it never combines `Ctrl+Alt`
(that pair is `AltGr` on many keyboard layouts). `Win+Alt+<key>` was avoided
because Xbox Game Bar owns it (e.g. `Win+Alt+R` records, `Win+Alt+C` clips). If
a hotkey is already taken, the app shows a warning at startup; edit
`%APPDATA%\SmartClipboard\config.json` and restart to change it.

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m smartclipboard
```

## Build the .exe

```powershell
.\build.bat
```

Produces `dist\SmartClipboard.exe` — a single portable file with no console
window. The app has **zero third-party runtime dependencies**; PyInstaller is
used only at build time.

> Note: PyInstaller single-file exes are sometimes flagged by antivirus
> heuristics (this is common for *all* PyInstaller output, not specific to this
> app). The build uses `--noupx` to reduce false positives.

## Tests

```powershell
.\.venv\Scripts\python.exe tests\test_findreplace.py
.\.venv\Scripts\python.exe tests\test_features.py
```

## Adding features later

Each capability is a method on `Features` (`smartclipboard/features.py`) bound
to a hotkey in `smartclipboard/app.py` via the `actions` map, with its default
chord declared in `DEFAULT_HOTKEYS` (`smartclipboard/config.py`). To add a
feature: add the method, a default hotkey, and one line in `actions`.
```
