"""Hero battle setups for simulating a team that has not fought this boss.

A battle's hero setups (BattleSetup.FirstTeam, `HeroSlotSetup`) are built by
the server from the account with the game's shared model code. On the
preparation screen the agent calls that same code for every selected hero
(capture_team_preview): ArtifactSetup.FromArtifact, ArtifactExtensions.
GetArtifactSetBonuses, BlessingSetup.From, RelicExtensions.ToRelicSetups,
AcademyWrapperReadOnly.Setup and the village building and area (observatory)
bonuses. This module

* assembles those parts into HeroSlotSetup JSON (keys as in battle-setup.json);
* checks the result against the setups the server sent for the same heroes in
  saved battles, which also tells how the building and area bonuses combine;
* provides the teams a simulation can use: the strategy group's heroes as they
  are now (agent team data request by hero id, any screen), the snapshot taken
  when the strategy group was saved (cache/strategy-teams, with its display
  data for exports) and an imported author's team (exported battle data). Saved
  and exported teams keep only what belongs to the heroes (gear, sets,
  blessing, relic, skills, masteries); the academy, building and area bonuses
  always come from the current account (agent bonus request by hero type);
* swaps a team into a saved opening, for the offline engine.

The client only builds a relic's stat bonus (RelicSetup.PartialCreateFromRelic);
the server adds its skill level, relic and stone skills and the relic info. The
agent reports those pieces (`relicBattle`) and `relic_setup` completes the setup
the way the server does.

Skill priorities (`p`) only steer the game's own auto battle. Every simulated
action comes from the strategy, so the in-game order is never read: each round
gets the neutral priority 0 for every skill (the shape the server sends).
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any
import zlib

from strategy_storage import atomic_write_bytes, atomic_write_json


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
STRATEGY_TEAM_ROOT = PROJECT_ROOT / "cache" / "strategy-teams"
CHECK_ROOT = PROJECT_ROOT / "cache" / "team-setup-checks"
CAPTURE_ROOTS = (PROJECT_ROOT / "cache" / "chimera-capture", PROJECT_ROOT / "cache" / "hydra-capture",
                 PROJECT_ROOT / "cache" / "hydra-forecast")
TEAM_SIZE = {"chimera": 5, "hydra": 6}
# ObservatoryLocationId of each boss (SharedModel.Meta.Village): Hydra = 6, AllianceBoss = 7.
AREA_LOCATIONS = {"hydra": ("6", "7"), "chimera": ("7", "6")}
# Ways the building (`z`) bonus may be combined; the check against saved
# battles picks the one the server uses.
BUILDING_RULES = ("building+area", "building", "capitol+area", "capitol")
# HeroSlotSetup fields that belong to the hero and its gear (compared with saved battles).
HERO_FIELDS = ("i", "h", "g", "w", "l", "x", "r", "v", "s", "b", "e", "y", "c", "bl", "re", "z")
PARTS = ("artifacts", "sets", "blessing", "relics", "academy", "building", "capitol")
# Parts that come from the account rather than the hero: read live, never saved.
ACCOUNT_PARTS = ("academy", "building", "capitol")
# Relic stats the server lists as a percent bonus while the client's partial setup
# has them flat (every saved battle: `m` only ever in `p`, all others in `f`).
RELIC_PERCENT_STATS = ("m",)
# A team's battle data inside an exported strategy (about 5 KB for six heroes).
SIMULATION_FORMAT = "zlib+base64"
MAX_SIMULATION_TEXT = 256 * 1024
MAX_SIMULATION_JSON = 2 * 1024 * 1024
TEAM_SOURCES = ("battle", "current", "strategy", "author")


class TeamSetupError(ValueError):
    """A stable, user-facing reason why a team cannot be simulated."""


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def hero_battle_inputs(item: dict[str, Any], model: dict[str, Any]) -> dict[str, Any] | None:
    """The agent's per-hero setup parts plus the hero-model fields a setup needs."""
    raw = item.get("setup")
    if not isinstance(raw, dict):
        return None
    parts: dict[str, Any] = {}
    for name in PARTS:
        value = raw.get(name)
        try:
            parts[name] = json.loads(value) if isinstance(value, str) and value else None
        except json.JSONDecodeError:
            parts[name] = None
    relic = raw.get("relicBattle")
    if isinstance(relic, dict):
        try:
            parts["relicBattle"] = {**relic, "relic": json.loads(relic["relic"])}
        except (KeyError, TypeError, json.JSONDecodeError):
            pass
    ascend = model.get("da") if isinstance(model.get("da"), dict) else {}
    mastery = model.get("m") if isinstance(model.get("m"), dict) else {}
    return {"model": {"heroId": model.get("i"), "typeId": model.get("t"), "grade": model.get("g"),
                      "level": model.get("l"), "experience": model.get("x", 0), "empower": model.get("e", 0),
                      "awakened": ascend.get("g"),
                      "skills": [{"i": skill.get("t"), "l": skill.get("l")} for skill in model.get("s") or []
                                 if isinstance(skill, dict) and _integer(skill.get("t"))],
                      "masteries": [value for value in mastery.get("m") or [] if _integer(value)]},
            "parts": parts}


def _stats_sum(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any] | None:
    """Two BuildingSetup values ({p: stats, f: stats}) added field by field (Q32.32 integers)."""
    if not isinstance(left, dict):
        return copy.deepcopy(right) if isinstance(right, dict) else None
    if not isinstance(right, dict):
        return copy.deepcopy(left)
    result = copy.deepcopy(left)
    for group in set(left) | set(right):
        a, b = left.get(group), right.get(group)
        if isinstance(a, dict) and isinstance(b, dict):
            result[group] = {key: (a.get(key) or 0) + (b.get(key) or 0) for key in set(a) | set(b)}
        elif isinstance(b, dict):
            result[group] = copy.deepcopy(b)
    return result


def building_bonus(parts: dict[str, Any], area: dict[str, Any] | None, rule: str) -> dict[str, Any] | None:
    base = parts.get("capitol") if rule.startswith("capitol") else parts.get("building")
    return _stats_sum(base, area) if rule.endswith("+area") else copy.deepcopy(base)


def relic_setup(partial: dict[str, Any], battle: dict[str, Any]) -> dict[str, Any]:
    """A relic's setup as the server builds it: the client's stat bonus plus skills and info."""
    relic = battle.get("relic") if isinstance(battle.get("relic"), dict) else {}
    level = battle.get("skillLevel")
    if not _integer(level) or not _integer(relic.get("i")):
        raise TeamSetupError("圣物数据不完整：请重启游戏加载新版模块后，在准备界面重新保存策略组")
    skills = [value for value in battle.get("skills") or [] if _integer(value)]
    for bonus in battle.get("factionSkills") or []:
        if isinstance(bonus, dict) and bonus.get("fraction") == battle.get("fraction"):
            skills.extend(value for value in bonus.get("skills") or [] if _integer(value))
    stones = {stone["id"]: stone for stone in battle.get("stones") or []
              if isinstance(stone, dict) and _integer(stone.get("id"))}
    stone_skills: list[dict[str, int]] = []
    sockets: list[dict[str, Any]] = []
    for socket in relic.get("k") or []:
        entry: dict[str, Any] = {"k": socket.get("k")}
        stone = stones.get(socket.get("s"))
        if _integer(socket.get("s")):
            entry["s"] = socket["s"]
        if stone:
            entry["t"] = stone.get("typeId")
            stone_skills.extend({"i": value, "l": 0} for value in stone.get("skills") or [] if _integer(value))
        sockets.append(entry)
    info = {key: relic[key] for key in ("i", "t", "a", "l", "e", "c") if key in relic}
    info["k"] = sockets
    percent = copy.deepcopy(partial.get("p")) if isinstance(partial.get("p"), dict) else {}
    flat = copy.deepcopy(partial.get("f")) if isinstance(partial.get("f"), dict) else {}
    for key in RELIC_PERCENT_STATS:
        if flat.get(key):
            percent[key] = (percent.get(key) or 0) + flat[key]
            flat[key] = 0
    return {"l": level, "r": [{"i": value, "l": level} for value in skills], "s": stone_skills,
            "p": percent, "f": flat, "n": info}


def hero_relic(parts: dict[str, Any]) -> dict[str, Any] | None:
    relics = parts.get("relics") if isinstance(parts.get("relics"), list) else []
    if not relics or not isinstance(relics[0], dict):
        return None
    if "r" in relics[0] and "n" in relics[0]:
        return copy.deepcopy(relics[0])  # already complete (a server-built setup)
    if not isinstance(parts.get("relicBattle"), dict):
        raise TeamSetupError("圣物数据不完整：请重启游戏加载新版模块后，在准备界面重新保存策略组")
    return relic_setup(relics[0], parts["relicBattle"])


def hero_slot_setup(hero: dict[str, Any], *, slot: int, owner_id: int, area: dict[str, Any] | None,
                    rule: str = BUILDING_RULES[0], power: float | None = None, rounds: int = 1) -> dict[str, Any]:
    """One HeroSlotSetup, keys as the game serializes a battle's hero setups."""
    model, parts = hero["model"], hero["parts"]
    relic = hero_relic(parts)
    neutral = {str(skill["i"]): 0 for skill in model["skills"]}
    setup: dict[str, Any] = {
        "d": 1, "t": slot, "u": owner_id, "i": model["typeId"], "h": model["heroId"], "g": model["grade"],
        "w": model.get("awakened") or 0,
        "l": model["level"], "x": model.get("experience") or 0, "m": 0, "hb": 0, "mb": 0,
        "r": model.get("empower") or 0, "v": 0,
        "z": building_bonus(parts, area, rule),
        "s": copy.deepcopy(model["skills"]),
        "b": copy.deepcopy(parts.get("artifacts") or []),
        "e": copy.deepcopy(parts.get("sets") or []),
        "y": list(model["masteries"]),
        "p": {"h": model["heroId"], "r": [{"p": dict(neutral)} for _ in range(max(1, rounds))]},
        "c": copy.deepcopy(parts.get("academy")),
        "bl": copy.deepcopy(parts.get("blessing")),
    }
    if relic:
        setup["re"] = relic
    if isinstance(power, (int, float)) and not isinstance(power, bool):
        setup["n"] = float(power)
    return {key: value for key, value in setup.items() if value is not None}


def differences(assembled: Any, captured: Any, path: str = "") -> list[str]:
    """Paths where two JSON values differ (object keys missing on either side count)."""
    if isinstance(assembled, dict) and isinstance(captured, dict):
        found: list[str] = []
        for key in sorted(set(assembled) | set(captured)):
            if key not in assembled or key not in captured:
                found.append(f"{path}.{key}" if path else key)
            else:
                found.extend(differences(assembled[key], captured[key], f"{path}.{key}" if path else key))
        return found
    if isinstance(assembled, list) and isinstance(captured, list):
        if len(assembled) != len(captured):
            return [f"{path}[len {len(assembled)}≠{len(captured)}]"]
        found = []
        for index, (left, right) in enumerate(zip(assembled, captured)):
            found.extend(differences(left, right, f"{path}[{index}]"))
        return found
    return [] if assembled == captured else [path or "."]


def _hero_fields(setup: dict[str, Any]) -> dict[str, Any]:
    return {key: setup[key] for key in HERO_FIELDS if key in setup}


def _ordered(fields: dict[str, Any]) -> dict[str, Any]:
    """Artifacts by kind and set bonuses by content: equal gear compares equal in any order."""
    result = dict(fields)
    if isinstance(result.get("b"), list):
        result["b"] = sorted(result["b"], key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(result.get("e"), list):
        result["e"] = sorted(result["e"], key=lambda item: json.dumps(item, sort_keys=True))
    return result


def captured_hero_setups(roots: tuple[Path, ...] = CAPTURE_ROOTS) -> dict[int, tuple[str, dict[str, Any]]]:
    """Newest server-built setup of every hero found in the saved openings."""
    folders: list[Path] = []
    for root in roots:
        try:
            folders.extend(item for item in root.iterdir() if (item / "battle-setup.json").is_file())
        except OSError:
            continue
    found: dict[int, tuple[str, dict[str, Any]]] = {}
    for folder in sorted(folders, key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads((folder / "battle-setup.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        setup = data[0] if isinstance(data, list) and data else data
        for hero in ((setup or {}).get("f") or {}).get("h") or []:
            if isinstance(hero, dict) and _integer(hero.get("h")) and hero["h"] not in found:
                found[hero["h"]] = (folder.name, hero)
    return found


def check_against_captures(heroes: list[dict[str, Any]], observatory: dict[str, Any], boss_mode: str,
                           roots: tuple[Path, ...] = CAPTURE_ROOTS) -> dict[str, Any]:
    """Compare assembled setups with server-built ones of the same heroes; pick the building rule."""
    captured = captured_hero_setups(roots)
    pairs = [(hero, captured[hero["model"]["heroId"]]) for hero in heroes
             if hero.get("model", {}).get("heroId") in captured]
    result: dict[str, Any] = {"schema": 1, "bossMode": boss_mode, "checkedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "heroes": len(heroes), "compared": len(pairs), "rule": None, "area": None,
                              "matched": 0, "orderMatched": 0, "differences": {}}
    if not pairs:
        return result
    best: tuple[int, str, str, dict[str, list[str]], int] | None = None
    for location in AREA_LOCATIONS.get(boss_mode, ("6", "7")):
        area = observatory.get(location) if isinstance(observatory, dict) else None
        for rule in BUILDING_RULES:
            found: dict[str, list[str]] = {}
            in_order = 0
            for hero, (_, server) in pairs:
                try:
                    mine = _hero_fields(hero_slot_setup(hero, slot=server.get("t", 1), owner_id=server.get("u", 0),
                                                        area=area, rule=rule))
                except TeamSetupError as error:
                    found[str(hero["model"]["heroId"])] = [str(error)]
                    continue
                theirs = _hero_fields(server)
                paths = differences(_ordered(mine), _ordered(theirs))
                if paths:
                    found[str(hero["model"]["heroId"])] = paths[:20]
                elif not differences(mine, theirs):
                    in_order += 1
            matched = len(pairs) - len(found)
            if best is None or matched > best[0]:
                best = (matched, rule, location, found, in_order)
    assert best is not None
    result.update(matched=best[0], rule=best[1], area=best[2], differences=best[3], orderMatched=best[4],
                  captures=sorted({capture for _, (capture, _) in pairs}))
    return result


def team_battle_setup(capture_setup: list[dict[str, Any]], heroes: list[dict[str, Any]], *, area: dict[str, Any] | None,
                      rule: str, powers: list[float | None] | None = None) -> list[dict[str, Any]]:
    """A saved opening's BattleSetup with its player team replaced (boss, stage and seed unchanged)."""
    result = copy.deepcopy(capture_setup)
    setup = result[0]
    team = setup.get("f")
    if not isinstance(team, dict) or not _integer(team.get("i")) or not team.get("h"):
        raise TeamSetupError("开局数据没有玩家队伍")
    first = team["h"][0] if isinstance(team["h"][0], dict) else {}
    rounds = len(((first.get("p") or {}).get("r")) or []) or 1
    team["h"] = [hero_slot_setup(hero, slot=index + 1, owner_id=team["i"], area=area, rule=rule,
                                 power=(powers[index] if powers and index < len(powers) else None), rounds=rounds)
                 for index, hero in enumerate(heroes)]
    return result


def prepare_team_input(capture: Path, heroes: list[dict[str, Any]], boss_mode: str, destination: Path, *,
                       area: dict[str, Any] | None, rule: str, powers: list[float | None] | None = None) -> dict[str, Any]:
    """Write a converter input folder with the team swapped in; returns its provenance."""
    size = TEAM_SIZE[boss_mode]
    if not 1 <= len(heroes) <= size:
        raise TeamSetupError(f"队伍需要 1 到 {size} 名英雄，当前为 {len(heroes)} 名")
    missing = [hero["model"].get("typeId") for hero in heroes
               # Academy (`c`) is absent for heroes without academy bonuses, as in the server's setups.
               if any(hero["parts"].get(name) is None for name in ("artifacts", "sets", "building"))]
    if missing:
        raise TeamSetupError("部分英雄缺少装备数据，请在准备界面重新读取队伍")
    original = json.loads((capture / "battle-setup.json").read_text(encoding="utf-8"))
    provenance = json.loads((capture / "capture-provenance.json").read_text(encoding="utf-8"))
    data = json.dumps(team_battle_setup(original, heroes, area=area, rule=rule, powers=powers),
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    destination.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(destination / "battle-setup.json", data)
    atomic_write_bytes(destination / "battle-settings.json", (capture / "battle-settings.json").read_bytes())
    swapped = copy.deepcopy(provenance)
    swapped["teamHeroIds"] = [hero["model"]["heroId"] for hero in heroes]
    swapped["teamHeroTypeIds"] = [hero["model"]["typeId"] for hero in heroes]
    swapped["battleSetups"] = {**(provenance.get("battleSetups") or {}), "file": "battle-setup.json",
                               "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                               "source": "team_setups.team_battle_setup"}
    swapped["teamSwappedFrom"] = capture.name
    atomic_write_json(destination / "capture-provenance.json", swapped)
    return swapped


def preview_team(preview: dict[str, Any] | None, boss_mode: str) -> dict[str, Any] | None:
    """The preparation-screen team with its battle inputs, or None when incomplete."""
    if not isinstance(preview, dict) or preview.get("status") != "captured" or preview.get("bossMode") != boss_mode:
        return None
    heroes = [hero for hero in preview.get("heroes") or [] if isinstance(hero.get("battle"), dict)]
    if not heroes or len(heroes) > TEAM_SIZE[boss_mode] or len(heroes) != len(preview.get("heroes") or []):
        return None
    return {"heroTypeIds": [hero["typeId"] for hero in heroes],
            "heroIds": [hero.get("heroId") for hero in heroes],
            "heroes": [hero["battle"] for hero in heroes],
            "powers": [hero.get("power") for hero in heroes],
            "observatory": preview.get("observatory") or {},
            "capturedAt": preview.get("capturedAt")}


def same_team(left: list[Any] | None, right: list[Any] | None) -> bool:
    values = [value for value in (left or []) if _integer(value) and value > 0]
    others = [value for value in (right or []) if _integer(value) and value > 0]
    return bool(values) and sorted(values) == sorted(others)


class StrategyTeamStore:
    """The team (heroes and gear) recorded when a strategy group was saved.

    Schema 2 also keeps the team's display snapshot (hero screen stats, sets,
    names) so an export carries what the team looked like when it was saved.
    """

    def __init__(self, root: Path = STRATEGY_TEAM_ROOT):
        self.root = root

    def _path(self, boss_mode: str, strategy_id: str) -> Path:
        safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in str(strategy_id))[:80]
        return self.root / f"{boss_mode}-{safe or 'default'}.json"

    def save(self, boss_mode: str, strategy_id: str, team: dict[str, Any],
             display: dict[str, Any] | None = None) -> None:
        """Keep the heroes' own data; account bonuses (academy, buildings, areas) are not stored."""
        heroes = [{"model": copy.deepcopy(hero["model"]),
                   "parts": {name: copy.deepcopy(value) for name, value in hero["parts"].items()
                             if name not in ACCOUNT_PARTS}}
                  for hero in team.get("heroes") or []]
        kept = {key: value for key, value in team.items() if key not in ("heroes", "observatory")}
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path(boss_mode, strategy_id), {
            "schema": 2, "bossMode": boss_mode, "strategyId": strategy_id,
            "savedAt": time.strftime("%Y-%m-%d %H:%M:%S"), **kept, "heroes": heroes,
            **({"display": copy.deepcopy(display)} if display else {})})

    def delete(self, boss_mode: str, strategy_id: str) -> None:
        try:
            self._path(boss_mode, strategy_id).unlink()
        except OSError:
            pass

    def load(self, boss_mode: str, strategy_id: str) -> dict[str, Any] | None:
        try:
            value = json.loads(self._path(boss_mode, strategy_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) and value.get("schema") in (1, 2) else None


def simulation_package(team: dict[str, Any]) -> dict[str, Any]:
    """A saved team's battle data for an export: compressed, without account-specific hero ids."""
    heroes = [{"model": {key: value for key, value in hero["model"].items() if key != "heroId"},
               "parts": {name: value for name, value in hero["parts"].items() if name not in ACCOUNT_PARTS}}
              for hero in team.get("heroes") or []]
    body = json.dumps({"heroTypeIds": [hero["model"].get("typeId") for hero in heroes],
                       "powers": team.get("powers") or [], "heroes": heroes},
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"format": SIMULATION_FORMAT, "schema": 1,
            "data": base64.b64encode(zlib.compress(body, 9)).decode("ascii")}


def unpack_simulation(package: Any) -> dict[str, Any] | None:
    """An exported team's battle data (simulation_package), or None when absent or invalid."""
    if (not isinstance(package, dict) or package.get("format") != SIMULATION_FORMAT
            or package.get("schema") != 1 or not isinstance(package.get("data"), str)
            or len(package["data"]) > MAX_SIMULATION_TEXT):
        return None
    try:
        inflate = zlib.decompressobj()
        text = inflate.decompress(base64.b64decode(package["data"], validate=True), MAX_SIMULATION_JSON)
        if inflate.unconsumed_tail:
            return None
        body = json.loads(text)
    except (binascii.Error, zlib.error, ValueError):
        return None
    heroes = body.get("heroes") if isinstance(body, dict) else None
    if not isinstance(heroes, list) or not 1 <= len(heroes) <= max(TEAM_SIZE.values()):
        return None
    result = []
    for index, hero in enumerate(heroes):
        model = hero.get("model") if isinstance(hero, dict) else None
        parts = hero.get("parts") if isinstance(hero, dict) else None
        if (not isinstance(model, dict) or not isinstance(parts, dict)
                or not all(_integer(model.get(key)) for key in ("typeId", "grade", "level"))
                or not isinstance(model.get("skills"), list) or not isinstance(model.get("masteries"), list)):
            return None
        # Stand-in hero ids: the author's own ids are not exported.
        result.append({"model": {**model, "heroId": index + 1}, "parts": parts})
    powers = body.get("powers")
    return {"heroTypeIds": [hero["model"]["typeId"] for hero in result], "heroes": result,
            "powers": powers if isinstance(powers, list) and len(powers) == len(result) else None}


def exported_team(saved: dict[str, Any]) -> dict[str, Any] | None:
    """The team snapshot an export carries: display data plus the battle data to simulate it."""
    display = saved.get("display")
    if not isinstance(display, dict) or not saved.get("heroes"):
        return None
    return {**copy.deepcopy(display), "savedAt": saved.get("savedAt"), "simulation": simulation_package(saved)}


def decode_account_bonuses(raw: Any, type_ids: list[int]) -> dict[str, Any]:
    """The agent's account bonuses by hero type → {"heroes": {typeId: parts}, "observatory": {...}}."""
    if not isinstance(raw, dict) or raw.get("type") != "account_bonuses":
        raise TeamSetupError("没有读到当前账号的加成")
    if raw.get("status") != "captured":
        raise TeamSetupError(f"没有读到当前账号的加成（{raw.get('reason') or raw.get('status')}）")
    heroes: dict[int, dict[str, Any]] = {}
    missing: list[int] = []
    for item in raw.get("heroes") or []:
        if not isinstance(item, dict) or not _integer(item.get("typeId")):
            continue
        if item.get("missing"):
            missing.append(item["typeId"])
            continue
        parts: dict[str, Any] = {}
        for name in ACCOUNT_PARTS:
            value = (item.get("setup") or {}).get(name)
            try:
                parts[name] = json.loads(value) if isinstance(value, str) and value else None
            except json.JSONDecodeError:
                parts[name] = None
        heroes[item["typeId"]] = parts
    missing.extend(type_id for type_id in type_ids if type_id not in heroes and type_id not in missing)
    if missing:
        raise TeamSetupError(f"游戏里找不到这些英雄的类型数据：{', '.join(map(str, missing))}")
    observatory = {}
    for location, value in (raw.get("observatory") or {}).items():
        try:
            parsed = json.loads(value) if isinstance(value, str) else None
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            observatory[str(location)] = parsed
    return {"heroes": heroes, "observatory": observatory}


def decode_roster(raw: Any) -> list[dict[str, Any]]:
    """The agent's champion list, strongest first.

    Each champion: id, typeId, rarity (1 Common .. 6 Mythical), grade (stars),
    level, empower, power, and where it is:
    ``vault`` = Master Vault (主仓库, Hero.InStorage), ``reserve`` = Reserve
    Vault (储备仓库, Hero.InBathhouse); neither = the champion list (斗士库).
    """
    if not isinstance(raw, dict) or raw.get("type") != "roster":
        raise TeamSetupError("没有读到账号的英雄列表")
    if raw.get("status") != "captured":
        raise TeamSetupError(f"没有读到账号的英雄列表（{raw.get('reason') or raw.get('status')}）")
    heroes = []
    for item in raw.get("heroes") or []:
        if not isinstance(item, dict) or not _integer(item.get("i")) or not _integer(item.get("t")):
            continue
        power = item.get("p")
        heroes.append({"id": item["i"], "typeId": item["t"], "rarity": item.get("r") or 0,
                       "grade": item.get("g") or 0, "level": item.get("l") or 0,
                       "empower": item.get("e") or 0,
                       "power": power if isinstance(power, (int, float)) and not isinstance(power, bool) else None,
                       "vault": bool(item.get("s")), "reserve": bool(item.get("b"))})
    heroes.sort(key=lambda hero: (-(hero["power"] or 0), -hero["grade"], -hero["level"], hero["id"]))
    return heroes


def with_account_bonuses(team: dict[str, Any], bonuses: dict[str, Any]) -> dict[str, Any]:
    """A saved or imported team with the current account's academy, building and area bonuses."""
    heroes = []
    for hero in team.get("heroes") or []:
        live = bonuses["heroes"].get(hero["model"].get("typeId")) or {}
        heroes.append({"model": hero["model"],
                       "parts": {**hero["parts"], **{name: live.get(name) for name in ACCOUNT_PARTS}}})
    return {**team, "heroes": heroes, "observatory": bonuses.get("observatory") or {}}


def team_sources(boss_mode: str, strategy_team: list[Any] | None, *, bound: bool,
                 saved: dict[str, Any] | None, reference: dict[str, Any] | None,
                 check: dict[str, Any] | None) -> dict[str, Any]:
    """What the simulation card shows for each team source (no battle data).

    ``bound``: the strategy's team names this account's heroes (hero ids), so
    their current data can be read; an imported strategy is bound by saving it
    once on the preparation screen with the team selected.
    """
    team = [value for value in (strategy_team or []) if _integer(value) and value > 0]
    author = reference if isinstance(reference, dict) and isinstance(reference.get("simulation"), dict) else None
    return {
        "strategyTeam": team,
        "current": {"heroTypeIds": team, "bound": bool(team) and bound},
        "strategy": ({"heroTypeIds": saved.get("heroTypeIds") or [], "savedAt": saved.get("savedAt"),
                      "matches": same_team(saved.get("heroTypeIds"), team)} if saved else None),
        "author": ({"heroTypeIds": [hero.get("typeId") for hero in author.get("heroes") or []
                                    if isinstance(hero, dict) and _integer(hero.get("typeId"))],
                    "savedAt": author.get("savedAt") or author.get("capturedAt")} if author else None),
        "check": check_summary(check),
    }


def check_summary(check: dict[str, Any] | None) -> dict[str, Any] | None:
    if not check:
        return None
    return {key: check.get(key) for key in ("checkedAt", "compared", "matched", "orderMatched", "rule", "area")}


def simulation_team(source: str, boss_mode: str, strategy_team: list[Any] | None, *,
                    saved: dict[str, Any] | None = None, reference: dict[str, Any] | None = None,
                    check: dict[str, Any] | None = None, current: Any = None,
                    bonuses: Any = None) -> dict[str, Any] | None:
    """The team a simulation puts into the saved opening; None keeps the battle's own team.

    * ``current``: the strategy group's heroes as they are now; a callable
      returning preview_team() of the agent's team data (bonuses included);
    * ``strategy``: the snapshot taken when the strategy group was saved;
    * ``author``: an imported strategy's author's team.

    The last two get the current account's academy, building and area bonuses:
    ``bonuses`` is a callable taking hero type ids and returning
    decode_account_bonuses' result.
    """
    if source == "battle":
        return None
    if source not in TEAM_SOURCES:
        raise TeamSetupError("队伍来源无效")
    team = [value for value in (strategy_team or []) if _integer(value) and value > 0]
    if source == "author":
        chosen = unpack_simulation((reference or {}).get("simulation"))
        if chosen is None:
            raise TeamSetupError("这个策略组没有作者队伍的数据（1.1.1 起导出的策略才带有）")
        if bonuses is None:
            raise TeamSetupError("需要打开游戏读取当前账号的学院、建筑和区域加成")
        chosen = with_account_bonuses(chosen, bonuses(chosen["heroTypeIds"]))
        chosen["savedAt"] = (reference or {}).get("savedAt") or (reference or {}).get("capturedAt")
        chosen["display"] = reference
    elif not team:
        raise TeamSetupError("当前策略组还没有设定队伍：在“策略组队伍”卡片上选择英雄")
    elif source == "strategy":
        if saved is None:
            raise TeamSetupError("这个策略组还没有保存时的队伍：打开游戏后保存一次策略组就会记下")
        if not same_team(saved.get("heroTypeIds"), team):
            raise TeamSetupError("策略组的队伍已更改：打开游戏后重新保存一次策略组")
        if bonuses is None:
            raise TeamSetupError("需要打开游戏读取当前账号的学院、建筑和区域加成")
        chosen = with_account_bonuses(saved, bonuses([hero["model"]["typeId"] for hero in saved.get("heroes") or []]))
    else:
        if current is None:
            raise TeamSetupError("需要打开游戏读取策略组英雄现在的装备")
        chosen = current()
        if chosen is None or not same_team(chosen.get("heroTypeIds"), team):
            raise TeamSetupError("游戏里读到的英雄和策略组的队伍不一致")
    location = str((check or {}).get("area") or AREA_LOCATIONS[boss_mode][0])
    return {"source": source, "heroes": chosen["heroes"], "powers": chosen.get("powers"),
            "area": (chosen.get("observatory") or {}).get(location),
            "rule": (check or {}).get("rule") or BUILDING_RULES[0],
            "savedAt": chosen.get("savedAt") or chosen.get("capturedAt"),
            "display": chosen.get("display"),
            "check": check_summary(check)}


def team_report(battle_setup: Path, stats: dict[str, list[float | None]], *, source: str,
                display: dict[str, Any] | None = None, saved_at: str | None = None) -> dict[str, Any] | None:
    """The team a simulation ran with, in the team view's format (TeamPreviewDialog).

    Gear, sets, blessing, relic, masteries and skill levels come from the battle
    set-up that was simulated; the stats are each hero's battle stats when the
    battle started (the engine's own numbers, every bonus included:
    simulation_common.opening_stats of run 1). Names and icons come from the
    team's display snapshot when there is one.
    """
    try:
        data = json.loads(battle_setup.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    setup = data[0] if isinstance(data, list) and data else data
    team = setup.get("f") if isinstance(setup, dict) and isinstance(setup.get("f"), dict) else {}
    heroes = []
    for hero in team.get("h") or []:
        if not isinstance(hero, dict) or not _integer(hero.get("i")):
            continue
        artifacts = [item for item in hero.get("b") or [] if isinstance(item, dict)]
        sets: dict[int, int] = {}
        for item in artifacts:
            if _integer(item.get("s")) and item["s"] > 0:  # 0: a piece without a set
                sets[item["s"]] = sets.get(item["s"], 0) + 1
        entry: dict[str, Any] = {
            "typeId": hero["i"], "level": hero.get("l") or 0, "grade": hero.get("g") or 0,
            "empower": hero.get("r") or 0, "awakened": hero.get("w") or 0,
            "skills": [{"typeId": skill["i"], "level": skill.get("l")} for skill in hero.get("s") or []
                       if isinstance(skill, dict) and _integer(skill.get("i"))],
            "masteries": [value for value in hero.get("y") or [] if _integer(value)],
            "sets": [{"set": set_id, "pieces": count}
                     for set_id, count in sorted(sets.items(), key=lambda pair: (-pair[1], pair[0]))],
            "equipped": len(artifacts)}
        if isinstance(hero.get("n"), (int, float)) and not isinstance(hero.get("n"), bool):
            entry["power"] = round(hero["n"])
        blessing = hero.get("bl").get("i") if isinstance(hero.get("bl"), dict) else None
        if _integer(blessing) and blessing > 0:
            entry["blessing"] = blessing
        relic = hero["re"].get("n") if isinstance(hero.get("re"), dict) else None
        if isinstance(relic, dict) and _integer(relic.get("t")):
            entry["relic"] = {"typeId": relic["t"], "rank": relic.get("a") or 0, "level": relic.get("l") or 0}
        values = stats.get(str(hero["i"])) if isinstance(stats, dict) else None
        if values and any(value is not None for value in values):
            entry["stats"] = {"total": [value if value is not None else 0.0 for value in values]}
        else:
            entry["statsReason"] = "opening_stats_unavailable"
        heroes.append(entry)
    shown = display if isinstance(display, dict) else {}
    return {"schema": 2, "source": source, "savedAt": saved_at, "heroes": heroes,
            "names": shown.get("names") if isinstance(shown.get("names"), dict) else {},
            "icons": shown.get("icons") if isinstance(shown.get("icons"), dict) else {}}


def latest_check(boss_mode: str, root: Path = CHECK_ROOT) -> dict[str, Any] | None:
    try:
        value = json.loads((root / f"{boss_mode}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def remember_check(result: dict[str, Any], root: Path = CHECK_ROOT) -> None:
    if not result.get("compared"):
        return
    try:
        root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(root / f"{result['bossMode']}.json", result)
    except OSError:
        pass
