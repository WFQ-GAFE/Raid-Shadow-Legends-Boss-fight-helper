"""Boss stages from the game's static data, for simulating a difficulty never fought.

A battle's boss side (BattleSetup `s`) is built by the server from the stage's
static definition: each unit's hero type, slot, grade, level and stat
modifiers (the stage's waves, `mo`) and the hero type's skills (its forms'
skill lists, all at level 1). The same values are in the static data the
offline engine loads (hydra_forecast_live.newest_static_data), so a saved
opening becomes the same rotation's other difficulty: its stage id,
difficulty and boss side are replaced; the team, settings and seed stay.
Checked against the server's set-ups of three Hydra difficulties.

The alliance Chimera adds, per difficulty (static config `ct`): the setup's
`c` (difficulty), `o` and `a` (the config's o and a as Q32.32) and the stage
modifiers `s.m`; and its HP carries over between battles: the boss unit's
`f.c.h` is minus the HP already lost (whole HP, Q32.32), 0 at full health.
The strategy's trial ids name the difficulty (8000<difficulty><slot>), so a
strategy runs on another difficulty with the same slots' trials.

The static data is a MessagePack-CSharp Lz4BlockArray (an array: ext 98 with
each block's uncompressed length, then one LZ4 block per bin). Only the boss
stages, their hero types and the Chimera configs are read, found by their
byte patterns, and kept in cache/boss-stages/<file>.json (~70 KB).
"""
from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import struct
import threading
from typing import Any

from strategy_storage import atomic_write_bytes, atomic_write_json
from ui_text import ui_text


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
STAGE_ROOT = PROJECT_ROOT / "cache" / "boss-stages"
SCHEMA = 2  # 2: the Chimera's difficulty configs
# StageData.s of each boss's stages, and the battle setup's KindId (`k`).
STAGE_NAMES = {"hydra": "Dungeon_Hydra", "chimera": "Dungeon_Chimera"}
BOSS_KIND = {"hydra": 5, "chimera": 8}
DIFFICULTIES = {"hydra": (1, 2, 3, 4), "chimera": (1, 2, 3, 4, 5, 6)}
CHIMERA_DIFFICULTY_NAMES = {1: "Easy", 2: "Normal", 3: "Hard", 4: "Brutal", 5: "Nightmare", 6: "UltraNightmare"}
FIXED_ONE = 1 << 32
TRIAL_BASE = 8_000_000
TRIAL_SLOTS = 27


class StageSetupError(ValueError):
    pass


def difficulty_of(stage_id: int) -> int:
    # Hydra 80<rotation>900<difficulty>, Chimera 130<rotation>900<difficulty>.
    return stage_id % 10


def stage_with_difficulty(stage_id: int, difficulty: int) -> int:
    """The same rotation's stage on another difficulty."""
    return stage_id - stage_id % 10 + difficulty


def fixed(value: float) -> int:
    """A config number as a battle setup writes it (Q32.32, truncated: 0.8 -> 3435973836)."""
    return int(Decimal(repr(round(float(value), 6))) * FIXED_ONE)


def trial_with_difficulty(trial_id: int, difficulty: int) -> int:
    """The same slot's Chimera trial on another difficulty (8000<difficulty><slot>)."""
    level, slot = divmod(trial_id - TRIAL_BASE, 100)
    return TRIAL_BASE + difficulty * 100 + slot if 1 <= level <= 6 and 1 <= slot <= TRIAL_SLOTS else trial_id


def strategy_on_difficulty(strategy: Any, difficulty: int, _trials: bool = False) -> Any:
    """A Chimera strategy with its trial ids moved to the same slots' trials on the difficulty:
    every id under a field named for trials (mandatoryTrialIds, the rules' ...TrialsAny
    conditions, the regroup conditions' trialIds, ...); other numbers stay."""
    if isinstance(strategy, dict):
        return {key: strategy_on_difficulty(value, difficulty, _trials or "trial" in str(key).lower())
                for key, value in strategy.items()}
    if isinstance(strategy, list):
        return [strategy_on_difficulty(value, difficulty, _trials) for value in strategy]
    if _trials and isinstance(strategy, int) and not isinstance(strategy, bool):
        return trial_with_difficulty(strategy, difficulty)
    return strategy


def chimera_health_lost(setup: list[dict[str, Any]]) -> int:
    """The HP the Chimera had already lost when the saved battle started (0 at full health)."""
    try:
        raw = setup[0]["s"]["h"][0]["f"]["c"]["h"]
    except (IndexError, KeyError, TypeError):
        return 0
    return max(0, -int(raw) // FIXED_ONE) if isinstance(raw, int) else 0


def chimera_trial_catalog(opening: dict[str, Any], difficulty: int, config: dict[str, Any]) -> dict[str, Any]:
    """An opening's trial catalog entry on another difficulty: the same slots' trials, renumbered."""
    entry = copy.deepcopy(opening)
    entry.update(difficultyId=difficulty, difficulty=CHIMERA_DIFFICULTY_NAMES.get(difficulty),
                 health=config.get("health"), stageIds=list(config.get("stageIds") or []))
    for trial in entry.get("trials") or []:
        if isinstance(trial, dict) and isinstance(trial.get("id"), int):
            trial["id"] = trial_with_difficulty(trial["id"], difficulty)
            if isinstance(trial.get("skillTypeId"), int):
                trial["skillTypeId"] = trial_with_difficulty(trial["skillTypeId"], difficulty)
            trial.pop("reward", None)  # rewards differ per difficulty; they only show in the editor
    return entry


# --- Reading the static data ---

def _read_int(data: bytes, position: int) -> tuple[int, int]:
    head = data[position]
    if head <= 0x7F:
        return head, position + 1
    size = {0xCC: 1, 0xCD: 2, 0xCE: 4, 0xCF: 8, 0xD0: 1, 0xD1: 2, 0xD2: 4, 0xD3: 8}[head]
    return int.from_bytes(data[position + 1:position + 1 + size], "big", signed=head >= 0xD0), position + 1 + size


def _lz4_block(source: bytes, size: int) -> bytes:
    out = bytearray()
    index = 0
    while index < len(source):
        token = source[index]
        index += 1
        literal = token >> 4
        if literal == 15:
            while True:
                extra = source[index]
                index += 1
                literal += extra
                if extra != 255:
                    break
        out += source[index:index + literal]
        index += literal
        if index >= len(source):
            break
        offset = source[index] | (source[index + 1] << 8)
        index += 2
        match = token & 15
        if match == 15:
            while True:
                extra = source[index]
                index += 1
                match += extra
                if extra != 255:
                    break
        match += 4
        start = len(out) - offset
        if offset >= match:
            out += out[start:start + match]
        else:  # an overlapping copy repeats the last `offset` bytes
            pattern = bytes(out[start:])
            out += (pattern * (match // offset + 1))[:match]
    if len(out) != size:
        raise StageSetupError("static data block has an unexpected length")
    return bytes(out)


def decompress(raw: bytes) -> bytes:
    if len(raw) < 8 or raw[0] != 0xDC or raw[3] != 0xC8 or raw[6] != 98:
        raise StageSetupError("static data is not an LZ4 block array")
    ext_size = int.from_bytes(raw[4:6], "big")
    payload = raw[7:7 + ext_size]
    position = 7 + ext_size
    lengths, cursor = [], 0
    while cursor < len(payload):
        value, cursor = _read_int(payload, cursor)
        lengths.append(value)
    blocks = []
    for length in lengths:
        size_bytes = {0xC4: 1, 0xC5: 2, 0xC6: 4}[raw[position]]
        size = int.from_bytes(raw[position + 1:position + 1 + size_bytes], "big")
        start = position + 1 + size_bytes
        blocks.append(_lz4_block(raw[start:start + size], length))
        position = start + size
    return b"".join(blocks)


def _value(data: bytes, position: int) -> tuple[Any, int]:
    """One msgpack value: maps become dicts, ext values bytes."""
    head = data[position]
    position += 1
    if head <= 0x7F:
        return head, position
    if head >= 0xE0:
        return head - 0x100, position
    if 0x80 <= head <= 0x8F or head in (0xDE, 0xDF):
        count = head & 0x0F
        if head == 0xDE:
            count, position = int.from_bytes(data[position:position + 2], "big"), position + 2
        elif head == 0xDF:
            count, position = int.from_bytes(data[position:position + 4], "big"), position + 4
        result = {}
        for _ in range(count):
            key, position = _value(data, position)
            item, position = _value(data, position)
            result[key if isinstance(key, (str, int)) else repr(key)] = item
        return result, position
    if 0x90 <= head <= 0x9F or head in (0xDC, 0xDD):
        count = head & 0x0F
        if head == 0xDC:
            count, position = int.from_bytes(data[position:position + 2], "big"), position + 2
        elif head == 0xDD:
            count, position = int.from_bytes(data[position:position + 4], "big"), position + 4
        items = []
        for _ in range(count):
            item, position = _value(data, position)
            items.append(item)
        return items, position
    if 0xA0 <= head <= 0xBF or head in (0xD9, 0xDA, 0xDB):
        if head <= 0xBF:
            size = head & 0x1F
        else:
            width = {0xD9: 1, 0xDA: 2, 0xDB: 4}[head]
            size, position = int.from_bytes(data[position:position + width], "big"), position + width
        return data[position:position + size].decode("utf-8", "replace"), position + size
    if head == 0xC0:
        return None, position
    if head in (0xC2, 0xC3):
        return head == 0xC3, position
    if head == 0xCA:
        return struct.unpack(">f", data[position:position + 4])[0], position + 4
    if head == 0xCB:
        return struct.unpack(">d", data[position:position + 8])[0], position + 8
    if 0xCC <= head <= 0xD3:
        return _read_int(data, position - 1)
    if head in (0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9):
        width = {0xC4: 1, 0xC5: 2, 0xC6: 4, 0xC7: 1, 0xC8: 2, 0xC9: 4}[head]
        size = int.from_bytes(data[position:position + width], "big")
        position += width + (1 if head >= 0xC7 else 0)
        return data[position:position + size], position + size
    if 0xD4 <= head <= 0xD8:
        size = {0xD4: 1, 0xD5: 2, 0xD6: 4, 0xD7: 8, 0xD8: 16}[head]
        return data[position + 1:position + 1 + size], position + 1 + size
    raise StageSetupError(f"unknown msgpack value {head:#x}")


def _maps_at(data: bytes, key: int) -> list[dict[str, Any]]:
    """The maps that may start with the key at `key` (a map's header is right before its first key)."""
    maps = []
    for start, test in ((key - 1, lambda head: 0x80 <= head <= 0x8F), (key - 3, lambda head: head == 0xDE)):
        if start >= 0 and test(data[start]):
            try:
                value, _ = _value(data, start)
            except (StageSetupError, IndexError, KeyError, UnicodeError, struct.error):
                continue
            if isinstance(value, dict):
                maps.append(value)
    return maps


def _int_bytes(value: int) -> list[bytes]:
    """The ways MessagePack may write a non-negative int."""
    forms = []
    if value <= 0x7F:
        forms.append(bytes([value]))
    if value <= 0xFF:
        forms.append(b"\xcc" + value.to_bytes(1, "big"))
    if value <= 0xFFFF:
        forms.append(b"\xcd" + value.to_bytes(2, "big"))
    forms.append(b"\xce" + value.to_bytes(4, "big"))
    if value <= 0x7FFFFFFF:
        forms.append(b"\xd2" + value.to_bytes(4, "big"))
    return forms


def _set(value: Any) -> Any:
    """A static value as a battle setup writes it: unset (nil) fields left out."""
    if isinstance(value, dict):
        return {key: _set(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_set(item) for item in value]
    return value


def _as_server(value: Any) -> Any:
    """A static value written the way the server's battle-setup JSON writes it:
    booleans as 0/1, whole numbers without a fraction (8.0 -> 8), floats at the
    config's precision (a float32 0.2 -> 0.2)."""
    if isinstance(value, dict):
        return {key: _as_server(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_as_server(item) for item in value]
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        rounded = round(value, 6)
        return int(rounded) if rounded.is_integer() else float(repr(rounded))
    return value


def _unit(raw: dict[str, Any]) -> dict[str, Any]:
    return _set({key: raw.get(key) for key in ("d", "t", "i", "g", "l", "m", "hb", "mb", "mo")})


def extract(data: bytes) -> dict[str, Any]:
    """The Hydra and Chimera stages, and the skills of their hero types, from decompressed static data."""
    stages: dict[str, dict[str, Any]] = {}
    for boss, name in STAGE_NAMES.items():
        marker = b"\xa1s" + bytes([0xA0 | len(name)]) + name.encode("ascii")
        found = data.find(marker)
        while found >= 0:
            # The stage map starts with its id: {i: <id>, s: <name>, ...}.
            key = data.rfind(b"\xa1i", max(0, found - 8), found)
            for stage in _maps_at(data, key) if key >= 0 else []:
                if stage.get("s") == name and isinstance(stage.get("i"), int) and isinstance(stage.get("e"), list):
                    units = [_unit(unit) | {"reserve": kind == "p"} for wave in stage["e"] if isinstance(wave, dict)
                             for kind in ("h", "p") for unit in wave.get(kind) or []]
                    stages[str(stage["i"])] = {"stageId": stage["i"], "boss": boss,
                                               "difficulty": difficulty_of(stage["i"]),
                                               "units": units, "modifiers": _set(stage.get("m"))}
                    break
            found = data.find(marker, found + 1)
    skills: dict[str, list[int]] = {}
    for type_id in sorted({unit["i"] for stage in stages.values() for unit in stage["units"]}):
        for form in _int_bytes(type_id):
            found = data.find(b"\xa1i" + form + b"\xa2hn")
            hero = next((item for item in (_maps_at(data, found) if found >= 0 else [])
                         if item.get("i") == type_id and isinstance(item.get("hf"), list)), None)
            if hero is not None:
                skills[str(type_id)] = [skill for shape in hero["hf"] if isinstance(shape, dict)
                                        for skill in shape.get("s") or [] if isinstance(skill, int)]
                break
    # The alliance Chimera's difficulties: {ct: [{i, h, s, ..., o, a}, ...]}.
    chimera: dict[str, dict[str, Any]] = {}
    found = data.find(b"\xa2ct")
    while found >= 0 and not chimera:
        try:
            value, _ = _value(data, found + 3)
        except (StageSetupError, IndexError, KeyError, UnicodeError, struct.error):
            value = None
        if isinstance(value, list) and value and all(
                isinstance(item, dict) and isinstance(item.get("i"), int) and isinstance(item.get("o"), (int, float))
                and isinstance(item.get("a"), (int, float)) for item in value):
            chimera = {str(item["i"]): {"difficulty": item["i"], "health": item.get("h"),
                                        "stageIds": item.get("s") or [], "o": item["o"], "a": item["a"]}
                       for item in value}
        found = data.find(b"\xa2ct", found + 1)
    return {"schema": SCHEMA, "stages": stages, "skills": skills, "chimera": chimera}


_LOCK = threading.Lock()
# The last result kept in memory too: the app asks for the Chimera's HP on every refresh.
_MEMORY: dict[Path, dict[str, Any]] = {}


def load_stages(static_data: Path, root: Path | None = None) -> dict[str, Any]:
    """The boss stages of this static data file, read once and cached."""
    base = root or STAGE_ROOT
    stat = static_data.stat()
    key = hashlib.sha256(f"{static_data.parent.name}/{static_data.name}/{stat.st_size}".encode()).hexdigest()[:24]
    path = base / f"{key}.json"
    with _LOCK:
        if path in _MEMORY:
            return _MEMORY[path]
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and cached.get("schema") == SCHEMA and cached.get("stages"):
                _MEMORY.clear()
                _MEMORY[path] = cached
                return cached
        except (OSError, ValueError):
            pass
        result = extract(decompress(static_data.read_bytes()))
        if not result["stages"]:
            raise StageSetupError(ui_text("stages.notFound"))
        result["source"] = {"file": static_data.name, "version": static_data.parent.name, "bytes": stat.st_size}
        base.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, result)
        _MEMORY.clear()
        _MEMORY[path] = result
        for old in base.glob("*.json"):
            if old != path:
                try:
                    old.unlink()
                except OSError:
                    pass
        return result


# --- Building a battle setup for another stage ---

def stage_battle_setup(capture_setup: list[dict[str, Any]], stage: dict[str, Any], stages: dict[str, Any],
                       health_lost: int = 0) -> list[dict[str, Any]]:
    """A saved opening's BattleSetup on another stage: boss side, stage id and difficulty replaced.

    ``stages``: load_stages' result (hero type skills, Chimera configs); ``health_lost``: the
    HP the Chimera has already lost (its HP carries over between the alliance's battles)."""
    result = copy.deepcopy(capture_setup)
    setup = result[0]
    side = setup.get("s")
    boss = stage["boss"]
    skills = stages.get("skills") or {}
    if setup.get("k") != BOSS_KIND[boss] or not isinstance(side, dict) or not side.get("h"):
        raise StageSetupError(ui_text("stages.otherBoss"))
    active = [unit for unit in stage["units"] if not unit["reserve"]]
    reserve = [unit for unit in stage["units"] if unit["reserve"]]
    config = (stages.get("chimera") or {}).get(str(stage["difficulty"]))
    if not active or (boss == "chimera" and not isinstance(config, dict)):
        raise StageSetupError(ui_text("stages.notFound"))
    # Fields the stage does not set (ids, experience, rank, ...) keep the server's values.
    shapes = {False: side["h"][0], True: (side.get("o") or side["h"])[0]}

    def unit(static: dict[str, Any]) -> dict[str, Any]:
        type_id = static["i"]
        if str(type_id) not in skills:
            raise StageSetupError(ui_text("stages.unknownBoss", typeId=type_id))
        built = copy.deepcopy(shapes[static["reserve"]])
        for key in ("d", "t", "i", "g", "l"):
            built[key] = static[key]
        for key in ("m", "hb", "mb"):
            built[key] = int(bool(static.get(key)))
        if boss == "hydra":
            built["f"] = _as_server(static.get("mo") or {})
        else:
            built["f"] = {"c": {"h": -max(0, int(health_lost)) * FIXED_ONE}}
        built["s"] = [{"i": skill, "l": 1} for skill in skills[str(type_id)]]
        return built

    setup["i"] = stage["stageId"]
    if boss == "hydra":
        setup["h"] = stage["difficulty"] - 1
        side["h"] = [unit(item) for item in active]
        side["o"] = [unit(item) for item in reserve]
    else:
        setup["c"] = stage["difficulty"]
        setup["o"], setup["a"] = fixed(config["o"]), fixed(config["a"])
        side["h"] = [unit(item) for item in active]
        side["m"] = _as_server(stage.get("modifiers") or [])
    return result


def prepare_stage_input(source: Path, stage: dict[str, Any], stages: dict[str, Any], destination: Path, *,
                        health_lost: int = 0) -> dict[str, Any]:
    """Write a converter input folder for another stage of the source's opening; returns its provenance."""
    original = json.loads((source / "battle-setup.json").read_text(encoding="utf-8"))
    provenance = json.loads((source / "capture-provenance.json").read_text(encoding="utf-8"))
    built_setup = stage_battle_setup(original, stage, stages, health_lost)
    data = json.dumps(built_setup, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    destination.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(destination / "battle-setup.json", data)
    atomic_write_bytes(destination / "battle-settings.json", (source / "battle-settings.json").read_bytes())
    built = copy.deepcopy(provenance)
    built["stageId"] = stage["stageId"]
    if "bossHeroTypeId" in provenance:  # the Chimera's type and level differ per difficulty
        boss = built_setup[0]["s"]["h"][0]
        built.update(bossHeroTypeId=boss["i"], bossLevel=boss["l"])
    built["battleSetups"] = {**(provenance.get("battleSetups") or {}), "file": "battle-setup.json",
                             "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                             "source": "boss_stages.stage_battle_setup"}
    built["stageBuiltFrom"] = provenance.get("stageId")
    atomic_write_json(destination / "capture-provenance.json", built)
    return built
