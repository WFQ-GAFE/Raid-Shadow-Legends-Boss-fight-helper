"""Which saved battle openings repeat each other.

Two openings repeat each other when everything the offline engine starts from
is the same except what every battle draws anew: the battle id (`z`), the
random seed (`r`) and the time (`t`), plus the in-game skill priorities of the
heroes (`p`), which simulations never use. A repeat adds nothing that a
simulation with another seed cannot do, so only the newest one is kept and the
simulation cards list each set-up once.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from typing import Any, Iterable

PER_BATTLE_KEYS = ("z", "r", "t")
HERO_PER_BATTLE_KEYS = ("p",)
_cache: dict[str, tuple[tuple[int, int, int, int], str | None]] = {}
_cache_lock = threading.Lock()


def _normalized(setup: Any) -> list[Any] | None:
    setups = setup if isinstance(setup, list) else [setup]
    result = []
    for item in setups:
        if not isinstance(item, dict):
            return None
        entry = {key: value for key, value in item.items() if key not in PER_BATTLE_KEYS}
        team = entry.get("f")
        if isinstance(team, dict) and isinstance(team.get("h"), list):
            entry["f"] = {**team, "h": [{key: value for key, value in hero.items() if key not in HERO_PER_BATTLE_KEYS}
                                        if isinstance(hero, dict) else hero for hero in team["h"]]}
        result.append(entry)
    return result


def capture_key(folder: Path) -> str | None:
    """The opening's set-up fingerprint (team, gear, bonuses, boss, stage, engine settings), or None."""
    setup_file, settings_file = folder / "battle-setup.json", folder / "battle-settings.json"
    try:
        setup_stat, settings_stat = setup_file.stat(), settings_file.stat()
    except OSError:
        return None
    stamp = (setup_stat.st_mtime_ns, setup_stat.st_size, settings_stat.st_mtime_ns, settings_stat.st_size)
    with _cache_lock:
        cached = _cache.get(str(folder))
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        normalized = _normalized(json.loads(setup_file.read_text(encoding="utf-8")))
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        normalized = None
    key = None
    if normalized is not None:
        body = json.dumps({"setup": normalized, "settings": settings}, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(body.encode("utf-8")).hexdigest()
    with _cache_lock:
        _cache[str(folder)] = (stamp, key)
    return key


def unique_first(folders: Iterable[Path]) -> list[Path]:
    """The folders in the given order, leaving out repeats of an earlier one."""
    seen: set[str] = set()
    result = []
    for folder in folders:
        key = capture_key(folder)
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        result.append(folder)
    return result


def drop_repeats(root: Path, keep: Path) -> list[str]:
    """Delete the openings in `root` that repeat `keep` or a newer opening; returns their names.

    `keep` (the battle just saved) always stays; among older repeats of another
    set-up the most recently modified one stays.
    """
    try:
        folders = [item for item in root.iterdir() if item.is_dir() and item != keep
                   and (item / "battle-setup.json").is_file()]
    except OSError:
        return []
    folders.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    kept = set(unique_first([keep, *folders]))
    removed = []
    for folder in folders:
        if folder in kept:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        with _cache_lock:
            _cache.pop(str(folder), None)
        removed.append(folder.name)
    return removed
