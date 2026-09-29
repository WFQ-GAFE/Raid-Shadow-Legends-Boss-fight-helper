"""Where the packaged application keeps strategies, caches and logs.

From 1.1.1 the data lives in ``%LOCALAPPDATA%\\RSL-Boss-helper``.
The first start copies what an earlier version saved, once, from the newest
of: the folder of 1.1.1 test builds (``WFQ-GAFE\\RSL-Boss-helper``), the one of
1.0.6 and 1.1.0 (``WFQ-GAFE\\RaidBossStrategyStudio-Flow-1.0.6``) and the 1.0.5
one (``WFQ-GAFE\\RaidBossStrategyStudio``). Old folders are only read, so an
older version still finds its own data. Content the tool rebuilds from the game
(copied game files, extracted icons) is not copied.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time

DATA_DIR_NAME = "RSL-Boss-helper"
# Earlier data folders under %LOCALAPPDATA%, newest first.
LEGACY_DIRS = (
    Path("WFQ-GAFE") / "RSL-Boss-helper",
    Path("WFQ-GAFE") / "RaidBossStrategyStudio-Flow-1.0.6",
    Path("WFQ-GAFE") / "RaidBossStrategyStudio",
)
MARKER = Path("config") / "data-migration.json"
# Relative paths (with forward slashes) that are rebuilt on demand or obsolete.
# An earlier folder's own marker is never copied: the target's marker is written
# only when its copy is complete.
SKIPPED = frozenset({
    "runtime", "out",
    "cache/chimera-visual-assets", "cache/chimera-icons", "cache/team-stats",
    "config/preview-seeded.json", MARKER.as_posix(),
})


def local_app_data() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) if local else Path.home() / "AppData" / "Local"


def data_root() -> Path:
    return local_app_data() / DATA_DIR_NAME


def legacy_source(base: Path) -> Path | None:
    return next((base / name for name in LEGACY_DIRS if (base / name / "config").is_dir()), None)


def _skipped(relative: Path) -> bool:
    parts = relative.as_posix().split("/")
    return any("/".join(parts[:index]) in SKIPPED for index in range(1, len(parts) + 1))


def migrate_legacy_data(target: Path, base: Path | None = None) -> dict | None:
    """Copy the previous version's data into ``target`` once.

    Existing files in the target are never overwritten, so an interrupted copy
    simply continues on the next start. The marker is written last.
    """
    marker = target / MARKER
    if marker.is_file():
        return None
    source = legacy_source(base or target.parent)
    copied = 0
    if source is not None:
        for directory, subdirectories, files in os.walk(source):
            relative_directory = Path(directory).relative_to(source)
            subdirectories[:] = [name for name in subdirectories if not _skipped(relative_directory / name)]
            for name in files:
                relative = relative_directory / name
                destination = target / relative
                if _skipped(relative) or destination.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(directory) / name, destination)
                copied += 1
    record = {"from": str(source) if source else None, "files": copied,
              "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record
