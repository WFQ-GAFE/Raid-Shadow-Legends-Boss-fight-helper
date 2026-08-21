from __future__ import annotations

import argparse
import ctypes
import json
import secrets
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

from agent_ipc import AgentIpc
from inject_probe import set_takeover
from reload_bound_agent import bound_state


WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_F24 = 0x87

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL


def find_window(pid: int) -> int:
    found: list[int] = []

    @WNDENUMPROC
    def callback(hwnd: int, _lparam: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
            return False
        return True

    if not user32.EnumWindows(callback, 0) and not found:
        error = ctypes.get_last_error()
        if error:
            raise ctypes.WinError(error)
    if not found:
        raise RuntimeError("no visible game window found for bound PID")
    return found[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--agent", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    result: dict[str, Any] = {
        "ok": False,
        "pid": args.pid,
        "accountName": args.account_name,
        "userId": args.user_id,
        "gameplayCommandSubmitted": False,
        "savedResult": False,
    }
    session_id = secrets.randbits(63) or 1
    takeover_armed = False
    code = 1
    try:
        initial = bound_state(args.pid, args.account_name, args.user_id)
        if initial["lifecycle"].get("screen") == "result":
            raise RuntimeError("refusing interrupt test on result screen")
        hwnd = find_window(args.pid)
        with AgentIpc(args.pid) as ipc:
            takeover = set_takeover(
                args.pid,
                args.agent.resolve(),
                session_id=session_id,
                active=True,
                expected_user_id=args.user_id,
            )
            if takeover.get("accepted") is not True:
                raise RuntimeError(f"takeover rejected: {takeover}")
            takeover_armed = True
            if not user32.PostMessageW(hwnd, WM_KEYDOWN, VK_F24, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            user32.PostMessageW(hwnd, WM_KEYUP, VK_F24, 1 << 31)

            deadline = time.monotonic() + 5.0
            interrupted: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                lifecycle = ipc.lifecycle() or {}
                if (
                    lifecycle.get("takeoverState") == "interrupted"
                    and lifecycle.get("sessionId") == session_id
                ):
                    interrupted = lifecycle
                    break
                time.sleep(0.01)
            if interrupted is None:
                raise RuntimeError("F24 game-window input did not interrupt takeover")
            result.update(
                {
                    "ok": True,
                    "window": hwnd,
                    "sessionId": session_id,
                    "interruptedState": interrupted,
                }
            )
            code = 0
    except Exception as error:
        result["error"] = str(error)
    finally:
        if takeover_armed:
            try:
                set_takeover(
                    args.pid,
                    args.agent.resolve(),
                    session_id=session_id,
                    active=False,
                )
            except Exception as error:
                result["cleanupError"] = str(error)
                result["ok"] = False
                code = 1
        encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(args.output)
        print(encoded, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
