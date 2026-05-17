# Shogun: Total War Gold — Voice Debug Tools
[![Discord](https://img.shields.io/discord/1505490825889579018?style=for-the-badge&logo=discord&label=Discord&color=5865F2)](https://discord.gg/zKbDADqWRC)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Support-FF5F5F?style=for-the-badge&logo=ko-fi)](https://ko-fi.com/louiewoolger)

Runtime debug tools for Shogun: Total War Gold / Collection that let you trigger throne-room and world-map MP3 voice clips on demand without waiting for in-game events. The tools inject into a running `ShogunM.exe` via a patched IAT entry and removes all injected code on exit. They do not modify the executable on disk or any audio files.

`shogun_throne_room_voice_debug_injector.py` covers all MP3s under `Voices\Throne\`, excluding the world-event Birth and Harvest messenger clips. `shogun_world_voice_debug_injector.py` covers `Voices\Herald\`, `Voices\Poems\`, and the Birth and Harvest messenger clips under `Voices\Throne\Messenger\`.

## Installation

Install the Python dependency:

```powershell
pip install -r .\requirements.txt
```

NASM is required at runtime to assemble the injected shellcode stub. The tool searches for `nasm.exe` in this order: the `NASM` environment variable, a local copy at `_tools\NASM\nasm.exe` relative to the repo root, then `PATH`.

```powershell
$env:NASM = 'C:\Tools\nasm\nasm.exe'
```

## Usage

Both tools take the game folder or the path to `ShogunM.exe` as a positional argument.

Attach to a running game instance:

```powershell
python .\shogun_throne_room_voice_debug_injector.py "F:\Games\Shogun Total War Gold"
python .\shogun_world_voice_debug_injector.py "F:\Games\Shogun Total War Gold"
```

Launch a fresh game instance (`--spawn` refuses to run if the game is already open):

```powershell
python .\shogun_throne_room_voice_debug_injector.py --spawn "F:\Games\Shogun Total War Gold"
```

Pre-select and immediately play a clip on startup:

```powershell
python .\shogun_throne_room_voice_debug_injector.py --play 12 "F:\Games\Shogun Total War Gold"
```

Additional flags: `--select <n|id>` pre-selects without playing; `--log <path>` writes the session log to a specific file; `--clip-config <path>` points to a custom JSON override file (throne-room tool only).

Once attached, both tools present a `voice> ` prompt. Commands take effect on the game's next message-loop tick.

| Command | Action |
|---|---|
| `list [page]` | List available clips, 20 per page |
| `find <text>` | Search by ID, path, label, or category (max 40 results) |
| `play [n\|id]` | Play the selected clip, or select and play by number or ID |
| `select <n\|id>` | Select without playing |
| `current` | Show details for the selected clip |
| `quit` | Detach cleanly and exit |

Hotkeys work on the game's message thread regardless of console focus: **F8** previous clip, **F9** next clip, **F10** play.

The `list` output for the throne-room tool marks each clip with a playback route: `[N]` (native throne scene), `[Q]` (advisor quote path), or `[G]` (generic fallback). The three routes are distinct code paths inside `ShogunM.exe`; a clip that plays through the wrong route may be silent or behave incorrectly. The route is inferred automatically from the file path and can be overridden in the JSON config.

## Configuration

`shogun_voice_debug_clips.json` is read by the throne-room tool at startup. It ships with entries for Portuguese and Dutch trader clips, head-on-plate messenger clips, and example priest and emissary clips, assigning those families stable short IDs for use with `--play` and the `select` command.

Each entry supports: `path` (required, game-relative), `method`, `token`, `label`, `id`, and `advisor_type`. Entries with no matching file on disk are reported as warnings at startup.

## Notes

- Session logs are appended to the game folder as `throne_room_voice_debug_injector.log` or `world_voice_debug_injector.log` by default. Use `--log` to redirect.
- The throne-room tool is most useful while the throne room is active. The world-map tool is most representative when triggered from the campaign map.
- Neither tool patches the executable on disk or modifies any audio assets.
