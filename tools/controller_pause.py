from __future__ import annotations

import ctypes
from ctypes import wintypes


EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateEventW.argtypes = [
    wintypes.LPVOID,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
kernel32.ResetEvent.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def pause_event_name(pid: int) -> str:
    return rf"Local\RaidChimeraControllerPause-{int(pid)}"


class ControllerPauseEvent:
    def __init__(self, pid: int) -> None:
        self.pid = int(pid)
        self.handle: int | None = None

    def open(self) -> ControllerPauseEvent:
        if self.handle:
            return self
        handle = kernel32.CreateEventW(
            None,
            True,
            False,
            pause_event_name(self.pid),
        )
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = int(handle)
        if not kernel32.ResetEvent(handle):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            self.handle = None
            raise ctypes.WinError(error)
        return self

    def is_set(self) -> bool:
        return bool(
            self.handle
            and kernel32.WaitForSingleObject(self.handle, 0) == WAIT_OBJECT_0
        )

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self) -> ControllerPauseEvent:
        return self.open()

    def __exit__(self, *_: object) -> None:
        self.close()


def signal_controller_pause(pid: int) -> bool:
    handle = kernel32.OpenEventW(
        EVENT_MODIFY_STATE | SYNCHRONIZE,
        False,
        pause_event_name(pid),
    )
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)
