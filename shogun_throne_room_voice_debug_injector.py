#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import json
import os
import pathlib
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
LOCAL_PYDEPS = ROOT / "_pydeps"
if LOCAL_PYDEPS.exists():
    sys.path.insert(0, str(LOCAL_PYDEPS))

try:
    import pefile  # type: ignore
except ImportError as exc:  # pragma: no cover - release environment guard
    raise SystemExit(
        "Missing dependency 'pefile'. Install it with 'pip install pefile' "
        f"or place it under {LOCAL_PYDEPS}."
    ) from exc

GAME_DIR = ROOT / "Total War Shogun 1 Gold"
GAME_EXE = GAME_DIR / "ShogunM.exe"
PROCESS_NAME = "ShogunM.exe"
DEFAULT_CLIPS = pathlib.Path(__file__).with_name("shogun_voice_debug_clips.json")
DEFAULT_LOG_NAME = "throne_room_voice_debug_injector.log"

PEEK_IAT_VA = 0x00400000 + 0x31B2C4
G_PATH_VA = 0x00C978E0
G_TYPE_VA = 0x00C979AC
G_VOICE_ROOT_PTR_VA = 0x0072ED10
G_CTX_A_VA = 0x00C979C4
G_CTX_B_VA = 0x00C979C8
G_CTX_C_VA = 0x00C97F10
G_AUDIO_PRIMARY_VA = 0x00C97970
G_AUDIO_SECONDARY_VA = 0x00C97980
G_ADVISOR_DELAY_VA = 0x00C97984
G_SCRIPT_SECONDARY_VA = 0x00C97988
FN_CLEANUP70_VA = 0x00598720
FN_CLEANUP80_VA = 0x005987E0
FN_OTHER_START_VA = 0x00599C80
FN_ADVISOR_START_VA = 0x00599350
FN_ALLOC_VA = 0x006FC29F
FN_AUDIO_INIT_VA = 0x005B82B0
FN_SET_DELAY_VA = 0x005B7D20
FN_SUBTITLE_START_VA = 0x0059AA80
FN_CTX_SET_DELAY_VA = 0x0059ADE0
FN_SCRIPT_TOKEN_BUILD_VA = 0x0059B950
FN_SCRIPT_OBJECT_INIT_VA = 0x005A0D70
FN_I64_SCALE_DIV_VA = 0x006FC170
FN_I64_DIVIDE_VA = 0x006FE600

STATE_OFF = 0x800
ALLOC_SIZE = 0x40000
HOOK_MAGIC = 0x31474456  # "VDG1"
PAGE_SIZE = 20
ADVISOR_SCRIPT_DELAY = 0x0C3D
ADVISOR_TOKEN_TABLE_VA = 0x00732DC0
ADVISOR_DURATION_BUCKETS = (0xF5, 0x131, 0x195, 0x208)

ACTION_NONE = 0
ACTION_READY = 1
ACTION_PREV = 2
ACTION_NEXT = 3
ACTION_PLAY = 4
ACTION_ERROR = 5
ACTION_STOPPED = 6
ACTION_SELECT = 7

PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_CREATE_THREAD = 0x0002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_EXECUTE_READWRITE = 0x40
PAGE_READWRITE = 0x04
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_NO_MORE_FILES = 18
ERROR_BAD_LENGTH = 24
ERROR_PARTIAL_COPY = 299

STATE_STRUCT = struct.Struct("<16I")
STATE_COMMAND_OFFSET = STATE_OFF + (13 * 4)
STATE_COMMAND_ARG_OFFSET = STATE_OFF + (14 * 4)

CMD_NONE = 0
CMD_PREV = 1
CMD_NEXT = 2
CMD_PLAY = 3
CMD_SELECT = 4


def normalize_game_path(raw: str) -> str:
    value = raw.strip().replace("/", "\\")
    while "\\\\" in value:
        value = value.replace("\\\\", "\\")
    value = value.lstrip("\\")
    return "\\" + value


def guess_token_for_path(game_path: str) -> str | None:
    lower = game_path.lower()
    stem = pathlib.PureWindowsPath(game_path).stem
    if "\\port_trader\\" in lower:
        return "portuguese_out"
    if "\\dutch_trader\\" in lower:
        return "dutch_out"
    if "\\messenger\\head_on_plate\\" in lower:
        return "head_out"
    if "\\priest\\response\\" in lower and stem:
        return f"priest_out_{stem}"
    if "\\emissary\\response\\" in lower and stem:
        return f"hostemiss_{stem}"
    if "\\fat_vis\\" in lower and "\\accepted\\" in lower:
        return "fatvis_accept"
    if "\\fat_vis\\" in lower and "\\rejected\\" in lower:
        return "fatvis_decline"
    return None


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def game_path_to_loc_key(game_path: str) -> str:
    path = pathlib.PureWindowsPath(game_path.lstrip("\\"))
    return str(path.with_suffix("")).replace("/", "\\")


def load_localized_strings(path: pathlib.Path) -> dict[str, str]:
    strings: dict[str, str] = {}
    if not path.exists():
        return strings

    current_key: str | None = None
    current_text: list[str] | None = None
    inline_re = re.compile(r'^\["(.+)"\]\s*\{"(.*)"\}\s*$')
    key_re = re.compile(r'^\["(.+)"\]$')
    text_re = re.compile(r'^\{"(.*)"\}\s*$')

    for raw_line in path.read_text(encoding="latin-1").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue

        if current_key is not None and current_text is not None:
            if stripped.endswith('"}'):
                current_text.append(stripped[:-2])
                strings[current_key.lower()] = "\n".join(current_text)
                current_key = None
                current_text = None
            else:
                current_text.append(stripped)
            continue

        inline_match = inline_re.match(stripped)
        if inline_match:
            strings[inline_match.group(1).lower()] = inline_match.group(2)
            current_key = None
            continue

        key_match = key_re.match(stripped)
        if key_match:
            current_key = key_match.group(1)
            continue
        if current_key is None:
            continue
        text_match = text_re.match(stripped)
        if text_match:
            strings[current_key.lower()] = text_match.group(1)
            current_key = None
            current_text = None
            continue

        if stripped.startswith('{"'):
            payload = stripped[2:]
            if payload.endswith('"}'):
                strings[current_key.lower()] = payload[:-2]
                current_key = None
                current_text = None
            else:
                current_text = [payload]

    return strings


def infer_clip_behavior(game_path: str) -> dict[str, Any]:
    path = pathlib.PureWindowsPath(game_path.lstrip("\\"))
    parts = list(path.parts)
    lower_parts = [part.lower() for part in parts]
    stem = path.stem

    result = {
        "method": "advisor_start",
        "token": "",
        "advisor_type": 0,
        "play_mode": "generic",
    }

    if not parts or lower_parts[0] != "throne":
        return result

    if len(parts) >= 2 and lower_parts[1] == "advisor":
        result["method"] = "advisor_quote_start"
        result["play_mode"] = "native"
        return result

    if len(parts) >= 2 and lower_parts[1] == "port_trader":
        result["method"] = "other_start"
        result["token"] = "portuguese_out"
        result["play_mode"] = "native"
        return result

    if len(parts) >= 2 and lower_parts[1] == "dutch_trader":
        result["method"] = "other_start"
        result["token"] = "dutch_out"
        result["play_mode"] = "native"
        return result

    if len(parts) >= 3 and lower_parts[1] == "messenger" and lower_parts[2] == "head_on_plate":
        result["method"] = "other_start"
        result["token"] = "head_out"
        result["play_mode"] = "native"
        return result

    if len(parts) >= 2 and lower_parts[1] == "messenger":
        result["method"] = "other_start"
        result["token"] = "sam_out"
        result["play_mode"] = "native"
        return result

    if len(parts) >= 2 and lower_parts[1] == "priest" and stem:
        result["method"] = "other_start"
        result["token"] = f"priest_out_{stem}"
        result["play_mode"] = "native"
        return result

    if len(parts) >= 2 and lower_parts[1] == "emissary" and stem:
        result["method"] = "other_start"
        result["token"] = f"hostemiss_{stem}"
        result["play_mode"] = "native"
        return result

    if len(parts) >= 2 and lower_parts[1] == "fat_vis":
        result["method"] = "other_start"
        result["token"] = "fatvis_decline" if "rejected" in lower_parts else "fatvis_accept"
        result["play_mode"] = "native"
        return result

    guessed = guess_token_for_path(game_path)
    if guessed:
        result["method"] = "other_start"
        result["token"] = guessed
        result["play_mode"] = "native"
    return result


def candidate_loc_keys(game_path: str) -> list[str]:
    path = pathlib.PureWindowsPath(game_path.lstrip("\\"))
    loc_key = game_path_to_loc_key(game_path)
    candidates = [loc_key.lower()]
    stem = path.stem.lower()
    if stem and stem not in candidates:
        candidates.append(stem)
    stem_no_digits = re.sub(r"\d+$", "", stem)
    if stem_no_digits and stem_no_digits not in candidates:
        candidates.append(stem_no_digits)
    return candidates


def is_world_event_messenger_path(game_path: str) -> bool:
    path = pathlib.PureWindowsPath(game_path.lstrip("\\"))
    parts = [part.lower() for part in path.parts]
    return len(parts) >= 3 and parts[0] == "throne" and parts[1] == "messenger" and parts[2] in {"birth", "harvest"}


def load_voice_localizations(game_dir: pathlib.Path) -> dict[str, str]:
    merged: dict[str, str] = {}
    loc_dir = game_dir / "Loc" / "Eng"
    if not loc_dir.exists():
        return merged
    for loc_path in sorted(loc_dir.iterdir()):
        if loc_path.is_file() and loc_path.suffix.lower() == ".txt":
            merged.update(load_localized_strings(loc_path))
    return merged


def resolve_nasm() -> pathlib.Path:
    configured = os.environ.get("NASM")
    if configured:
        candidate = pathlib.Path(configured).expanduser()
        if candidate.exists():
            return candidate

    bundled = ROOT / "_tools" / "NASM" / "nasm.exe"
    if bundled.exists():
        return bundled

    for executable in ("nasm.exe", "nasm"):
        resolved = shutil.which(executable)
        if resolved:
            return pathlib.Path(resolved)

    raise SystemExit(
        "Unable to find NASM. Install nasm and make sure it is on PATH, "
        "or set the NASM environment variable to nasm.exe."
    )


def resolve_game_exe(path: pathlib.Path) -> pathlib.Path:
    resolved = path.expanduser().resolve()
    if resolved.is_dir():
        resolved = resolved / "ShogunM.exe"
    if not resolved.exists():
        raise SystemExit(f"target not found: {resolved}")
    if resolved.name.lower() != "shogunm.exe":
        raise SystemExit(f"target must be ShogunM.exe or its game folder: {resolved}")
    return resolved


def load_clip_overrides(path: pathlib.Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if not path.exists():
        return {}, []

    raw_items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_items, list):
        raise ValueError(f"{path} must contain a JSON array")

    overrides: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise ValueError(f"{path} item {index} must be an object")
        game_path = normalize_game_path(str(item.get("path", "")))
        if not game_path:
            raise ValueError(f"{path} item {index} is missing path")
        method = str(item.get("method", "other_start")).strip() or "other_start"
        if method not in {"other_start", "advisor_start", "advisor_quote_start"}:
            raise ValueError(f"{path} item {index} has unsupported method {method}")
        overrides[game_path.lower()] = {
            "id": str(item.get("id", "")).strip(),
            "label": str(item.get("label", "")).strip(),
            "method": method,
            "token": str(item.get("token", "")).strip(),
            "advisor_type": int(item.get("advisor_type", 0)) & 0xFFFFFFFF,
        }
    return overrides, warnings


def build_clip_id(loc_key: str, suffix: str) -> str:
    suffix_name = suffix.lstrip(".").lower()
    return f"{slugify(loc_key)}_{suffix_name}"


def load_clip_config(path: pathlib.Path, game_dir: pathlib.Path) -> tuple[list[dict[str, Any]], list[str]]:
    voices_root = game_dir / "Voices"
    throne_root = voices_root / "Throne"
    if not throne_root.exists():
        raise FileNotFoundError(f"Missing throne voice root: {throne_root}")

    localized_strings = load_voice_localizations(game_dir)
    overrides, warnings = load_clip_overrides(path)
    seen_override_paths: set[str] = set()
    clips: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for asset_path in sorted(throne_root.rglob("*")):
        if not asset_path.is_file():
            continue
        if asset_path.suffix.lower() != ".mp3":
            continue

        relative = asset_path.relative_to(voices_root)
        game_path = normalize_game_path(str(relative).replace("/", "\\"))
        if len(game_path) >= 240:
            warnings.append(f"skipping_long_path asset={asset_path}")
            continue
        if game_path.lower() == "\\throne\\voice.mp3":
            continue
        if is_world_event_messenger_path(game_path):
            continue

        loc_key = game_path_to_loc_key(game_path)
        inferred = infer_clip_behavior(game_path)
        override = overrides.get(game_path.lower(), {})
        if override:
            seen_override_paths.add(game_path.lower())
        if inferred["play_mode"] != "native":
            warnings.append(f"skipping_unsupported_mp3 path={game_path}")
            continue

        clip_id = override.get("id") or build_clip_id(loc_key, asset_path.suffix)
        original_clip_id = clip_id
        suffix_counter = 2
        while clip_id in seen_ids:
            clip_id = f"{original_clip_id}_{suffix_counter}"
            suffix_counter += 1
        seen_ids.add(clip_id)

        subtitle = ""
        for key in candidate_loc_keys(game_path):
            subtitle = localized_strings.get(key, "")
            if subtitle:
                break
        label = override.get("label") or loc_key
        token = override.get("token") or inferred["token"]
        method = override.get("method") or inferred["method"]
        advisor_type = int(override.get("advisor_type", inferred["advisor_type"])) & 0xFFFFFFFF
        path_parts = pathlib.PureWindowsPath(game_path.lstrip("\\")).parts
        category = path_parts[1] if len(path_parts) >= 2 else (path_parts[0] if path_parts else "")

        clips.append(
            {
                "id": clip_id,
                "label": label,
                "method": method,
                "play_mode": inferred["play_mode"],
                "path": game_path,
                "token": token,
                "advisor_type": advisor_type,
                "asset_path": str(asset_path),
                "loc_key": loc_key,
                "subtitle": subtitle,
                "extension": asset_path.suffix.lower(),
                "category": category,
            }
        )

    for override_path in sorted(overrides):
        if override_path not in seen_override_paths:
            warnings.append(f"override_without_asset path={override_path}")

    return clips, warnings


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CreateToolhelp32Snapshot = kernel32.CreateToolhelp32Snapshot
CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
CreateToolhelp32Snapshot.restype = wintypes.HANDLE

Module32FirstW = kernel32.Module32FirstW
Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
Module32FirstW.restype = wintypes.BOOL

Module32NextW = kernel32.Module32NextW
Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
Module32NextW.restype = wintypes.BOOL

Process32FirstW = kernel32.Process32FirstW
Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
Process32FirstW.restype = wintypes.BOOL

Process32NextW = kernel32.Process32NextW
Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
Process32NextW.restype = wintypes.BOOL

OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
OpenProcess.restype = wintypes.HANDLE

ReadProcessMemory = kernel32.ReadProcessMemory
ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
ReadProcessMemory.restype = wintypes.BOOL

WriteProcessMemory = kernel32.WriteProcessMemory
WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
WriteProcessMemory.restype = wintypes.BOOL

VirtualAllocEx = kernel32.VirtualAllocEx
VirtualAllocEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
VirtualAllocEx.restype = wintypes.LPVOID

VirtualFreeEx = kernel32.VirtualFreeEx
VirtualFreeEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD]
VirtualFreeEx.restype = wintypes.BOOL

VirtualProtectEx = kernel32.VirtualProtectEx
VirtualProtectEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
VirtualProtectEx.restype = wintypes.BOOL

WaitForSingleObject = kernel32.WaitForSingleObject
WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
WaitForSingleObject.restype = wintypes.DWORD

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]
CloseHandle.restype = wintypes.BOOL


def check_bool(result: int | bool, func_name: str) -> None:
    if not result:
        raise ctypes.WinError(ctypes.get_last_error(), f"{func_name} failed")


def enumerate_processes() -> list[tuple[int, str]]:
    snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error(), "CreateToolhelp32Snapshot(processes) failed")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        results: list[tuple[int, str]] = []
        if not Process32FirstW(snapshot, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error(), "Process32FirstW failed")
        while True:
            results.append((int(entry.th32ProcessID), entry.szExeFile))
            if not Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return results
    finally:
        CloseHandle(snapshot)


def find_pids_by_name(name: str) -> list[int]:
    target = name.lower()
    pids = [pid for pid, exe_name in enumerate_processes() if exe_name.lower() == target]
    pids.sort(reverse=True)
    return pids


def enumerate_modules(pid: int) -> dict[str, int]:
    snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snapshot == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error(), f"CreateToolhelp32Snapshot(modules, pid={pid}) failed")
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        modules: dict[str, int] = {}
        if not Module32FirstW(snapshot, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error(), f"Module32FirstW(pid={pid}) failed")
        while True:
            modules[entry.szModule.lower()] = int(entry.modBaseAddr)
            if not Module32NextW(snapshot, ctypes.byref(entry)):
                break
        return modules
    finally:
        CloseHandle(snapshot)


def wait_for_required_modules(
    pid: int,
    required: list[str],
    *,
    timeout_sec: float = 15.0,
    poll_sec: float = 0.2,
    spawned: subprocess.Popen[bytes] | None = None,
) -> dict[str, int]:
    deadline = time.time() + timeout_sec
    last_exc: BaseException | None = None
    normalized = [name.lower() for name in required]

    while time.time() < deadline:
        if spawned is not None:
            exit_code = spawned.poll()
            if exit_code is not None:
                raise SystemExit(
                    f"ShogunM.exe exited before it became injectable (pid={pid}, exit_code={exit_code})."
                )

        try:
            modules = enumerate_modules(pid)
        except OSError as exc:
            if exc.winerror in {ERROR_NO_MORE_FILES, ERROR_BAD_LENGTH, ERROR_PARTIAL_COPY}:
                last_exc = exc
                time.sleep(poll_sec)
                continue
            raise

        missing = [name for name in normalized if name not in modules]
        if not missing:
            return modules

        last_exc = RuntimeError(
            f"target process missing required modules: {', '.join(missing)}"
        )
        time.sleep(poll_sec)

    if last_exc is not None:
        raise SystemExit(
            f"Timed out waiting for ShogunM.exe module initialization (pid={pid}). "
            f"Last issue: {last_exc}"
        )

    raise SystemExit(f"Timed out waiting for ShogunM.exe module initialization (pid={pid}).")


def choose_attachable_pid(name: str, required: list[str]) -> int:
    pids = find_pids_by_name(name)
    if not pids:
        raise SystemExit("ShogunM.exe is not running. Start it first or use --spawn.")

    issues: list[str] = []
    for pid in pids:
        try:
            wait_for_required_modules(pid, required, timeout_sec=1.5, poll_sec=0.15)
            return pid
        except BaseException as exc:
            issues.append(f"{pid}: {exc}")

    raise SystemExit(
        "Found ShogunM.exe processes but none became attachable. "
        f"Candidates: {', '.join(issues)}"
    )


def export_rva(dll_path: pathlib.Path, export_name: str) -> int:
    pe = pefile.PE(str(dll_path))
    try:
        for sym in pe.DIRECTORY_ENTRY_EXPORT.symbols:
            if sym.name and sym.name.decode(errors="replace") == export_name:
                return int(sym.address)
    finally:
        pe.close()
    raise KeyError(f"{export_name} not found in {dll_path}")


def syswow64_dll(name: str) -> pathlib.Path:
    return pathlib.Path(r"C:\Windows\SysWOW64") / name


def read_remote(process: int, address: int, size: int) -> bytes:
    buf = (ctypes.c_ubyte * size)()
    read = ctypes.c_size_t()
    ok = ReadProcessMemory(process, ctypes.c_void_p(address), buf, size, ctypes.byref(read))
    check_bool(ok, "ReadProcessMemory")
    return bytes(buf[: read.value])


def write_remote(process: int, address: int, data: bytes) -> None:
    written = ctypes.c_size_t()
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    ok = WriteProcessMemory(process, ctypes.c_void_p(address), buf, len(data), ctypes.byref(written))
    check_bool(ok, "WriteProcessMemory")
    if written.value != len(data):
        raise RuntimeError(f"short WriteProcessMemory at 0x{address:08X}: {written.value} != {len(data)}")


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
    clip_off = align_up(STATE_OFF + 0x100, 0x100)
    str_off = align_up(clip_off + (len(clips) * 16) + 16, 0x100)

    for index, clip in enumerate(clips):
        path_label = f"clip_path_{index}"
        token_label = f"clip_token_{index}" if clip["token"] else "0"
        method_value = {
            "other_start": 0,
            "advisor_start": 1,
            "advisor_quote_start": 2,
        }.get(clip["method"], 0xFFFFFFFF)
        clip_entries.append(
            f"    dd {path_label}\n"
            f"    dd {token_label}\n"
            f"    dd {method_value}\n"
            f"    dd 0x{clip['advisor_type']:08X}"
        )
        string_defs.append(f"{path_label}: db '{clip['path']}', 0")
        if clip["token"]:
            string_defs.append(f"clip_token_{index}: db '{clip['token']}', 0")

    asm = f"""BITS 32
ORG 0x{remote_base:08X}

%define HOOK_MAGIC          0x{HOOK_MAGIC:08X}
%define ACTION_NONE         {ACTION_NONE}
%define ACTION_READY        {ACTION_READY}
%define ACTION_PREV         {ACTION_PREV}
%define ACTION_NEXT         {ACTION_NEXT}
%define ACTION_PLAY         {ACTION_PLAY}
%define ACTION_ERROR        {ACTION_ERROR}
%define ACTION_STOPPED      {ACTION_STOPPED}
%define ACTION_SELECT       {ACTION_SELECT}

%define CMD_NONE            {CMD_NONE}
%define CMD_PREV            {CMD_PREV}
%define CMD_NEXT            {CMD_NEXT}
%define CMD_PLAY            {CMD_PLAY}
%define CMD_SELECT          {CMD_SELECT}

%define G_PATH              0x{G_PATH_VA:08X}
%define G_TYPE              0x{G_TYPE_VA:08X}
%define G_VOICE_ROOT_PTR    0x{G_VOICE_ROOT_PTR_VA:08X}
%define G_CTX_A             0x{G_CTX_A_VA:08X}
%define G_CTX_B             0x{G_CTX_B_VA:08X}
%define G_CTX_C             0x{G_CTX_C_VA:08X}
%define G_AUDIO_PRIMARY     0x{G_AUDIO_PRIMARY_VA:08X}
%define G_AUDIO_SECONDARY   0x{G_AUDIO_SECONDARY_VA:08X}
%define G_ADVISOR_DELAY     0x{G_ADVISOR_DELAY_VA:08X}
%define G_SCRIPT_SECONDARY  0x{G_SCRIPT_SECONDARY_VA:08X}
%define FN_CLEANUP70        0x{FN_CLEANUP70_VA:08X}
%define FN_CLEANUP80        0x{FN_CLEANUP80_VA:08X}
%define FN_OTHER_START      0x{FN_OTHER_START_VA:08X}
%define FN_ADVISOR_START    0x{FN_ADVISOR_START_VA:08X}
%define FN_ALLOC            0x{FN_ALLOC_VA:08X}
%define FN_AUDIO_INIT       0x{FN_AUDIO_INIT_VA:08X}
%define FN_SET_DELAY        0x{FN_SET_DELAY_VA:08X}
%define FN_SUBTITLE_START   0x{FN_SUBTITLE_START_VA:08X}
%define FN_CTX_SET_DELAY    0x{FN_CTX_SET_DELAY_VA:08X}
%define FN_SCRIPT_TOKEN     0x{FN_SCRIPT_TOKEN_BUILD_VA:08X}
%define FN_SCRIPT_INIT      0x{FN_SCRIPT_OBJECT_INIT_VA:08X}
%define FN_I64_SCALE_DIV    0x{FN_I64_SCALE_DIV_VA:08X}
%define FN_I64_DIVIDE       0x{FN_I64_DIVIDE_VA:08X}
%define ADVISOR_TOKEN_TABLE 0x{ADVISOR_TOKEN_TABLE_VA:08X}
%define FN_GETASYNCKEYSTATE 0x{remote_get_async:08X}
%define FN_GETCURRENTTID    0x{remote_get_tid:08X}
%define FN_ORIGINAL_PEEK    0x{original_peek:08X}

%define VK_F8               0x77
%define VK_F9               0x78
%define VK_F10              0x79
%define ADVISOR_SCRIPT_DELAY 0x{ADVISOR_SCRIPT_DELAY:08X}
%define CLIP_COUNT          {len(clips)}
%define ENTRY_SIZE          16
%define INITIAL_SELECTION   {initial_selection}
%define STATE_OFF           0x{STATE_OFF:08X}
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

    mov eax, [esi+4]
    mov [state_last_token_ptr], eax

    cmp dword [G_CTX_A], 0
    je .not_ready
    cmp dword [G_CTX_B], 0
    je .not_ready
    cmp dword [G_CTX_C], 0
    je .not_ready

    mov eax, [esi+8]
    cmp eax, 1
    je .do_advisor
    cmp eax, 2
    je .do_advisor_quote
    cmp eax, 0
    jne .bad_method

    push dword [esi+4]
    call FN_OTHER_START
    add esp, 4
    jmp .played

.do_advisor:
    mov eax, [esi+12]
    mov [G_TYPE], eax
    call FN_ADVISOR_START
    jmp .played

.do_advisor_quote:
    call FN_CLEANUP80
    mov al, byte [G_PATH]
    test al, al
    je .audio_default_path
    push dword 0x68
    call FN_ALLOC
    add esp, 4
    mov ebx, eax
    test ebx, ebx
    je .audio_alloc_fail
    xor edx, edx
    mov byte [ebx+1], dl
    mov dword [ebx+4], edx
    mov dword [ebx+8], edx
    mov byte [ebx+0xC], 1
    push dword G_PATH
    mov ecx, ebx
    call FN_AUDIO_INIT
    mov [G_AUDIO_SECONDARY], ebx

    mov ebp, [G_CTX_C]
    mov edx, [ebp+0x38]
    push dword 1
    push edx
    push dword G_PATH
    mov ecx, [G_CTX_A]
    call FN_SUBTITLE_START

    mov ecx, [G_CTX_A]
    push dword ADVISOR_SCRIPT_DELAY
    call FN_CTX_SET_DELAY

    mov ecx, ebx
    push dword ADVISOR_SCRIPT_DELAY
    call FN_SET_DELAY

    mov ecx, [G_AUDIO_SECONDARY]
    mov eax, [ecx+0x40]
    mov edx, [ecx+0x44]
    xor ebp, ebp
    push edx
    push eax
    push ebp
    push dword 0xA
    call FN_I64_SCALE_DIV
    push ebp
    push dword 0x989680
    push edx
    push eax
    call FN_I64_DIVIDE
    sar eax, 3
    lea eax, [eax + eax*2]
    add eax, eax
    mov ebp, 3
    cmp eax, 0x36
    jge .advisor_bucket_short
    xor ebp, ebp
    jmp .advisor_bucket_done

.advisor_bucket_short:
    cmp eax, 0x4A
    jge .advisor_bucket_mid
    mov ebp, 1
    jmp .advisor_bucket_done

.advisor_bucket_mid:
    cmp eax, 0x6A
    jge .advisor_bucket_done
    mov ebp, 2

.advisor_bucket_done:
    push dword 0x10
    call FN_ALLOC
    add esp, 4
    mov edi, eax
    test edi, edi
    je .advisor_store_state
    mov esi, ebp
    shl esi, 2
    mov ecx, [G_CTX_B]
    mov edx, [ADVISOR_TOKEN_TABLE + esi]
    push edx
    call FN_SCRIPT_TOKEN
    push eax
    mov ecx, edi
    call FN_SCRIPT_INIT
    jmp .advisor_finish_state

.advisor_store_state:
    xor edi, edi
    mov esi, ebp
    shl esi, 2

.advisor_finish_state:
    mov [G_SCRIPT_SECONDARY], edi
    mov eax, [advisor_delay_table + esi]
    mov [G_ADVISOR_DELAY], eax
    jmp .played

.audio_default_path:
    mov dword [state_last_error], 13
    mov dword [state_last_kind], ACTION_ERROR
    inc dword [state_last_seq]
    ret

.audio_alloc_fail:
    mov dword [G_AUDIO_SECONDARY], 0
    mov dword [G_SCRIPT_SECONDARY], 0
    mov dword [state_last_error], 12
    mov dword [state_last_kind], ACTION_ERROR
    inc dword [state_last_seq]
    ret

.bad_method:
    mov dword [state_last_error], 2
    mov dword [state_last_kind], ACTION_ERROR
    inc dword [state_last_seq]
    ret

.played:
    mov dword [state_last_error], 0
    mov dword [state_last_kind], ACTION_PLAY
    inc dword [state_trigger_count]
    inc dword [state_last_seq]
    ret

.not_ready:
    mov dword [state_last_error], 10
    mov dword [state_last_kind], ACTION_ERROR
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

advisor_delay_table:
    dd 0x{ADVISOR_DURATION_BUCKETS[0]:08X}
    dd 0x{ADVISOR_DURATION_BUCKETS[1]:08X}
    dd 0x{ADVISOR_DURATION_BUCKETS[2]:08X}
    dd 0x{ADVISOR_DURATION_BUCKETS[3]:08X}

times STR_OFF - ($-$$) db 0

{chr(10).join(string_defs)}
"""
    return asm


def assemble_stub(asm_source: str) -> bytes:
    nasm = resolve_nasm()
    with tempfile.TemporaryDirectory() as tmp:
        asm_path = pathlib.Path(tmp) / "voice_debug_hook.asm"
        bin_path = pathlib.Path(tmp) / "voice_debug_hook.bin"
        asm_path.write_text(asm_source, encoding="utf-8", newline="\n")
        subprocess.run(
            [str(nasm), "-f", "bin", "-o", str(bin_path), str(asm_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return bin_path.read_bytes()


def action_name(value: int) -> str:
    return {
        ACTION_NONE: "none",
        ACTION_READY: "ready",
        ACTION_PREV: "prev",
        ACTION_NEXT: "next",
        ACTION_PLAY: "play",
        ACTION_ERROR: "error",
        ACTION_STOPPED: "stopped",
        ACTION_SELECT: "select",
    }.get(value, f"unknown_{value}")


def decode_state(data: bytes) -> dict[str, int]:
    values = STATE_STRUCT.unpack_from(data[: STATE_STRUCT.size])
    keys = [
        "magic",
        "active",
        "stop",
        "clip_count",
        "selection",
        "last_seq",
        "last_kind",
        "last_clip",
        "trigger_count",
        "last_error",
        "last_token_ptr",
        "last_path_ptr",
        "thread_id",
        "command",
        "command_arg",
        "reserved2",
    ]
    return dict(zip(keys, values))


def open_process(pid: int) -> int:
    access = (
        PROCESS_QUERY_INFORMATION
        | PROCESS_QUERY_LIMITED_INFORMATION
        | PROCESS_VM_OPERATION
        | PROCESS_VM_READ
        | PROCESS_VM_WRITE
        | PROCESS_CREATE_THREAD
    )
    handle = OpenProcess(access, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error(), f"OpenProcess(pid={pid}) failed")
    return int(handle)


def process_is_alive(process: int) -> bool:
    rc = WaitForSingleObject(process, 0)
    return rc == WAIT_TIMEOUT


def patch_pointer(process: int, address: int, value: int) -> None:
    old_protect = wintypes.DWORD()
    ok = VirtualProtectEx(process, ctypes.c_void_p(address), 4, PAGE_READWRITE, ctypes.byref(old_protect))
    check_bool(ok, "VirtualProtectEx(make writable)")
    try:
        write_remote(process, address, struct.pack("<I", value & 0xFFFFFFFF))
    finally:
        restored = wintypes.DWORD()
        ok = VirtualProtectEx(process, ctypes.c_void_p(address), 4, old_protect.value, ctypes.byref(restored))
        check_bool(ok, "VirtualProtectEx(restore)")


def queue_command(process: int, remote_base: int, command: int, arg: int = 0) -> None:
    write_remote(process, remote_base + STATE_COMMAND_ARG_OFFSET, struct.pack("<I", arg & 0xFFFFFFFF))
    write_remote(process, remote_base + STATE_COMMAND_OFFSET, struct.pack("<I", command & 0xFFFFFFFF))


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clip_summary(clip: dict[str, Any], *, width: int = 88) -> str:
    summary = collapse_whitespace(clip.get("subtitle") or clip.get("label") or clip.get("loc_key") or "")
    if not summary:
        summary = clip["path"]
    if len(summary) > width:
        return summary[: width - 3] + "..."
    return summary


def clip_mode_marker(clip: dict[str, Any]) -> str:
    if clip.get("method") == "advisor_quote_start":
        return "Q"
    return "N" if clip.get("play_mode") == "native" else "G"


def format_clip_row(index: int, clip: dict[str, Any], selected_index: int) -> str:
    marker = ">" if index == selected_index else " "
    category = clip.get("category", "")
    return (
        f"{marker}{index + 1:03d} [{clip_mode_marker(clip)}] "
        f"{clip['id']} | {category} | {clip['path']} | {clip_summary(clip)}"
    )


def print_clip_page(clips: list[dict[str, Any]], page: int, selected_index: int, *, page_size: int = PAGE_SIZE) -> None:
    total_pages = max(1, (len(clips) + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    end = min(start + page_size, len(clips))
    print(f"clips page {page}/{total_pages} showing {start + 1}-{end} of {len(clips)}")
    for index in range(start, end):
        print(format_clip_row(index, clips[index], selected_index))


def print_current_clip(clips: list[dict[str, Any]], index: int) -> None:
    if not (0 <= index < len(clips)):
        print(f"current clip is invalid selection={index}")
        return
    clip = clips[index]
    print(f"current {format_clip_row(index, clip, index)}")
    if clip.get("subtitle"):
        print(f"subtitle: {collapse_whitespace(clip['subtitle'])}")


def print_search_results(
    clips: list[dict[str, Any]],
    query: str,
    selected_index: int,
    *,
    limit: int = 40,
) -> None:
    needle = query.casefold()
    matches: list[int] = []
    for index, clip in enumerate(clips):
        haystack = "\n".join(
            [
                clip["id"],
                clip["path"],
                clip.get("label", ""),
                clip.get("loc_key", ""),
                clip.get("subtitle", ""),
                clip.get("category", ""),
            ]
        ).casefold()
        if needle in haystack:
            matches.append(index)

    if not matches:
        print(f"no clips matched '{query}'")
        return

    print(f"matches for '{query}': {len(matches)}")
    for index in matches[:limit]:
        print(format_clip_row(index, clips[index], selected_index))
    if len(matches) > limit:
        print(f"... {len(matches) - limit} more matches omitted")


def resolve_clip_reference(reference: str, clip_index_by_id: dict[str, int], clip_count: int) -> int:
    ref = reference.strip()
    if not ref:
        raise ValueError("missing clip reference")
    if ref.isdigit():
        clip_number = int(ref)
        if not 1 <= clip_number <= clip_count:
            raise ValueError(f"clip number out of range: {clip_number}")
        return clip_number - 1
    clip_index = clip_index_by_id.get(ref.casefold())
    if clip_index is None:
        raise ValueError(f"unknown clip id: {reference}")
    return clip_index


def default_selection_index(clips: list[dict[str, Any]], clip_index_by_id: dict[str, int]) -> int:
    for preferred_id in ("portuguese_offer", "dutch_offer", "priest_rejected_takeda", "emissary_accepted_oda"):
        clip_index = clip_index_by_id.get(preferred_id.casefold())
        if clip_index is not None:
            return clip_index
    return 0


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
    print("markers: [N]=native throne scene route, [Q]=native advisor quote route")


def console_reader(command_queue: queue.Queue[str], stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            line = input("voice> ")
        except EOFError:
            command_queue.put("quit")
            return
        except Exception:
            return
        if stop_event.is_set():
            return
        command_queue.put(line)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inject a main-thread PeekMessageA hotkey hook into ShogunM.exe."
    )
    parser.add_argument("target", nargs="?", help="Game folder or ShogunM.exe path")
    parser.add_argument("--exe", type=pathlib.Path, default=GAME_EXE, help="Path to ShogunM.exe or the game folder")
    parser.add_argument("--clip-config", type=pathlib.Path, default=DEFAULT_CLIPS, help="JSON clip overrides")
    parser.add_argument("--spawn", action="store_true", help="Launch a fresh game instance")
    parser.add_argument("--select", default=None, help="Initial clip number or id")
    parser.add_argument("--play", default=None, help="Queue a clip number or id to play after install")
    parser.add_argument("--log", type=pathlib.Path, default=None, help="Optional console log file path")
    args = parser.parse_args()

    exe_input = pathlib.Path(args.target) if args.target else args.exe
    exe_path = resolve_game_exe(exe_input)
    log_path = args.log if args.log else exe_path.parent / DEFAULT_LOG_NAME

    clips, warnings = load_clip_config(args.clip_config, exe_path.parent)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if not clips:
        raise SystemExit("No voice clips were found.")

    clip_index_by_id = {clip["id"].casefold(): index for index, clip in enumerate(clips)}

    selection_index = default_selection_index(clips, clip_index_by_id)
    if args.select:
        selection_index = resolve_clip_reference(args.select, clip_index_by_id, len(clips))

    play_index: int | None = None
    if args.play:
        play_index = resolve_clip_reference(args.play, clip_index_by_id, len(clips))
        selection_index = play_index

    spawned = None
    pid: int | None = None
    if args.spawn:
        existing_pids = find_pids_by_name(PROCESS_NAME)
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
        pid = choose_attachable_pid(PROCESS_NAME, ["user32.dll", "kernel32.dll"])
        print(f"attaching pid={pid} exe={exe_path}", flush=True)

    modules = wait_for_required_modules(
        pid,
        ["user32.dll", "kernel32.dll"],
        spawned=spawned,
    )
    user32_base = modules["user32.dll"]

    remote_peek = user32_base + export_rva(syswow64_dll("user32.dll"), "PeekMessageA")
    remote_get_async = user32_base + export_rva(syswow64_dll("user32.dll"), "GetAsyncKeyState")
    kernel32_base = modules["kernel32.dll"]
    remote_get_tid = kernel32_base + export_rva(syswow64_dll("kernel32.dll"), "GetCurrentThreadId")

    process = open_process(pid)
    remote_base = 0
    original_iat = 0
    log_file = log_path.open("a", encoding="utf-8", buffering=1)
    selected_index = selection_index
    last_known_state: dict[str, int] | None = None
    pending_remote_commands: list[tuple[int, int, str]] = []
    command_queue: queue.Queue[str] = queue.Queue()
    console_stop = threading.Event()
    console_thread: threading.Thread | None = None

    if sys.stdin.isatty():
        console_thread = threading.Thread(
            target=console_reader,
            args=(command_queue, console_stop),
            name="voice_debug_console",
            daemon=True,
        )
        console_thread.start()

    def write_log(text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {text}"
        print(line, flush=True)
        log_file.write(line + "\n")

    try:
        iat_bytes = read_remote(process, PEEK_IAT_VA, 4)
        original_iat = struct.unpack("<I", iat_bytes)[0]
        write_log(f"peek_iat current=0x{original_iat:08X} expected=0x{remote_peek:08X}")
        if original_iat != remote_peek:
            raise SystemExit(
                f"PeekMessageA IAT entry is not the expected original value; refusing to patch 0x{PEEK_IAT_VA:08X}"
            )

        remote_ptr = VirtualAllocEx(
            process,
            None,
            ALLOC_SIZE,
            MEM_COMMIT | MEM_RESERVE,
            PAGE_EXECUTE_READWRITE,
        )
        if not remote_ptr:
            raise ctypes.WinError(ctypes.get_last_error(), "VirtualAllocEx failed")
        remote_base = int(ctypes.cast(remote_ptr, ctypes.c_void_p).value)
        write_log(f"remote_alloc base=0x{remote_base:08X} size=0x{ALLOC_SIZE:X}")

        asm = build_asm_source(
            remote_base=remote_base,
            original_peek=remote_peek,
            remote_get_async=remote_get_async,
            remote_get_tid=remote_get_tid,
            clips=clips,
            initial_selection=selection_index,
        )
        blob = assemble_stub(asm)
        if len(blob) > ALLOC_SIZE:
            raise RuntimeError(f"assembled blob too large: 0x{len(blob):X} > 0x{ALLOC_SIZE:X}")

        write_remote(process, remote_base, blob)
        write_log(f"stub_written bytes=0x{len(blob):X}")
        patch_pointer(process, PEEK_IAT_VA, remote_base)
        write_log(f"peek_iat patched hook=0x{remote_base:08X}")
        native_count = sum(1 for clip in clips if clip.get("play_mode") == "native")
        generic_count = len(clips) - native_count
        write_log(
            f"catalog loaded throne_mp3_clips={len(clips)} native={native_count} generic={generic_count}"
        )
        write_log(
            f"ready selection={clips[selection_index]['id']} hotkeys='F8 previous, F9 next, F10 play' note='use play only after campaign/throne systems are loaded'"
        )
        if sys.stdin.isatty():
            print_console_help()
            print_clip_page(clips, 1, selection_index)

        last_seq = -1
        play_queued = False
        shutting_down = False
        while True:
            try:
                raw = read_remote(process, remote_base + STATE_OFF, STATE_STRUCT.size)
            except OSError:
                write_log("process_read_failed exiting")
                break

            state = decode_state(raw)
            last_known_state = state
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
                        print_clip_page(clips, page, selected_index)
                    elif command_name == "find":
                        if not command_arg:
                            print("find requires text")
                        else:
                            print_search_results(clips, command_arg, selected_index)
                    elif command_name in {"current", "now"}:
                        print_current_clip(clips, selected_index)
                    elif command_name in {"quit", "exit"}:
                        shutting_down = True
                        write_log("shutdown_requested")
                        break
                    elif command_name == "select":
                        clip_index = resolve_clip_reference(command_arg, clip_index_by_id, len(clips))
                        pending_remote_commands.append(
                            (CMD_SELECT, clip_index, f"queued_command select index={clip_index + 1} clip={clips[clip_index]['id']}")
                        )
                    elif command_name == "play":
                        if command_arg:
                            clip_index = resolve_clip_reference(command_arg, clip_index_by_id, len(clips))
                            pending_remote_commands.append(
                                (CMD_SELECT, clip_index, f"queued_command select index={clip_index + 1} clip={clips[clip_index]['id']}")
                            )
                            pending_remote_commands.append(
                                (CMD_PLAY, 0, f"queued_command play clip={clips[clip_index]['id']}")
                            )
                        else:
                            pending_remote_commands.append(
                                (CMD_PLAY, 0, f"queued_command play clip={clips[selected_index]['id']}")
                            )
                    else:
                        clip_index = resolve_clip_reference(command_text, clip_index_by_id, len(clips))
                        pending_remote_commands.append(
                            (CMD_SELECT, clip_index, f"queued_command select index={clip_index + 1} clip={clips[clip_index]['id']}")
                        )
                except ValueError as exc:
                    print(f"command error: {exc}")

            if shutting_down:
                break

            if play_index is not None and not play_queued and state["active"] == 1:
                pending_remote_commands.append(
                    (CMD_PLAY, 0, f"queued_command play clip={clips[play_index]['id']}")
                )
                play_queued = True

            if state["command"] == CMD_NONE and pending_remote_commands:
                command, arg, description = pending_remote_commands.pop(0)
                queue_command(process, remote_base, command, arg)
                write_log(description)

            if state["last_seq"] != last_seq:
                last_seq = state["last_seq"]
                kind = action_name(state["last_kind"])
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
                    detail = "game_not_ready_for_play" if state["last_error"] == 10 else f"code={state['last_error']}"
                    write_log(
                        f"event=error detail={detail} selection={selection} clip={clip_id}"
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
                write_remote(process, remote_base + STATE_OFF + 8, struct.pack("<I", 1))
                time.sleep(0.2)
        except Exception:
            pass
        try:
            if original_iat and process_is_alive(process):
                patch_pointer(process, PEEK_IAT_VA, original_iat)
                write_log(f"peek_iat restored original=0x{original_iat:08X}")
            elif original_iat:
                write_log("process_already_exited_before_restore")
        except Exception as exc:
            write_log(f"restore_failed error={exc}")
        try:
            if remote_base:
                VirtualFreeEx(process, ctypes.c_void_p(remote_base), 0, MEM_RELEASE)
        except Exception:
            pass
        if console_thread is not None and console_thread.is_alive():
            console_thread.join(timeout=0.2)
        CloseHandle(process)
        log_file.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
