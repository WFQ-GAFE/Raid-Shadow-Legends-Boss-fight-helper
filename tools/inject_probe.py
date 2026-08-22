#!/usr/bin/env python3
"""Load the observe-only agent into one explicitly selected Raid.exe process.

The script validates the target path and architecture, creates the result pipe,
loads the DLL through LoadLibraryW, and prints the agent's JSON readiness report.
It does not expose arbitrary DLL or non-Raid process loading.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import struct
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from agent_ipc import AGENT_BUILD_ID, COMMAND_VERSION, AgentIpc, read_agent_status
from chimera_inventory import inspect_pe
from raid_processes import is_supported_raid_executable, raid_processes


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
DEFAULT_AGENT = PROJECT_ROOT / "build" / "agent-1231" / "Release" / "RaidChimeraAgent.dll"
PIPE_NAME_PREFIX = r"\\.\pipe\RaidChimeraPrototype-"

PROCESS_CREATE_THREAD = 0x0002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_READ = 0x0010
PROCESS_RIGHTS = (
    PROCESS_CREATE_THREAD
    | PROCESS_QUERY_INFORMATION
    | PROCESS_VM_OPERATION
    | PROCESS_VM_WRITE
    | PROCESS_VM_READ
)
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

PIPE_ACCESS_INBOUND = 0x00000001
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
ERROR_PIPE_CONNECTED = 535
ERROR_NOT_FOUND = 1168
RESULT_BUFFER_SIZE = 8 * 1024 * 1024

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.VirtualAllocEx.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.DWORD,
    wintypes.DWORD,
]
kernel32.VirtualAllocEx.restype = wintypes.LPVOID
kernel32.VirtualFreeEx.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.DWORD,
]
kernel32.VirtualFreeEx.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.CreateRemoteThread.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.LPVOID,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.CreateRemoteThread.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GetExitCodeThread.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeThread.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetProcAddress.argtypes = [wintypes.HMODULE, wintypes.LPCSTR]
kernel32.GetProcAddress.restype = wintypes.LPVOID
kernel32.LoadLibraryW.argtypes = [wintypes.LPCWSTR]
kernel32.LoadLibraryW.restype = wintypes.HMODULE
kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]
kernel32.FreeLibrary.restype = wintypes.BOOL
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32FirstW.restype = wintypes.BOOL
kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.restype = wintypes.BOOL
kernel32.CreateNamedPipeW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
]
kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
kernel32.ConnectNamedPipe.restype = wintypes.BOOL
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
kernel32.CancelIoEx.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
kernel32.DisconnectNamedPipe.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def emit(payload: dict[str, object], output: Path | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")


def configure_text_streams() -> None:
    """Keep game localization text independent from the Windows code page."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def fail(reason: str, output: Path | None = None, **details: object) -> int:
    emit({"ok": False, "reason": reason, **details}, output)
    return 1


def remote_module_base(pid: int, wanted: str) -> int:
    snapshot = kernel32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
    )
    if snapshot == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Module32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szModule.casefold() == wanted.casefold():
                return ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value or 0
            ok = kernel32.Module32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    raise RuntimeError(f"Remote module not found: {wanted}")


def result_pipe_name(pid: int) -> str:
    return f"{PIPE_NAME_PREFIX}{pid}"


def create_result_pipe(pid: int) -> int:
    pipe = kernel32.CreateNamedPipeW(
        result_pipe_name(pid),
        PIPE_ACCESS_INBOUND,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE,
        1,
        0,
        RESULT_BUFFER_SIZE,
        0,
        None,
    )
    if pipe == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    return pipe


def read_result_blocking(pipe: int) -> dict[str, object]:
    if not kernel32.ConnectNamedPipe(pipe, None):
        error = ctypes.get_last_error()
        if error != ERROR_PIPE_CONNECTED:
            raise ctypes.WinError(error)
    buffer = ctypes.create_string_buffer(RESULT_BUFFER_SIZE)
    read = wintypes.DWORD()
    if not kernel32.ReadFile(pipe, buffer, len(buffer), ctypes.byref(read), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return json.loads(buffer.raw[: read.value].decode("utf-8"))


def start_result_reader(
    pipe: int,
) -> tuple[
    queue.Queue[tuple[bool, dict[str, object] | BaseException]],
    threading.Thread,
]:
    result_queue: queue.Queue[tuple[bool, dict[str, object] | BaseException]] = queue.Queue(1)

    def worker() -> None:
        try:
            result_queue.put((True, read_result_blocking(pipe)))
        except BaseException as error:
            result_queue.put((False, error))

    reader = threading.Thread(target=worker, name="chimera-probe-pipe", daemon=True)
    reader.start()
    return result_queue, reader


def close_result_pipe(pipe: int, reader: threading.Thread) -> None:
    # ConnectNamedPipe/ReadFile run synchronously on the reader thread. Closing
    # their handle from this thread can block forever when the agent never
    # connects. Cancel the pending I/O first, let the reader unwind, and only
    # then release the pipe handle.
    if not kernel32.CancelIoEx(pipe, None):
        error = ctypes.get_last_error()
        if error not in (0, ERROR_NOT_FOUND):
            raise ctypes.WinError(error)
    reader.join(timeout=2.0)
    kernel32.DisconnectNamedPipe(pipe)
    kernel32.CloseHandle(pipe)
    if reader.is_alive():
        raise TimeoutError("Result pipe reader did not stop after cancellation")


def inject(pid: int, dll: Path) -> dict[str, object]:
    process = kernel32.OpenProcess(PROCESS_RIGHTS, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())

    remote_memory = None
    remote_thread = None
    try:
        encoded = (str(dll) + "\0").encode("utf-16le")
        remote_memory = kernel32.VirtualAllocEx(
            process, None, len(encoded), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
        )
        if not remote_memory:
            raise ctypes.WinError(ctypes.get_last_error())

        written = ctypes.c_size_t()
        source = ctypes.create_string_buffer(encoded)
        if not kernel32.WriteProcessMemory(
            process,
            remote_memory,
            source,
            len(encoded),
            ctypes.byref(written),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(encoded):
            raise RuntimeError(f"Short remote write: {written.value}/{len(encoded)}")

        local_kernel32 = kernel32.GetModuleHandleW("kernel32.dll")
        local_loader = kernel32.GetProcAddress(local_kernel32, b"LoadLibraryW")
        if not local_kernel32 or not local_loader:
            raise ctypes.WinError(ctypes.get_last_error())
        remote_kernel32 = remote_module_base(pid, "kernel32.dll")
        loader_rva = int(local_loader) - int(local_kernel32)
        remote_loader = remote_kernel32 + loader_rva

        thread_id = wintypes.DWORD()
        remote_thread = kernel32.CreateRemoteThread(
            process,
            None,
            0,
            remote_loader,
            remote_memory,
            0,
            ctypes.byref(thread_id),
        )
        if not remote_thread:
            raise ctypes.WinError(ctypes.get_last_error())
        wait = kernel32.WaitForSingleObject(remote_thread, 15000)
        if wait == WAIT_TIMEOUT:
            raise TimeoutError("Remote LoadLibraryW did not finish")
        if wait != WAIT_OBJECT_0:
            raise ctypes.WinError(ctypes.get_last_error())

        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeThread(remote_thread, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value == 0:
            raise RuntimeError("Remote LoadLibraryW returned null")
        return {"threadId": thread_id.value, "loadLibraryResultLow32": exit_code.value}
    finally:
        if remote_thread:
            kernel32.CloseHandle(remote_thread)
        if remote_memory:
            kernel32.VirtualFreeEx(process, remote_memory, 0, MEM_RELEASE)
        kernel32.CloseHandle(process)


def read_process_memory(process: int, address: int, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t()
    if not kernel32.ReadProcessMemory(
        process, address, buffer, size, ctypes.byref(read)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if read.value != size:
        raise RuntimeError(f"Short remote read: {read.value}/{size}")
    return buffer.raw


def remote_export_address(
    process: int, pid: int, module_name: str, export: bytes
) -> int:
    module_base = remote_module_base(pid, module_name)
    dos = read_process_memory(process, module_base, 0x40)
    if dos[:2] != b"MZ":
        raise RuntimeError(f"Remote module has no DOS header: {module_name}")
    pe_offset = struct.unpack_from("<I", dos, 0x3C)[0]
    headers = read_process_memory(process, module_base + pe_offset, 0x108)
    if headers[:4] != b"PE\0\0":
        raise RuntimeError(f"Remote module has no PE header: {module_name}")
    optional_offset = 24
    if struct.unpack_from("<H", headers, optional_offset)[0] != 0x20B:
        raise RuntimeError(f"Remote module is not PE32+: {module_name}")
    export_rva, export_size = struct.unpack_from(
        "<II", headers, optional_offset + 112
    )
    if not export_rva or export_size < 40:
        raise RuntimeError(f"Remote module has no export directory: {module_name}")
    directory = read_process_memory(process, module_base + export_rva, 40)
    (
        _characteristics,
        _timestamp,
        _major,
        _minor,
        _name,
        _ordinal_base,
        function_count,
        name_count,
        functions_rva,
        names_rva,
        ordinals_rva,
    ) = struct.unpack("<IIHHIIIIIII", directory)
    if not function_count or not name_count:
        raise RuntimeError(f"Remote module exports no named functions: {module_name}")
    name_rvas = struct.unpack(
        f"<{name_count}I",
        read_process_memory(process, module_base + names_rva, name_count * 4),
    )
    ordinals = struct.unpack(
        f"<{name_count}H",
        read_process_memory(process, module_base + ordinals_rva, name_count * 2),
    )
    function_rvas = struct.unpack(
        f"<{function_count}I",
        read_process_memory(
            process, module_base + functions_rva, function_count * 4
        ),
    )
    for index, name_rva in enumerate(name_rvas):
        raw_name = read_process_memory(process, module_base + name_rva, 256)
        name = raw_name.split(b"\0", 1)[0]
        if name != export:
            continue
        ordinal = ordinals[index]
        if ordinal >= len(function_rvas):
            break
        function_rva = function_rvas[ordinal]
        if export_rva <= function_rva < export_rva + export_size:
            raise RuntimeError(f"Forwarded remote export is not supported: {name!r}")
        return module_base + function_rva
    raise RuntimeError(f"Agent export not found: {export.decode('ascii')}")


def run_remote_function(
    process: int, function: int, parameter: int | None = None
) -> dict[str, object]:
    thread_id = wintypes.DWORD()
    thread = kernel32.CreateRemoteThread(
        process,
        None,
        0,
        function,
        parameter,
        0,
        ctypes.byref(thread_id),
    )
    if not thread:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        wait = kernel32.WaitForSingleObject(thread, 15000)
        if wait == WAIT_TIMEOUT:
            raise TimeoutError("Remote function did not finish")
        if wait != WAIT_OBJECT_0:
            raise ctypes.WinError(ctypes.get_last_error())
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeThread(thread, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {"threadId": thread_id.value, "result": exit_code.value}
    finally:
        kernel32.CloseHandle(thread)


def unload(pid: int, module_name: str, dll: Path) -> dict[str, object]:
    module_base = remote_module_base(pid, module_name)
    process = kernel32.OpenProcess(PROCESS_RIGHTS, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    remote_thread = None
    try:
        shutdown_address = remote_export_address(
            process, pid, module_name, b"RaidChimeraAgentShutdown"
        )
        shutdown_result = run_remote_function(process, shutdown_address)
        if shutdown_result["result"] == 0:
            raise RuntimeError("Agent shutdown export returned false")

        local_kernel32 = kernel32.GetModuleHandleW("kernel32.dll")
        local_free_library = kernel32.GetProcAddress(local_kernel32, b"FreeLibrary")
        if not local_kernel32 or not local_free_library:
            raise ctypes.WinError(ctypes.get_last_error())
        remote_kernel32 = remote_module_base(pid, "kernel32.dll")
        free_library_rva = int(local_free_library) - int(local_kernel32)
        remote_free_library = remote_kernel32 + free_library_rva

        thread_id = wintypes.DWORD()
        remote_thread = kernel32.CreateRemoteThread(
            process,
            None,
            0,
            remote_free_library,
            module_base,
            0,
            ctypes.byref(thread_id),
        )
        if not remote_thread:
            raise ctypes.WinError(ctypes.get_last_error())
        wait = kernel32.WaitForSingleObject(remote_thread, 15000)
        if wait == WAIT_TIMEOUT:
            raise TimeoutError("Remote FreeLibrary did not finish")
        if wait != WAIT_OBJECT_0:
            raise ctypes.WinError(ctypes.get_last_error())
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeThread(remote_thread, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value == 0:
            raise RuntimeError("Remote FreeLibrary returned false")
        return {
            "shutdown": shutdown_result,
            "threadId": thread_id.value,
            "freeLibraryResult": exit_code.value,
            "previousModuleBase": module_base,
        }
    finally:
        if remote_thread:
            kernel32.CloseHandle(remote_thread)
        kernel32.CloseHandle(process)


def queue_command(
    pid: int,
    dll: Path,
    *,
    session_id: int,
    context: int,
    generator: int,
    mode: int,
    skill_data: int,
    target_id: int,
    skill_id: int,
    verified_skill_type_id: int,
    expected_area_id: int,
    expected_region_id: int,
    expected_round: int,
    expected_turn: int,
    expected_player_turn_count: int,
    expected_active_hero_id: int,
    expected_active_hero_turn_count: int,
    expected_active_hero_form_index: int,
    execute: bool,
    nonce: int,
) -> dict[str, object]:
    status = read_agent_status(pid)
    if status is None:
        raise RuntimeError("Agent shared state is unavailable")
    if not status.get("compatible") or not status.get("ready"):
        raise RuntimeError(f"Agent is incompatible or not ready: {status}")
    if status.get("buildId") != AGENT_BUILD_ID or status.get("commandVersion") != COMMAND_VERSION:
        raise RuntimeError(f"Agent ABI does not match this tool: {status}")
    payload = struct.pack(
        "<IIQQQQQiiiiiiiiiiiII4x",
        0x5243484D,
        COMMAND_VERSION,
        session_id,
        context,
        generator,
        mode,
        skill_data,
        target_id,
        skill_id,
        verified_skill_type_id,
        expected_area_id,
        expected_region_id,
        expected_round,
        expected_turn,
        expected_player_turn_count,
        expected_active_hero_id,
        expected_active_hero_turn_count,
        expected_active_hero_form_index,
        1 if execute else 0,
        nonce,
    )
    process = kernel32.OpenProcess(PROCESS_RIGHTS, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    remote_memory = None
    try:
        remote_memory = kernel32.VirtualAllocEx(
            process, None, len(payload), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
        )
        if not remote_memory:
            raise ctypes.WinError(ctypes.get_last_error())
        written = ctypes.c_size_t()
        source = ctypes.create_string_buffer(payload)
        if not kernel32.WriteProcessMemory(
            process,
            remote_memory,
            source,
            len(payload),
            ctypes.byref(written),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(payload):
            raise RuntimeError(f"Short remote write: {written.value}/{len(payload)}")

        function = remote_export_address(
            process, pid, dll.name, b"RaidChimeraAgentQueueCommand"
        )
        invocation = run_remote_function(process, function, remote_memory)
        return {
            "queued": invocation["result"] == 1,
            "agentResult": invocation["result"],
            "threadId": invocation["threadId"],
            "execute": execute,
            "nonce": nonce,
        }
    finally:
        if remote_memory:
            kernel32.VirtualFreeEx(process, remote_memory, 0, MEM_RELEASE)
        kernel32.CloseHandle(process)


def encode_takeover_request(
    *,
    session_id: int,
    active: bool,
    expected_user_id: int | None = None,
    boss_mode: str | None = None,
) -> bytes:
    if (
        not isinstance(session_id, int)
        or isinstance(session_id, bool)
        or session_id <= 0
        or session_id > 0xFFFFFFFFFFFFFFFF
    ):
        raise ValueError("Takeover requires a valid session ID")
    if active and (
        not isinstance(expected_user_id, int)
        or isinstance(expected_user_id, bool)
        or expected_user_id <= 0
        or expected_user_id > 0xFFFFFFFFFFFFFFFF
    ):
        raise ValueError("Beginning takeover requires a valid game user ID")
    if boss_mode is not None and boss_mode not in {"chimera", "hydra"}:
        raise ValueError("Boss mode must be 'chimera' or 'hydra'")
    bound_user_id = expected_user_id if active else 0
    flags = 1 if active else 0
    if active and boss_mode is not None:
        flags |= 2
        if boss_mode == "hydra":
            flags |= 4
    return struct.pack(
        "<IIQIIII",
        0x5243544C,
        1,
        session_id,
        1 if active else 2,
        flags,
        bound_user_id & 0xFFFFFFFF,
        (bound_user_id >> 32) & 0xFFFFFFFF,
    )


def set_takeover(
    pid: int,
    dll: Path,
    *,
    session_id: int,
    active: bool,
    expected_user_id: int | None = None,
    boss_mode: str | None = None,
) -> dict[str, object]:
    status = read_agent_status(pid)
    if status is None or not status.get("compatible") or not status.get("ready"):
        raise RuntimeError(f"Agent is incompatible or not ready: {status}")
    payload = encode_takeover_request(
        session_id=session_id,
        active=active,
        expected_user_id=expected_user_id,
        boss_mode=boss_mode,
    )
    bound_user_id = expected_user_id if active else 0
    process = kernel32.OpenProcess(PROCESS_RIGHTS, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    remote_memory = None
    try:
        remote_memory = kernel32.VirtualAllocEx(
            process, None, len(payload), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
        )
        if not remote_memory:
            raise ctypes.WinError(ctypes.get_last_error())
        written = ctypes.c_size_t()
        source = ctypes.create_string_buffer(payload)
        if not kernel32.WriteProcessMemory(
            process, remote_memory, source, len(payload), ctypes.byref(written)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(payload):
            raise RuntimeError(f"Short remote write: {written.value}/{len(payload)}")
        function = remote_export_address(
            process, pid, dll.name, b"RaidChimeraAgentSetTakeover"
        )
        invocation = run_remote_function(process, function, remote_memory)
        return {
            "accepted": invocation["result"] == 1,
            "agentResult": invocation["result"],
            "threadId": invocation["threadId"],
            "active": active,
            "sessionId": session_id,
            "expectedUserId": bound_user_id or None,
            "bossMode": boss_mode if active else None,
        }
    finally:
        if remote_memory:
            kernel32.VirtualFreeEx(process, remote_memory, 0, MEM_RELEASE)
        kernel32.CloseHandle(process)


def seed_battle_context(pid: int, dll: Path, context: int) -> dict[str, object]:
    status = read_agent_status(pid)
    if status is None or not status.get("compatible") or not status.get("ready"):
        raise RuntimeError(f"Agent is incompatible or not ready: {status}")
    if status.get("buildId") != AGENT_BUILD_ID or context <= 0:
        raise RuntimeError(f"Agent ABI or battle context does not match: {status}")
    process = kernel32.OpenProcess(PROCESS_RIGHTS, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        function = remote_export_address(
            process, pid, dll.name, b"RaidChimeraAgentSeedBattleContext"
        )
        invocation = run_remote_function(process, function, context)
        return {
            "accepted": invocation["result"] == 1,
            "agentResult": invocation["result"],
            "threadId": invocation["threadId"],
            "context": context,
        }
    finally:
        kernel32.CloseHandle(process)


def seed_selection_context(pid: int, dll: Path, context: int) -> dict[str, object]:
    status = read_agent_status(pid)
    if status is None or not status.get("compatible") or not status.get("ready"):
        raise RuntimeError(f"Agent is incompatible or not ready: {status}")
    if status.get("buildId") != AGENT_BUILD_ID or context <= 0:
        raise RuntimeError(f"Agent ABI or selection context does not match: {status}")
    process = kernel32.OpenProcess(PROCESS_RIGHTS, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        function = remote_export_address(
            process, pid, dll.name, b"RaidChimeraAgentSeedSelectionContext"
        )
        invocation = run_remote_function(process, function, context)
        return {
            "accepted": invocation["result"] == 1,
            "agentResult": invocation["result"],
            "threadId": invocation["threadId"],
            "context": context,
        }
    finally:
        kernel32.CloseHandle(process)


def encode_lifecycle_request(
    *,
    session_id: int,
    context: int,
    action: int,
    nonce: int,
    hero_ids: list[int] | None = None,
) -> bytes:
    if (
        not isinstance(session_id, int)
        or isinstance(session_id, bool)
        or not 0 < session_id <= 0xFFFFFFFFFFFFFFFF
        or not isinstance(context, int)
        or isinstance(context, bool)
        or not 0 < context <= 0xFFFFFFFFFFFFFFFF
        or action not in {1, 2, 3, 4, 5, 6}
        or not isinstance(nonce, int)
        or isinstance(nonce, bool)
        or not 0 < nonce <= 0xFFFFFFFF
    ):
        raise ValueError("Invalid lifecycle command envelope")
    selected = list(hero_ids or [])
    if action == 5 and (
        len(selected) != 5
        or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
            for value in selected
        )
        or len(set(selected)) != 5
    ):
        raise ValueError("Selecting a team requires five unique positive hero IDs")
    if action != 5 and selected:
        raise ValueError("Hero IDs are only valid for the select-heroes action")
    padded = selected + [0] * (5 - len(selected))
    return struct.pack(
        "<IIQQIIII5iI",
        0x52434C43,
        2,
        session_id,
        context,
        action,
        0,
        nonce,
        0,
        *padded,
        len(selected),
    )


def queue_lifecycle_command(
    pid: int,
    dll: Path,
    *,
    session_id: int,
    context: int,
    action: int,
    nonce: int,
    hero_ids: list[int] | None = None,
) -> dict[str, object]:
    status = read_agent_status(pid)
    if status is None or not status.get("compatible") or not status.get("ready"):
        raise RuntimeError(f"Agent is incompatible or not ready: {status}")
    if status.get("buildId") != AGENT_BUILD_ID:
        raise RuntimeError(f"Agent ABI does not match this tool: {status}")
    selected = list(hero_ids or [])
    payload = encode_lifecycle_request(
        session_id=session_id,
        context=context,
        action=action,
        nonce=nonce,
        hero_ids=selected,
    )
    process = kernel32.OpenProcess(PROCESS_RIGHTS, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    remote_memory = None
    try:
        remote_memory = kernel32.VirtualAllocEx(
            process, None, len(payload), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
        )
        if not remote_memory:
            raise ctypes.WinError(ctypes.get_last_error())
        written = ctypes.c_size_t()
        source = ctypes.create_string_buffer(payload)
        if not kernel32.WriteProcessMemory(
            process, remote_memory, source, len(payload), ctypes.byref(written)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(payload):
            raise RuntimeError(f"Short remote write: {written.value}/{len(payload)}")
        function = remote_export_address(
            process,
            pid,
            dll.name,
            b"RaidChimeraAgentQueueLifecycleCommand",
        )
        invocation = run_remote_function(process, function, remote_memory)
        return {
            "queued": invocation["result"] == 1,
            "agentResult": invocation["result"],
            "threadId": invocation["threadId"],
            "action": action,
            "nonce": nonce,
            "heroIds": selected,
        }
    finally:
        if remote_memory:
            kernel32.VirtualFreeEx(process, remote_memory, 0, MEM_RELEASE)
        kernel32.CloseHandle(process)


def main() -> int:
    configure_text_streams()
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True, help="Exact Raid.exe PID")
    parser.add_argument("--agent", type=Path, default=DEFAULT_AGENT)
    parser.add_argument("--output", type=Path, help="Optional generated JSON result path")
    parser.add_argument(
        "--check-only", action="store_true", help="Only check whether the agent is loaded"
    )
    parser.add_argument(
        "--unload", action="store_true", help="Unload only the exact project agent module"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Unload an existing exact project agent module before probing",
    )
    parser.add_argument(
        "--command-probe",
        action="store_true",
        help="Queue a main-thread guard probe without executing a command",
    )
    parser.add_argument(
        "--cast",
        action="store_true",
        help="Queue one guarded main-thread manual command",
    )
    parser.add_argument("--begin-takeover", action="store_true")
    parser.add_argument("--end-takeover", action="store_true")
    parser.add_argument("--seed-selection-context", action="store_true")
    parser.add_argument("--seed-battle-context", action="store_true")
    parser.add_argument("--context", type=lambda value: int(value, 0))
    parser.add_argument("--generator", type=lambda value: int(value, 0))
    parser.add_argument("--mode", type=lambda value: int(value, 0))
    parser.add_argument("--skill-data", type=lambda value: int(value, 0))
    parser.add_argument("--target-id", type=int)
    parser.add_argument("--skill-id", type=int)
    parser.add_argument("--verified-skill-type-id", type=int)
    parser.add_argument("--expected-area-id", type=int, default=-(2**31))
    parser.add_argument("--expected-region-id", type=int, default=-(2**31))
    parser.add_argument("--expected-round", type=int, default=-(2**31))
    parser.add_argument("--expected-turn", type=int, default=-(2**31))
    parser.add_argument("--expected-player-turn-count", type=int, default=-(2**31))
    parser.add_argument("--expected-active-hero-id", type=int, default=-(2**31))
    parser.add_argument(
        "--expected-active-hero-turn-count", type=int, default=-(2**31)
    )
    parser.add_argument(
        "--expected-active-hero-form-index", type=int, default=-(2**31)
    )
    parser.add_argument("--session-id", type=lambda value: int(value, 0), default=1)
    parser.add_argument("--expected-user-id", type=lambda value: int(value, 0))
    args = parser.parse_args()
    output = args.output.resolve() if args.output else None

    agent = args.agent.resolve()
    if not agent.is_file() or agent.name != "RaidChimeraAgent.dll":
        return fail("invalid_agent_path", output, path=str(agent))
    if inspect_pe(agent)["machine"] != "x64":
        return fail("agent_is_not_x64", output, path=str(agent))

    processes = raid_processes()
    target = processes.get(args.pid)
    if not target:
        return fail("pid_is_not_running_raid", output, pid=args.pid)
    target_path = target.get("path")
    if not isinstance(target_path, str):
        return fail("cannot_read_target_path", output, pid=args.pid)
    target_file = Path(target_path).resolve()
    if not is_supported_raid_executable(target_file):
        return fail(
            "unexpected_raid_installation",
            output,
            actual=str(target_file),
        )

    if args.check_only:
        try:
            module_base = remote_module_base(args.pid, agent.name)
            status = read_agent_status(args.pid)
            emit(
                {
                    "ok": True,
                    "pid": args.pid,
                    "agentLoaded": True,
                    "moduleBase": module_base,
                    "agentCompatible": bool(status and status.get("compatible")),
                    "agentReady": bool(status and status.get("ready")),
                    "agentStatus": status,
                },
                output,
            )
        except RuntimeError:
            emit(
                {"ok": True, "pid": args.pid, "agentLoaded": False},
                output,
            )
        except OSError as error:
            return fail("module_check_failed", output, pid=args.pid, error=str(error))
        return 0

    if args.seed_selection_context or args.seed_battle_context:
        if args.seed_selection_context and args.seed_battle_context:
            return fail("choose_selection_or_battle_context_seed", output)
        if args.context is None or args.context <= 0:
            return fail("context_seed_argument_missing", output)
        try:
            result = (
                seed_selection_context(args.pid, agent, args.context)
                if args.seed_selection_context
                else seed_battle_context(args.pid, agent, args.context)
            )
            if not result["accepted"]:
                return fail("context_seed_rejected", output, pid=args.pid, **result)
            emit({"ok": True, "pid": args.pid, "contextSeed": result}, output)
        except Exception as error:
            return fail("context_seed_failed", output, pid=args.pid, error=str(error))
        return 0

    if args.begin_takeover or args.end_takeover:
        if args.begin_takeover and args.end_takeover:
            return fail("choose_begin_or_end_takeover", output)
        try:
            result = set_takeover(
                args.pid,
                agent,
                session_id=args.session_id,
                active=args.begin_takeover,
                expected_user_id=args.expected_user_id,
            )
            if not result["accepted"]:
                return fail("takeover_control_rejected", output, pid=args.pid, **result)
            emit({"ok": True, "pid": args.pid, "takeover": result}, output)
        except Exception as error:
            return fail("takeover_control_failed", output, pid=args.pid, error=str(error))
        return 0

    if args.command_probe or args.cast:
        if args.command_probe and args.cast:
            return fail("choose_probe_or_cast", output)
        if (
            args.generator is None
            or args.mode is None
            or args.skill_data is None
            or args.target_id is None
            or args.skill_id is None
            or args.verified_skill_type_id is None
        ):
            return fail("command_arguments_missing", output)
        try:
            result = queue_command(
                args.pid,
                agent,
                session_id=args.session_id,
                context=args.context or 0,
                generator=args.generator,
                mode=args.mode,
                skill_data=args.skill_data,
                target_id=args.target_id,
                skill_id=args.skill_id,
                verified_skill_type_id=args.verified_skill_type_id,
                expected_area_id=args.expected_area_id,
                expected_region_id=args.expected_region_id,
                expected_round=args.expected_round,
                expected_turn=args.expected_turn,
                expected_player_turn_count=args.expected_player_turn_count,
                expected_active_hero_id=args.expected_active_hero_id,
                expected_active_hero_turn_count=args.expected_active_hero_turn_count,
                expected_active_hero_form_index=args.expected_active_hero_form_index,
                execute=args.cast,
                nonce=args.nonce,
            )
            if not result["queued"]:
                return fail("agent_rejected_queue", output, pid=args.pid, **result)
            emit({"ok": True, "pid": args.pid, "command": result}, output)
        except PermissionError as error:
            return fail(
                "access_denied_run_elevated",
                output,
                pid=args.pid,
                winerror=error.winerror,
            )
        except OSError as error:
            if getattr(error, "winerror", None) == 5:
                return fail("access_denied_run_elevated", output, pid=args.pid, winerror=5)
            return fail("command_windows_error", output, pid=args.pid, error=str(error))
        except Exception as error:
            return fail("command_queue_failed", output, pid=args.pid, error=str(error))
        return 0

    if args.unload:
        try:
            result = unload(args.pid, agent.name, agent)
            emit({"ok": True, "pid": args.pid, "unload": result}, output)
        except Exception as error:
            return fail("unload_failed", output, pid=args.pid, error=str(error))
        return 0

    reload_result: dict[str, object] | None = None
    if args.reload:
        try:
            reload_result = unload(args.pid, agent.name, agent)
        except RuntimeError as error:
            if "Remote module not found" not in str(error):
                return fail("reload_unload_failed", output, pid=args.pid, error=str(error))
        except Exception as error:
            return fail("reload_unload_failed", output, pid=args.pid, error=str(error))

    pipe = create_result_pipe(args.pid)
    load_result: dict[str, object] | None = None
    result_queue, result_reader = start_result_reader(pipe)
    try:
        load_result = inject(args.pid, agent)
        try:
            # A complete static hero catalog resolves several thousand localized
            # strings on first load.  Keep this comfortably above normal startup
            # time so the reader does not abandon a healthy agent mid-export.
            succeeded, result_or_error = result_queue.get(timeout=90.0)
        except queue.Empty as error:
            raise TimeoutError("Agent did not connect to the result pipe") from error
        if not succeeded:
            raise result_or_error
        if not isinstance(result_or_error, dict):
            raise RuntimeError("Agent returned an invalid result")
        probe_result = result_or_error
        with AgentIpc(args.pid) as ipc:
            agent_status = ipc.wait_until_ready()
        emit(
            {
                "ok": True,
                "pid": args.pid,
                "target": str(target_file),
                "reloadUnload": reload_result,
                "load": load_result,
                "agentStatus": agent_status,
                "probe": probe_result,
            },
            output,
        )
    except TimeoutError as error:
        return fail(
            "probe_timeout",
            output,
            pid=args.pid,
            load=load_result,
            error=str(error),
        )
    except PermissionError as error:
        return fail(
            "access_denied_run_elevated",
            output,
            pid=args.pid,
            winerror=error.winerror,
        )
    except OSError as error:
        if getattr(error, "winerror", None) == 5:
            return fail("access_denied_run_elevated", output, pid=args.pid, winerror=5)
        return fail("windows_error", output, pid=args.pid, error=str(error))
    except Exception as error:
        return fail("probe_failed", output, pid=args.pid, error=str(error))
    finally:
        close_result_pipe(pipe, result_reader)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
