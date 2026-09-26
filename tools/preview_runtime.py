"""A preview gets an independent store, seeded once without modifying stable data."""
from __future__ import annotations

import json
from pathlib import Path
from strategy_storage import atomic_write_json


def preview_root(stable: Path) -> Path:
    return stable.with_name(stable.name + "-Flow-1.0.6")


def seed_preview(stable: Path, preview: Path) -> None:
    marker = preview / "config" / "preview-seeded.json"
    if marker.exists():
        return
    copied = []
    for relative in ("config/raid-boss-strategies.user.json", "config/raid-boss-ui-preferences.user.json",
                     "cache/chimera-hero-catalog.json", "cache/chimera-ui-catalog.json"):
        source, target = stable / relative, preview / relative
        if source.is_file() and not target.exists():
            value = json.loads(source.read_text(encoding="utf-8-sig"))
            target.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation also protects a user's independently edited preview.
            try:
                with target.open("x", encoding="utf-8") as stream:
                    json.dump(value, stream, ensure_ascii=False, indent=2)
                copied.append(relative)
            except FileExistsError:
                pass
    atomic_write_json(marker, {"version": "1.0.6", "copied": copied})
