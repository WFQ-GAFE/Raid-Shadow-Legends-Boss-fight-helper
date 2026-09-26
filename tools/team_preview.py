"""The prepared team: each hero's overall stats, sets, masteries, blessing and relic.

On the preparation screen the agent publishes, for every selected hero (agent
``team_preview`` slot, all serialized with the game's own JsonMain):

* the hero model (``Hero``: level, stars, awakening, empower, skills,
  masteries, blessing),
* the hero screen's own numbers — ``DefaultHeroStats.Base`` and ``.Bonus``
  (the in-game "Base | Bonus"; total = base + bonus),
* the power, each equipped artifact's slot/set/rank/rarity and the relics,
* the localized set, blessing, relic and mastery names and the game's icon
  names for sets and blessings.

This module decodes that into a plain team description for the interface and
for strategy exports. Key names follow the game's [Json("...")] attributes
(docs/1.0.6-team-preview.md lists their source).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

from strategy_storage import atomic_write_json


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
SNAPSHOT_ROOT = PROJECT_ROOT / "cache" / "team-snapshots"
MAX_SNAPSHOT_BYTES = 512 * 1024
FIXED_ONE = 4294967296.0
# BattleStats JSON keys in StatKindId order 1..10: HP, ATK, DEF, SPD, RES, ACC,
# crit rate, crit damage, crit heal, ignore defence.
STAT_KEYS = ("h", "a", "d", "s", "r", "t", "u", "m", "c", "i")
SCHEMA = 2


def fixed(value: Any) -> float:
    return float(value) / FIXED_ONE if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _json(value: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) and value else None
    except json.JSONDecodeError:
        return None


def decode_stats(value: Any) -> list[float] | None:
    """A serialized BattleStats as ten values in StatKindId order."""
    stats = _json(value)
    if not isinstance(stats, dict):
        return None
    return [round(fixed(stats.get(key)), 4) for key in STAT_KEYS]


def icon_name(url: Any) -> str | None:
    """The sprite name at the end of a game icon URL."""
    if not isinstance(url, str) or not url.strip():
        return None
    name = url.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    name = name.split("?", 1)[0].rsplit(".", 1)[0] if "." in name else name.split("?", 1)[0]
    return name if name and all(char.isalnum() or char in "_-" for char in name) else None


def decode_hero(item: dict[str, Any]) -> dict[str, Any] | None:
    model = _json(item.get("heroJson"))
    if not isinstance(model, dict):
        return None
    ascend = model.get("da") if isinstance(model.get("da"), dict) else {}
    mastery = model.get("m") if isinstance(model.get("m"), dict) else {}
    hero: dict[str, Any] = {
        "heroId": _integer(item.get("heroId")) or _integer(model.get("i")),
        "typeId": _integer(model.get("t")), "level": _integer(model.get("l")) or 0,
        "grade": _integer(model.get("g")) or 0, "empower": _integer(model.get("e")) or 0,
        "awakened": _integer(ascend.get("g")) or 0,
        "skills": [{"typeId": skill.get("t"), "level": skill.get("l")}
                   for skill in model.get("s") or [] if isinstance(skill, dict) and _integer(skill.get("t"))],
        "masteries": [value for value in mastery.get("m") or [] if _integer(value)],
    }
    if _integer(ascend.get("b")):
        hero["blessing"] = ascend["b"]
    power = item.get("power") if isinstance(item.get("power"), (int, float)) else model.get("p")
    if isinstance(power, (int, float)) and not isinstance(power, bool):
        hero["power"] = round(float(power))
    base, bonus = decode_stats(item.get("baseStats")), decode_stats(item.get("bonusStats"))
    if base and bonus:
        hero["stats"] = {"base": base, "bonus": bonus,
                         "total": [round(left + right, 4) for left, right in zip(base, bonus)]}
    elif item.get("statsReason"):
        hero["statsReason"] = item["statsReason"]
    sets: dict[int, int] = {}
    for artifact in item.get("artifacts") or []:
        set_id = _integer(artifact.get("set")) if isinstance(artifact, dict) else None
        if set_id:
            sets[set_id] = sets.get(set_id, 0) + 1
    hero["equipped"] = sum(sets.values())
    hero["sets"] = [{"set": set_id, "pieces": count}
                    for set_id, count in sorted(sets.items(), key=lambda pair: (-pair[1], pair[0]))]
    relic = item.get("relic") if isinstance(item.get("relic"), dict) else None
    if relic and _integer(relic.get("typeId")):
        hero["relic"] = {"typeId": relic["typeId"], "rank": _integer(relic.get("rank")) or 0,
                         "level": _integer(relic.get("level")) or 0}
    return hero


def decode_preview(raw: Any) -> dict[str, Any] | None:
    """The agent's team_preview slot → a team description."""
    if not isinstance(raw, dict) or raw.get("type") != "team_preview":
        return None
    result: dict[str, Any] = {"schema": SCHEMA, "bossMode": raw.get("bossMode"),
                              "observedAtTick": raw.get("observedAtTick"),
                              "heroIds": [value for value in raw.get("heroIds") or [] if _integer(value)]}
    if raw.get("status") != "captured" or raw.get("schema") != SCHEMA:
        result.update(status="unavailable",
                      reason=raw.get("reason") or ("agent_outdated" if raw.get("status") == "captured" else raw.get("status")))
        return result
    heroes = [hero for hero in (decode_hero(item) for item in raw.get("heroes") or [] if isinstance(item, dict))
              if hero is not None]
    names = raw.get("names") if isinstance(raw.get("names"), dict) else {}
    icons = raw.get("icons") if isinstance(raw.get("icons"), dict) else {}
    result.update(
        status="captured", heroes=heroes,
        names={key: value for key, value in names.items() if isinstance(value, dict)},
        icons={key: {identity: icon_name(url) for identity, url in value.items() if icon_name(url)}
               for key, value in icons.items() if isinstance(value, dict)},
        teamKey=hashlib.sha256(json.dumps(raw.get("heroes"), sort_keys=True).encode("utf-8")).hexdigest()[:24])
    return result


def public_preview(preview: dict[str, Any] | None) -> dict[str, Any] | None:
    return copy.deepcopy(preview) if preview is not None else None


def team_key(type_ids: Any) -> str | None:
    """Order-free identity of a team: its heroes' type ids."""
    values = sorted(value for value in (type_ids or []) if _integer(value) and value > 0)
    return "-".join(map(str, values)) or None


def portable_snapshot(public: dict[str, Any]) -> dict[str, Any]:
    """A shareable team description: no account-specific hero ids."""
    heroes = [{key: value for key, value in hero.items() if key != "heroId"} for hero in public.get("heroes") or []]
    return {"schema": SCHEMA, "bossMode": public.get("bossMode"), "heroes": heroes,
            "names": public.get("names") or {}, "icons": public.get("icons") or {},
            "capturedAt": public.get("capturedAt") or time.strftime("%Y-%m-%d %H:%M:%S")}


def sanitize_reference_team(value: Any) -> dict[str, Any] | None:
    """An imported author's team: kept for reference only, bounded in size."""
    if not isinstance(value, dict) or not isinstance(value.get("heroes"), list):
        return None
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        return None
    return {"schema": value.get("schema"), "bossMode": value.get("bossMode"),
            "heroes": [hero for hero in value["heroes"][:6] if isinstance(hero, dict)],
            "names": value.get("names") if isinstance(value.get("names"), dict) else {},
            "icons": value.get("icons") if isinstance(value.get("icons"), dict) else {},
            "capturedAt": value.get("capturedAt")}


class TeamSnapshotStore:
    """The newest portable snapshot of each team seen on a preparation screen."""

    def __init__(self, root: Path = SNAPSHOT_ROOT):
        self.root = root
        self.written: dict[str, str] = {}

    def remember(self, public: dict[str, Any]) -> None:
        if public.get("status") != "captured":
            return
        key = team_key(hero.get("typeId") for hero in public.get("heroes") or [])
        if not key or self.written.get(key) == public.get("teamKey"):
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.root / f"{key}.json", portable_snapshot(public))
            self.written[key] = str(public.get("teamKey"))
        except OSError:
            pass

    def find(self, type_ids: Any) -> dict[str, Any] | None:
        key = team_key(type_ids)
        if not key:
            return None
        try:
            value = json.loads((self.root / f"{key}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) and value.get("schema") == SCHEMA else None
