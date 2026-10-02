"""Champion data for the team picker's search and the champion profiles.

The isolated offline engine (``raid_offline_probe.exe hero-data``) reads the
game's copied static-data cache and returns every playable champion's
definition, skills and localized names and descriptions. compact() keeps what
the picker filters on and what a profile shows. The result is cached per
engine bundle, whose name changes with the static data, the game build and
the probe; the newest cached result is still served while the game is closed.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from hydra_forecast_live import PROJECT_ROOT
from strategy_storage import atomic_write_bytes
from ui_text import ui_text

SCHEMA = 2
HERO_DATA_ROOT = PROJECT_ROOT / "cache" / "hero-data"
KEEP_FILES = 2
RECHECK_SECONDS = 600.0
PROBE_TIMEOUT_SECONDS = 240
FIXED_ONE = float(2 ** 32)
# 6 stars, level 60: HP, ATK and DEF are the static base stat times this,
# rounded (HP is then multiplied by 15). Fitted to the hero screen's base stats
# of 118 champions; speed, resistance, accuracy and critical stats stay as is.
LEVEL_60_MULTIPLIER = 11.0118
STAT_KEYS = ("Health", "Attack", "Defence", "Speed", "Resistance", "Accuracy", "CriticalChance", "CriticalDamage")

# Damage formula variables, by the stat the damage is based on.
SCALING = {
    "ATK": "ATK", "B_ATK": "ATK", "DEF": "DEF", "B_DEF": "DEF", "HP": "HP", "B_HP": "HP", "CUR_HP": "HP",
    "SPD": "SPD", "ACC": "ACC", "RES": "RES",
    "TRG_HP": "TRG_HP", "TRG_B_HP": "TRG_HP", "REL_TRG_HP": "TRG_HP", "TRG_CUR_HP": "TRG_HP",
}
# Effect kinds a player searches for, under the picker's names; other kinds are
# mechanics behind the skills (counters, damage multipliers, visuals).
SPECIAL_KINDS = {
    "Revive": "Revive", "Heal": "Heal", "IncreaseStamina": "IncreaseStamina", "ReduceStamina": "ReduceStamina",
    "ExtraTurn": "ExtraTurn", "ReduceCooldown": "ReduceCooldown", "IncreaseCooldown": "IncreaseCooldown",
    "RemoveDebuff": "RemoveDebuff", "RemoveBuff": "RemoveBuff", "StealBuff": "StealBuff",
    "TransferDebuff": "TransferDebuff", "IncreaseDebuffLifetime": "IncreaseDebuffLifetime",
    "ReduceDebuffLifetime": "ReduceDebuffLifetime", "IncreaseBuffLifetime": "IncreaseBuffLifetime",
    "ReduceBuffLifetime": "ReduceBuffLifetime", "DestroyHp": "DestroyHp", "DestroyStats": "DestroyStats",
    "TeamAttack": "TeamAttack", "ActivateSkill": "ActivateSkill", "Detonate": "Detonate",
    "DetonateContinuousDamage": "DetonateContinuousDamage", "ForceStatusEffectTick": "ForceStatusEffectTick",
    "MultiplyDebuff": "MultiplyDebuff", "PassiveCounterattack": "Counterattack", "PassiveBlockDebuff": "BlockDebuff",
    "PassiveReflectDamage": "ReflectDamage", "PassiveShareDamage": "ShareDamage", "SwapHealth": "SwapHealth",
    "EvenStamina": "EvenStamina", "SheepTransformation": "Polymorph", "AddIgnoredEffects": "IgnoreBuffs",
    "ChangeDefenceModifier": "IgnoreDefence", "IgnoreDefenceModifier": "IgnoreDefence",
}
ALL_TARGETS = frozenset({"AllEnemies", "AllAllies", "AllDeadAllies", "AllHeroes", "AllDeadHeroes", "OwnerAllies"})
SELF_TARGETS = frozenset({"Producer", "Owner"})
HIDDEN_SKILLS = frozenset({"HiddenOnHud", "HiddenOnHudWithVisualization", "HiddenOnHudVisibleForAl",
                           "HiddenOnHudWithVisualisationVisibleForAI"})


class HeroDataError(RuntimeError):
    """A stable reason why the champion data could not be read."""


def fixed(value: Any) -> float:
    """A game fixed-point number (32.32) as a float; absent means 0."""
    if isinstance(value, dict) and isinstance(value.get("m_rawValue"), int):
        return value["m_rawValue"] / FIXED_ONE
    return 0.0


def nullable(value: Any, default: Any = None) -> Any:
    """The value of a Nullable<T> as dumped ({"hasValue": true, "value": ...});
    a present value equal to the type's default is left out by the dump."""
    if isinstance(value, dict) and "hasValue" in value:
        return value.get("value", default) if value.get("hasValue") else None
    return value


def max_level_stats(base_stats: Any) -> list[float]:
    """HP, ATK, DEF, SPD, RES, ACC, C.RATE %, C.DMG % at 6 stars, level 60."""
    stats = base_stats if isinstance(base_stats, dict) else {}
    values = [fixed(stats.get(key)) for key in STAT_KEYS]
    hp, atk, defence = (round(value * LEVEL_60_MULTIPLIER) for value in values[:3])
    rest = [round(value, 2) if value % 1 else int(value) for value in values[3:]]
    return [hp * 15, atk, defence, *rest]


def _text(texts: dict[str, Any], kind: str, identifier: int) -> str:
    value = texts.get(f"l10n:{kind}?id={identifier}#static")
    return value if isinstance(value, str) else ""


def _scope(effect: dict[str, Any], by_id: dict[Any, dict[str, Any]], skill_targets: str | None,
           depth: int = 0) -> str:
    """Who an effect lands on: target, all, self, random or other."""
    target = (effect.get("TargetParams") or {}).get("TargetType", "Target")
    if target in ALL_TARGETS:
        return "all"
    if target in SELF_TARGETS:
        return "self"
    if isinstance(target, str) and target.startswith("Random"):
        return "random"
    if target == "RelationTarget":
        related = by_id.get(nullable((effect.get("Relation") or {}).get("EffectTypeId"), 0))
        if related is not None and related is not effect and depth < 4:
            return _scope(related, by_id, skill_targets, depth + 1)
        return "target"
    if target == "Target":
        return "self" if skill_targets == "Producer" else "target"
    return "other"


def _percent(value: float) -> float | int:
    percent = round(value * 100, 1)
    return int(percent) if percent == int(percent) else percent


def compact_skill(skill: dict[str, Any], texts: dict[str, Any]) -> dict[str, Any]:
    identifier = skill.get("Id", 0)
    effects = [effect for effect in skill.get("Effects") or [] if isinstance(effect, dict)]
    by_id = {effect.get("Id"): effect for effect in effects}
    targets = nullable(skill.get("Targets"), "Producer")
    out: dict[str, Any] = {"name": _text(texts, "skill/name", identifier),
                           "desc": _text(texts, "skill/description", identifier)}
    if skill.get("Cooldown"):
        out["cd"] = skill["Cooldown"]
    if skill.get("Group") == "Passive":
        out["passive"] = True
    if skill.get("Visibility") in HIDDEN_SKILLS:
        out["hidden"] = True
    damage: list[str] = []
    damage_scopes: list[str] = []
    scaling: set[str] = set()
    special: set[str] = set()
    applied: dict[str, list[list[Any]]] = {"buffs": [], "debuffs": []}
    aoe = False
    for effect in effects:
        kind = effect.get("KindId", "Revive")  # EffectKindId 0 is Revive
        formula = effect.get("MultiplierFormula") if isinstance(effect.get("MultiplierFormula"), str) else ""
        scope = _scope(effect, by_id, targets)
        if kind == "Damage":
            if formula:
                damage.append(formula)
                damage_scopes.append(scope)
                scaling.update(SCALING[token] for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", formula)
                               if token in SCALING)
            aoe = aoe or scope == "all"
        elif kind in ("ApplyBuff", "ApplyDebuff"):
            chance = nullable(effect.get("Chance"))
            infos = (effect.get("ApplyStatusEffectParams") or {}).get("StatusEffectInfos") or []
            for info in infos:
                if isinstance(info, dict) and isinstance(info.get("TypeId"), int):
                    applied["buffs" if kind == "ApplyBuff" else "debuffs"].append([
                        info["TypeId"], info.get("Duration", 0),
                        _percent(fixed(chance)) if chance is not None else None,
                        scope, bool(effect.get("Condition"))])
        name = SPECIAL_KINDS.get(kind)
        if name == "IgnoreDefence" and not formula.startswith("-"):
            name = None
        if name:
            special.add(name)
    if damage:
        out["damage"] = damage
        # Who each damage formula hits ("target", "all", ...), when not all the target.
        if any(scope != "target" for scope in damage_scopes):
            out["damageScopes"] = damage_scopes
    if scaling:
        out["scaling"] = sorted(scaling)
    if aoe:
        out["aoe"] = True
    for key, rows in applied.items():
        if rows:
            out[key] = rows
    if special:
        out["special"] = sorted(special)
    books: dict[str, float] = {}
    for bonus in skill.get("SkillLevelBonuses") or []:
        if isinstance(bonus, dict):
            kind = bonus.get("SkillBonusType", "Attack")
            books[kind] = books.get(kind, 0.0) + fixed(bonus.get("Value"))
    if books:
        out["books"] = {kind: (int(round(value)) if kind == "CooltimeTurn" else _percent(value))
                        for kind, value in books.items()}
    return out


def _aura(leader: dict[str, Any]) -> dict[str, Any]:
    absolute = bool(leader.get("IsAbsolute"))
    amount = fixed(leader.get("Amount"))
    aura: dict[str, Any] = {"stat": leader.get("StatKindId"),
                            "value": (int(round(amount)) if absolute else _percent(amount)),
                            "absolute": absolute}
    for key, name in (("Area", "area"), ("Element", "element"), ("Faction", "faction")):
        value = nullable(leader.get(key))
        if value is not None:
            aura[name] = value
    return aura


def _forms(hero: dict[str, Any]) -> list[dict[str, Any]]:
    forms = []
    for form in hero.get("Forms") or []:
        if not isinstance(form, dict):
            continue
        forms.append({"element": form.get("Element"), "role": form.get("Role", "Attack"),
                      "stats": max_level_stats(form.get("BaseStats")),
                      "skills": [value for value in form.get("SkillTypeIds") or [] if isinstance(value, int)]})
    return forms


def compact(raw: dict[str, Any]) -> dict[str, Any]:
    """The probe's hero-data report reduced to the picker's data."""
    texts = raw.get("texts") if isinstance(raw.get("texts"), dict) else {}
    entries = [item for item in raw.get("heroes") or [] if isinstance(item, dict) and isinstance(item.get("id"), int)]
    skills: dict[str, dict[str, Any]] = {}
    for item in entries:
        for skill in item.get("skills") or []:
            if isinstance(skill, dict) and isinstance(skill.get("Id"), int):
                skills[str(skill["Id"])] = compact_skill(skill, texts)
    by_id = {item["id"]: item for item in entries}
    heroes: dict[str, dict[str, Any]] = {}
    for identifier, item in sorted(by_id.items()):
        variant = identifier % 10
        if variant and identifier - variant in by_id:
            continue  # an ascension variant, kept under its base below
        hero = item.get("hero") if isinstance(item.get("hero"), dict) else {}
        forms = _forms(hero)
        entry: dict[str, Any] = {"name": _text(texts, "hero-type/name", identifier),
                                 "faction": hero.get("Fraction"), "rarity": hero.get("Rarity"), "forms": forms}
        auras = [_aura(leader) for leader in hero.get("LeaderSkills") or [] if isinstance(leader, dict)]
        if auras:
            entry["aura"] = auras
        ascension: dict[str, Any] = {}
        for level in range(1, 7):
            variant_item = by_id.get(identifier + level)
            if not variant_item:
                continue
            variant_forms = _forms(variant_item.get("hero") or {})
            ascension[str(level)] = {
                "stats": [form["stats"] for form in variant_forms],
                "skills": [[skill for skill in form["skills"]
                            if index >= len(forms) or skill not in forms[index]["skills"]]
                           for index, form in enumerate(variant_forms)],
            }
        if ascension:
            entry["ascension"] = ascension
        heroes[str(identifier)] = entry
    enums = raw.get("enums") if isinstance(raw.get("enums"), dict) else {}
    used = {row[0] for skill in skills.values() for key in ("buffs", "debuffs") for row in skill.get(key, [])}
    return {"schema": SCHEMA, "heroes": heroes, "skills": skills,
            "statusEffects": _status_effects(raw, used, enums),
            "labels": _labels(raw, heroes, texts, enums)}


def _strength(kind: str, formula: Any) -> float | None:
    """A status effect's strength in percent from its formula (0.5*TRG_B_ATK: 50)."""
    match = re.match(r"\s*(\d*\.?\d+)\s*(\*|$)", formula) if isinstance(formula, str) else None
    if not match:
        return None
    value = float(match.group(1))
    if kind == "IncreaseDamageTaken":
        value = value - 1 if 1 < value < 2 else -1
    elif kind == "ReduceDamageTaken":
        value = 1 - value
    return _percent(value) if 0 < value <= 1 else None


def _status_effects(raw: dict[str, Any], used: set[int], enums: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The buffs and debuffs the skills apply: the game's name and description of
    their kind, and the strength when the kind comes in several (25% / 50%)."""
    client = raw.get("labels") if isinstance(raw.get("labels"), dict) else {}
    native = enums.get("SharedModel.Battle.Effects.StatusEffectTypeId", {})
    types = {item["Id"]: item for item in raw.get("effectTypes") or []
             if isinstance(item, dict) and isinstance(item.get("Id"), int)}
    strengths: dict[str, set[float | None]] = {}
    for item in types.values():
        kind = str(item.get("KindId", ""))
        strengths.setdefault(kind, set()).add(_strength(kind, item.get("MultiplierFormula")))
    result: dict[str, dict[str, Any]] = {}
    for identifier in sorted(used):
        item = types.get(identifier, {})
        kind = str(item.get("KindId", "")) or None
        entry: dict[str, Any] = {"native": native.get(str(identifier))}
        if kind:
            entry["kind"] = kind
            # A stronger variant may have its own name: Fear / True Fear ("fear2").
            family = (item.get("StatusParams") or {}).get("StrengthInFamily", 0)
            keys = ([f"{kind.lower()}{family}"] if isinstance(family, int) and family > 1 else []) + [kind.lower()]
            name = next((client[key] for key in (f"l10n:battle-hud/effect-kindId?id={key}#label" for key in keys)
                         if isinstance(client.get(key), str)), None)
            description = next((client[key] for key in (f"l10n:status-effects/effect-kindId?id={key}#description" for key in keys)
                                if isinstance(client.get(key), str)), None)
            # Placeholder texts ("None", "<N/A>", "... ???") are left out.
            if isinstance(name, str) and name and name not in ("None", "<N/A>"):
                entry["name"] = name
            if isinstance(description, str) and description and "???" not in description:
                entry["desc"] = description
            strength = _strength(kind, item.get("MultiplierFormula"))
            if strength is not None and len(strengths.get(kind, set()) - {None}) > 1:
                entry["strength"] = strength
        result[str(identifier)] = {key: value for key, value in entry.items() if value is not None}
    return result


# The client's keys for the stats a profile or an aura shows.
STAT_LABEL_KEYS = {"Health": "health", "Attack": "attack", "Defence": "defence", "Speed": "speed",
                   "Resistance": "resistance", "Accuracy": "accuracy", "CriticalChance": "critChance",
                   "CriticalDamage": "critDamage"}
ROLE_LABEL_KEYS = {"Attack": "attack", "Defense": "defence", "Health": "health", "Support": "support"}


def _labels(raw: dict[str, Any], heroes: dict[str, dict[str, Any]], texts: dict[str, Any],
            enums: dict[str, Any]) -> dict[str, dict[str, str]]:
    """The game client's own names for factions, affinities, roles, rarities, stats and battle areas."""
    client = raw.get("labels") if isinstance(raw.get("labels"), dict) else {}

    def pick(table: dict[str, str]) -> dict[str, str]:
        return {name: client[key] for name, key in table.items() if isinstance(client.get(key), str) and client[key]}

    factions = sorted({hero["faction"] for hero in heroes.values() if isinstance(hero.get("faction"), str)})
    rarities = sorted({hero["rarity"] for hero in heroes.values() if isinstance(hero.get("rarity"), str)})
    elements = sorted({form["element"] for hero in heroes.values() for form in hero["forms"]
                       if isinstance(form.get("element"), str)})
    areas = {name: texts.get(f"l10n:area/name?id={value}#static")
             for value, name in (enums.get("SharedModel.Meta.Stages.AreaTypeId") or {}).items()}
    return {
        "faction": pick({name: f"l10n:hero/fraction?name={name}#label" for name in factions}),
        "rarity": pick({name: f"l10n:hero/rarity/{name.lower()}#label" for name in rarities}),
        "element": pick({name: f"l10n:hero/element/{name.lower()}#label" for name in elements}),
        "role": pick({name: f"l10n:hero/role/{key}#label" for name, key in ROLE_LABEL_KEYS.items()}),
        "stat": pick({name: f"l10n:common/hero-stats/short/{key}#label" for name, key in STAT_LABEL_KEYS.items()}),
        "area": {name: value for name, value in areas.items() if isinstance(value, str) and value},
    }


def run_probe(probe: Path, bundle: Path, timeout: float = PROBE_TIMEOUT_SECONDS) -> dict[str, Any]:
    """The probe's hero-data report for every playable champion."""
    try:
        # A GUI-subsystem program; no console window either way.
        process = subprocess.run([str(probe), "hero-data", str(bundle), "all"], capture_output=True, text=True,
                                 encoding="utf-8", timeout=timeout, check=False,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as error:
        raise HeroDataError(ui_text("heroData.timeout")) from error
    try:
        wrapper = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise HeroDataError(ui_text("heroData.noData")) from error
    observation = wrapper.get("observation") if isinstance(wrapper, dict) else None
    if process.returncode != 0 or not isinstance(observation, dict) \
            or observation.get("phase") != "hero_data_exported" or not isinstance(observation.get("heroData"), dict):
        stage = observation.get("stage") if isinstance(observation, dict) else None
        raise HeroDataError(ui_text("heroData.engineFailed", stage=stage or process.returncode))
    return observation["heroData"]


class HeroDataService:
    """The compact champion data, built in the background once per engine bundle."""

    def __init__(self, root: Path = HERO_DATA_ROOT,
                 bundle_provider: Callable[[int | None], Path] | None = None,
                 runner: Callable[[Path, Path], dict[str, Any]] = run_probe) -> None:
        self.root = root
        self.bundle_provider = bundle_provider
        self.runner = runner
        self.lock = threading.Lock()
        self.data: dict[str, Any] | None = None
        self.key = ""
        self.error = ""
        self.building = False
        self.checked_at = 0.0

    def snapshot(self, pid: int | None) -> dict[str, Any]:
        """{"status": "ready", "data": ...}, "building" or "unavailable" with a reason."""
        with self.lock:
            if self.data is None and not self.building:
                self.data = self._newest_cached()
            stale = time.monotonic() - self.checked_at > RECHECK_SECONDS
            if stale and not self.building:
                self.building = True
                self.checked_at = time.monotonic()
                threading.Thread(target=self._build, args=(pid,), name="hero-data", daemon=True).start()
            if self.data is not None:
                return {"status": "ready", "data": self.data}
            if self.building:
                return {"status": "building"}
            return {"status": "unavailable", "reason": self.error or ui_text("heroData.unavailable")}

    def _newest_cached(self) -> dict[str, Any] | None:
        try:
            files = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        except OSError:
            return None
        for path in files:
            data = self._load(path)
            if data is not None:
                self.key = path.stem
                return data
        return None

    @staticmethod
    def _load(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None

    def _build(self, pid: int | None) -> None:
        data: dict[str, Any] | None = None
        key = ""
        error = ""
        try:
            if self.bundle_provider is None:
                from chimera_simulation_service import engine_bundle
                self.bundle_provider = engine_bundle
            bundle = self.bundle_provider(pid)
            key = bundle.name
            path = self.root / f"{key}.json"
            data = self._load(path) if key != self.key or self.data is None else self.data
            if data is None:
                data = compact(self.runner(bundle / "raid_offline_probe.exe", bundle))
                self.root.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                for old in sorted(self.root.glob("*.json"), key=lambda item: item.stat().st_mtime_ns,
                                  reverse=True)[KEEP_FILES:]:
                    old.unlink(missing_ok=True)
        except Exception as caught:  # The picker works without the data; keep the reason.
            error = str(caught) or type(caught).__name__
        with self.lock:
            if data is not None:
                self.data, self.key, self.error = data, key, ""
            else:
                self.error = error
                # Try again sooner after a failure (the game may not be open yet).
                self.checked_at = time.monotonic() - RECHECK_SECONDS + 15
            self.building = False


__all__ = ["HeroDataError", "HeroDataService", "compact", "compact_skill", "max_level_stats", "run_probe"]
