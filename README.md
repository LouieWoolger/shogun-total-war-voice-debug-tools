# Shogun: Total War Gold — Voice Debug Tools

Runtime debug injectors for **SHOGUN: Total War Gold / Collection** (`ShogunM.exe`) that let you manually trigger throne-room and world-map voice clips on demand for testing and verification.

These tools do not modify or replace the game's audio assets, and do not patch the executable permanently. All injected code is removed when the tool exits.

---

## Overview

The game plays its MP3 voice lines through internal playback functions that are normally triggered by scripted game events. These tools hook into that same playback machinery at runtime, allowing you to select and fire any voice clip from outside the game's own logic — without waiting for the event that would normally trigger it.

This is useful for:

- Verifying that a replacement or repaired audio file plays correctly through the game's own audio path.
- Confirming that the correct playback route (`[N]`, `[Q]`, or `[G]`) is used for a given clip.
- Quickly auditing a large set of clips without replaying the game's events.

---

## Included Tools

| File | Purpose |
|------|---------|
| `shogun_throne_room_voice_debug_injector.py` | Triggers throne-room MP3 clips: advisor, emissary, priest, trader, and throne-scene messenger lines. |
| `shogun_world_voice_debug_injector.py` | Triggers selected world-map / campaign MP3 clips: herald lines, death poems, and annual-event messenger lines. |
| `shogun_voice_debug_clips.json` | Optional per-path override metadata used by the throne-room tool. Ships pre-populated with entries for Portuguese and Dutch trader clips, head-on-plate messenger clips, and example priest and emissary clips. See [Clip Override File](#clip-override-file-shogun_voice_debug_clipsjson). |
| `requirements.txt` | Python dependency list (`pefile`). |
| `LICENSE` | GNU GPL v3.0 licence text. |

### Scope

The **throne-room tool** covers all supported MP3s under `Voices\Throne\`, excluding the world-event `Birth` and `Harvest` messenger clips (those belong to the world tool). It does not include the game's `.wav` voice assets.

The **world-map tool** covers:

- `Voices\Herald\*.mp3`
- `Voices\Poems\*.mp3`
- `Voices\Throne\Messenger\Birth\*.mp3`
- `Voices\Throne\Messenger\Harvest\*.mp3`

---

## Requirements

| Requirement | Notes |
|-------------|-------|
| Windows | Both tools use Win32 APIs (`WriteProcessMemory`, `VirtualAllocEx`, etc.) and are Windows-only. |
| Python 3.7 or later | The scripts use `from __future__ import annotations`. No 3.10+ syntax is present. |
| `pefile` | Python library used to resolve export addresses from `user32.dll` and `kernel32.dll` in the SysWOW64 directory. Install via `requirements.txt`. |
| NASM (Netwide Assembler) | Used at runtime to assemble the injected x86 shellcode stub before it is written into the game process. The tool generates NASM assembly source, invokes `nasm.exe` as a subprocess, and injects the resulting binary blob. |

---

## Installation and Setup

**1. Install the Python dependency:**

```powershell
pip install -r .\requirements.txt
```

**2. Make NASM available.**

The tool searches for `nasm.exe` in the following order:

1. The path set in the `NASM` environment variable.
2. A bundled copy at `_tools\NASM\nasm.exe` relative to the repo root.
3. `PATH`.

To point at a specific installation:

```powershell
$env:NASM = 'C:\Tools\nasm\nasm.exe'
```

---

## Usage

Both tools accept either the game folder or the full path to `ShogunM.exe` as the target.

### Command-Line Flags

| Flag | Applies to | Argument | Description |
|------|-----------|----------|-------------|
| *(positional)* | both | path | Game folder or path to `ShogunM.exe`. |
| `--exe` | both | path | Alternative way to specify the game folder or `ShogunM.exe` path. |
| `--spawn` | both | — | Launch a fresh instance of `ShogunM.exe` instead of attaching to an already-running one. Refuses to run if the game is already running. |
| `--select` | both | number or id | Pre-select a clip by its list number or clip ID when the tool starts. |
| `--play` | both | number or id | Select and immediately queue a clip to play as soon as the hook is ready. |
| `--log` | both | file path | Write the session log to the specified file. If omitted, the log is written to the game folder as `throne_room_voice_debug_injector.log` or `world_voice_debug_injector.log`. |
| `--clip-config` | throne-room only | file path | Path to the JSON clip override file. Defaults to `shogun_voice_debug_clips.json` in the same directory as the script. |

### Throne-Room Tool

Attach to an already-running game:

```powershell
python .\shogun_throne_room_voice_debug_injector.py "F:\Games\Shogun Total War Gold"
```

Spawn a fresh game instance:

```powershell
python .\shogun_throne_room_voice_debug_injector.py --spawn "F:\Games\Shogun Total War Gold"
```

Write the session log to a specific file:

```powershell
python .\shogun_throne_room_voice_debug_injector.py --log "C:\logs\throne.log" "F:\Games\Shogun Total War Gold"
```

Use a custom clip override file:

```powershell
python .\shogun_throne_room_voice_debug_injector.py --clip-config "C:\my_overrides.json" "F:\Games\Shogun Total War Gold"
```

Start with a specific clip pre-selected and queued to play immediately:

```powershell
python .\shogun_throne_room_voice_debug_injector.py --play 12 "F:\Games\Shogun Total War Gold"
```

### World-Map Tool

Attach to an already-running game:

```powershell
python .\shogun_world_voice_debug_injector.py "F:\Games\Shogun Total War Gold"
```

Spawn a fresh game instance:

```powershell
python .\shogun_world_voice_debug_injector.py --spawn "F:\Games\Shogun Total War Gold"
```

Write the session log to a specific file:

```powershell
python .\shogun_world_voice_debug_injector.py --log "C:\logs\world.log" "F:\Games\Shogun Total War Gold"
```

---

## Console Commands

Both tools present an interactive console once attached (`voice> ` prompt). Commands are sent to the injected hook via shared memory and take effect on the game's next message-loop tick.

| Command | Description |
|---------|-------------|
| `list` | Display the first page of available clips, numbered from 1. Shows clip number, playback marker, clip ID, category, path, and subtitle where available. The currently selected clip is prefixed with `>`. |
| `list <page>` | Display a specific page. Pages are 20 clips each. Example: `list 2` shows clips 21–40. |
| `find <text>` | Search for clips whose ID, path, label, localisation key, subtitle, or category contains the given text (case-insensitive). Example: `find portuguese` or `find poem`. Results are capped at 40 matches. |
| `<number>` | Select the clip with that list number. Shorthand for `select <number>`. |
| `select <n\|id>` | Select a clip by its 1-based list number or by its clip ID string. The clip becomes the current selection but is not played. |
| `play` | Play the currently selected clip through the game's audio path. |
| `play <n\|id>` | Select a clip by number or ID and play it immediately. |
| `current` | Print the details of the currently selected clip (number, marker, ID, category, path, subtitle). |
| `help` | Print the command reference. |
| `quit` | Detach the injector cleanly, restore the patched IAT entry, free the allocated remote memory, and exit. |

### Hotkeys

Hotkeys are polled on the game's own message thread and work regardless of whether the console window is focused.

| Key | Action |
|-----|--------|
| `F8` | Select the previous clip. |
| `F9` | Select the next clip. |
| `F10` | Play the currently selected clip. |

---

## Playback Route Markers

Each clip in the `list` output carries a marker in square brackets indicating which internal playback route the tool uses for that clip. This applies to the throne-room tool only; the world-map tool does not display markers.

| Marker | Route | Used for |
|--------|-------|----------|
| `[N]` | Native throne scene route (`fn_other_start`) | Messenger, priest, emissary, trader, and foreign visitor (Portuguese / Dutch) clips. Each uses a specific script token (e.g. `priest_out_accept`, `hostemiss_yes`) that the game's scripting system expects alongside the audio. |
| `[Q]` | Native advisor quote route (`fn_advisor_quote_start`) | Advisor speech clips under `Voices\Throne\Advisor\`. Uses the game's secondary advisor quote path, including the quote-length script bucket logic from the shipped game. |
| `[G]` | Generic route (`advisor_start`) | Clips that do not map to a recognised native path and fall back to a generic playback method. |

**Why this matters for testing:** The three routes are distinct code paths inside `ShogunM.exe`. A clip that plays correctly through one route may not behave correctly if assigned to the wrong one. The marker tells you at a glance which path the tool is using, so if playback is wrong or silent you know whether to investigate the route assignment, the token, or the audio file itself. The route for each clip is inferred automatically from its file path. If the inferred route is wrong for a specific clip, it can be overridden in `shogun_voice_debug_clips.json`.

---

## Clip Override File: `shogun_voice_debug_clips.json`

This file is used only by the throne-room tool. It is optional in the sense that the tool runs without it, but it ships pre-populated with entries and is read automatically from the script directory unless `--clip-config` points elsewhere.

### What it does

When the throne-room tool starts, it scans `Voices\Throne\` and automatically infers each clip's playback method, script token, and display label from its file path. For most clips this inference is correct. The JSON file lets you override those inferred values on a per-clip basis — and also lets you assign custom IDs and labels for use with `--select`, `--play`, and the console commands.

The shipped file contains pre-defined entries for the Portuguese trader clips, Dutch trader clips, head-on-plate messenger clips, and example priest and emissary response clips. These exist because the path-based inference for those families produces the correct token, but the shipped entries give them stable, readable IDs (e.g. `portuguese_offer`, `dutch_rejected`) that are easier to use from the command line.

### When to edit it

- To give a clip a stable short ID for use with `--play` or the `play`/`select` console commands.
- To assign a more readable label in `list` output.
- To override the inferred method or token if a clip plays incorrectly or silently.

### Format

The file must contain a JSON array. Each entry is an object with the following fields:

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `path` | Yes | string | Game-relative path of the clip (e.g. `\Throne\Priest\Response\Rejected\Takeda.Mp3`). Must match the actual file; entries with no matching file are reported as warnings at startup. |
| `method` | No | string | Playback route: `other_start`, `advisor_start`, or `advisor_quote_start`. Overrides the path-inferred value. |
| `token` | No | string | Script token string passed to the game (e.g. `priest_out_Takeda`). Overrides the path-inferred value. |
| `label` | No | string | Human-readable name shown in `list` output. |
| `id` | No | string | Short identifier for use with `--select`, `--play`, and the `select`/`play` console commands. |
| `advisor_type` | No | integer | Advisor type flag for `advisor_start` clips. Defaults to `0`. |

**Example entry:**

```json
{
  "id": "portuguese_offer",
  "label": "Portuguese traders ask audience",
  "method": "other_start",
  "path": "\\Throne\\Port_Trader\\Offer.Mp3",
  "token": "portuguese_out"
}
```

---

## Logging

Both tools write a timestamped log of all events — selection changes, playback triggers, errors, and IAT patch and restore operations — to a log file.

- **Default location:** the game folder, named `throne_room_voice_debug_injector.log` or `world_voice_debug_injector.log`.
- **Custom location:** use `--log <path>` to write to a specific file.

The log is appended to on each run, not overwritten.

---

## Notes

- `--spawn` is strict: it refuses to run if `ShogunM.exe` is already running. Close any existing instances before using it.
- The injector patches a single IAT entry (`PeekMessageA`) to redirect into the injected stub. On clean exit — via `quit`, Ctrl+C, or the game closing — the original pointer is restored and the remote allocation is freed.
- The throne-room tool is intended for use while the throne room is active or accessible. The world-map tool targets the world audio object and is most representative when triggered from the campaign map flow.
- Neither tool patches the game executable on disk or modifies any audio assets.
- These tools are for manual testing only. They are not gameplay mods or trainers.

---

## Verification in Game

1. Start the game normally, or use `--spawn`.
2. Run the appropriate tool against the game folder or `ShogunM.exe`.
3. Use `list` or `find` to locate the clip you want to test.
4. Use `select <number>` or `F8`/`F9` to choose it.
5. Trigger playback with `play` or `F10`.
6. Confirm that the clip starts, plays to completion, and behaves correctly for its voice family.

---

## Licence

GNU General Public License v3.0. See `LICENSE` for the full text.
