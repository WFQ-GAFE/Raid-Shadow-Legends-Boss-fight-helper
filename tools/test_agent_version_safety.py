from __future__ import annotations

import os
import re
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from agent_ipc import AGENT_BUILD_ID, reload_block_reason
import controller_manager as controller_module
from controller_manager import ControllerManager
from ui_text import render


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_agent_source_and_controller_share_the_input_capture_build_id() -> None:
    source = (PROJECT_ROOT / "src" / "agent" / "agent.cpp").read_text(encoding="utf-8")
    match = re.search(r"kAgentBuildId\s*=\s*(\d+)ULL", source)
    assert match is not None
    assert int(match.group(1)) == AGENT_BUILD_ID == 2026100401


def test_retired_hydra_research_capture_is_gone_and_blocked_at_package_time() -> None:
    package = (PROJECT_ROOT / "tools" / "build_chimera_desktop.ps1").read_text(encoding="utf-8")
    source = (PROJECT_ROOT / "src" / "agent" / "agent.cpp").read_text(encoding="utf-8")
    assert "RAID_HYDRA_RESEARCH_CAPTURE" not in source and "RAID_HYDRA_SELECTOR_HOOK" not in source
    assert not (PROJECT_ROOT / "src" / "agent" / "battle_input_capture.hpp").exists()
    assert not (PROJECT_ROOT / "src" / "agent" / "hydra_selector_capture.hpp").exists()
    for marker in ("hydra_mark_selector_observation_installed", "RandomHungerVictimSelectedFrom", "hydraReplayInput"):
        assert f'"{marker}"' in package  # Packaging refuses a binary that still carries it.


def test_il2cpp_gc_handles_keep_the_full_pointer_width() -> None:
    """A 32-bit handle crashed Unity 6000 when passed back to get_target."""
    api = (PROJECT_ROOT / "src" / "agent" / "il2cpp_api.hpp").read_text(encoding="utf-8")
    assert "using GcHandleNew = std::uintptr_t (*)(void*, bool);" in api
    assert "using GcHandleGetTarget = void* (*)(std::uintptr_t);" in api
    assert "using GcHandleFree = void (*)(std::uintptr_t);" in api


def test_previous_crash_build_requires_full_game_restart() -> None:
    previous = {"buildId": 2026092301, "compatible": False}
    assert reload_block_reason(previous) == "incompatible_agent_restart_required"


def test_stale_team_capture_build_requires_restart_even_with_current_layout() -> None:
    # This build has the same shared layout but retains invalid AppModel data
    # across account reinitialization; the new tool must never request it again.
    previous = {"buildId": 2026092902, "compatible": True}
    assert reload_block_reason(previous) == "incompatible_agent_restart_required"


def test_current_compatible_build_can_use_existing_reload_path() -> None:
    current = {"buildId": AGENT_BUILD_ID, "compatible": True}
    assert reload_block_reason(current) is None


def test_incompatible_agent_abi_requires_restart_even_with_same_build_id() -> None:
    incompatible = {"buildId": AGENT_BUILD_ID, "compatible": False}
    assert reload_block_reason(incompatible) == "incompatible_agent_restart_required"


def test_unreadable_loaded_agent_is_never_hot_reloaded() -> None:
    assert reload_block_reason(None) == "agent_status_unavailable_restart_required"


def test_controller_refuses_to_reload_previous_crash_build() -> None:
    manager = ControllerManager()
    manager.preparing = True
    manager.pid = os.getpid()
    calls: list[list[str]] = []

    def check_agent(arguments: list[str], _timeout: int):
        calls.append(arguments)
        return SimpleNamespace(returncode=0, stdout="", stderr=""), {
            "agentLoaded": True,
            "agentCompatible": False,
            "agentReady": False,
            "agentStatus": {"buildId": 2026092301, "compatible": False},
        }

    with (
        patch.object(controller_module, "require_expected_account"),
        patch.object(manager, "_run_injector", side_effect=check_agent),
        patch.object(controller_module.subprocess, "Popen") as launch,
    ):
        manager._prepare_and_run(os.getpid(), "offline", 1, "hydra")

    launch.assert_not_called()
    assert calls == [[str(os.getpid()), "--check-only"]]
    assert render(manager.snapshot()["status"], "zh-CN") == "启动失败"
    assert "阻止在线卸载或重载" in render(manager.snapshot()["error"], "zh-CN")


def test_an_older_agent_layout_is_reported_not_mapped() -> None:
    """A game still holding an older agent has a smaller shared section:
    mapping this build's view over it failed with a bare access error."""
    import ctypes
    from ctypes import wintypes

    from agent_ipc import AgentIpc, AgentLayoutMismatch, AgentSharedHeader, SHARED_STATE_MAGIC
    from chimera_controller import latest_account_state

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileMappingW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                            wintypes.DWORD, wintypes.LPCWSTR]
    kernel32.CreateFileMappingW.restype = wintypes.HANDLE
    kernel32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_size_t]
    kernel32.MapViewOfFile.restype = wintypes.LPVOID
    fake_pid = 4_000_000_000 - os.getpid()
    old_size = 2_719_920
    mapping = kernel32.CreateFileMappingW(wintypes.HANDLE(-1), None, 0x04, 0, old_size,
                                          rf"Local\RaidChimeraAgentState-{fake_pid}")
    assert mapping
    view = kernel32.MapViewOfFile(mapping, 0x0002, 0, 0, ctypes.sizeof(AgentSharedHeader))
    try:
        header = AgentSharedHeader.from_address(view)
        header.magic, header.shared_state_version, header.struct_size = SHARED_STATE_MAGIC, 4, old_size
        header.pid, header.build_id = fake_pid, 2026092403
        try:
            AgentIpc(fake_pid).open()
        except AgentLayoutMismatch as error:
            assert "2026092403" in str(error)
        else:
            raise AssertionError("an older agent layout was mapped")
        assert latest_account_state(fake_pid) is None
    finally:
        kernel32.UnmapViewOfFile(ctypes.c_void_p(view))
        kernel32.CloseHandle(mapping)
