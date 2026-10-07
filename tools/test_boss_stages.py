"""Simulating a boss difficulty never fought: stages from the game's static data (boss_stages)."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import struct
import tempfile
import time

import boss_stages
from boss_stages import StageSetupError, decompress, extract, load_stages, prepare_stage_input, stage_battle_setup


ONE = 1 << 32


def pack(value) -> bytes:
    """A small MessagePack writer (maps of 16 keys and more as map16, like the game's stages)."""
    if value is None:
        return b"\xc0"
    if isinstance(value, bool):
        return b"\xc3" if value else b"\xc2"
    if isinstance(value, int):
        if 0 <= value <= 0x7F:
            return bytes([value])
        if 0 <= value <= 0xFFFF:
            return b"\xcd" + value.to_bytes(2, "big")
        if 0 <= value <= 0xFFFFFFFF:
            return b"\xce" + value.to_bytes(4, "big")
        return b"\xd3" + value.to_bytes(8, "big", signed=True)
    if isinstance(value, float):
        return b"\xcb" + struct.pack(">d", value)
    if isinstance(value, str):
        data = value.encode("utf-8")
        return (bytes([0xA0 | len(data)]) if len(data) < 32 else b"\xd9" + bytes([len(data)])) + data
    if isinstance(value, list):
        return bytes([0x90 | len(value)]) + b"".join(pack(item) for item in value)
    if isinstance(value, dict):
        head = bytes([0x80 | len(value)]) if len(value) < 16 else b"\xde" + len(value).to_bytes(2, "big")
        return head + b"".join(pack(key) + pack(item) for key, item in value.items())
    raise TypeError(value)


def lz4_literals(data: bytes) -> bytes:
    """One LZ4 block of literals only."""
    if len(data) < 15:
        return bytes([len(data) << 4]) + data
    rest, extra = len(data) - 15, b""
    while rest >= 255:
        extra += b"\xff"
        rest -= 255
    return b"\xf0" + extra + bytes([rest]) + data


def block_array(blocks: list[tuple[bytes, int]]) -> bytes:
    """A MessagePack-CSharp Lz4BlockArray of (compressed block, uncompressed length)."""
    lengths = b"".join(pack(length) for _, length in blocks)
    out = b"\xdc" + (len(blocks) + 1).to_bytes(2, "big") + b"\xc8" + len(lengths).to_bytes(2, "big") + b"\x62" + lengths
    for block, _ in blocks:
        out += b"\xc5" + len(block).to_bytes(2, "big") + block
    return out


def modifiers(health: int, speed: int) -> dict:
    stats = lambda **values: {key: values.get(key, 0) for key in "hadsrtumci"}  # noqa: E731
    return {"p": stats(h=health), "f": stats(s=speed), "c": None}  # the game leaves "current" unset


def static_unit(type_id: int, slot: int, level: int, health: int, speed: int) -> dict:
    return {"d": 1 if slot > 0 else -1, "t": slot, "i": type_id, "g": 6, "l": level, "m": False, "hb": False,
            "mb": False, "mo": modifiers(health, speed)}


CHIMERA_MODIFIERS = [{"r": 1, "k": 1, "v": 7.0, "a": False, "b": True}, {"r": 1, "k": 3, "v": 0.15, "a": False, "b": True}]
# As the server writes them in a battle setup.
SERVER_MODIFIERS = [{"r": 1, "k": 1, "v": 7, "a": 0, "b": 1}, {"r": 1, "k": 3, "v": 0.15, "a": 0, "b": 1}]


def static_data() -> bytes:
    """Static data holding Hydra stage 8039004 (two active heads, one in reserve), the Chimera's
    Nightmare stage 13049005, their hero types and the Chimera's difficulty configs."""
    stage = {"i": 8039004, "s": "Dungeon_Hydra", "ir": None, "h": 6,
             "e": [{"i": 1, "h": [static_unit(26070, 1, 310, 4 * ONE, 50 * ONE), static_unit(26140, 2, 310, 3 * ONE, 70 * ONE)],
                    "p": [static_unit(26090, -1, 310, 2 * ONE, 60 * ONE)]}],
             "v": None, "m": None, "o": 1, "p": None, "r": [], "t": None, "f": None, "w": {}, "bq": None, "sc": None,
             "q": None, "g": None}
    def chimera(stage_id, type_id, level, modifiers):
        return {"i": stage_id, "s": "Dungeon_Chimera", "ir": None, "h": 5,
                "e": [{"i": 1, "h": [{"d": 1, "t": 1, "i": type_id, "g": 6, "l": level, "m": False, "hb": False, "mb": False}],
                       "p": None}],
                "v": None, "m": modifiers, "o": 1, "p": None, "r": [], "t": None, "f": None, "w": {}, "bq": None,
                "sc": None, "q": None, "g": None}
    heroes = [{"i": type_id, "hn": {"k": f"l10n:hero-type/name?id={type_id}#static"}, "v": 0,
               "hf": [{"e": 3, "s": skills} for skills in forms]}
              for type_id, forms in ((26070, [[260401, 260402, 260007]]), (26140, [[261201, 260007]]), (26090, [[260801]]),
                                     (26886, [[1268801, 1268802], [2268801]]), (26926, [[1266901]]))]
    configs = [{"i": level, "h": health, "s": [13019000 + level, 13049000 + level], "r": {}, "c": [], "l": [],
                "cc": [], "o": o, "a": a} for level, health, o, a in ((5, 1195000000, 8.0, 0.6), (6, 1610000000, 11.0, 0.8))]
    stages = [stage, chimera(13049005, 26886, 280, CHIMERA_MODIFIERS),
              chimera(13049006, 26926, 300, [{"r": 1, "k": 1, "v": 8.0, "a": False, "b": True}]),
              {"i": 8039099, "s": "Dungeon_Other"}]
    return pack({"x": [1, 2, 3], "stages": stages, "heroes": heroes, "j.": {"ht": [], "ct": configs}})


def server_unit(type_id: int, slot: int, level: int, health: int, speed: int, skills: list[int]) -> dict:
    return {"d": 1 if slot > 0 else -1, "t": slot, "u": -1, "i": type_id, "h": 0, "g": 6, "l": level, "x": 0,
            "m": 0, "hb": 0, "mb": 0, "r": 0, "v": 0,
            "f": {key: value for key, value in modifiers(health, speed).items() if value is not None},
            "s": [{"i": skill, "l": 1} for skill in skills]}


def opening() -> list[dict]:
    """A Brutal (8039003) opening as the server sends it."""
    return [{"z": "battle-guid", "r": 1611440968, "k": 5,
             "f": {"i": 95815853, "h": [{"i": 39104, "t": 8896}]},
             "s": {"p": 0, "i": -1, "l": 0, "z": 0, "ls": 0,
                   "h": [server_unit(26070, 1, 295, 3 * ONE, 40 * ONE, [260401, 260402, 260007]),
                         server_unit(26140, 2, 295, 2 * ONE, 60 * ONE, [261201, 260007])],
                   "o": [server_unit(26090, -1, 295, ONE, 50 * ONE, [260801])]},
             "i": 8039003, "d": 0, "h": 2, "pi": 704, "ip": 0, "q": 0, "bm": 1, "qb": 0, "mt": 1000, "bb": 0, "fb": 0}]


def test_static_data_blocks_decompress_including_overlapping_copies() -> None:
    # "abc", then 9 bytes copied from 3 back: the copy overlaps what it writes.
    repeated = bytes([(3 << 4) | 5]) + b"abc" + b"\x03\x00"
    raw = block_array([(repeated, 12), (lz4_literals(b"x" * 300), 300)])
    assert decompress(raw) == b"abc" * 4 + b"x" * 300
    try:
        decompress(b"\x92\x01\x02")
    except StageSetupError:
        pass
    else:
        raise AssertionError("not an LZ4 block array")


def test_stages_and_boss_skills_are_read_from_static_data() -> None:
    result = extract(static_data())
    assert sorted(result["stages"]) == ["13049005", "13049006", "8039004"]  # only the boss dungeons' stages
    stage = result["stages"]["8039004"]
    assert stage["boss"] == "hydra" and stage["difficulty"] == 4 and stage["modifiers"] is None
    assert [(unit["i"], unit["t"], unit["reserve"]) for unit in stage["units"]] == [
        (26070, 1, False), (26140, 2, False), (26090, -1, True)]
    head = stage["units"][0]
    assert head["l"] == 310 and head["mo"]["p"]["h"] == 4 * ONE and "c" not in head["mo"]  # unset values left out
    assert result["skills"] == {"26070": [260401, 260402, 260007], "26090": [260801], "26140": [261201, 260007],
                                "26886": [1268801, 1268802, 2268801], "26926": [1266901]}  # every form's skills, in order
    chimera = result["stages"]["13049005"]
    assert chimera["boss"] == "chimera" and chimera["difficulty"] == 5 and chimera["modifiers"] == CHIMERA_MODIFIERS
    assert result["chimera"]["5"] == {"difficulty": 5, "health": 1195000000, "stageIds": [13019005, 13049005],
                                      "o": 8.0, "a": 0.6}


def test_another_difficulty_replaces_only_the_boss_side_and_stage() -> None:
    data = extract(static_data())
    stage = data["stages"]["8039004"]
    built = stage_battle_setup(opening(), stage, data)
    expected = opening()
    expected[0].update(i=8039004, h=3)
    expected[0]["s"]["h"] = [server_unit(26070, 1, 310, 4 * ONE, 50 * ONE, [260401, 260402, 260007]),
                             server_unit(26140, 2, 310, 3 * ONE, 70 * ONE, [261201, 260007])]
    expected[0]["s"]["o"] = [server_unit(26090, -1, 310, 2 * ONE, 60 * ONE, [260801])]
    assert built == expected  # team, seed, battle id and the other fields stay
    for wrong, stages in ((dict(copy.deepcopy(stage), boss="chimera"), data), (stage, {"skills": {}})):
        try:
            stage_battle_setup(opening(), wrong, stages)
        except StageSetupError:
            continue
        raise AssertionError("built from the wrong boss or without the boss's skills")


def chimera_opening(lost: int = 56707308) -> list[dict]:
    """An Ultra-Nightmare (13049006) opening as the server sends it, the Chimera already hurt."""
    unit = {"d": 1, "t": 1, "u": -1, "i": 26926, "h": 0, "g": 6, "l": 300, "x": 0, "m": 0, "hb": 0, "mb": 0,
            "r": 0, "v": 0, "f": {"c": {"h": -lost * ONE}}, "s": [{"i": 1266901, "l": 1}]}
    return [{"z": "guid", "r": 366473740, "k": 8, "f": {"i": 95815853, "h": [{"i": 29968, "t": 7376}]},
             "s": {"p": 0, "i": -1, "l": 0, "h": [unit], "m": [{"r": 1, "k": 1, "v": 8.0, "a": 0, "b": 1}], "z": 0, "ls": 0},
             "i": 13049006, "d": 0, "c": 6, "pi": 806, "ip": 0, "q": 0, "bm": 1, "qb": 0, "bt": 65,
             "o": 11 * ONE, "a": 3435973836, "bb": 0, "fb": 0}]


def test_the_chimera_takes_its_difficulty_config_and_carried_hp() -> None:
    data = extract(static_data())
    assert boss_stages.chimera_health_lost(chimera_opening()) == 56707308
    assert boss_stages.chimera_health_lost(chimera_opening(0)) == 0
    built = stage_battle_setup(chimera_opening(), data["stages"]["13049005"], data, health_lost=1000)
    expected = chimera_opening()
    expected[0].update(i=13049005, c=5, o=8 * ONE, a=2576980377)  # 0.6 truncated, as the server writes 0.8
    boss = expected[0]["s"]["h"][0]
    boss.update(i=26886, l=280, f={"c": {"h": -1000 * ONE}},
                s=[{"i": 1268801, "l": 1}, {"i": 1268802, "l": 1}, {"i": 2268801, "l": 1}])
    expected[0]["s"]["m"] = SERVER_MODIFIERS
    # The JSON text too: Python's == would take False for 0 and 7.0 for 7.
    assert json.dumps(built, sort_keys=True) == json.dumps(expected, sort_keys=True)
    assert boss_stages.fixed(0.8) == 3435973836 and boss_stages.fixed(11.0) == 11 * ONE
    # Full health is the default.
    assert stage_battle_setup(chimera_opening(), data["stages"]["13049005"], data)[0]["s"]["h"][0]["f"] == {"c": {"h": 0}}
    no_config = dict(data, chimera={})
    try:
        stage_battle_setup(chimera_opening(), data["stages"]["13049005"], no_config)
    except StageSetupError:
        pass
    else:
        raise AssertionError("built a Chimera without its difficulty config")


def test_a_strategy_moves_to_the_same_trial_slots_on_another_difficulty() -> None:
    strategy = {"objectives": {"mandatoryTrialIds": [8000609, 8000626], "minimumDamage": 8000609,
                               "earlyRetryConditions": [{"trialIds": [8000601], "bossTurn": 40}]},
                "rules": [{"when": {"eligibleTrialsAny": [8000612], "activeTrialsAny": [8000627]},
                           "skill": {"typeId": 85701}, "flag": True}]}
    moved = boss_stages.strategy_on_difficulty(strategy, 3)
    assert moved["objectives"]["mandatoryTrialIds"] == [8000309, 8000326]
    assert moved["objectives"]["minimumDamage"] == 8000609  # a number outside the trial fields stays
    assert moved["objectives"]["earlyRetryConditions"][0] == {"trialIds": [8000301], "bossTurn": 40}
    assert moved["rules"][0]["when"] == {"eligibleTrialsAny": [8000312], "activeTrialsAny": [8000327]}
    assert moved["rules"][0]["skill"] == {"typeId": 85701} and moved["rules"][0]["flag"] is True
    assert strategy["objectives"]["mandatoryTrialIds"] == [8000609, 8000626]  # the original is untouched
    # Not a trial: slot 28 does not exist, 8000700 is no difficulty.
    assert boss_stages.trial_with_difficulty(8000628, 3) == 8000628 and boss_stages.trial_with_difficulty(8000701, 3) == 8000701
    catalog = boss_stages.chimera_trial_catalog(
        {"difficultyId": 6, "difficulty": "UltraNightmare", "health": 1610000000, "stageIds": [13049006],
         "trials": [{"id": 8000609, "skillTypeId": 8000609, "formId": 1, "reward": {"rollCount": 1}}]},
        3, {"health": 290900000, "stageIds": [13019003, 13049003]})
    assert catalog == {"difficultyId": 3, "difficulty": "Hard", "health": 290900000, "stageIds": [13019003, 13049003],
                       "trials": [{"id": 8000309, "skillTypeId": 8000309, "formId": 1}]}


def write_opening(folder: Path, setup: list[dict]) -> None:
    import hashlib
    folder.mkdir(parents=True)
    data = json.dumps(setup).encode("utf-8")
    (folder / "battle-setup.json").write_bytes(data)
    (folder / "battle-settings.json").write_text("{}", encoding="utf-8")
    (folder / "capture-provenance.json").write_text(json.dumps({
        "schema": 1, "type": "verified_hydra_replay_source", "battleSetupId": "abc", "seed": 1611440968,
        "stageId": setup[0]["i"], "teamHeroTypeIds": [8896], "teamHeroIds": [39104],
        "battleSetups": {"file": "battle-setup.json", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}),
        encoding="utf-8")


def test_the_built_input_names_its_stage_and_where_it_came_from() -> None:
    data = extract(static_data())
    with tempfile.TemporaryDirectory() as temporary:
        source, destination = Path(temporary) / "opening", Path(temporary) / "stage-input"
        write_opening(source, opening())
        provenance = prepare_stage_input(source, data["stages"]["8039004"], data, destination)
        written = (destination / "battle-setup.json").read_bytes()
        assert json.loads(written)[0]["i"] == 8039004 and provenance["stageId"] == 8039004
        assert provenance["stageBuiltFrom"] == 8039003 and provenance["seed"] == 1611440968
        assert provenance["battleSetups"]["bytes"] == len(written)
        assert json.loads((source / "battle-setup.json").read_text("utf-8"))[0]["i"] == 8039003  # opening unchanged


def test_stages_are_read_once_per_static_data_file() -> None:
    calls = []
    original = boss_stages.extract

    def counting(data):
        calls.append(len(data))
        return original(data)

    with tempfile.TemporaryDirectory() as temporary:
        payload = static_data()
        static = Path(temporary) / "11.75.0" / "f5ae"
        static.parent.mkdir()
        static.write_bytes(block_array([(lz4_literals(payload), len(payload))]))
        boss_stages.extract = counting
        try:
            first = load_stages(static, root=Path(temporary) / "cache")
            second = load_stages(static, root=Path(temporary) / "cache")
        finally:
            boss_stages.extract = original
        assert len(calls) == 1 and first == second and "8039004" in second["stages"]
        assert second["source"] == {"file": "f5ae", "version": "11.75.0", "bytes": static.stat().st_size}


def test_a_hydra_opening_runs_on_another_difficulty() -> None:
    import chimera_simulation_service
    from chimera_simulation_service import SimulationService
    from hydra_simulation_service import HydraSimulationService

    data = extract(static_data())
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        capture = root / "capture" / "abc-1"
        write_opening(capture, opening())
        (capture / "packed").mkdir()
        (capture / "packed" / "conversion-report.json").write_text("{}", encoding="utf-8")
        converted, calls = [], []

        def convert(folder, probe):
            converted.append(folder)
            (folder / "packed").mkdir()
            return {}

        def runner(probe, packed, strategy, *, seed, **_):
            calls.append((packed, seed))
            return {"status": "unknown", "reason": "test"}

        original = chimera_simulation_service.convert
        chimera_simulation_service.convert = convert
        try:
            service = HydraSimulationService(capture_roots={"capture": root / "capture", "forecast": root / "forecast"},
                                             simulation_root=root / "sims", runner=runner,
                                             bundle_provider=lambda pid: root, stage_provider=lambda: data)
            # Whoever sees the job end must be able to read its summary right away.
            ended = []
            update = service._update

            def watched(**values):
                if values.get("status") in ("complete", "cancelled", "failed"):
                    ended.append((root / "sims" / service.status()["id"] / "summary.json").is_file())
                update(**values)
            service._update = watched
            for wrong in (5, 0):
                try:
                    service.start({"rules": []}, {"id": "s"}, "capture:abc-1", 1, difficulty=wrong)
                except ValueError:
                    continue
                raise AssertionError(f"difficulty {wrong} accepted")
            job = service.start({"rules": []}, {"id": "s", "name": "测试"}, "capture:abc-1", 2, difficulty=4)
            for _ in range(200):
                if service.status()["status"] != "running":
                    break
                time.sleep(0.02)
            assert service.status()["status"] == "complete", service.status()
            assert ended == [True], ended
        finally:
            chimera_simulation_service.convert = original
        stage_input = root / "sims" / job["id"] / "stage-input"
        assert converted == [stage_input] and {packed for packed, _ in calls} == {stage_input / "packed"}
        assert sorted(calls, key=lambda call: call[1] is not None)[0][1] is None  # run 1: the opening's seed
        capture_facts = service.load(job["id"])["capture"]
        assert capture_facts["stageId"] == 8039004 and capture_facts["difficulty"] == 4
        assert capture_facts["openingStageId"] == 8039003 and capture_facts["openingDifficulty"] == 3
        assert json.loads((stage_input / "battle-setup.json").read_text("utf-8"))[0]["h"] == 3
        # Its own difficulty is just the opening.
        job = service.start({"rules": []}, {"id": "s"}, "capture:abc-1", 1, difficulty=3)
        for _ in range(200):
            if service.status()["status"] != "running":
                break
            time.sleep(0.02)
        assert not (root / "sims" / job["id"] / "stage-input").exists()
        assert "openingStageId" not in service.load(job["id"])["capture"]
        # Only the Chimera's HP carries over; a percentage must be in (0, 100].
        for service_, health in ((service, 50), (SimulationService(capture_root=root, simulation_root=root), 0),
                                 (SimulationService(capture_root=root, simulation_root=root), 101)):
            try:
                service_.start({"rules": []}, {}, "x", 1, boss_health=health)
            except ValueError:
                continue
            raise AssertionError(f"boss health {health} accepted")


def write_chimera_opening(folder: Path, setup: list[dict]) -> None:
    import gzip
    import hashlib
    folder.mkdir(parents=True)
    data = json.dumps(setup).encode("utf-8")
    (folder / "battle-setup.json").write_bytes(data)
    (folder / "battle-settings.json").write_text("{}", encoding="utf-8")
    (folder / "capture-provenance.json").write_text(json.dumps({
        "schema": 1, "type": "verified_chimera_replay_source", "battleSetupId": "abc", "seed": 366473740,
        "stageId": 13049006, "teamHeroTypeIds": [7376], "teamHeroIds": [29968], "bossHeroTypeId": 26926, "bossLevel": 300,
        "battleSetups": {"file": "battle-setup.json", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}),
        encoding="utf-8")
    trials = [{"id": 8000609, "skillTypeId": 8000609, "formId": 1}]
    (folder / "decision-static.json").write_text(json.dumps({"trialCatalog": {"available": True, "difficulties": [
        {"difficultyId": 6, "difficulty": "UltraNightmare", "health": 1610000000, "stageIds": [13049006], "trials": trials}]}}),
        encoding="utf-8")
    with gzip.open(folder / "decision-states.jsonl.gz", "wt", encoding="utf-8") as stream:
        stream.write(json.dumps({"chimeraStartSelection": {"stageId": 13049006, "heroIds": [29968], "heroTypeIds": [7376]},
                                 "rotationIdentity": {"metadata": {"turnsBetweenForms": 5}}}) + "\n")
    (folder / "packed").mkdir()
    (folder / "packed" / "conversion-report.json").write_text("{}", encoding="utf-8")


def test_the_strategy_team_and_another_difficulty_go_together() -> None:
    """The simulation panel always swaps in the strategy's team; the stage is rebuilt on top of it."""
    import chimera_simulation_service
    from chimera_simulation_service import SimulationService
    from test_team_setups import hero, server_hero

    data = extract(static_data())
    setup = chimera_opening()
    setup[0]["f"] = {"i": 77, "p": 1, "l": 1, "z": {}, "ls": [], "h": [server_hero(11, 7001, 1)]}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_chimera_opening(root / "captures" / "abc-1", setup)
        converted, calls = [], []

        def convert(folder, probe):
            converted.append(folder.name)
            (folder / "packed").mkdir()
            return {}

        def runner(probe, packed, strategy, *, seed, team_selection, start_selection, **_):
            calls.append((packed, team_selection, start_selection))
            return {"status": "unknown", "reason": "test"}

        originals = chimera_simulation_service.convert, chimera_simulation_service.ensure_ui_catalog_cache
        chimera_simulation_service.convert = convert
        chimera_simulation_service.ensure_ui_catalog_cache = lambda: {"difficulties": []}
        try:
            service = SimulationService(capture_root=root / "captures", simulation_root=root / "sims", runner=runner,
                                        bundle_provider=lambda pid: root, stage_provider=lambda: data)
            team = {"source": "strategy", "heroes": [hero(21, 8001), hero(22, 8002)], "powers": None, "area": None,
                    "rule": "building", "savedAt": "2026-10-07 10:00:00", "check": None}
            job = service.start({"rules": []}, {"id": "s"}, "abc-1", 1, team=team, difficulty=5)
            for _ in range(200):
                if service.status()["status"] != "running":
                    break
                time.sleep(0.02)
            assert service.status()["status"] == "complete", service.status()
        finally:
            chimera_simulation_service.convert, chimera_simulation_service.ensure_ui_catalog_cache = originals
        assert converted == ["team-input", "stage-input"]  # the saved battle itself is never rewritten
        (packed, team_selection, start), = calls
        assert packed.parent.name == "stage-input" and team_selection == {"heroTypeIds": [8001, 8002], "heroIds": [21, 22]}
        assert start == {"stageId": 13049005, "heroIds": [21, 22], "heroTypeIds": [8001, 8002]}
        built = json.loads((packed.parent / "battle-setup.json").read_text("utf-8"))[0]
        assert [unit["i"] for unit in built["f"]["h"]] == [8001, 8002] and built["s"]["h"][0]["i"] == 26886
        facts = service.load(job["id"])["capture"]
        assert facts["teamSource"] == "strategy" and facts["teamHeroTypeIds"] == [8001, 8002] and facts["difficulty"] == 5


def test_a_chimera_opening_runs_on_another_difficulty_and_health() -> None:
    import chimera_simulation_service
    from chimera_simulation_service import SimulationService, list_captures

    data = extract(static_data())
    # The shipped catalog has no HP or stages (only live catalogs do): those come from the stage data.
    known = {"difficultyId": 5, "difficulty": "Nightmare",
             "trials": [{"id": 8000509, "skillTypeId": 8000509, "formId": 1, "description": "editor's"}]}
    strategy = {"rules": [{"when": {"eligibleTrialsAny": [8000609]}}], "objectives": {"mandatoryTrialIds": [8000609]}}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_chimera_opening(root / "captures" / "abc-1", chimera_opening())
        assert list_captures(root=root / "captures")[0]["bossHealthLost"] == 56707308
        converted, calls = [], []

        def convert(folder, probe):
            converted.append(folder)
            (folder / "packed").mkdir()
            return {}

        def runner(probe, packed, strategy, *, seed, static_state, start_selection, **_):
            calls.append((packed, strategy, static_state, start_selection))
            return {"status": "unknown", "reason": "test"}

        originals = chimera_simulation_service.convert, chimera_simulation_service.ensure_ui_catalog_cache
        try:
            for catalog in ({"difficulties": [known]}, {"difficulties": []}):
                chimera_simulation_service.convert = convert
                chimera_simulation_service.ensure_ui_catalog_cache = lambda catalog=catalog: catalog
                service = SimulationService(capture_root=root / "captures", simulation_root=root / "sims", runner=runner,
                                            bundle_provider=lambda pid: root, stage_provider=lambda: data)
                calls.clear()
                job = service.start(strategy, {"id": "s"}, "abc-1", 1, difficulty=5, boss_health=80)
                for _ in range(200):
                    if service.status()["status"] != "running":
                        break
                    time.sleep(0.02)
                assert service.status()["status"] == "complete", service.status()
                stage_input = root / "sims" / job["id"] / "stage-input"
                (packed, ran, static_state, start), = calls
                assert packed == stage_input / "packed" and start["stageId"] == 13049005 and start["heroIds"] == [29968]
                assert ran["objectives"]["mandatoryTrialIds"] == [8000509]  # the same slot on Nightmare
                assert ran["rules"][0]["when"]["eligibleTrialsAny"] == [8000509]
                saved = json.loads((root / "sims" / job["id"] / "strategy.json").read_text("utf-8"))
                assert saved["objectives"]["mandatoryTrialIds"] == [8000509]
                difficulty, = static_state["trialCatalog"]["difficulties"]
                assert difficulty["difficultyId"] == 5 and difficulty["trials"][0]["id"] == 8000509
                assert difficulty["health"] == 1195000000 and difficulty["stageIds"] == [13019005, 13049005]
                # The editor's catalog when it has the difficulty, else the opening's renumbered.
                assert difficulty["trials"][0].get("description") == ("editor's" if catalog["difficulties"] else None)
                built = json.loads((stage_input / "battle-setup.json").read_text("utf-8"))[0]
                assert built["c"] == 5 and built["o"] == 8 * ONE and built["s"]["h"][0]["i"] == 26886
                assert built["s"]["h"][0]["f"] == {"c": {"h": -239000000 * ONE}}  # 20% of 1.195B lost
                facts = service.load(job["id"])["capture"]
                assert (facts["stageId"], facts["difficulty"], facts["bossHeroTypeId"]) == (13049005, 5, 26886)
                assert (facts["openingDifficulty"], facts["bossHealthPercent"]) == (6, 80)
            # Its own difficulty at full health: the boss side rebuilt, the trials kept.
            calls.clear()
            job = service.start(strategy, {"id": "s"}, "abc-1", 1, boss_health=100)
            for _ in range(200):
                if service.status()["status"] != "running":
                    break
                time.sleep(0.02)
            (packed, ran, static_state, start), = calls
            built = json.loads((root / "sims" / job["id"] / "stage-input" / "battle-setup.json").read_text("utf-8"))[0]
            assert built["i"] == 13049006 and built["s"]["h"][0]["f"] == {"c": {"h": 0}} and built["o"] == 11 * ONE
            assert ran["objectives"]["mandatoryTrialIds"] == [8000609] and start["stageId"] == 13049006
            assert service.load(job["id"])["capture"]["bossHealthPercent"] == 100
        finally:
            chimera_simulation_service.convert, chimera_simulation_service.ensure_ui_catalog_cache = originals
