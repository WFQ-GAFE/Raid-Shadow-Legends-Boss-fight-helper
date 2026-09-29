"""Boss skills (Hydra heads, the Chimera) by skill type id, for labelling enemy actions.

Simulation reports show which skill a head or the Chimera used. The game's
static data has each skill's name, description and cooldown: the rotation
catalog brings the heads' own skills, the agent answers a read-only request
for any other skill id (the heads' shared mark, swallow and neck skills, the
Chimera's forms), and live battle states name the skills bosses carry. What
was learned is kept in cache/boss-skills.json, so reports read the same when
the game is closed.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterable

SCHEMA = 1
TEXT_FIELDS = ("name", "nameKey", "description", "descriptionKey")
REPORTED_FIELDS = ("name", "description", "defaultCooldown")


def skill_type_id(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def enemy_skill_ids(timeline: Any, actors: Any = None) -> list[int]:
    """The skills the bosses used in a simulated battle: their own actions (action log rows) and,
    given the battle's actors, the skill uses inside actions (e.g. a head provoked into attacking)."""
    bosses = {
        actor.get("actorId") for actor in actors if isinstance(actor, dict) and actor.get("player") is False
    } if isinstance(actors, list) else set()
    found = set()
    for row in timeline if isinstance(timeline, list) else ():
        if not isinstance(row, dict):
            continue
        if row.get("source") == "enemy" and skill_type_id(row.get("skillTypeId")) is not None:
            found.add(row["skillTypeId"])
        for use in row.get("uses") or []:
            if isinstance(use, dict) and use.get("actorId") in bosses and skill_type_id(use.get("skillTypeId")) is not None:
                found.add(use["skillTypeId"])
    return sorted(found)


def listed_skills(bosses: Any) -> list[dict[str, Any]]:
    """The skills listed by boss entries (Hydra heads of the rotation catalog, bosses of a live state)."""
    return [
        skill for boss in bosses if isinstance(boss, dict)
        for skill in boss.get("skills") or [] if isinstance(skill, dict)
    ] if isinstance(bosses, list) else []


class BossSkillCatalog:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.skills: dict[int, dict[str, Any]] = {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        entries = raw.get("skills") if isinstance(raw, dict) else None
        for key, entry in (entries.items() if isinstance(entries, dict) else ()):
            if str(key).isdigit() and isinstance(entry, dict):
                self.skills[int(key)] = entry

    def learn(self, skills: Any, static: bool = False) -> bool:
        """Merge skills from static data (static: the whole entry, final) or a live state (fills gaps)."""
        changed = False
        with self.lock:
            for skill in skills if isinstance(skills, list) else ():
                type_id = skill_type_id(skill.get("typeId")) if isinstance(skill, dict) else None
                if type_id is None:
                    continue
                previous = self.skills.get(type_id, {})
                entry = dict(previous)
                for key in TEXT_FIELDS:
                    value = skill.get(key)
                    if isinstance(value, str) and value.strip() and (static or not entry.get(key)):
                        entry[key] = value.strip()
                cooldown = skill.get("defaultCooldown")
                if static and isinstance(cooldown, int) and not isinstance(cooldown, bool) and cooldown >= 0:
                    entry["defaultCooldown"] = cooldown
                if static:
                    entry["static"] = True
                if entry != previous:
                    self.skills[type_id] = entry
                    changed = True
            if changed:
                self._save()
        return changed

    def missing(self, ids: Iterable[int]) -> list[int]:
        """Skills the static data has not been asked about yet."""
        with self.lock:
            return sorted({
                type_id for type_id in ids
                if skill_type_id(type_id) is not None and not self.skills.get(type_id, {}).get("static")
            })

    def lookup(self, ids: Iterable[int]) -> dict[str, dict[str, Any]]:
        with self.lock:
            result = {}
            for type_id in sorted(set(ids)):
                entry = self.skills.get(type_id) or {}
                reported = {key: entry[key] for key in REPORTED_FIELDS if key in entry}
                if reported.get("name") or reported.get("description"):
                    result[str(type_id)] = reported
            return result

    def _save(self) -> None:
        payload = {"schema": SCHEMA, "skills": {str(key): value for key, value in sorted(self.skills.items())}}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            pass

