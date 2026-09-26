"""Desktop resource lifetime helpers, with no game operations."""
import hashlib
from pathlib import Path
import threading
import time
from strategy_storage import atomic_write_bytes


def persistent_agent(source: Path, root: Path) -> Path:
    """Loaded DLLs must outlive PyInstaller's per-launch extraction directory."""
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    target = root.resolve() / 'runtime' / 'agents' / digest / 'RaidChimeraAgent.dll'
    if target.is_file():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError('本地代理文件校验失败，请重新安装工具。')
        return target  # Do not replace a correct DLL that a game may have loaded.
    try:
        atomic_write_bytes(target, payload)
    except OSError:
        # Concurrent workers may materialize the same content-addressed DLL.
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise
    return target


def remove_session_file(path: Path | None) -> bool:
    if path is None:
        return True
    for attempt in range(3):
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError:
            if attempt < 2:
                time.sleep(.05)
    return False


class WindowStartup:
    def __init__(self):
        self.closed = threading.Event()

    def close(self):
        self.closed.set()

    def reveal(self, window, url, notify, timeout=15.0, mount_delay=2.0):
        deadline = time.monotonic() + timeout
        while not self.closed.is_set():
            if window.events.loaded.wait(.05):
                break
            if time.monotonic() >= deadline:
                if not self.closed.is_set():
                    notify('窗口未能完成初始化，请重新打开工具。')
                return
        if self.closed.is_set():
            return
        try:
            window.show()
            if self.closed.wait(mount_delay):
                return
            mounted = window.evaluate_js("document.documentElement.dataset.appMounted === 'true'")
            if not mounted and not self.closed.is_set():
                window.load_url(url)
        except Exception:
            if not self.closed.is_set():
                notify('窗口初始化失败，请重新打开工具。')
