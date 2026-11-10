"""Update check, verified download and the in-place swap (app_update), without the network."""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import time
from pathlib import Path

import app_update
from app_update import AppUpdater, parse_version, release_info, release_notes
from ui_text import render

BODY = """# RSL-Boss-helper 1.2.0

## 简体中文

- **新功能：** 中文说明

## English

- **New:** English notes
  - nested

## Português (Brasil)

- **Novo:** notas
"""


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def document(version: str, payload: bytes, digest: str | None = None, name: str | None = None) -> dict:
    return {
        "tag_name": f"v{version}", "draft": False, "prerelease": False,
        "html_url": f"https://github.com/{app_update.REPOSITORY}/releases/tag/v{version}",
        "published_at": "2026-11-20T10:00:00Z", "body": BODY,
        "assets": [{
            "name": name or f"RSL-Boss-helper-{version}.exe", "size": len(payload),
            "digest": "sha256:" + (digest or hashlib.sha256(payload).hexdigest()),
            "browser_download_url": f"https://github.com/{app_update.REPOSITORY}/releases/download/v{version}/RSL-Boss-helper-{version}.exe",
        }],
    }


def fake_fetch(release: dict, payload: bytes, calls: list[str]):
    def fetch(url, version, accept, timeout=15):
        calls.append(url)
        if url == app_update.LATEST_RELEASE_API:
            return Response(json.dumps(release).encode("utf-8"))
        return Response(payload)
    return fetch


def wait(updater: AppUpdater, *statuses: str) -> None:
    deadline = time.monotonic() + 5
    while updater.status not in statuses:
        assert time.monotonic() < deadline, updater.status
        time.sleep(0.01)


def test_versions_and_notes_by_language():
    assert parse_version("v1.1.6") == (1, 1, 6)
    assert parse_version("1.10.0") > parse_version("1.9.9")
    assert parse_version("1.1.6-beta") is None
    notes = release_notes(BODY)
    assert set(notes) == {"zh-CN", "en", "pt-BR"}
    assert notes["en"].startswith("- **New:**") and "  - nested" in notes["en"]
    assert "Novo" in notes["pt-BR"] and "RSL-Boss-helper 1.2.0" not in notes["zh-CN"]
    # A body without language sections stays whole.
    assert release_notes("just text") == {"": "just text"}


def test_a_release_needs_its_exe_with_size_and_digest_from_github():
    payload = b"MZ" + b"x" * 100
    info = release_info(document("1.2.0", payload))
    assert info["version"] == "1.2.0" and info["download"]["size"] == len(payload)
    assert info["download"]["sha256"] == hashlib.sha256(payload).hexdigest()
    # No digest, another version's file, or a download outside github.com: download page only.
    no_digest = document("1.2.0", payload)
    no_digest["assets"][0]["digest"] = None
    assert release_info(no_digest)["download"] is None
    assert release_info(document("1.2.0", payload, name="RSL-Boss-helper-1.1.9.exe"))["download"] is None
    elsewhere = document("1.2.0", payload)
    elsewhere["assets"][0]["browser_download_url"] = "https://example.com/RSL-Boss-helper-1.2.0.exe"
    assert release_info(elsewhere)["download"] is None
    assert release_info({**document("1.2.0", payload), "prerelease": True}) is None


def test_check_download_stage_and_swap_in_place():
    payload = b"MZ" + b"new version" * 1000
    calls: list[str] = []
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        app = root / "app"
        app.mkdir()
        exe = app / "RSL-Boss-helper-1.1.6.exe"
        exe.write_bytes(b"MZ old version")
        data = root / "data"
        updater = AppUpdater("1.1.6", data, exe, fetch=fake_fetch(document("1.2.0", payload), payload, calls))
        updater.check()
        wait(updater, "available")
        state = updater.snapshot()
        assert state["version"] == "1.2.0" and state["canInstall"] and state["backup"] == exe.name + ".old"
        # One check per launch; another only when the user asks for it.
        updater.check()
        assert calls == [app_update.LATEST_RELEASE_API]
        updater.check(force=True)
        wait(updater, "available")
        updater.thread.join(2)
        assert calls == [app_update.LATEST_RELEASE_API] * 2
        # Nothing restarts while something runs.
        updater.download()
        wait(updater, "ready")
        assert (data / "cache" / "updates" / "RSL-Boss-helper-1.2.0.exe").read_bytes() == payload
        try:
            updater.stage("busy")
            raise AssertionError("staged while busy")
        except RuntimeError as error:
            assert str(error) == "busy"
        updater.stage(None)
        assert updater.pending and updater.status == "installing"
        assert (app / (exe.name + ".new")).read_bytes() == payload
        assert updater.apply() == exe
        assert exe.read_bytes() == payload
        assert (app / (exe.name + ".old")).read_bytes() == b"MZ old version"
        assert not (app / (exe.name + ".new")).exists()
        assert not (data / "cache" / "updates" / "RSL-Boss-helper-1.2.0.exe").exists()
        # The new version shows the result once, with its notes.
        after = AppUpdater("1.2.0", data, exe, fetch=fake_fetch(document("1.2.0", payload), payload, calls))
        installed = after.snapshot()["installed"]
        assert installed["ok"] and installed["from"] == "1.1.6" and installed["to"] == "1.2.0"
        assert installed["backup"] == exe.name + ".old" and "English notes" in installed["notes"]["en"]
        after.dismiss_installed()
        assert AppUpdater("1.2.0", data, exe).snapshot()["installed"] is None


def test_a_download_that_fails_the_digest_is_dropped():
    payload = b"MZ" + b"tampered" * 100
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        exe = root / "RSL-Boss-helper.exe"
        exe.write_bytes(b"MZ old")
        release = document("1.2.0", payload, digest="0" * 64)
        updater = AppUpdater("1.1.6", root / "data", exe, fetch=fake_fetch(release, payload, []))
        updater.check()
        wait(updater, "available")
        updater.download()
        wait(updater, "failed")
        assert updater.failed_step == "download"
        assert "SHA-256" in render(updater.error, "en")
        assert list((root / "data" / "cache" / "updates").iterdir()) == []
        try:
            updater.stage(None)
            raise AssertionError("staged a failed download")
        except RuntimeError:
            pass
        assert exe.read_bytes() == b"MZ old"


def test_no_update_for_the_same_or_older_release_and_failed_checks_say_why():
    payload = b"MZ"
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        updater = AppUpdater("1.2.0", root, None, fetch=fake_fetch(document("1.1.5", payload), payload, []))
        updater.check()
        wait(updater, "current")
        state = updater.snapshot()
        assert state["version"] is None and state["release"] is None and not state["canInstall"]

        def offline(*_args, **_kwargs):
            raise OSError("no route")
        failing = AppUpdater("1.1.6", root / "other", None, fetch=offline)
        failing.check()
        wait(failing, "failed")
        assert failing.failed_step == "check" and "no route" in render(failing.error, "en")
