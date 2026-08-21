#!/usr/bin/env python3
"""Receive one JSON readiness report from the read-only agent DLL."""

from __future__ import annotations

import ctypes
import argparse
import json
from ctypes import wintypes


PIPE_NAME_PREFIX = r"\\.\pipe\RaidChimeraPrototype-"
PIPE_ACCESS_INBOUND = 0x00000001
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
ERROR_PIPE_CONNECTED = 535
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    args = parser.parse_args()
    pipe = kernel32.CreateNamedPipeW(
        f"{PIPE_NAME_PREFIX}{args.pid}",
        PIPE_ACCESS_INBOUND,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
        1,
        0,
        65536,
        0,
        None,
    )
    if pipe == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        connected = bool(kernel32.ConnectNamedPipe(pipe, None))
        if not connected and ctypes.get_last_error() != ERROR_PIPE_CONNECTED:
            raise ctypes.WinError(ctypes.get_last_error())

        buffer = ctypes.create_string_buffer(65536)
        read = wintypes.DWORD()
        if not kernel32.ReadFile(pipe, buffer, len(buffer), ctypes.byref(read), None):
            raise ctypes.WinError(ctypes.get_last_error())
        payload = json.loads(buffer.raw[: read.value].decode("utf-8"))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        kernel32.DisconnectNamedPipe(pipe)
        kernel32.CloseHandle(pipe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
