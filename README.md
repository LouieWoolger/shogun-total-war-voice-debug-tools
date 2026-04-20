# Shogun: Total War Gold - Voice Debug Tools v1.0.0

Manual voice-trigger tools for `ShogunM.exe`.

This release provides two small Windows injectors that let you manually trigger voice clips inside the game for testing:

- throne-room speech clips such as advisor, emissary, priest, trader, and messenger lines
- selected world-map/campaign MP3 clips such as herald lines and death poems

These tools are intended for testing and verification. They do not replace the game audio assets, and they do not patch the game executable permanently.

## What This Release Does

This release adds two runtime debug tools for SHOGUN: Total War Gold / Collection:

1. `shogun_throne_room_voice_debug_injector.py`
   Injects a small hotkey/console trigger into the running game and lets you play supported throne-room MP3 lines on demand.
2. `shogun_world_voice_debug_injector.py`
   Injects a separate trigger for selected world-map/campaign MP3 lines.

The throne-room tool is designed for fast validation of throne speech behavior through the game's own throne playback path.

The world-map tool is intentionally narrower. In `v1.0.0` it targets:

- `Voices\Herald\*.mp3`
- `Voices\Poems\*.mp3`

## Files

- `shogun_throne_room_voice_debug_injector.py`
  Throne-room voice debug tool.
- `shogun_world_voice_debug_injector.py`
  World-map voice debug tool.
- `shogun_voice_debug_clips.json`
  Optional throne-room override metadata used by the throne-room tool.
- `requirements.txt`
  Python dependency list.
- `README.md`
  This file.

## Requirements

- Windows
- Python 3
- `pefile`
- `nasm`

Install the Python dependency:

```powershell
pip install -r .\requirements.txt
```

Make sure `nasm.exe` is available either:

- on `PATH`
- or via the `NASM` environment variable

Example:

```powershell
$env:NASM = 'C:\Tools\nasm\nasm.exe'
```

## Usage

Pass either:

- the game folder
- or the full path to `ShogunM.exe`

### Throne-Room Tool

Attach to an already running game:

```powershell
python .\shogun_throne_room_voice_debug_injector.py "F:\Games\Shogun Total War Gold"
```

Spawn a fresh game instance:

```powershell
python .\shogun_throne_room_voice_debug_injector.py --spawn "F:\Games\Shogun Total War Gold"
```

### World Tool

Attach to an already running game:

```powershell
python .\shogun_world_voice_debug_injector.py "F:\Games\Shogun Total War Gold"
```

Spawn a fresh game instance:

```powershell
python .\shogun_world_voice_debug_injector.py --spawn "F:\Games\Shogun Total War Gold"
```

## Console Commands

Both tools support the same console workflow:

```text
list
list 2
find portuguese
find poem
select 42
play
play 42
current
quit
```

Hotkeys:

- `F8` previous clip
- `F9` next clip
- `F10` play selected clip

## Notes

- `--spawn` is intentionally strict and refuses to run if `ShogunM.exe` is already running.
- If `--log` is not supplied, the log file is written into the selected game folder.
- The throne-room tool is limited to supported throne-room MP3 clips. It does not include the game's `.wav` voice assets.
- The world tool in `v1.0.0` is limited to `Herald` and `Poems`.
- These tools are for manual testing. They are not intended as a general gameplay mod or trainer.

## Verification In Game

1. Start the game normally or use `--spawn`.
2. Run the appropriate tool.
3. Use `list` or `find` to choose a clip.
4. Trigger playback with `play <number>` or `F10`.
5. Confirm the clip starts, plays to completion, and behaves as expected for that voice family.
