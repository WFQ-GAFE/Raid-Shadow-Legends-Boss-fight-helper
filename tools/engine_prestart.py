"""An offline engine started ahead of its battle (raid_offline_probe <mode> <dir> <parameter> --await-input).

Starting the isolated engine costs about 1.6 s (process, game runtime, static data). A
takeover starts the next battle's engine while the game still loads that battle; when
the opening has been captured, its files are copied into the engine's input directory
and "input" starts the battle at once. One engine serves one battle; one that is not
used is cancelled, so its launcher still removes its AppContainer profile and access.

The files are copied, never moved, into the directory: files created there inherit the
read access the launcher granted its AppContainer, a moved file keeps its own.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

INPUT_FILES = ("battle-setup.msgpack", "battle-settings.msgpack")
# Its battle starts within seconds of the regroup or Start; a stale engine is not used.
MAX_AGE_SECONDS = 90.0
CANCEL_WAIT_SECONDS = 10.0


class PrestartedEngine:
    def __init__(self, probe: Path, mode: str, parameter: str, work: Path, *,
                 clock: Callable[[], float] = time.monotonic,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen) -> None:
        self.probe, self.mode, self.parameter, self.work = probe, mode, parameter, work
        self.clock = clock
        self.started_at = clock()
        self.taken = False
        work.mkdir(parents=True, exist_ok=False)
        self.process = popen(
            [str(probe), mode, str(work), parameter, "--await-input"],
            cwd=str(probe.parent), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def usable(self, probe: Path, mode: str, parameter: str) -> bool:
        """Still waiting, for this engine and battle kind, and not too old."""
        return (not self.taken and self.process.poll() is None and Path(self.probe) == Path(probe)
                and self.mode == mode and self.parameter == parameter
                and self.clock() - self.started_at <= MAX_AGE_SECONDS)

    def take(self, packed: Path) -> subprocess.Popen:
        """Give the engine its battle; the caller then talks to the process as to a new one."""
        for name in INPUT_FILES:
            shutil.copyfile(packed / name, self.work / name)
        self.taken = True
        assert self.process.stdin is not None
        self.process.stdin.write(b"input\n")
        self.process.stdin.flush()
        return self.process

    def finished(self) -> None:
        """After its battle: the input copies are no longer needed."""
        shutil.rmtree(self.work, ignore_errors=True)

    def cancel(self) -> None:
        """Not used: let the launcher clean up (graceful), then remove the folder. Never blocks."""
        self.taken = True
        threading.Thread(target=self._cancel, daemon=True, name="engine-prestart-cancel").start()

    def _cancel(self) -> None:
        try:
            if self.process.poll() is None:
                try:
                    assert self.process.stdin is not None
                    self.process.stdin.write(b"cancel\n")
                    self.process.stdin.flush()
                except (OSError, ValueError):
                    pass
                try:
                    self.process.communicate(timeout=CANCEL_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.communicate()
        finally:
            self.finished()


class EnginePrestarter:
    """The one engine waiting for a monitor's next battle (a forecast monitor holds one)."""

    def __init__(self, mode: str, parameter: str, root: Path,
                 start: Callable[..., PrestartedEngine] = PrestartedEngine) -> None:
        self.mode, self.parameter, self.root, self.start = mode, parameter, root, start
        self._lock = threading.Lock()
        self._pending: PrestartedEngine | None = None

    def prestart(self, probe_for: Callable[[], Path]) -> None:
        """Start the next battle's engine in the background (finding the engine copy may take a moment)."""
        def run() -> None:
            try:
                probe = probe_for()
                engine = self.start(probe, self.mode, self.parameter,
                                    self.root / f"{int(time.time() * 1000)}-{threading.get_ident()}")
            except Exception:  # Without it the battle's engine simply starts cold.
                return
            with self._lock:
                previous, self._pending = self._pending, engine
            if previous is not None:
                previous.cancel()
        threading.Thread(target=run, daemon=True, name="engine-prestart").start()

    def take(self) -> PrestartedEngine | None:
        with self._lock:
            engine, self._pending = self._pending, None
        return engine

    def cancel(self) -> None:
        engine = self.take()
        if engine is not None:
            engine.cancel()


def popen_with(engine: PrestartedEngine | None, probe: Path, mode: str, parameter: str, packed: Path,
               cold: Callable[[], subprocess.Popen]) -> tuple[subprocess.Popen, PrestartedEngine | None]:
    """The prestarted engine when it fits this battle, else a cold start (and the unfit one cancelled)."""
    if engine is not None and engine.usable(probe, mode, parameter):
        try:
            return engine.take(packed), engine
        except (OSError, ValueError):  # It ended meanwhile: start cold.
            pass
    if engine is not None:
        engine.cancel()
    return cold(), None
