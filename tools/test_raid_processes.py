"""RAID's game executable is recognised by its own files, on any Plarium Play folder layout (1.1.5)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from raid_processes import is_supported_raid_executable, raid_executable_problem


def game(root: Path, *parts: str, files=("Raid.exe", "GameAssembly.dll", "UnityPlayer.dll"), data=True) -> Path:
    folder = root.joinpath(*parts)
    folder.mkdir(parents=True)
    for name in files:
        (folder / name).write_bytes(b"")
    if data:
        (folder / "Raid_Data").mkdir()
    return folder / "Raid.exe"


def test_every_plarium_layout_is_accepted() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        # Current installs, old installs (a player's game was never detected in 1.1.4), another drive.
        for parts in (("PlariumPlay", "StandAloneApps", "raid-shadow-legends", "build"),
                      ("PlariumPlay", "StandAloneApps", "raid", "build"),
                      ("Games", "RAID")):
            executable = game(root, *parts)
            assert is_supported_raid_executable(executable) and raid_executable_problem(executable) is None


def test_anything_that_is_not_the_game_is_refused() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        lone = game(root, "lone", files=("Raid.exe",), data=False)
        assert raid_executable_problem(lone) == "missing GameAssembly.dll, UnityPlayer.dll, Raid_Data"
        no_data = game(root, "no-data", data=False)
        assert raid_executable_problem(no_data) == "missing Raid_Data"
        other = game(root, "other", files=("Other.exe", "GameAssembly.dll", "UnityPlayer.dll"))
        assert raid_executable_problem(other.with_name("Other.exe")) == "not_raid_exe"
        assert not is_supported_raid_executable(root / "nowhere" / "Raid.exe")


def test_the_account_list_logs_each_game_process_once_with_its_verdict() -> None:
    import threading
    from unittest.mock import patch

    import chimera_web
    from chimera_web import ChimeraService

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        good = game(root, "PlariumPlay", "StandAloneApps", "raid", "build")
        found = {7: {"pid": 7, "path": str(good), "windows": []},
                 8: {"pid": 8, "path": str(root / "elsewhere" / "Raid.exe"), "windows": []},
                 9: {"pid": 9, "path": None, "windows": []}}
        service = object.__new__(ChimeraService)
        service.lock, service.processes, service.last_process_refresh = threading.Lock(), {}, 0.0
        service.logged_processes = set()
        service.reload_hero_catalog_if_changed = lambda: None
        logged: list[str] = []
        with patch.object(chimera_web, "raid_processes", side_effect=lambda: {k: dict(v) for k, v in found.items()}), \
                patch.object(chimera_web, "attach_windows"), \
                patch.object(chimera_web, "identify_account", return_value=None), \
                patch.object(chimera_web, "desktop_lifecycle_log", side_effect=logged.append):
            assert [item["pid"] for item in service.refresh_processes(force=True)] == [7]
            service.refresh_processes(force=True)
        assert len(logged) == 3  # not repeated on the next refresh
        assert logged[0] == f"raid_process pid=7 accepted path={good}"
        assert logged[1].startswith("raid_process pid=8 missing GameAssembly.dll")
        assert logged[2] == "raid_process pid=9 path_not_readable path=None"
