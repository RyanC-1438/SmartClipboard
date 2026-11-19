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
| `Ctrl+Shift+Win+T` | **Transform in place:** apply the rules to the clipboard and leave the result there (no paste) — then paste it yourself with `Ctrl+V`, as many times as you like |

Copy normally with `Ctrl+C`, then press `Ctrl+Shift+Win+R` to paste the transformed
text. Your original clipboard contents are restored afterward. Transform in
place is the fallback for apps that ignore simulated keys (e.g. apps running as
administrator) or are slow to paste; pressing it again applies the rules again.

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
  `X X2 X!`. Ignored when Replace is empty (a deletion) or when Find already
  contains Replace (e.g. a reversed `Tunnel12` → `Tunnel1`).
- **Keep numbers whole** (on by default, per rule in Options). A match that
  would cut a number in two is skipped, so `Z1` → `Z2` changes `Z1Status` and
  `Z1_x` but leaves `Z10Status` and `Z12` alone. Turn it off for plain
  substring matching.
- **Delete by replacing with nothing.** Leave the Replace box empty.
- **Swap direction.** The ↔ button on a rule swaps Find and Replace;
  **↔ Swap all** reverses every rule (e.g. to undo a `Z1`→`Z2` rename). Regex
  rules and rules with an empty Replace are skipped.
- **Order matters only for ties.** Use ▲▼ to move a rule; the higher rule wins
  when two match at the same spot.
- **Preview…** opens a box where you type or paste sample text and see the
  result live with replacements highlighted. It never reads the clipboard on
  its own.

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
| Ctrl+drag onto another slot | Swap the two (slots 2–5) |

Slot 1 *is* the backup clipboard: `Ctrl+Shift+Win+C` fills it and
`Ctrl+Shift+Win+V` pastes it. Like before, slot 1 lives only in memory (so it
can't be swapped with the others). Slots 2–5 are saved in `config.json` so they
survive a restart.

**Paste a slot without opening the window:** `Ctrl+Alt+Numpad1` … `Numpad5`
paste palette slots 1–5 (NumLock must be on). Change or turn them off in
Settings → Change hotkeys.

Dragging only happens while **Ctrl** is held when you press; a plain click is
always just a click, so a quick click can't turn into an accidental drag.

#### Snippets (optional row under the palette)
Click **Snippets ▾** to show a row of small, named slots: 6 per page, 5 pages
(◀ 1/5 ▶ on the right). They're saved in `config.json`.

| Mouse | Action |
| --- | --- |
| Left-click | Copy the snippet to the clipboard |
| Right-click | Store the clipboard, then type a short name (Enter to save, Esc for an automatic name) |
| Shift+right-click | Rename without changing the contents |
| Middle-click | Clear |
| Ctrl+drag onto another snippet | Swap the two (same page) |

Hover a snippet to see its name and full text. Snippets you named yourself
show in **bold blue**; ones using the automatic name stay plain.

#### Undo
Storing over, clearing or renaming a palette slot or snippet (including slot 1
being replaced by `Ctrl+Shift+Win+C`) shows **↶ Undo** for 8 seconds; `Ctrl+Z`
in the window does the same. It remembers one change, as an "oops" button.

With **Chain pastes** ticked, **+ Add rule** starts the new rule's Find with
the previous rule's Replace (Z1→Z2, then Z2→…).

### Tray icon
- **Left-click** (or double-click) the tray icon to open Settings.
- **Right-click** for the menu: *Settings…*, *Pause hotkeys* (turns every
  hotkey off until you untick it; the icon turns grey), and *Quit*.
- Starting `SmartClipboard.exe` again while it's running just opens Settings in
  the running copy.
- Tray notifications disappear after about 3 seconds.
- If Windows Explorer restarts, the icon comes back on its own.

### Settings
`Ctrl+Shift+Win+S` (or the tray icon) opens the rules table. Edit
**Find** and **Replace** directly in the row, flip each rule On/Off with its
toggle button, and use **Options…** for the per-rule case / protect / numbers /
regex settings. Changes save automatically.

The window is laid out top-down for pinning: palette, then the rule tools (Add
rule, Swap all, Preview, Chain pastes), then the rules. **Pin** keeps the window on
top of other windows (no admin needed), and **⚙** hides or shows the General
box so a small window shows only the palette and your rules. Shrinking the
window cuts off the bottom first. Hovering a palette slot shows its full text.

The **General** box at the bottom holds:
- **Change hotkeys…**: pick modifiers and a key for each action. The change
  takes effect immediately (no restart). If another app already owns a chord,
  that action keeps its old one and you're told.
- **Start with Windows** (in the .exe only): adds a per-user startup entry
  (`HKCU\…\Run`, no admin). Untick to remove it.
- **Notifications**: *Every paste* shows a small tray notification with what
  was pasted and how many replacements; *Problems only* shows just "no
  matches", blocked pastes, etc.; *Off* shows nothing.
- **Restore delay**: how long the pasted text stays on the clipboard before
  yours is put back (see below).

### How pasting works (and what it does to your clipboard)
- The paste is sent when you **release** Ctrl/Shift/Win, so you can keep them
  held and tap R twice; each tap queues one paste.
- Your clipboard is put back **in full** (images, files, Excel cells, rich
  text), `restore_delay_ms` (default 800) after the paste. This happens in the
  background, so the hotkey doesn't feel slower. If you copy something during
  that time, your new copy is kept.
- **Win+V history**: the restore of your own clipboard is never added (it's
  already there from your copy). The pasted result is added, so you can
  re-paste it from Win+V; set `"paste_results_in_history": false` in
  `config.json` to keep it out too.
- **Apps running as administrator** ignore simulated keys from a normal app
  (a Windows rule). SmartClipboard detects this and, instead of failing
  silently, leaves the result on your clipboard and tells you to press Ctrl+V.
  It never runs elevated itself.
- If `config.json` can't be read (e.g. a typo), a copy is saved as
  `config.json.broken-<time>` and you get a warning; your rules aren't lost.

## Why it can't interfere with your typing / isn't a keylogger

It registers global hotkeys through the Windows `RegisterHotKey` API. Windows
only notifies the app when one of those *exact* chords is pressed — the process
never receives, sees, or can record any other keystroke. It is **not** a
low-level keyboard hook. It also:

- requires **no administrator rights** (no UAC elevation),
- makes **no network connections**,
- only reads/writes a single `config.json` (rules, settings, palette slots 2–5)
  under `%APPDATA%\SmartClipboard`, plus one registry value only if you tick
  "Start with Windows",
- only reads the clipboard when you use one of its hotkeys or right-click a
  palette slot,
- contains no `eval`/`exec`/shell execution.

## Hotkey choice

`Ctrl+Shift+Win+<key>` was chosen because this triple-modifier namespace is
effectively unused by Windows and other apps, and it never combines `Ctrl+Alt`
(that pair is `AltGr` on many keyboard layouts). `Win+Alt+<key>` was avoided
because Xbox Game Bar owns it (e.g. `Win+Alt+R` records, `Win+Alt+C` clips). If
a hotkey is already taken, the app shows a warning at startup; change it in
Settings → **Change hotkeys…**.

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
