"""Checks GitHub for a newer release and replaces the running exe with it.

Check: the public "latest release" API, with no account and no personal data,
once when the tool starts and again whenever the user asks.
The release notes are split by language (the "## 简体中文 / English / Português"
sections every release body has).

Download: the release's exe goes into the data folder and is checked against the
size and SHA-256 that GitHub publishes for the asset. A release without them
offers only its download page.

Install: only when the user asks, and only while nothing runs (no takeover, no
simulation). Windows lets a running exe be renamed but not overwritten. So the
verified file is first copied next to the exe as "<name>.new". At exit, the
running file becomes "<name>.old", the one backup, which the next update
replaces. The new file then takes the exe's name, so shortcuts keep working.
The new instance waits for this process to exit before it takes the
single-instance lock.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ui_text import ui_text

REPOSITORY = "WFQ-GAFE/Raid-Shadow-Legends-Boss-fight-helper"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
# Tests and local trials only: a feed served from this computer.
FEED_VARIABLE = "RSL_UPDATE_FEED"
LOCAL_HOSTS = ("127.0.0.1", "localhost")
ASSET_NAME = re.compile(r"^RSL-Boss-helper-(\d+(?:\.\d+){1,3})\.exe$")
MAX_ASSET_BYTES = 200 * 1024 * 1024
CHUNK = 256 * 1024
# The language sections of a release body, by UI language.
NOTE_HEADINGS = (("zh-CN", "简体中文"), ("en", "English"), ("pt-BR", "Português"))
STATE_FILE = "app-update.json"


def parse_version(text: Any) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})", str(text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def version_text(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def release_notes(body: Any) -> dict[str, str]:
    """The body's section for each UI language; the whole body under "" when it has none."""
    text = str(body or "").replace("\r\n", "\n")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        code = next((code for code, name in NOTE_HEADINGS if heading and heading.group(1).startswith(name)), None)
        if code:
            current = code
            sections[current] = []
        elif current:
            sections[current].append(line)
    notes = {code: "\n".join(lines).strip() for code, lines in sections.items()}
    notes = {code: value for code, value in notes.items() if value}
    return notes or ({"": text.strip()} if text.strip() else {})


def _allowed_url(url: str, local: bool) -> bool:
    parsed = urlparse(url)
    if local:
        return parsed.scheme == "http" and parsed.hostname in LOCAL_HOSTS
    return parsed.scheme == "https" and parsed.hostname == "github.com"


def release_info(document: Any, local: bool = False) -> dict[str, Any] | None:
    """The newest release as the UI shows it, or None when it is not a usable release."""
    if not isinstance(document, dict) or document.get("draft") or document.get("prerelease"):
        return None
    version = parse_version(document.get("tag_name"))
    if version is None:
        return None
    page = str(document.get("html_url") or "")
    if not (local or page.startswith(f"https://github.com/{REPOSITORY}/releases/")):
        page = RELEASES_PAGE
    download = None
    for asset in document.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        match = ASSET_NAME.match(str(asset.get("name") or ""))
        if not match or parse_version(match.group(1)) != version:
            continue
        digest = str(asset.get("digest") or "")
        sha256 = digest[7:].lower() if digest.startswith("sha256:") else ""
        size = asset.get("size")
        url = str(asset.get("browser_download_url") or "")
        if (re.fullmatch(r"[0-9a-f]{64}", sha256) and isinstance(size, int) and not isinstance(size, bool)
                and 0 < size <= MAX_ASSET_BYTES and _allowed_url(url, local)):
            download = {"name": asset["name"], "size": size, "sha256": sha256, "url": url}
        break
    return {
        "version": version_text(version),
        "page": page,
        "publishedAt": str(document.get("published_at") or ""),
        "notes": release_notes(document.get("body")),
        "download": download,
    }


def _fetch(url: str, version: str, accept: str, timeout: float = 15):
    request = Request(url, headers={"User-Agent": f"RSL-Boss-helper/{version}", "Accept": accept})
    return urlopen(request, timeout=timeout)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def wait_for_process(pid: int, timeout: float) -> None:
    """Block until process ``pid`` has exited (an update's previous instance), at most ``timeout`` s."""
    if os.name != "nt" or pid <= 0:
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    synchronize = 0x00100000
    handle = kernel32.OpenProcess(synchronize, False, int(pid))
    if not handle:
        return  # Already gone.
    try:
        kernel32.WaitForSingleObject(handle, int(timeout * 1000))
    finally:
        kernel32.CloseHandle(handle)


def open_in_browser(url: str) -> None:
    """Open a page in the user's browser. Through Explorer, so the browser does not inherit
    the tool's administrator rights."""
    if not (url.startswith("https://github.com/") or _allowed_url(url, True)):
        raise ValueError(ui_text("update.badPage"))
    subprocess.Popen(["explorer.exe", url], close_fds=True)


class AppUpdater:
    """One per app. ``executable``: the running exe (frozen builds), or None when running from
    source, which can check and open the download page but not install."""

    def __init__(self, current_version: str, data_root: Path, executable: Path | None = None,
                 *, fetch: Callable[..., Any] = _fetch, clock: Callable[[], float] = time.time) -> None:
        self.current = parse_version(current_version)
        self.current_text = current_version
        self.folder = Path(data_root) / "cache" / "updates"
        self.state_path = Path(data_root) / "config" / STATE_FILE
        self.executable = Path(executable) if executable else None
        self.fetch = fetch
        self.clock = clock
        feed = os.environ.get(FEED_VARIABLE, "")
        self.local = bool(feed) and _allowed_url(feed, True)
        self.feed = feed if self.local else LATEST_RELEASE_API
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.status = "idle"  # idle | checking | current | available | downloading | ready | failed
        self.error = ""
        self.failed_step = ""  # check | download | install
        self.release: dict[str, Any] | None = None
        self.progress: list[int] | None = None
        self.checked_at = 0.0
        self.staged: Path | None = None
        self.installed: dict[str, Any] | None = None
        # The last install's result, shown once: an update to this version, or one that failed.
        installed = self._read_state().get("installed")
        if (isinstance(installed, dict) and not installed.get("seen")
                and (installed.get("ok") is False or installed.get("to") == current_version)):
            self.installed = installed
        if self.can_install:
            try:  # A staged copy left by an exit that never finished the swap.
                self.executable.with_name(self.executable.name + ".new").unlink(missing_ok=True)
            except OSError:
                pass

    # Saved state: the last install's result, shown once by the version it started.
    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_state(self, **changes: Any) -> None:
        state = {**self._read_state(), **changes}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.state_path)
        except OSError:
            pass

    @property
    def can_install(self) -> bool:
        return self.executable is not None and self.executable.suffix.lower() == ".exe"

    def _newer(self, release: dict[str, Any] | None) -> bool:
        version = parse_version(release.get("version")) if release else None
        return bool(version and self.current and version > self.current)

    def snapshot(self, full: bool = True) -> dict[str, Any]:
        with self.lock:
            release = self.release if self._newer(self.release) else None
            value: dict[str, Any] = {
                "current": self.current_text,
                "status": self.status,
                "version": release["version"] if release else None,
                "canInstall": self.can_install and bool(release and release.get("download")),
                "progress": list(self.progress) if self.progress else None,
                "error": self.error or None,
                "failedStep": self.failed_step or None,
                "checkedAt": int(self.checked_at * 1000) or None,
                "installed": self.installed,
            }
            if full:
                value["release"] = release
                value["page"] = release["page"] if release else RELEASES_PAGE
                value["backup"] = self.executable.name + ".old" if self.executable else None
            return value

    def _start(self, target: Callable[[], None], name: str) -> bool:
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(2)  # A step whose status is final may still be finishing.
            if self.thread.is_alive():
                return False
        self.thread = threading.Thread(target=target, daemon=True, name=name)
        self.thread.start()
        return True

    def check(self, force: bool = False) -> None:
        """Ask GitHub: once per launch (the startup check), again only when the user asks (``force``)."""
        with self.lock:
            if self.status in ("checking", "downloading", "ready", "installing"):
                return
            if not force and self.status != "idle":
                return
            previous = self.status
            self.status, self.error, self.failed_step = "checking", "", ""
        if not self._start(self._check, "app-update-check"):
            with self.lock:
                self.status = previous

    def _check(self) -> None:
        try:
            with self.fetch(self.feed, self.current_text, "application/vnd.github+json") as response:
                document = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
            release = release_info(document, self.local)
            if release is None:
                raise ValueError(ui_text("update.noRelease"))
        except Exception as error:  # Offline, blocked, rate limited: the dialog offers "Try again".
            with self.lock:
                self.status, self.failed_step = "failed", "check"
                self.error = ui_text("update.checkFailed", error=_reason(error))
                self.checked_at = self.clock()
            return
        with self.lock:
            self.release = release
            self.checked_at = self.clock()
            self.status = "available" if self._newer(release) else "current"

    def download(self) -> None:
        with self.lock:
            release = self.release if self._newer(self.release) else None
            if not release or not release.get("download"):
                raise RuntimeError(ui_text("update.noDownload"))
            if self.status in ("downloading", "ready"):
                return
            self.status, self.error, self.failed_step = "downloading", "", ""
            self.progress = [0, int(release["download"]["size"])]
        if not self._start(lambda: self._download(release["download"]), "app-update-download"):
            with self.lock:
                self.status = "available"

    def _download(self, asset: dict[str, Any]) -> None:
        target = self.folder / asset["name"]
        partial = target.with_name(target.name + ".part")
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            if not (target.is_file() and target.stat().st_size == asset["size"] and file_sha256(target) == asset["sha256"]):
                digest = hashlib.sha256()
                done = 0
                with self.fetch(asset["url"], self.current_text, "application/octet-stream", 60) as response, \
                        partial.open("wb") as handle:
                    while True:
                        block = response.read(CHUNK)
                        if not block:
                            break
                        if done == 0 and not block.startswith(b"MZ"):
                            raise ValueError(ui_text("update.notAnExe"))
                        done += len(block)
                        if done > asset["size"]:
                            raise ValueError(ui_text("update.sizeMismatch"))
                        digest.update(block)
                        handle.write(block)
                        with self.lock:
                            self.progress = [done, asset["size"]]
                if done != asset["size"]:
                    raise ValueError(ui_text("update.sizeMismatch"))
                if digest.hexdigest() != asset["sha256"]:
                    raise ValueError(ui_text("update.hashMismatch"))
                os.replace(partial, target)
        except Exception as error:
            try:
                partial.unlink(missing_ok=True)  # Our own partial download.
            except OSError:
                pass
            with self.lock:
                self.status, self.failed_step = "failed", "download"
                self.error = ui_text("update.downloadFailed", error=_reason(error))
                self.progress = None
            return
        with self.lock:
            self.status = "ready"
            self.progress = [asset["size"], asset["size"]]

    def stage(self, busy: str | None) -> None:
        """Copy the verified download next to the exe for the swap at exit. ``busy``: why not now."""
        if busy:
            raise RuntimeError(busy)
        with self.lock:
            release = self.release if self._newer(self.release) else None
            if self.status != "ready" or not release or not release.get("download") or not self.can_install:
                raise RuntimeError(ui_text("update.notReady"))
            asset = release["download"]
        source = self.folder / asset["name"]
        staged = self.executable.with_name(self.executable.name + ".new")
        try:
            shutil.copyfile(source, staged)
            if staged.stat().st_size != asset["size"] or file_sha256(staged) != asset["sha256"]:
                raise ValueError(ui_text("update.hashMismatch"))
        except Exception as error:
            try:
                staged.unlink(missing_ok=True)  # Our own copy.
            except OSError:
                pass
            raise RuntimeError(ui_text("update.stageFailed", folder=str(self.executable.parent), error=_reason(error))) from error
        with self.lock:
            self.staged = staged
            self.status = "installing"

    @property
    def pending(self) -> bool:
        return self.staged is not None

    def apply(self) -> Path | None:
        """At exit: swap the staged file in. Returns the exe to start (the new one, or the old
        one when the swap failed), or None without a staged update."""
        if self.staged is None or self.executable is None:
            return None
        exe, staged = self.executable, self.staged
        backup = exe.with_name(exe.name + ".old")
        release = self.release or {}
        result: dict[str, Any] = {"from": self.current_text, "to": release.get("version"), "at": int(self.clock() * 1000),
                                  "notes": release.get("notes") or {}}
        try:
            _retry(lambda: os.replace(exe, backup))
            try:
                _retry(lambda: os.replace(staged, exe))
            except OSError:
                os.replace(backup, exe)  # Put the running version back.
                raise
        except OSError as error:
            result.update(ok=False, error=ui_text("update.swapFailed", error=_reason(error)))
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            result.update(ok=True, backup=backup.name)
            try:
                (self.folder / str((release.get("download") or {}).get("name") or "")).unlink(missing_ok=True)
            except OSError:
                pass
        self._write_state(installed=result)
        return exe

    @staticmethod
    def restart(exe: Path, pid: int) -> None:
        """Start ``exe`` as a fresh instance (its own PyInstaller unpacking) that waits for ``pid``."""
        environment = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([str(exe), "--after-update", str(pid)], cwd=str(exe.parent), env=environment,
                         close_fds=True, creationflags=flags if os.name == "nt" else 0)

    def dismiss_installed(self) -> None:
        with self.lock:
            if self.installed is None:
                return
            self.installed = None
        saved = self._read_state().get("installed")
        if isinstance(saved, dict):
            self._write_state(installed={**saved, "seen": True})


def _retry(action: Callable[[], None], attempts: int = 6, pause: float = 0.4) -> None:
    """Antivirus scanners briefly lock a new exe; try a few times."""
    for attempt in range(attempts):
        try:
            action()
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(pause)


def _reason(error: Exception) -> str:
    reason = getattr(error, "reason", None)
    code = getattr(error, "code", None)
    if code == 403:
        return ui_text("update.rateLimited")
    return str(reason or error) or type(error).__name__
