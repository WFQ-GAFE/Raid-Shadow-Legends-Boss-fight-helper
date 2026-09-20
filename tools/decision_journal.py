"""Bounded local decision history, independent of the game's command channel."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets


class DecisionJournal:
    # Separate segments prevent two desktop windows from rotating the same file.
    PATTERN = re.compile(r"decisions-\d{8}T\d{12}Z-[0-9a-f]{12}\.jsonl\Z")

    def __init__(self, directory: Path, *, max_bytes: int = 4 * 1024 * 1024,
                 keep_files: int = 64) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self.keep_files = max(1, keep_files)
        self.path: Path | None = None
        self.error: str | None = None
        self.disabled = False

    def write(self, event: str, **fields) -> bool:
        if self.disabled:
            return False
        try:
            now = datetime.now(timezone.utc)
            record = {"schema": 1, "time": now.isoformat(), "event": event, **fields}
            data = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            if len(data) > self.max_bytes:
                raise ValueError("单条诊断记录超过大小上限")
            self.directory.mkdir(parents=True, exist_ok=True)
            rotating = self.path is None or not self.path.exists() or self.path.stat().st_size + len(data) > self.max_bytes
            if rotating:
                stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
                self.path = self.directory / f"decisions-{stamp}-{secrets.token_hex(6)}.jsonl"
            with self.path.open("ab") as stream:
                stream.write(data)
            # Only our own immediate files are eligible for retention cleanup.
            files = sorted(p for p in self.directory.iterdir()
                           if self.PATTERN.fullmatch(p.name) and p.is_file() and not p.is_symlink()) if rotating else []
            excess = max(0, len(files) - self.keep_files)
            for path in files:
                if excess == 0:
                    break
                if path != self.path:
                    try:
                        path.unlink()
                        excess -= 1
                    except OSError:
                        pass  # Another window may still own this segment.
            return True
        except (OSError, ValueError, TypeError) as error:
            self.error = str(error)
            self.disabled = True  # Do not repeatedly stall decisions on broken storage.
            return False
