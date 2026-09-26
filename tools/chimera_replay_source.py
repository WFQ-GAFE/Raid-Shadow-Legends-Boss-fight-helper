"""Validate one agent-published Chimera battle-start source.

The agent serializes the active battle's own BattleSetup and the login's
BattleSettings with the game's JsonMain (the same capture as Hydra, KindId 8).
A source is accepted only when it matches the opening native decision (battle
generation, RNG seed and setup id), the current game account and the five
heroes on the field. Nothing here reads game memory.
"""
from __future__ import annotations

import hashlib
from typing import Any

from extract_hydra_playerprefs import _field, _guid_forms, _positive_integer
from hydra_replay_source import (GUID_HEX, MAX_SETTINGS_BYTES, MAX_SLOT_BYTES,
                                 SETTINGS_SOURCE, SETUPS_SOURCE, ReplaySourceError,
                                 _bounded_text, _integer, _parse_json, _require)


CHIMERA_KIND_ID = 8
TEAM_SIZE = 5


def summarize_chimera_setup(model: dict) -> dict[str, Any] | None:
    """Identity of one Chimera BattleSetup (compact or long JSON names)."""
    if _field(model, "k", "KindId") != CHIMERA_KIND_ID:
        return None
    seed = _field(model, "r", "RandomSeed")
    if type(seed) is not int:
        raise ValueError("Chimera BattleSetup.RandomSeed is missing")
    guid_forms = _guid_forms(_field(model, "z", "Id"))
    stage = _positive_integer(_field(model, "i", "StageId"), "Chimera StageId")
    team = _field(model, "f", "FirstTeam")
    if not isinstance(team, dict):
        raise ValueError("Chimera FirstTeam is missing")
    owner = _positive_integer(_field(team, "i", "TeamOwnerId"), "Chimera TeamOwnerId")
    slots = _field(team, "h", "HeroSlotSetups")
    if (not isinstance(slots, list) or len(slots) != TEAM_SIZE
            or not all(isinstance(slot, dict) for slot in slots)):
        raise ValueError("Chimera FirstTeam does not have exactly five HeroSlotSetups")
    ordered = []
    for slot in slots:
        number = _field(slot, "t", "Slot")
        if type(number) is not int or not 0 <= number <= TEAM_SIZE:
            raise ValueError("Chimera hero Slot is invalid")
        ordered.append((number,
                        _positive_integer(_field(slot, "h", "InventoryHeroId"), "InventoryHeroId"),
                        _positive_integer(_field(slot, "i", "HeroTypeId"), "HeroTypeId")))
    ordered.sort()
    if [item[0] for item in ordered] not in (list(range(TEAM_SIZE)), list(range(1, TEAM_SIZE + 1))):
        raise ValueError("Chimera hero Slots are duplicated or incomplete")
    enemy = _field(model, "s", "SecondTeam")
    enemy_slots = _field(enemy, "h", "HeroSlotSetups") if isinstance(enemy, dict) else None
    if not isinstance(enemy_slots, list) or len(enemy_slots) != 1 or not isinstance(enemy_slots[0], dict):
        raise ValueError("Chimera SecondTeam does not have exactly one boss")
    boss = enemy_slots[0]
    return {
        "battleSetupIdForms": sorted(guid_forms),
        "seed": seed,
        "stageId": stage,
        "teamOwnerId": owner,
        "inventoryHeroIds": [item[1] for item in ordered],
        "heroTypeIds": [item[2] for item in ordered],
        "bossHeroTypeId": _positive_integer(_field(boss, "i", "HeroTypeId"), "Chimera boss HeroTypeId"),
        "bossLevel": _positive_integer(_field(boss, "l", "Level"), "Chimera boss Level"),
    }


def _opening_identity(decision: object) -> dict[str, Any]:
    _require(isinstance(decision, dict), "opening_decision_missing")
    assert isinstance(decision, dict)
    battle, rng = decision.get("battle"), decision.get("battleRandom")
    _require(decision.get("type") == "decision_state"
             and decision.get("bossMode") == "chimera"
             and isinstance(battle, dict)
             and battle.get("hydraBattle") is not True
             and battle.get("kindId") == CHIMERA_KIND_ID
             and battle.get("finished") is False,
             "opening_decision_not_chimera")
    _require(_integer(decision.get("battleGeneration"), positive=True),
             "opening_generation_missing")
    _require(_integer(battle.get("turn")) and _integer(battle.get("playerTurnCount"))
             and battle["playerTurnCount"] in (0, 1),
             "opening_decision_not_at_start")
    _require(isinstance(rng, dict) and rng.get("schema") == 1
             and rng.get("available") is True
             and rng.get("source") == "BattleState.Random_fields"
             and rng.get("readStatus") == "stable_double_read"
             and rng.get("turn") == battle.get("turn")
             and rng.get("playerTurnCount") == battle.get("playerTurnCount")
             and rng.get("seedAvailable") is True
             and rng.get("battleSetupIdAvailable") is True
             and _integer(rng.get("seed"))
             and isinstance(rng.get("battleSetupId"), str)
             and GUID_HEX.fullmatch(rng["battleSetupId"]) is not None,
             "opening_random_identity_missing")
    heroes = decision.get("heroes")
    _require(isinstance(heroes, list) and len(heroes) == TEAM_SIZE
             and all(isinstance(hero, dict) and _integer(hero.get("typeId"), positive=True)
                     and hero.get("modelFound") is True for hero in heroes),
             "opening_team_incomplete")
    assert isinstance(heroes, list)
    positioned = [(hero["battlePosition"], hero["typeId"]) for hero in heroes
                  if _integer(hero.get("battlePosition"))]
    ordered_types = ([type_id for _, type_id in sorted(positioned)]
                     if len(positioned) == TEAM_SIZE
                     and len({position for position, _ in positioned}) == TEAM_SIZE
                     else None)
    selection = decision.get("chimeraStartSelection")
    selected_ids: list[int] | None = None
    if selection is not None:
        _require(isinstance(selection, dict)
                 and isinstance(selection.get("heroIds"), list)
                 and len(selection["heroIds"]) == TEAM_SIZE
                 and all(_integer(item, positive=True) for item in selection["heroIds"])
                 and len(set(selection["heroIds"])) == TEAM_SIZE
                 and isinstance(selection.get("heroTypeIds"), list)
                 and len(selection["heroTypeIds"]) == TEAM_SIZE,
                 "opening_start_selection_incomplete")
        assert isinstance(selection, dict)
        _require(sorted(selection["heroTypeIds"]) == sorted(hero["typeId"] for hero in heroes),
                 "opening_native_team_differs_from_selection")
        selected_ids = list(selection["heroIds"])
    return {"battleGeneration": decision["battleGeneration"],
            "battleSetupId": rng["battleSetupId"], "seed": rng["seed"],
            "stageId": decision.get("chimeraStageId"),
            "heroTypeIds": sorted(hero["typeId"] for hero in heroes),
            "orderedHeroTypeIds": ordered_types,
            "selectedHeroIds": selected_ids,
            "openingTurn": battle["turn"], "openingPlayerTurnCount": battle["playerTurnCount"],
            "openingRandomWords": rng.get("words"),
            "decisionObservedAtTick": decision.get("observedAtTick")}


def validate_chimera_replay_source(source: object, opening: object, account: object,
                                   expected_account_name: str) -> tuple[dict[str, Any], bytes, bytes]:
    """Return provenance and the original UTF-8 inputs, or raise a stable reason."""
    _require(isinstance(source, dict) and source.get("type") == "chimera_replay_source"
             and type(source.get("schema")) is int and source["schema"] == 1
             and source.get("status") == "captured", "source_schema_or_status_invalid")
    assert isinstance(source, dict)
    _require(_integer(source.get("battleGeneration"), positive=True)
             and isinstance(source.get("battleSetupId"), str)
             and GUID_HEX.fullmatch(source["battleSetupId"]) is not None
             and _integer(source.get("seed"))
             and -0x80000000 <= source["seed"] <= 0x7FFFFFFF
             and _integer(source.get("stageId"), positive=True)
             and _integer(source.get("observedAtTick"))
             and source["observedAtTick"] >= 0,
             "source_identity_invalid")
    _require(source.get("battleSetupsSource") == SETUPS_SOURCE
             and source.get("battleSettingsSource") == SETTINGS_SOURCE,
             "source_origin_invalid")
    _require(isinstance(account, dict) and account.get("type") == "account_state"
             and account.get("accountName") == expected_account_name
             and _integer(account.get("userId"), positive=True),
             "game_account_identity_missing")
    assert isinstance(account, dict)
    native = _opening_identity(opening)
    for key in ("battleGeneration", "battleSetupId", "seed"):
        _require(source[key] == native[key], f"source_{key}_differs_from_opening")
    if native["stageId"] is not None:
        _require(source["stageId"] == native["stageId"], "source_stageId_differs_from_opening")

    setups_text, setups_bytes = _bounded_text(source.get("battleSetupsJson"),
                                              reason="battle_setups_json_missing")
    settings_text, settings_bytes = _bounded_text(source.get("battleSettingsJson"),
                                                  reason="battle_settings_json_missing")
    _require(len(setups_bytes) + len(settings_bytes) <= MAX_SLOT_BYTES, "source_json_size_limit")
    _require(len(settings_bytes) <= MAX_SETTINGS_BYTES, "battle_settings_size_limit")
    setups = _parse_json(setups_text, "battle_setups_json_invalid")
    settings = _parse_json(settings_text, "battle_settings_json_invalid")
    _require(isinstance(setups, list) and len(setups) == 1 and isinstance(setups[0], dict),
             "battle_setups_not_single_list")
    _require(isinstance(settings, dict) and bool(settings), "battle_settings_not_object")
    assert isinstance(settings, dict)
    for key, lower, upper in (("ActiveEngineVersion", 1, 1_000_000),
                              ("WarmupBattleRandomCount", 0, 100_000),
                              ("MaxTurnsInBattle", 1, 1_000_000)):
        value = settings.get(key)
        _require(_integer(value) and lower <= value <= upper, f"battle_settings_{key}_invalid")
    try:
        summary = summarize_chimera_setup(setups[0])
    except ValueError as error:
        raise ReplaySourceError("battle_setup_invalid") from error
    _require(summary is not None, "battle_setup_not_chimera")
    assert summary is not None
    _require(source["battleSetupId"] in summary["battleSetupIdForms"], "battle_setup_id_mismatch")
    _require(summary["seed"] == source["seed"], "battle_setup_seed_mismatch")
    _require(summary["stageId"] == source["stageId"], "battle_setup_stage_mismatch")
    _require(summary["teamOwnerId"] == account["userId"], "battle_setup_owner_mismatch")
    _require(sorted(summary["heroTypeIds"]) == native["heroTypeIds"], "battle_setup_team_mismatch")
    if native["orderedHeroTypeIds"] is not None:
        _require(summary["heroTypeIds"] == native["orderedHeroTypeIds"], "battle_setup_team_order_mismatch")
    if native["selectedHeroIds"] is not None:
        _require(sorted(summary["inventoryHeroIds"]) == sorted(native["selectedHeroIds"]),
                 "battle_setup_team_instances_mismatch")
    provenance = {
        "schema": 1, "type": "verified_chimera_replay_source",
        "battleGeneration": source["battleGeneration"],
        "battleSetupId": source["battleSetupId"], "seed": source["seed"],
        "stageId": source["stageId"], "observedAtTick": source["observedAtTick"],
        "decisionObservedAtTick": native["decisionObservedAtTick"],
        "openingTurn": native["openingTurn"],
        "openingPlayerTurnCount": native["openingPlayerTurnCount"],
        "openingRandomWords": native["openingRandomWords"],
        "accountName": expected_account_name, "accountUserId": account["userId"],
        "battleSetups": {"file": "battle-setup.json", "source": SETUPS_SOURCE,
                         "bytes": len(setups_bytes),
                         "sha256": hashlib.sha256(setups_bytes).hexdigest()},
        "battleSettings": {"file": "battle-settings.json", "source": SETTINGS_SOURCE,
                           "bytes": len(settings_bytes),
                           "sha256": hashlib.sha256(settings_bytes).hexdigest()},
        "teamHeroIds": summary["inventoryHeroIds"],
        "teamHeroTypeIds": summary["heroTypeIds"],
        "bossHeroTypeId": summary["bossHeroTypeId"],
        "bossLevel": summary["bossLevel"],
        "activeEngineVersion": settings["ActiveEngineVersion"],
        "warmupBattleRandomCount": settings["WarmupBattleRandomCount"],
        "maxTurnsInBattle": settings["MaxTurnsInBattle"],
    }
    return provenance, setups_bytes, settings_bytes
