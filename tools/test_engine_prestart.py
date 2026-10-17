"""Engines started ahead of their battle (engine_prestart, 1.1.4); offline, the engine process is a fake."""
from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import engine_prestart
from engine_prestart import EnginePrestarter, PrestartedEngine, popen_with


class FakeProcess:
    def __init__(self, args, **kwargs) -> None:
        self.args, self.kwargs = args, kwargs
        self.written: list[bytes] = []
        self.code: int | None = None
        self.stdin = self

    def write(self, data: bytes) -> None:
        if self.code is not None:
            raise OSError("pipe closed")
        self.written.append(data)
        if data == b"cancel\n":
            self.code = 7

    def flush(self) -> None:
        pass

    def poll(self):
        return self.code

    def communicate(self, timeout=None):
        return b"", b""

    def kill(self) -> None:
        self.code = 1


def engine(root: Path, name: str = "one", clock=time.monotonic) -> PrestartedEngine:
    return PrestartedEngine(Path("probe.exe"), "chimera-forecast", "captured", root / name, clock=clock, popen=FakeProcess)


def packed(root: Path) -> Path:
    folder = root / "packed"
    folder.mkdir()
    for name in engine_prestart.INPUT_FILES:
        (folder / name).write_bytes(name.encode())
    return folder


def wait_until(condition, seconds: float = 2.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition()


def test_the_engine_waits_with_its_flag_and_takes_the_battle_files_by_copy() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        waiting = engine(root)
        assert waiting.process.args[-1] == "--await-input" and waiting.process.args[2] == str(root / "one")
        assert waiting.usable(Path("probe.exe"), "chimera-forecast", "captured")
        assert not waiting.usable(Path("probe.exe"), "chimera-forecast", "123")  # another seed
        assert not waiting.usable(Path("other.exe"), "chimera-forecast", "captured")
        source = packed(root)
        process = waiting.take(source)
        assert process.written == [b"input\n"] and not waiting.usable(Path("probe.exe"), "chimera-forecast", "captured")
        # Copied, not moved: the originals stay, the copies are new files in the engine's folder.
        assert all((source / name).is_file() and (root / "one" / name).read_bytes() == name.encode()
                   for name in engine_prestart.INPUT_FILES)
        waiting.finished()
        assert not (root / "one").exists()


def test_an_old_or_ended_engine_is_not_used_and_a_cold_start_is_made() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        now = [0.0]
        old = engine(root, "old", clock=lambda: now[0])
        now[0] = engine_prestart.MAX_AGE_SECONDS + 1
        cold = FakeProcess(["cold"])
        process, used = popen_with(old, Path("probe.exe"), "chimera-forecast", "captured", packed(root), lambda: cold)
        assert process is cold and used is None
        wait_until(lambda: not (root / "old").exists())  # cancelled, folder removed
        assert old.process.written == [b"cancel\n"]
        ended = engine(root, "ended")
        ended.process.code = 0
        process, used = popen_with(ended, Path("probe.exe"), "chimera-forecast", "captured", root / "packed", lambda: cold)
        assert process is cold and used is None


def test_a_new_prestart_replaces_and_cancels_the_waiting_engine() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        started: list[PrestartedEngine] = []

        def start(probe, mode, parameter, work):
            started.append(PrestartedEngine(probe, mode, parameter, work, popen=FakeProcess))
            return started[-1]

        prestarter = EnginePrestarter("forecast", "1000", root, start=start)
        prestarter.prestart(lambda: Path("probe.exe"))
        wait_until(lambda: len(started) == 1)
        wait_until(lambda: prestarter._pending is started[0])
        prestarter.prestart(lambda: Path("probe.exe"))
        wait_until(lambda: len(started) == 2 and prestarter._pending is started[1])
        wait_until(lambda: started[0].process.written == [b"cancel\n"])
        assert prestarter.take() is started[1] and prestarter.take() is None
        # A failing engine lookup leaves no engine: the battle starts cold.
        prestarter.prestart(lambda: (_ for _ in ()).throw(OSError("bundle")))
        time.sleep(0.1)
        assert prestarter.take() is None


def test_the_chimera_forecast_gives_its_prestarted_engine_to_the_simulation() -> None:
    import test_chimera_forecast_live as live

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        seen: dict = {}
        watcher = live.monitor(root, live.result(), [])
        watcher.runner = lambda *args, **kwargs: seen.update(kwargs) or live.result()
        waiting = object()
        watcher.prestarter._pending = waiting
        watcher.observe(live.CONFIG, live.state(1), capture=live.Capture(root / "capture"), capability_memory=None)
        watcher.battle.job.thread.join()
        assert seen["engine"] is waiting and watcher.prestarter.take() is None


def test_a_regroup_starts_the_next_battles_engine_first() -> None:
    import chimera_controller as controller

    order: list[str] = []
    with patch.object(controller, "prestart_forecast_engine", side_effect=lambda *a: order.append("prestart")), \
            patch.object(controller, "free_regroup_and_retry_manual", side_effect=lambda *a, **k: order.append("regroup")):
        controller.request_chimera_regroup_retry({"objectives": {}}, {"pid": 7}, agent=Path("offline"), ipc=None,
                                                 session_id=1, nonce=2, runtime_state={})
    assert order == ["prestart", "regroup"]
