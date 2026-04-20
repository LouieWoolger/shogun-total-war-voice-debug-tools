#!/usr/bin/env python3
from __future__ import annotations

import argparse
import pathlib
import queue
import struct
import subprocess
import sys
import threading
import time
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import shogun_throne_room_voice_debug_injector as base


DEFAULT_LOG_NAME = "world_voice_debug_injector.log"
HOOK_MAGIC = 0x32474456  # "VDG2"
WORLD_AUDIO_OBJECT_VA = 0x00C2881C
FN_WORLD_AUDIO_DESTROY_VA = 0x00547FB0
FN_WORLD_AUDIO_INIT_VA = 0x005B82B0
FN_ALLOC_VA = 0x006FC29F
WORLD_CATEGORIES = ("Herald", "Poems")
WORLD_EVENT_ROOTS = (
    ("Messenger", pathlib.PureWindowsPath("Throne", "Messenger", "Birth")),
    ("Messenger", pathlib.PureWindowsPath("Throne", "Messenger", "Harvest")),
)


def load_world_clip_config(game_dir: pathlib.Path) -> tuple[list[dict[str, Any]], list[str]]:
    voices_root = game_dir / "Voices"
    localized_strings = base.load_voice_localizations(game_dir)
    clips: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    warnings: list[str] = []

    def add_asset(asset_path: pathlib.Path, category: str) -> None:
        relative = asset_path.relative_to(voices_root)
        game_path = base.normalize_game_path(str(relative).replace("/", "\\"))
        loc_key = base.game_path_to_loc_key(game_path)
        clip_id = base.build_clip_id(loc_key, asset_path.suffix)
        original_clip_id = clip_id
        suffix_counter = 2
        while clip_id in seen_ids:
            clip_id = f"{original_clip_id}_{suffix_counter}"
            suffix_counter += 1
        seen_ids.add(clip_id)

        subtitle = ""
        for key in base.candidate_loc_keys(game_path):
            subtitle = localized_strings.get(key, "")
            if subtitle:
                break

        clips.append(
            {
                "id": clip_id,
                "label": loc_key,
                "path": game_path,
                "loc_key": loc_key,
                "subtitle": subtitle,
                "category": category,
                "asset_path": str(asset_path),
            }
        )

    for category in WORLD_CATEGORIES:
        category_root = voices_root / category
        if not category_root.exists():
            warnings.append(f"missing_world_voice_root category={category} path={category_root}")
            continue

        for asset_path in sorted(category_root.rglob("*.mp3")):
            add_asset(asset_path, category)

    for category, relative_root in WORLD_EVENT_ROOTS:
        event_root = voices_root.joinpath(*relative_root.parts)
        if not event_root.exists():
            warnings.append(f"missing_world_event_root category={category} path={event_root}")
            continue
        for asset_path in sorted(event_root.rglob("*.mp3")):
            add_asset(asset_path, category)

    return clips, warnings


def build_asm_source(
    remote_base: int,
    original_peek: int,
    remote_get_async: int,
    remote_get_tid: int,
    clips: list[dict[str, Any]],
    initial_selection: int,
) -> str:
    clip_entries: list[str] = []
    string_defs: list[str] = []
    clip_off = base.align_up(base.STATE_OFF + 0x100, 0x100)
    str_off = base.align_up(clip_off + (len(clips) * 4), 0x100)

    for index, clip in enumerate(clips):
        path_label = f"clip_path_{index}"
        clip_entries.append(f"    dd {path_label}")
        string_defs.append(f"{path_label}: db '{clip['path']}', 0")

    asm = f"""BITS 32
ORG 0x{remote_base:08X}

%define HOOK_MAGIC          0x{HOOK_MAGIC:08X}
%define ACTION_NONE         {base.ACTION_NONE}
%define ACTION_READY        {base.ACTION_READY}
%define ACTION_PREV         {base.ACTION_PREV}
%define ACTION_NEXT         {base.ACTION_NEXT}
%define ACTION_PLAY         {base.ACTION_PLAY}
%define ACTION_ERROR        {base.ACTION_ERROR}
%define ACTION_STOPPED      {base.ACTION_STOPPED}
%define ACTION_SELECT       {base.ACTION_SELECT}

%define CMD_NONE            {base.CMD_NONE}
%define CMD_PREV            {base.CMD_PREV}
%define CMD_NEXT            {base.CMD_NEXT}
%define CMD_PLAY            {base.CMD_PLAY}
%define CMD_SELECT          {base.CMD_SELECT}

%define G_PATH              0x{base.G_PATH_VA:08X}
%define G_VOICE_ROOT_PTR    0x{base.G_VOICE_ROOT_PTR_VA:08X}
%define G_WORLD_AUDIO       0x{WORLD_AUDIO_OBJECT_VA:08X}
%define FN_WORLD_DESTROY    0x{FN_WORLD_AUDIO_DESTROY_VA:08X}
%define FN_WORLD_INIT       0x{FN_WORLD_AUDIO_INIT_VA:08X}
%define FN_ALLOC            0x{FN_ALLOC_VA:08X}
%define FN_GETASYNCKEYSTATE 0x{remote_get_async:08X}
%define FN_GETCURRENTTID    0x{remote_get_tid:08X}
%define FN_ORIGINAL_PEEK    0x{original_peek:08X}

%define VK_F8               0x77
%define VK_F9               0x78
%define VK_F10              0x79
%define CLIP_COUNT          {len(clips)}
%define ENTRY_SIZE          4
%define INITIAL_SELECTION   {initial_selection}
%define STATE_OFF           0x{base.STATE_OFF:08X}
%define CLIP_OFF            0x{clip_off:08X}
%define STR_OFF             0x{str_off:08X}

hook_entry:
    push ebp
    mov ebp, esp
    push dword [ebp+24]
    push dword [ebp+20]
    push dword [ebp+16]
    push dword [ebp+12]
    push dword [ebp+8]
    call FN_ORIGINAL_PEEK
    push eax
    pushfd
    pushad
    call service_hotkeys
    popad
    popfd
    pop eax
    mov esp, ebp
    pop ebp
    ret 20

service_hotkeys:
    cmp dword [state_stop], 0
    jne .done

    cmp dword [state_magic], HOOK_MAGIC
    je .thread_check

    mov dword [state_magic], HOOK_MAGIC
    mov dword [state_active], 1
    mov dword [state_stop], 0
    mov dword [state_clip_count], CLIP_COUNT
    mov dword [state_selection], INITIAL_SELECTION
    mov dword [state_last_seq], 0
    mov dword [state_last_kind], ACTION_NONE
    mov dword [state_last_clip], 0xFFFFFFFF
    mov dword [state_trigger_count], 0
    mov dword [state_last_error], 0
    mov dword [state_last_token_ptr], 0
    mov dword [state_last_path_ptr], 0
    mov dword [state_command], CMD_NONE
    mov dword [state_command_arg], 0
    mov byte [state_key_prev], 0
    mov byte [state_key_next], 0
    mov byte [state_key_play], 0
    call FN_GETCURRENTTID
    mov dword [state_thread_id], eax
    mov dword [state_last_kind], ACTION_READY
    inc dword [state_last_seq]

.thread_check:
    call FN_GETCURRENTTID
    cmp eax, [state_thread_id]
    jne .done

    call consume_command

    push dword VK_F8
    call FN_GETASYNCKEYSTATE
    xor ecx, ecx
    test ax, 0x8000
    setnz cl
    mov al, [state_key_prev]
    mov [state_key_prev], cl
    test al, al
    jne .check_next
    test cl, cl
    je .check_next
    call select_prev

.check_next:
    push dword VK_F9
    call FN_GETASYNCKEYSTATE
    xor ecx, ecx
    test ax, 0x8000
    setnz cl
    mov al, [state_key_next]
    mov [state_key_next], cl
    test al, al
    jne .check_play
    test cl, cl
    je .check_play
    call select_next

.check_play:
    push dword VK_F10
    call FN_GETASYNCKEYSTATE
    xor ecx, ecx
    test ax, 0x8000
    setnz cl
    mov al, [state_key_play]
    mov [state_key_play], cl
    test al, al
    jne .done
    test cl, cl
    je .done
    call play_selected

.done:
    ret

consume_command:
    mov eax, [state_command]
    cmp eax, CMD_NONE
    je .finish
    mov dword [state_command], CMD_NONE
    cmp eax, CMD_PREV
    je select_prev
    cmp eax, CMD_NEXT
    je select_next
    cmp eax, CMD_PLAY
    je play_selected
    cmp eax, CMD_SELECT
    je select_absolute
    mov dword [state_last_error], 3
    mov dword [state_last_kind], ACTION_ERROR
    inc dword [state_last_seq]
.finish:
    ret

select_prev:
    mov eax, [state_selection]
    test eax, eax
    jne .dec
    mov eax, [state_clip_count]
    test eax, eax
    jz .finish
    dec eax
    mov [state_selection], eax
    jmp .record

.dec:
    dec eax
    mov [state_selection], eax

.record:
    mov dword [state_last_kind], ACTION_PREV
    mov eax, [state_selection]
    mov [state_last_clip], eax
    inc dword [state_last_seq]

.finish:
    ret

select_absolute:
    mov ecx, [state_command_arg]
    cmp ecx, [state_clip_count]
    jae .bad_index
    mov [state_selection], ecx
    mov dword [state_last_kind], ACTION_SELECT
    mov [state_last_clip], ecx
    inc dword [state_last_seq]
    ret

.bad_index:
    mov dword [state_last_error], 11
    mov dword [state_last_kind], ACTION_ERROR
    inc dword [state_last_seq]
    ret

select_next:
    mov eax, [state_clip_count]
    test eax, eax
    jz .finish
    mov ecx, [state_selection]
    inc ecx
    cmp ecx, eax
    jb .store
    xor ecx, ecx

.store:
    mov [state_selection], ecx
    mov dword [state_last_kind], ACTION_NEXT
    mov [state_last_clip], ecx
    inc dword [state_last_seq]

.finish:
    ret

destroy_existing:
    mov ecx, [G_WORLD_AUDIO]
    test ecx, ecx
    jz .done
    push dword 1
    call FN_WORLD_DESTROY
    mov dword [G_WORLD_AUDIO], 0
.done:
    ret

play_selected:
    mov eax, [state_clip_count]
    test eax, eax
    jnz .have_clips
    mov dword [state_last_error], 1
    mov dword [state_last_kind], ACTION_ERROR
    inc dword [state_last_seq]
    ret

.have_clips:
    mov eax, [state_selection]
    mov [state_last_clip], eax
    imul eax, ENTRY_SIZE
    lea esi, [clip_table + eax]

    mov eax, G_PATH
    mov [state_last_path_ptr], eax
    mov edi, G_PATH
    mov edx, [G_VOICE_ROOT_PTR]
    test edx, edx
    je .skip_prefix
    call append_string
    dec edi

.skip_prefix:
    mov edx, [esi]
    call append_string

    call destroy_existing

    push dword 0x68
    call FN_ALLOC
    add esp, 4
    mov esi, eax
    test esi, esi
    jnz .have_object
    mov dword [state_last_error], 12
    mov dword [state_last_kind], ACTION_ERROR
    inc dword [state_last_seq]
    ret

.have_object:
    xor edx, edx
    mov [esi+0x01], dl
    mov [esi+0x04], edx
    mov [esi+0x08], edx
    mov byte [esi+0x0C], 0x01
    push dword G_PATH
    mov ecx, esi
    call FN_WORLD_INIT
    mov [G_WORLD_AUDIO], esi
    mov dword [state_last_error], 0
    mov dword [state_last_kind], ACTION_PLAY
    inc dword [state_trigger_count]
    inc dword [state_last_seq]
    ret

append_string:
    push esi
    mov esi, edx
.append_loop:
    lodsb
    stosb
    test al, al
    jne .append_loop
    pop esi
    ret

times STATE_OFF - ($-$$) db 0

state:
state_magic:          dd 0
state_active:         dd 0
state_stop:           dd 0
state_clip_count:     dd 0
state_selection:      dd 0
state_last_seq:       dd 0
state_last_kind:      dd 0
state_last_clip:      dd 0
state_trigger_count:  dd 0
state_last_error:     dd 0
state_last_token_ptr: dd 0
state_last_path_ptr:  dd 0
state_thread_id:      dd 0
state_command:        dd 0
state_command_arg:    dd 0
state_reserved2:      dd 0
state_key_prev:       db 0
state_key_next:       db 0
state_key_play:       db 0
                         db 0

times CLIP_OFF - ($-$$) db 0

clip_table:
{chr(10).join(clip_entries)}

times STR_OFF - ($-$$) db 0

{chr(10).join(string_defs)}
"""
    return asm


def print_console_help() -> None:
    print("commands:")
    print("  list [page]        show numbered clips")
    print("  find <text>        search by id, path, or subtitle")
    print("  <number>           select clip by number")
    print("  select <n|id>      select clip by number or id")
    print("  play               play current selection")
    print("  play <n|id>        select that clip and play it")
    print("  current            show current selection")
    print("  help               show commands")
    print("  quit               detach injector")
    print("categories: Herald, Poems, and world-event messenger Birth/Harvest MP3s")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inject a world-map MP3 debug trigger into ShogunM.exe."
    )
    parser.add_argument("target", nargs="?", help="Game folder or ShogunM.exe path")
    parser.add_argument("--exe", type=pathlib.Path, default=base.GAME_EXE, help="Path to ShogunM.exe or the game folder")
    parser.add_argument("--spawn", action="store_true", help="Launch a fresh game instance")
    parser.add_argument("--select", default=None, help="Initial clip number or id")
    parser.add_argument("--play", default=None, help="Queue a clip number or id to play after install")
    parser.add_argument("--log", type=pathlib.Path, default=None, help="Optional console log file path")
    args = parser.parse_args()

    exe_input = pathlib.Path(args.target) if args.target else args.exe
    exe_path = base.resolve_game_exe(exe_input)
    log_path = args.log if args.log else exe_path.parent / DEFAULT_LOG_NAME

    clips, warnings = load_world_clip_config(exe_path.parent)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if not clips:
        raise SystemExit("No world-map MP3 clips were found.")

    clip_index_by_id = {clip["id"].casefold(): index for index, clip in enumerate(clips)}

    selection_index = 0
    if args.select:
        selection_index = base.resolve_clip_reference(args.select, clip_index_by_id, len(clips))

    play_index: int | None = None
    if args.play:
        play_index = base.resolve_clip_reference(args.play, clip_index_by_id, len(clips))
        selection_index = play_index

    spawned = None
    pid: int | None = None
    if args.spawn:
        existing_pids = base.find_pids_by_name(base.PROCESS_NAME)
        if existing_pids:
            raise SystemExit(
                "Refusing to --spawn because ShogunM.exe is already running "
                f"(pids: {', '.join(str(pid) for pid in existing_pids)}). "
                "Close those processes first, or run without --spawn to attach to an existing game."
            )
        spawned = subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))
        pid = spawned.pid
        print(f"spawned pid={pid} exe={exe_path}", flush=True)
    else:
        pid = base.choose_attachable_pid(base.PROCESS_NAME, ["user32.dll", "kernel32.dll"])
        print(f"attaching pid={pid} exe={exe_path}", flush=True)

    modules = base.wait_for_required_modules(
        pid,
        ["user32.dll", "kernel32.dll"],
        spawned=spawned,
    )
    user32_base = modules["user32.dll"]
    remote_peek = user32_base + base.export_rva(base.syswow64_dll("user32.dll"), "PeekMessageA")
    remote_get_async = user32_base + base.export_rva(base.syswow64_dll("user32.dll"), "GetAsyncKeyState")
    kernel32_base = modules["kernel32.dll"]
    remote_get_tid = kernel32_base + base.export_rva(base.syswow64_dll("kernel32.dll"), "GetCurrentThreadId")

    process = base.open_process(pid)
    remote_base = 0
    original_iat = 0
    log_file = log_path.open("a", encoding="utf-8", buffering=1)
    selected_index = selection_index
    pending_remote_commands: list[tuple[int, int, str]] = []
    command_queue: queue.Queue[str] = queue.Queue()
    console_stop = threading.Event()
    console_thread: threading.Thread | None = None

    if sys.stdin.isatty():
        console_thread = threading.Thread(
            target=base.console_reader,
            args=(command_queue, console_stop),
            name="world_voice_debug_console",
            daemon=True,
        )
        console_thread.start()

    def write_log(text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {text}"
        print(line, flush=True)
        log_file.write(line + "\n")

    try:
        iat_bytes = base.read_remote(process, base.PEEK_IAT_VA, 4)
        original_iat = struct.unpack("<I", iat_bytes)[0]
        write_log(f"peek_iat current=0x{original_iat:08X} expected=0x{remote_peek:08X}")
        if original_iat != remote_peek:
            raise SystemExit(
                f"PeekMessageA IAT entry is not the expected original value; refusing to patch 0x{base.PEEK_IAT_VA:08X}"
            )

        remote_ptr = base.VirtualAllocEx(
            process,
            None,
            base.ALLOC_SIZE,
            base.MEM_COMMIT | base.MEM_RESERVE,
            base.PAGE_EXECUTE_READWRITE,
        )
        if not remote_ptr:
            raise base.ctypes.WinError(base.ctypes.get_last_error(), "VirtualAllocEx failed")
        remote_base = int(base.ctypes.cast(remote_ptr, base.ctypes.c_void_p).value)
        write_log(f"remote_alloc base=0x{remote_base:08X} size=0x{base.ALLOC_SIZE:X}")

        asm = build_asm_source(
            remote_base=remote_base,
            original_peek=remote_peek,
            remote_get_async=remote_get_async,
            remote_get_tid=remote_get_tid,
            clips=clips,
            initial_selection=selection_index,
        )
        blob = base.assemble_stub(asm)
        if len(blob) > base.ALLOC_SIZE:
            raise RuntimeError(f"assembled blob too large: 0x{len(blob):X} > 0x{base.ALLOC_SIZE:X}")

        base.write_remote(process, remote_base, blob)
        write_log(f"stub_written bytes=0x{len(blob):X}")
        base.patch_pointer(process, base.PEEK_IAT_VA, remote_base)
        write_log(f"peek_iat patched hook=0x{remote_base:08X}")
        write_log(f"catalog loaded world_mp3_clips={len(clips)}")
        write_log(
            f"ready selection={clips[selection_index]['id']} hotkeys='F8 previous, F9 next, F10 play' note='use from the campaign/world-map flow for the closest behavior'"
        )
        if sys.stdin.isatty():
            print_console_help()
            base.print_clip_page(clips, 1, selection_index, page_size=base.PAGE_SIZE)

        last_seq = -1
        play_queued = False
        shutting_down = False
        while True:
            try:
                raw = base.read_remote(process, remote_base + base.STATE_OFF, base.STATE_STRUCT.size)
            except OSError:
                write_log("process_read_failed exiting")
                break

            state = base.decode_state(raw)
            if state["magic"] != HOOK_MAGIC:
                time.sleep(0.25)
                continue

            while True:
                try:
                    console_command = command_queue.get_nowait()
                except queue.Empty:
                    break

                command_text = console_command.strip()
                if not command_text:
                    continue

                parts = command_text.split(maxsplit=1)
                command_name = parts[0].casefold()
                command_arg = parts[1].strip() if len(parts) > 1 else ""

                try:
                    if command_name in {"help", "?"}:
                        print_console_help()
                    elif command_name in {"list", "ls"}:
                        page = int(command_arg) if command_arg else 1
                        base.print_clip_page(clips, page, selected_index, page_size=base.PAGE_SIZE)
                    elif command_name == "find":
                        if not command_arg:
                            print("find requires text")
                        else:
                            base.print_search_results(clips, command_arg, selected_index)
                    elif command_name in {"current", "now"}:
                        base.print_current_clip(clips, selected_index)
                    elif command_name in {"quit", "exit"}:
                        shutting_down = True
                        write_log("shutdown_requested")
                        break
                    elif command_name == "select":
                        clip_index = base.resolve_clip_reference(command_arg, clip_index_by_id, len(clips))
                        pending_remote_commands.append(
                            (base.CMD_SELECT, clip_index, f"queued_command select index={clip_index + 1} clip={clips[clip_index]['id']}")
                        )
                    elif command_name == "play":
                        if command_arg:
                            clip_index = base.resolve_clip_reference(command_arg, clip_index_by_id, len(clips))
                            pending_remote_commands.append(
                                (base.CMD_SELECT, clip_index, f"queued_command select index={clip_index + 1} clip={clips[clip_index]['id']}")
                            )
                            pending_remote_commands.append(
                                (base.CMD_PLAY, 0, f"queued_command play clip={clips[clip_index]['id']}")
                            )
                        else:
                            pending_remote_commands.append(
                                (base.CMD_PLAY, 0, f"queued_command play clip={clips[selected_index]['id']}")
                            )
                    else:
                        clip_index = base.resolve_clip_reference(command_text, clip_index_by_id, len(clips))
                        pending_remote_commands.append(
                            (base.CMD_SELECT, clip_index, f"queued_command select index={clip_index + 1} clip={clips[clip_index]['id']}")
                        )
                except ValueError as exc:
                    print(f"command error: {exc}")

            if shutting_down:
                break

            if play_index is not None and not play_queued and state["active"] == 1:
                pending_remote_commands.append(
                    (base.CMD_PLAY, 0, f"queued_command play clip={clips[play_index]['id']}")
                )
                play_queued = True

            if state["command"] == base.CMD_NONE and pending_remote_commands:
                command, arg, description = pending_remote_commands.pop(0)
                base.queue_command(process, remote_base, command, arg)
                write_log(description)

            if state["last_seq"] != last_seq:
                last_seq = state["last_seq"]
                kind = base.action_name(state["last_kind"])
                selection = state["selection"]
                if 0 <= selection < len(clips):
                    selected_index = selection
                clip = clips[selection] if 0 <= selection < len(clips) else None
                clip_id = clip["id"] if clip else "<invalid>"
                clip_path = clip["path"] if clip else "<invalid>"
                if kind in {"prev", "next", "ready", "select"}:
                    write_log(
                        f"event={kind} selection={selection} clip={clip_id} path={clip_path} thread=0x{state['thread_id']:08X}"
                    )
                elif kind == "play":
                    write_log(
                        f"event=play selection={selection} clip={clip_id} path={clip_path} triggers={state['trigger_count']}"
                    )
                elif kind == "error":
                    write_log(
                        f"event=error detail=code={state['last_error']} selection={selection} clip={clip_id}"
                    )
                elif kind == "stopped":
                    write_log("event=stopped")
                    break

            time.sleep(0.15)
    except KeyboardInterrupt:
        write_log("shutdown_requested")
    finally:
        console_stop.set()
        try:
            if remote_base:
                base.write_remote(process, remote_base + base.STATE_OFF + 8, struct.pack("<I", 1))
                time.sleep(0.2)
        except Exception:
            pass
        try:
            if original_iat and base.process_is_alive(process):
                base.patch_pointer(process, base.PEEK_IAT_VA, original_iat)
                write_log(f"peek_iat restored original=0x{original_iat:08X}")
            elif original_iat:
                write_log("process_already_exited_before_restore")
        except Exception as exc:
            write_log(f"restore_failed error={exc}")
        try:
            if remote_base:
                base.VirtualFreeEx(process, base.ctypes.c_void_p(remote_base), 0, base.MEM_RELEASE)
        except Exception:
            pass
        if console_thread is not None and console_thread.is_alive():
            console_thread.join(timeout=0.2)
        base.CloseHandle(process)
        log_file.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
