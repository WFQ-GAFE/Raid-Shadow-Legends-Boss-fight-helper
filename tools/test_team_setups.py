"""Simulating another team in a saved opening (team_setups)."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import zlib

from team_preview import decode_preview, display_preview, portable_snapshot
from ui_text import render
from team_setups import (
    StrategyTeamStore, TeamSetupError, building_bonus, check_against_captures, decode_account_bonuses,
    decode_roster, differences,
    exported_team, hero_battle_inputs, hero_slot_setup, latest_check, prepare_team_input, preview_team, relic_setup,
    ordered_team_matches, remember_check, same_team, simulation_package, simulation_team, team_battle_setup,
    team_report, team_sources, unpack_simulation, validate_simulation_snapshot, validate_simulation_team,
    with_account_bonuses,
)


ONE = 1 << 32
BUILDING = {"p": {"h": 10 * ONE, "a": 5 * ONE}, "f": {"h": 100}}
AREA = {"p": {"h": 2 * ONE, "d": 3 * ONE}}
ARTIFACTS = [{"i": 901, "k": 1, "r": 6}]
SETS = [{"i": 4, "c": 2}]
ACADEMY = {"g": [1, 2]}
RELIC = {"l": 4, "r": [{"i": 3000007, "l": 4}], "s": [], "p": {"h": 0}, "f": {"h": 7 * ONE},
         "n": {"i": 49, "t": 8, "a": 4, "l": 12, "e": 1, "c": 1, "k": []}}


def hero(hero_id: int, type_id: int, **parts: object) -> dict:
    values = {"artifacts": ARTIFACTS, "sets": SETS, "blessing": {"i": 7}, "relics": [RELIC],
              "academy": ACADEMY, "building": BUILDING, "capitol": {"p": {"h": 1}}}
    values.update(parts)
    return {"model": {"heroId": hero_id, "typeId": type_id, "grade": 6, "level": 60, "experience": 0, "empower": 2,
                      "awakened": 3, "skills": [{"i": type_id * 10 + 1, "l": 5}, {"i": type_id * 10 + 2, "l": 4}],
                      "masteries": [500101, 500102]},
            "parts": values}


def server_hero(hero_id: int, type_id: int, slot: int, *, area: dict | None = AREA) -> dict:
    """What the server sends for `hero(...)`: building plus area bonus, default priorities."""
    return {"d": 1, "t": slot, "u": 77, "i": type_id, "h": hero_id, "g": 6, "w": 3, "l": 60, "x": 0, "m": 0,
            "hb": 0, "mb": 0, "r": 2, "v": 0, "z": building_bonus({"building": BUILDING}, area, "building+area"),
            "s": [{"i": type_id * 10 + 1, "l": 5}, {"i": type_id * 10 + 2, "l": 4}], "b": ARTIFACTS, "e": SETS,
            "y": [500101, 500102], "p": {"h": hero_id, "r": [{"p": {str(type_id * 10 + 1): 0}}] * 4},
            "c": ACADEMY, "bl": {"i": 7}, "re": RELIC, "n": 50000.0}


def write_capture(folder: Path, heroes: list[dict]) -> None:
    folder.mkdir(parents=True)
    setup = [{"f": {"i": 77, "p": 1, "l": 1, "z": {}, "ls": [], "h": heroes}, "s": {"boss": True}}]
    data = json.dumps(setup).encode("utf-8")
    (folder / "battle-setup.json").write_bytes(data)
    (folder / "battle-settings.json").write_text('{"seed": 1}', encoding="utf-8")
    (folder / "capture-provenance.json").write_text(json.dumps({
        "type": "verified_chimera_replay_source", "seed": 1234, "stageId": 13049006,
        "teamHeroIds": [h["h"] for h in heroes], "teamHeroTypeIds": [h["i"] for h in heroes],
        "battleSetups": {"file": "battle-setup.json", "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest()}}), encoding="utf-8")


def test_assembled_setup_has_the_server_shape_and_neutral_priorities() -> None:
    setup = hero_slot_setup(hero(11, 7001), slot=2, owner_id=77, area=AREA, rule="building+area", power=50000,
                            rounds=4)
    server = server_hero(11, 7001, 2)
    assert set(setup) == set(server)
    # Every field but the skill priorities equals the server's; priorities are neutral (0) for every skill.
    assert differences({k: v for k, v in setup.items() if k != "p"}, {k: v for k, v in server.items() if k != "p"}) == []
    assert setup["p"] == {"h": 11, "r": [{"p": {"70011": 0, "70012": 0}}] * 4}
    assert setup["re"] == RELIC and setup["w"] == 3 and setup["n"] == 50000.0
    unawakened = hero(12, 7002)
    unawakened["model"]["awakened"] = None
    unawakened["parts"]["academy"] = None
    plain = hero_slot_setup(unawakened, slot=1, owner_id=77, area=None, rule="building")
    assert plain["w"] == 0 and "c" not in plain and plain["z"] == BUILDING and len(plain["p"]["r"]) == 1


def test_a_hidden_skill_part_fights_at_the_level_of_its_skill() -> None:
    # Lydia: the client keeps 47105 (a hidden part of her A1 47101, UseLevelOfSkillId) at 1; the server sends 5.
    lydia = hero(21597, 4716)
    lydia["model"]["skills"] = [{"i": 47101, "l": 5}, {"i": 47103, "l": 6}, {"i": 47105, "l": 1}, {"i": 47106, "l": 1}]
    setup = hero_slot_setup(lydia, slot=3, owner_id=77, area=None, rule="building", skill_links={47105: 47101})
    assert setup["s"] == [{"i": 47101, "l": 5}, {"i": 47103, "l": 6}, {"i": 47105, "l": 5}, {"i": 47106, "l": 1}]
    # The hero's own record is untouched, and a link to a skill the hero lacks changes nothing.
    assert lydia["model"]["skills"][2] == {"i": 47105, "l": 1}
    assert hero_slot_setup(lydia, slot=3, owner_id=77, area=None, rule="building", skill_links={47106: 99999})["s"][3]["l"] == 1


def test_hero_data_names_the_skill_whose_level_a_skill_uses() -> None:
    from hero_data import compact_skill, skill_level_links
    assert compact_skill({"Id": 47105, "UseLevelOfSkillId": {"hasValue": True, "value": 47101}}, {})["levelOf"] == 47101
    assert "levelOf" not in compact_skill({"Id": 47101}, {})
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        assert skill_level_links(root) == {}
        (root / "a.json").write_text(json.dumps({"schema": 4, "skills": {"47105": {"levelOf": 47101}, "47101": {}}}),
                                     encoding="utf-8")
        assert skill_level_links(root) == {47105: 47101}


def test_differences_names_the_paths() -> None:
    assert differences({"a": 1, "b": [1, 2]}, {"a": 1, "b": [1, 2]}) == []
    assert differences({"a": 1, "b": [1, 3], "c": 0}, {"a": 2, "b": [1, 2]}) == ["a", "b[1]", "c"]
    assert differences({"b": [1]}, {"b": [1, 2]}) == ["b[len 1≠2]"]


def test_check_against_saved_battles_finds_the_building_rule() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_capture(root / "captures" / "one", [server_hero(11, 7001, 1), server_hero(12, 7002, 2)])
        observatory = {"6": {"p": {"h": 9 * ONE}}, "7": AREA}
        result = check_against_captures([hero(11, 7001), hero(12, 7002), hero(13, 7003)], observatory, "chimera",
                                        roots=(root / "captures",))
        assert result["compared"] == 2 and result["matched"] == 2 and result["heroes"] == 3
        assert result["rule"] == "building+area" and result["area"] == "7" and result["differences"] == {}
        # Gear changed since that battle: reported per hero.
        changed = hero(11, 7001, artifacts=[{"i": 902, "k": 1, "r": 6}])
        result = check_against_captures([changed], observatory, "chimera", roots=(root / "captures",))
        assert result["matched"] == 0 and result["differences"]["11"] == ["b[0].i"]
        # Nothing to compare with: no rule, nothing remembered.
        empty = check_against_captures([hero(99, 7009)], observatory, "chimera", roots=(root / "captures",))
        assert empty["compared"] == 0 and empty["rule"] is None
        remember_check(empty, root / "checks")
        assert latest_check("chimera", root / "checks") is None
        remember_check(result, root / "checks")
        assert latest_check("chimera", root / "checks")["compared"] == 1


def test_team_is_swapped_into_the_opening_with_matching_provenance() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        capture = root / "opening"
        write_capture(capture, [server_hero(11, 7001, 1), server_hero(12, 7002, 2)])
        original = json.loads((capture / "battle-setup.json").read_text(encoding="utf-8"))
        swapped = team_battle_setup(original, [hero(21, 8001)], area=AREA, rule="building+area", powers=[123.0])
        assert swapped[0]["s"] == {"boss": True} and swapped[0]["f"]["i"] == 77
        assert [(h["t"], h["h"], h["u"], h["n"]) for h in swapped[0]["f"]["h"]] == [(1, 21, 77, 123.0)]
        assert len(swapped[0]["f"]["h"][0]["p"]["r"]) == 4  # the opening's round count
        provenance = prepare_team_input(capture, [hero(21, 8001), hero(22, 8002)], "chimera", root / "input",
                                        area=AREA, rule="building+area")
        data = (root / "input" / "battle-setup.json").read_bytes()
        assert provenance["battleSetups"]["sha256"] == hashlib.sha256(data).hexdigest()
        assert provenance["battleSetups"]["bytes"] == len(data)
        assert provenance["teamHeroIds"] == [21, 22] and provenance["teamHeroTypeIds"] == [8001, 8002]
        assert provenance["seed"] == 1234 and provenance["stageId"] == 13049006 and provenance["teamSwappedFrom"] == "opening"
        assert json.loads((root / "input" / "capture-provenance.json").read_text(encoding="utf-8")) == provenance
        assert (root / "input" / "battle-settings.json").read_text(encoding="utf-8") == '{"seed": 1}'
        for heroes, message in (([hero(i, 8000 + i) for i in range(6)], "1 到 5"),
                                ([hero(21, 8001, artifacts=None)], "缺少装备数据")):
            try:
                prepare_team_input(capture, heroes, "chimera", root / "bad", area=None, rule="building")
            except TeamSetupError as error:
                assert message in render(str(error), "zh-CN")
            else:
                raise AssertionError("invalid team accepted")


def test_preview_team_store_and_sources() -> None:
    battle = hero(21, 8001)
    type_ids = list(range(8001, 8006))
    battles = [hero(21 + index, type_id) for index, type_id in enumerate(type_ids)]
    preview = {"status": "captured", "bossMode": "chimera", "capturedAt": "2026-09-27 10:00:00",
               "observatory": {"7": AREA},
               "heroes": [{"heroId": 21 + index, "typeId": type_id, "power": 5000, "battle": battles[index]}
                          for index, type_id in enumerate(type_ids)]}
    team = preview_team(preview, "chimera")
    assert team is not None and team["heroTypeIds"] == type_ids and team["heroes"] == battles
    assert preview_team(preview, "hydra") is None
    assert preview_team({**preview, "heroes": [{"heroId": 21, "typeId": 8001}]}, "chimera") is None
    assert same_team([8001, 8002], [8002, 8001, 0]) and not same_team([], []) and not same_team([8001], [8002])
    display = {"schema": 2, "bossMode": "chimera",
               "heroes": [{"typeId": type_id, "equipped": 9} for type_id in type_ids], "names": {},
               "icons": {}, "capturedAt": "2026-09-27 10:00:00"}
    with tempfile.TemporaryDirectory() as temporary:
        store = StrategyTeamStore(Path(temporary))
        assert store.load("chimera", "strategy-1") is None
        store.save("chimera", "strategy-1", team, display=display)
        saved = store.load("chimera", "strategy-1")
        assert saved["heroTypeIds"] == type_ids and saved["savedAt"] and saved["strategyId"] == "strategy-1"
        assert saved["display"] == display
        # Only the heroes' own data is stored: no academy, building or area bonuses.
        assert "observatory" not in saved and set(saved["heroes"][0]["parts"]) == {
            "artifacts", "sets", "blessing", "relics"}
        live = {"heroes": {type_id: {"academy": ACADEMY, "building": BUILDING, "capitol": {"p": {"h": 1}}}
                           for type_id in type_ids},
                "observatory": {"7": AREA}}
        requested = []

        def bonuses(type_ids):
            requested.append(type_ids)
            return live
        check = {"compared": 1, "matched": 1, "rule": "building", "area": "7", "differences": {}}
        # An export carries the display snapshot and the battle data without the account's hero ids.
        shared = exported_team(saved)
        assert shared["heroes"] == display["heroes"] and shared["savedAt"] == saved["savedAt"]
        exported = zlib.decompress(base64.b64decode(shared["simulation"]["data"])).decode("utf-8")
        assert '"heroId"' not in exported and json.loads(exported)["heroTypeIds"] == type_ids
        sources = team_sources("chimera", type_ids, bound=True, saved=saved, reference=shared, check=check)
        assert sources["current"] == {"heroTypeIds": type_ids, "bound": True}
        assert sources["strategy"]["matches"] and sources["author"] == {"heroTypeIds": type_ids,
                                                                        "savedAt": saved["savedAt"]}
        assert sources["check"] == {"checkedAt": None, "compared": 1, "matched": 1, "orderMatched": None,
                                    "rule": "building", "area": "7"}
        assert "artifacts" not in json.dumps(sources)
        assert team_sources("chimera", [8001], bound=False, saved=None, reference=display, check=None)["author"] is None
        # The snapshot taken at save: bonuses by hero type from the current account.
        chosen = simulation_team("strategy", "chimera", type_ids, saved=saved, check={"rule": "building", "area": "7"},
                                 bonuses=bonuses)
        assert requested == [type_ids]
        assert chosen["source"] == "strategy" and chosen["rule"] == "building" and chosen["area"] == AREA
        assert chosen["heroes"][0]["parts"] == battle["parts"] and chosen["savedAt"] == saved["savedAt"]
        # The author's team (imported): stand-in hero ids, the importer's bonuses.
        author = simulation_team("author", "chimera", type_ids, reference=shared, check=check, bonuses=bonuses)
        assert author["source"] == "author" and author["heroes"][0]["model"]["heroId"] == 1
        assert author["heroes"][0]["parts"] == battle["parts"] and author["savedAt"] == saved["savedAt"]
        # The team as it is now, read from the game.
        now = simulation_team("current", "chimera", type_ids, check=None, current=lambda: team)
        assert now["rule"] == "building+area" and now["area"] == AREA  # Chimera area (7) by default
        assert simulation_team("battle", "chimera", [], saved=None) is None
        for source, strategy_team, arguments, message in (
                ("strategy", [], {"saved": saved, "bonuses": bonuses}, "还没有设定队伍"),
                ("strategy", type_ids, {"bonuses": bonuses}, "还没有保存时的队伍"),
                ("strategy", [8002], {"saved": saved, "bonuses": bonuses}, "队伍已更改"),
                ("strategy", type_ids, {"saved": saved}, "打开游戏"),
                ("author", [8001], {"reference": display, "bonuses": bonuses}, "没有作者队伍"),
                ("author", type_ids, {"reference": shared}, "打开游戏"),
                ("current", type_ids, {}, "打开游戏"),
                ("current", [8002], {"current": lambda: team}, "不一致"),
                ("preparation", [8001], {"saved": saved}, "无效")):
            try:
                simulation_team(source, "chimera", strategy_team, **arguments)
            except TeamSetupError as error:
                assert message in render(str(error), "zh-CN"), (source, str(error))
            else:
                raise AssertionError((source, message))
        store.delete("chimera", "strategy-1")
        assert store.load("chimera", "strategy-1") is None


def test_exported_battle_data_is_validated() -> None:
    package = simulation_package({"heroes": [hero(21, 8001)], "powers": [5000]})
    assert len(package["data"]) < 2000 and package["format"] == "zlib+base64"
    team = unpack_simulation(package)
    assert team["heroTypeIds"] == [8001] and team["powers"] == [5000]
    assert set(team["heroes"][0]["parts"]) == {"artifacts", "sets", "blessing", "relics"}
    for broken in (None, {**package, "format": "gzip"}, {**package, "data": "!!"}, {**package, "data": "eJw="},
                   {**package, "data": package["data"][:40]},
                   simulation_package({"heroes": []}),
                   simulation_package({"heroes": [{"model": {"typeId": 8001}, "parts": {}}]})):
        assert unpack_simulation(broken) is None, broken


def test_ordered_team_validation_preserves_every_slot_and_the_leader() -> None:
    assert same_team([8001, 8002], [8002, 8001, 0])
    assert ordered_team_matches([8001, 8002], [8001, 8002])
    assert ordered_team_matches([8001, 8001], [8001, 8001])
    for left, right in (([8001, 8002], [8002, 8001]), ([8001, 0], [8001]),
                        ([8001, True], [8001, 1]), (["8001"], [8001]),
                        (None, [8001]), ([], []), ((8001,), [8001])):
        assert not ordered_team_matches(left, right), (left, right)
    type_ids = list(range(8001, 8006))
    team = {"heroTypeIds": type_ids, "heroIds": list(range(21, 26)),
            "heroes": [hero(21 + index, type_id) for index, type_id in enumerate(type_ids)]}
    assert validate_simulation_team(team, "chimera", type_ids)
    assert not validate_simulation_team(team, "hydra", type_ids)
    assert not validate_simulation_team(team, "chimera", list(reversed(type_ids)))
    assert not validate_simulation_team({**team, "heroTypeIds": list(reversed(type_ids))})
    assert not validate_simulation_team({**team, "heroIds": list(reversed(team["heroIds"]))})
    assert not validate_simulation_team({**team, "heroes": list(reversed(team["heroes"]))})
    assert validate_simulation_team({"heroes": [hero(21, 8001)]})
    assert not validate_simulation_team({"heroes": [hero(21, 8001)]}, "chimera")


def test_simulation_validation_rejects_missing_inputs_but_allows_empty_equipment() -> None:
    bare = hero(21, 8001, artifacts=[], sets=[], blessing=None, relics=None)
    assert validate_simulation_team({"heroes": [bare]})
    assert unpack_simulation(simulation_package({"heroes": [bare]})) is not None
    for section, key, value in (("model", "typeId", True), ("model", "heroId", "21"),
                                ("model", "grade", 0), ("model", "level", 0),
                                ("model", "level", "60"), ("model", "level", 61),
                                ("model", "skills", []), ("model", "skills", [{"i": 80011}]),
                                ("model", "skills", [{"i": True, "l": 1}]),
                                ("model", "skills", [{"i": 80011, "l": -1}]),
                                ("model", "masteries", [True]), ("parts", "artifacts", None),
                                ("parts", "artifacts", [{}]), ("parts", "sets", None),
                                ("parts", "sets", [{"i": "4"}]),
                                ("parts", "blessing", {"i": "7"}), ("parts", "relics", [{}])):
        broken = copy.deepcopy(bare)
        broken[section][key] = value
        assert not validate_simulation_team({"heroes": [broken]}), (section, key, value)
        # An export intentionally removes account-specific hero ids.
        if key != "heroId":
            assert unpack_simulation(simulation_package({"heroes": [broken]})) is None, (section, key, value)
    for key in ("artifacts", "sets", "blessing", "relics"):
        broken = copy.deepcopy(bare)
        broken["parts"].pop(key)
        assert not validate_simulation_team({"heroes": [broken]}), key
    assert not validate_simulation_team({"heroes": [bare], "powers": [float("nan")]})
    assert not validate_simulation_team({"heroes": [bare], "powers": [10 ** 1000]})
    assert not validate_simulation_team({"heroes": [bare], "powers": [True]})
    assert not validate_simulation_team({"heroes": [bare], "powers": [1, 2]})


def test_relic_validation_requires_the_socketed_stones_and_skills() -> None:
    partial = {"l": 0, "p": {"h": 0}, "f": {"h": ONE}}
    battle = {"relic": {"i": 375, "t": 85, "a": 5, "l": 15, "e": 1, "c": 1,
                        "k": [{"k": 2, "s": 419}, {"k": 1}]},
              "skillLevel": 5, "skills": [3000084], "fraction": 4, "factionSkills": [],
              "stones": [{"id": 419, "typeId": 64, "skills": [4000063]}]}
    equipped = hero(21, 8001, relics=[partial], relicBattle=battle)
    assert validate_simulation_team({"heroes": [equipped]})
    assert unpack_simulation(simulation_package({"heroes": [equipped]})) is not None
    for key, value in (("skillLevel", None), ("skills", None), ("fraction", None),
                       ("factionSkills", None), ("stones", []),
                       ("stones", [{"id": 419, "typeId": 64}])):
        broken = copy.deepcopy(equipped)
        broken["parts"]["relicBattle"][key] = value
        assert not validate_simulation_team({"heroes": [broken]}), key
        assert unpack_simulation(simulation_package({"heroes": [broken]})) is None, key
    broken = copy.deepcopy(equipped)
    broken["parts"].pop("relicBattle")
    assert not validate_simulation_team({"heroes": [broken]})
    complete = hero(21, 8001)
    assert validate_simulation_team({"heroes": [complete]})
    for key in ("r", "s", "n"):
        broken = copy.deepcopy(complete)
        broken["parts"]["relics"][0].pop(key)
        assert not validate_simulation_team({"heroes": [broken]}), key
    socketed = copy.deepcopy(complete)
    socketed["parts"]["relics"][0]["n"]["k"] = [{"k": 1, "s": 419}]
    assert not validate_simulation_team({"heroes": [socketed]})


def test_real_serialized_set_defaults_and_duplicate_stone_skills_are_valid() -> None:
    # Shape taken from the existing six-hero captures: no-effect set bonuses
    # serialize as {}, while active set skills/stat bonuses have nested fields.
    # All identities below are standalone fixture values, not account ids.
    artifacts = [{"k": 7, "s": 59, "n": 5, "r": 6, "q": 3, "al": 2,
                  "ab": {"k": 2, "p": {"a": 1, "v": 94489280512}, "s": 0, "l": 0},
                  "p": {"h": 0, "a": 429496728, "d": 558345746, "i": 0},
                  "f": {"h": 3908420239360, "a": 240518168576, "d": 966367641600, "i": 0},
                  "cb": {"dp": 0, "dv": 0, "db": 0, "dd": 0, "tp": 0, "tv": 0, "tb": 0, "td": 0}}]
    sets = [{}, {"l": {"i": 100048}, "b": [{"k": 2, "b": 429496729, "a": 0}]},
            {"l": {"i": 100041}, "t": {"k": 4, "b": 515396075, "a": 0}}, {},
            {"l": {"i": 100031}}]
    partial = {"l": 0, "p": {"h": 0}, "f": {"h": 7 * ONE}}
    battle = {"relic": {"i": 49, "t": 24, "a": 5, "l": 15, "e": 1, "c": 1,
                        "k": [{"k": 2, "s": 101}, {"k": 2, "s": 102}]},
              "skillLevel": 5, "skills": [3000023], "fraction": 1, "factionSkills": [],
              "stones": [{"id": 101, "typeId": 3, "skills": [4000002]},
                         {"id": 102, "typeId": 3, "skills": [4000002]}]}
    type_ids = list(range(8001, 8007))
    heroes = [hero(21 + index, type_id, artifacts=artifacts, sets=sets,
                   relics=[partial], relicBattle=battle) for index, type_id in enumerate(type_ids)]
    display = {"bossMode": "hydra", "heroes": [{"typeId": type_id} for type_id in type_ids]}
    team = {"bossMode": "hydra", "heroTypeIds": type_ids, "heroes": heroes, "display": display}
    assert validate_simulation_team(team, "hydra", type_ids)
    snapshot = exported_team(team, "hydra", type_ids)
    assert snapshot is not None and validate_simulation_snapshot(snapshot, "hydra", type_ids)
    assert unpack_simulation(snapshot["simulation"], "hydra", type_ids) is not None
    # The completed server setup preserves both stone effects; identical skill
    # ids here must not be mistaken for a malformed hero skill list.
    complete = relic_setup(partial, battle)
    assert complete["s"] == [{"i": 4000002, "l": 0}, {"i": 4000002, "l": 0}]
    completed_team = copy.deepcopy(team)
    for entry in completed_team["heroes"]:
        entry["parts"]["relics"] = [complete]
        entry["parts"].pop("relicBattle")
    assert validate_simulation_team(completed_team, "hydra", type_ids)
    assert exported_team(completed_team, "hydra", type_ids) is not None


def test_snapshot_and_team_sources_require_matching_valid_simulation_slots() -> None:
    type_ids = list(range(8001, 8006))
    saved = {"bossMode": "chimera", "heroTypeIds": type_ids,
             "heroes": [hero(21 + index, type_id) for index, type_id in enumerate(type_ids)],
             "display": {"bossMode": "chimera", "heroes": [{"typeId": type_id} for type_id in type_ids]}}
    shared = exported_team(saved, "chimera", type_ids)
    assert shared is not None and validate_simulation_snapshot(shared, "chimera", type_ids)
    nested = {"bossMode": "chimera", "display": saved["display"], "simulation": shared["simulation"]}
    assert validate_simulation_snapshot(nested, "chimera", type_ids)
    assert not validate_simulation_snapshot(shared, "hydra", type_ids)
    for changed in ({**shared, "heroes": list(reversed(shared["heroes"]))},
                    {**shared, "simulation": {**shared["simulation"], "data": "!!"}},
                    {**shared, "heroes": [{"typeId": "8001"}] + shared["heroes"][1:]},
                    {**shared, "heroTypeIds": list(reversed(type_ids))}):
        assert not validate_simulation_snapshot(changed, "chimera", type_ids)
        sources = team_sources("chimera", type_ids, bound=False, saved=saved, reference=changed, check=None)
        assert sources["author"] is None
    assert exported_team({**saved, "display": {"heroes": list(reversed(saved["display"]["heroes"]))}}) is None
    assert exported_team({**saved, "heroTypeIds": list(reversed(type_ids))}) is None
    sources = team_sources("chimera", list(reversed(type_ids)), bound=True,
                           saved=saved, reference=shared, check=None)
    assert not sources["strategy"]["matches"] and sources["author"] is None
    calls = []
    for source, arguments in (("author", {"reference": shared}), ("strategy", {"saved": saved}),
                              ("current", {"current": lambda: saved})):
        try:
            simulation_team(source, "chimera", list(reversed(type_ids)),
                            bonuses=lambda ids: calls.append(ids), **arguments)
        except TeamSetupError:
            pass
        else:
            raise AssertionError(f"{source} accepted the wrong leader and slot order")
    assert calls == []  # Bad imported/saved inputs are rejected before live bonus reads.


def test_package_validation_checks_declared_ids_and_complete_compression() -> None:
    package = simulation_package({"heroes": [hero(21, 8001)]})
    body = json.loads(zlib.decompress(base64.b64decode(package["data"])))

    def encoded(value):
        return {**package, "data": base64.b64encode(zlib.compress(json.dumps(value).encode("utf-8"))).decode("ascii")}

    assert unpack_simulation(package) is not None
    assert unpack_simulation(package, "chimera") is None
    assert unpack_simulation(package, strategy_team=[8002]) is None
    assert unpack_simulation({**package, "schema": True}) is None
    assert unpack_simulation(encoded({**body, "heroTypeIds": [8002]})) is None
    assert unpack_simulation(encoded({key: value for key, value in body.items() if key != "heroTypeIds"})) is None
    compressed = base64.b64decode(package["data"])
    for data in (compressed[:-1], compressed + b"trailing", compressed + zlib.compress(b"{}")):
        assert unpack_simulation({**package, "data": base64.b64encode(data).decode("ascii")}) is None


def test_preview_keeps_battle_parts_out_of_the_interface_and_exports() -> None:
    model = {"i": 21, "t": 8001, "g": 6, "l": 60, "s": [{"t": 80011, "l": 5}], "da": {"g": 2}, "m": {"m": [500101]}}
    raw = {"type": "team_preview", "schema": 2, "status": "captured", "bossMode": "chimera", "observedAtTick": 5,
           "heroIds": [21],
           "heroes": [{"heroId": 21, "heroJson": json.dumps(model),
                       "setup": {"artifacts": json.dumps(ARTIFACTS), "sets": json.dumps(SETS),
                                 "building": json.dumps(BUILDING), "academy": "not json"}}],
           "observatory": {"7": json.dumps(AREA)}}
    preview = decode_preview(raw)
    battle = preview["heroes"][0]["battle"]
    assert battle["parts"]["artifacts"] == ARTIFACTS and battle["parts"]["academy"] is None
    assert battle["model"]["skills"] == [{"i": 80011, "l": 5}] and battle["model"]["awakened"] == 2
    assert preview["observatory"] == {"7": AREA}
    shown = display_preview(preview)
    assert "observatory" not in shown and "battle" not in shown["heroes"][0]
    assert "battle" not in json.dumps(portable_snapshot(preview))
    assert hero_battle_inputs({"heroJson": "{}"}, model) is None


def test_relic_setup_is_completed_like_the_server() -> None:
    battle = {"relic": {"i": 375, "t": 85, "a": 5, "l": 15, "e": 1, "c": 1,
                        "k": [{"k": 2, "s": 419}, {"k": 2, "s": 440}, {"k": 1, "s": 358}]},
              "skillLevel": 5, "skills": [3000084], "fraction": 4,
              "factionSkills": [{"fraction": 3, "skills": [3100084]}],
              "stones": [{"id": 358, "typeId": 37, "skills": [4000036]}, {"id": 419, "typeId": 64, "skills": [4000063]},
                         {"id": 440, "typeId": 3, "skills": [4000002]}]}
    partial = {"l": 0, "p": {"h": 0, "m": 0}, "f": {"a": 665719930880, "m": 1331439861}}
    # The server's setup for this relic (a saved Hydra battle): critical damage as a percent bonus.
    assert relic_setup(partial, battle) == {
        "l": 5, "r": [{"i": 3000084, "l": 5}],
        "s": [{"i": 4000063, "l": 0}, {"i": 4000002, "l": 0}, {"i": 4000036, "l": 0}],
        "p": {"h": 0, "m": 1331439861}, "f": {"a": 665719930880, "m": 0},
        "n": {"i": 375, "t": 85, "a": 5, "l": 15, "e": 1, "c": 1,
              "k": [{"k": 2, "s": 419, "t": 64}, {"k": 2, "s": 440, "t": 3}, {"k": 1, "s": 358, "t": 37}]}}
    assert relic_setup(partial, {**battle, "fraction": 3})["r"] == [{"i": 3000084, "l": 5}, {"i": 3100084, "l": 5}]
    partial_only = hero(21, 8001, relics=[partial])
    try:
        hero_slot_setup(partial_only, slot=1, owner_id=77, area=None, rule="building")
    except TeamSetupError as error:
        assert "圣物数据不完整" in render(str(error), "zh-CN")
    else:
        raise AssertionError("an incomplete relic was accepted")
    partial_only["parts"]["relicBattle"] = battle
    assert hero_slot_setup(partial_only, slot=1, owner_id=77, area=None, rule="building")["re"]["n"]["i"] == 375
    parsed = hero_battle_inputs({"setup": {"relics": json.dumps([partial]),
                                           "relicBattle": {**battle, "relic": json.dumps(battle["relic"])}}},
                                {"i": 21, "t": 8001, "g": 6, "l": 60})
    assert parsed["parts"]["relicBattle"]["relic"]["i"] == 375


def test_account_bonuses_answer() -> None:
    raw = {"type": "account_bonuses", "schema": 1, "nonce": 5, "status": "captured",
           "heroes": [{"typeId": 8001, "setup": {"academy": json.dumps(ACADEMY), "building": json.dumps(BUILDING)}}],
           "observatory": {"6": json.dumps(AREA), "7": "not json"}}
    decoded = decode_account_bonuses(raw, [8001])
    assert decoded["heroes"][8001] == {"academy": ACADEMY, "building": BUILDING, "capitol": None}
    assert decoded["observatory"] == {"6": AREA}
    for answer, type_ids, message in (
            ({**raw, "status": "unavailable", "reason": "user_unavailable"}, [8001], "user_unavailable"),
            ({**raw, "heroes": [{"typeId": 8001, "missing": True}]}, [8001], "找不到"),
            (raw, [8001, 8002], "找不到"),
            ({"type": "team_preview"}, [8001], "没有读到")):
        try:
            decode_account_bonuses(answer, type_ids)
        except TeamSetupError as error:
            assert message in render(str(error), "zh-CN"), render(str(error), "zh-CN")
        else:
            raise AssertionError(message)


def test_check_separates_gear_order_from_real_differences() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        two = [{"i": 901, "k": 1, "r": 6}, {"i": 902, "k": 4, "r": 5}]
        server = {**server_hero(11, 7001, 1), "b": list(reversed(two))}
        write_capture(root / "captures" / "one", [server])
        mine = hero(11, 7001, artifacts=two)
        result = check_against_captures([mine], {"7": AREA}, "chimera", roots=(root / "captures",))
        assert result["matched"] == 1 and result["orderMatched"] == 0
        in_order = hero(11, 7001, artifacts=list(reversed(two)))
        result = check_against_captures([in_order], {"7": AREA}, "chimera", roots=(root / "captures",))
        assert result["matched"] == 1 and result["orderMatched"] == 1


def test_roster_lists_the_account_champions_strongest_first() -> None:
    raw = {"type": "roster", "schema": 1, "nonce": 3, "status": "captured",
           "heroes": [{"i": 11, "t": 8006, "r": 5, "g": 6, "l": 60, "e": 2, "p": 90000},
                      {"i": 12, "t": 7006, "r": 6, "g": 6, "l": 60, "p": 120000, "s": 1},
                      {"i": 13, "t": 1001, "g": 1, "l": 1, "b": 1},
                      {"i": "bad", "t": 1}, {"t": 5}]}
    heroes = decode_roster(raw)
    assert [hero["id"] for hero in heroes] == [12, 11, 13]
    assert heroes[0] == {"id": 12, "typeId": 7006, "rarity": 6, "grade": 6, "level": 60, "empower": 0, "power": 120000,
                         "vault": True, "reserve": False}
    assert heroes[2]["reserve"] and heroes[2]["power"] is None and heroes[2]["rarity"] == 0
    for answer, message in (({**raw, "status": "unavailable", "reason": "user_unavailable"}, "user_unavailable"),
                            ({"type": "team_data"}, "英雄列表")):
        try:
            decode_roster(answer)
        except TeamSetupError as error:
            assert message in render(str(error), "zh-CN"), render(str(error), "zh-CN")
        else:
            raise AssertionError(message)


def test_report_shows_the_simulated_team_with_its_opening_stats() -> None:
    from simulation_common import opening_stats

    one = 2**32
    state = {"heroes": [{"typeId": 8576, "numericObservation": {"statsRaw": {
        "Health": str(69266 * one), "Attack": str(2823 * one), "Defence": str(5971 * one), "Speed": str(334 * one),
        "Resistance": str(473 * one), "Accuracy": str(386 * one), "CriticalChance": str(int(1.07 * one)),
        "CriticalDamage": str(int(1.69 * one))}}}, {"typeId": 9906}]}
    stats = opening_stats(state)
    assert list(stats) == ["8576"] and stats["8576"][:4] == [69266.0, 2823.0, 5971.0, 334.0]
    assert abs(stats["8576"][6] - 1.07) < 1e-4
    hero = {"i": 8576, "g": 6, "l": 60, "r": 1, "w": 6, "n": 243124.5, "y": [500213, 500313],
            "s": [{"i": 85701, "l": 2}], "bl": {"i": 1202}, "re": {"n": {"i": 465, "t": 3, "a": 5, "l": 15}},
            "b": [{"k": kind, "s": 47 if kind < 9 else 0} for kind in range(1, 10)]}
    other = {"i": 9906, "g": 6, "l": 60, "b": [], "s": []}
    with tempfile.TemporaryDirectory() as temporary:
        setup = Path(temporary) / "battle-setup.json"
        setup.write_text(json.dumps([{"f": {"h": [hero, other]}}]), encoding="utf-8")
        report = team_report(setup, stats, source="current", saved_at="2026-09-29 10:00:00",
                             display={"names": {"sets": {"47": "Protection"}}, "icons": {"sets": {"47": "x"}},
                                      "heroes": [{"typeId": 1}]})
        assert report["source"] == "current" and report["savedAt"] == "2026-09-29 10:00:00"
        assert report["names"] == {"sets": {"47": "Protection"}} and report["icons"] == {"sets": {"47": "x"}}
        first, second = report["heroes"]
        assert first["sets"] == [{"set": 47, "pieces": 8}] and first["equipped"] == 9  # the piece without a set counts
        assert first["relic"] == {"typeId": 3, "rank": 5, "level": 15} and first["blessing"] == 1202
        assert first["power"] == 243124 and first["awakened"] == 6 and first["empower"] == 1
        assert first["skills"] == [{"typeId": 85701, "level": 2}] and first["masteries"] == [500213, 500313]
        assert first["stats"]["total"][:4] == [69266.0, 2823.0, 5971.0, 334.0] and "base" not in first["stats"]
        assert second["statsReason"] == "opening_stats_unavailable" and "relic" not in second
        assert team_report(Path(temporary) / "missing.json", stats, source="battle") is None
