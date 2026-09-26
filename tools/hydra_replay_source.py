"""Validate and save one agent-published Hydra replay source.

Only published JSON slots are read. A source is saved after its original
BattleSetup matches the opening native decision and the current game account.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from extract_hydra_playerprefs import summarize_hydra_setup
from strategy_storage import atomic_write_bytes, atomic_write_json


MAX_SLOT_BYTES = 2_097_151  # The shared slot has 2 MiB and reserves a NUL byte.
MAX_SETTINGS_BYTES = 1_048_576  # The isolated original JSON converter's input bound.
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000
SETUPS_SOURCE = "BattleContext.Setup.JsonMain.ToJsonStr"
SETTINGS_SOURCE = "SharedModelManager.GameParameters.BattleSettings.JsonMain.ToJsonStr"
GUID_HEX = re.compile(r"[0-9a-f]{32}\Z")


class ReplaySourceError(ValueError):
    """A stable, non-sensitive reason for refusing a replay source."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ReplaySourceError(reason)


def _integer(value: object, *, positive: bool = False) -> bool:
    return type(value) is int and (not positive or value > 0)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReplaySourceError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ReplaySourceError("non_finite_json_number")


def _parse_json(text: str, reason: str) -> Any:
    try:
        result = json.loads(text, object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
    except ReplaySourceError:
        raise
    except (ValueError, RecursionError) as error:
        raise ReplaySourceError(reason) from error
    stack = [(result, 0)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        _require(depth <= MAX_JSON_DEPTH, "json_depth_limit")
        _require(nodes <= MAX_JSON_NODES, "json_node_limit")
        if isinstance(node, dict):
            stack.extend((value, depth + 1) for value in node.values())
        elif isinstance(node, list):
            stack.extend((value, depth + 1) for value in node)
    return result


def _bounded_text(value: object, *, reason: str) -> tuple[str, bytes]:
    _require(isinstance(value, str) and bool(value), reason)
    assert isinstance(value, str)
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as error:
        raise ReplaySourceError(reason) from error
    _require(len(encoded) <= MAX_SLOT_BYTES, "source_json_size_limit")
    return value, encoded


def _opening_identity(decision: object) -> dict[str, Any]:
    _require(isinstance(decision, dict), "opening_decision_missing")
    assert isinstance(decision, dict)
    battle, rng = decision.get("battle"), decision.get("battleRandom")
    _require(decision.get("type") == "decision_state"
             and decision.get("bossMode") == "hydra"
             and isinstance(battle, dict)
             and battle.get("hydraBattle") is True
             and battle.get("kindId") == 5
             and battle.get("finished") is False,
             "opening_decision_not_hydra")
    _require(_integer(decision.get("battleGeneration"), positive=True),
             "opening_generation_missing")
    _require(_integer(battle.get("turn")) and _integer(battle.get("playerTurnCount"))
             and battle["playerTurnCount"] in (0, 1),
             "opening_decision_not_at_start")
    _require(isinstance(rng, dict) and rng.get("schema") == 1
             and rng.get("available") is True
             and rng.get("source") == "BattleState.Random_fields"
             and rng.get("readStatus") == "stable_double_read"
             and _integer(rng.get("turn"))
             and rng.get("turn") == battle.get("turn")
             and rng.get("playerTurnCount") == battle.get("playerTurnCount")
             and rng.get("seedAvailable") is True
             and rng.get("battleSetupIdAvailable") is True,
             "opening_random_identity_missing")
    assert isinstance(rng, dict)
    words = rng.get("words")
    _require(isinstance(words, list) and len(words) == 4
             and all(_integer(word) and 0 <= word <= 0xFFFFFFFF for word in words)
             and any(words), "opening_random_identity_missing")
    _require(_integer(rng.get("seed")) and isinstance(rng.get("battleSetupId"), str)
             and GUID_HEX.fullmatch(rng["battleSetupId"]) is not None,
             "opening_random_identity_missing")
    selection = decision.get("hydraStartSelection")
    _require(isinstance(selection, dict)
             and _integer(selection.get("stageId"), positive=True),
             "opening_start_selection_missing")
    assert isinstance(selection, dict)
    selected_ids, selected_types = selection.get("heroIds"), selection.get("heroTypeIds")
    _require(isinstance(selected_ids, list) and len(selected_ids) == 6
             and all(_integer(item, positive=True) for item in selected_ids)
             and len(set(selected_ids)) == 6
             and isinstance(selected_types, list) and len(selected_types) == 6
             and all(_integer(item, positive=True) for item in selected_types),
             "opening_start_selection_incomplete")
    for key in ("hydraStageId", "chimeraStageId"):
        if key in decision:
            _require(decision[key] == selection["stageId"],
                     "opening_stage_conflicting")
    heroes = decision.get("heroes")
    _require(isinstance(heroes, list) and len(heroes) == 6,
             "opening_team_incomplete")
    assert isinstance(heroes, list)
    team: list[tuple[int, int]] = []
    positions: list[tuple[int, int, int]] = []
    for hero in heroes:
        _require(isinstance(hero, dict) and _integer(hero.get("id"))
                 and hero["id"] >= 0
                 and _integer(hero.get("typeId"), positive=True)
                 and hero.get("modelFound") is True,
                 "opening_team_incomplete")
        assert isinstance(hero, dict)
        team.append((hero["id"], hero["typeId"]))
        if _integer(hero.get("battlePosition")):
            positions.append((hero["battlePosition"], hero["id"], hero["typeId"]))
    _require(len({hero_id for hero_id, _ in team}) == 6, "opening_team_duplicate_hero")
    if len(positions) == 6 and {item[0] for item in positions} in (set(range(6)), set(range(1, 7))):
        native_types = [type_id for _, _, type_id in sorted(positions)]
    else:
        raise ReplaySourceError("opening_team_positions_missing")
    _require(native_types == selected_types,
             "opening_native_team_differs_from_selection")
    return {"battleGeneration": decision["battleGeneration"],
            "battleSetupId": rng["battleSetupId"], "seed": rng["seed"],
            "stageId": selection["stageId"],
            "selectedHeroIds": selected_ids, "selectedHeroTypeIds": selected_types,
            "decisionObservedAtTick": decision.get("observedAtTick")}


def validate_replay_source(source: object, opening: object, account: object,
                           expected_account_name: str) -> tuple[dict[str, Any], bytes, bytes]:
    """Return provenance and original UTF-8 inputs, or a refusal reason."""
    _require(isinstance(source, dict) and source.get("type") == "hydra_replay_source"
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
    for key in ("battleGeneration", "battleSetupId", "seed", "stageId"):
        _require(source[key] == native[key], f"source_{key}_differs_from_opening")

    setups_text, setups_bytes = _bounded_text(source.get("battleSetupsJson"),
                                              reason="battle_setups_json_missing")
    settings_text, settings_bytes = _bounded_text(source.get("battleSettingsJson"),
                                                  reason="battle_settings_json_missing")
    _require(len(setups_bytes) + len(settings_bytes) <= MAX_SLOT_BYTES,
             "source_json_size_limit")
    _require(len(settings_bytes) <= MAX_SETTINGS_BYTES,
             "battle_settings_size_limit")
    setups = _parse_json(setups_text, "battle_setups_json_invalid")
    settings = _parse_json(settings_text, "battle_settings_json_invalid")
    _require(isinstance(setups, list) and len(setups) == 1
             and isinstance(setups[0], dict), "battle_setups_not_single_list")
    _require(isinstance(settings, dict) and bool(settings),
             "battle_settings_not_object")
    assert isinstance(settings, dict)
    for key, lower, upper in (("ActiveEngineVersion", 1, 1_000_000),
                              ("WarmupBattleRandomCount", 0, 100_000),
                              ("MaxTurnsInBattle", 1, 1_000_000)):
        value = settings.get(key)
        _require(_integer(value) and lower <= value <= upper,
                 f"battle_settings_{key}_invalid")
    try:
        summary = summarize_hydra_setup(setups[0])
    except ValueError as error:
        raise ReplaySourceError("battle_setup_invalid") from error
    _require(summary is not None, "battle_setup_not_hydra")
    assert summary is not None
    _require(source["battleSetupId"] in summary["battleSetupIdForms"],
             "battle_setup_id_mismatch")
    _require(summary["seed"] == source["seed"], "battle_setup_seed_mismatch")
    _require(summary["stageId"] == source["stageId"], "battle_setup_stage_mismatch")
    _require(summary["teamOwnerId"] == account["userId"],
             "battle_setup_owner_mismatch")
    _require(summary["inventoryHeroIds"] == native["selectedHeroIds"]
             and summary["heroTypeIds"] == native["selectedHeroTypeIds"],
             "battle_setup_team_mismatch")
    provenance = {
        "schema": 1, "type": "verified_hydra_replay_source",
        "battleGeneration": source["battleGeneration"],
        "battleSetupId": source["battleSetupId"], "seed": source["seed"],
        "stageId": source["stageId"], "observedAtTick": source["observedAtTick"],
        "decisionObservedAtTick": native["decisionObservedAtTick"],
        "accountName": expected_account_name, "accountUserId": account["userId"],
        "battleSetups": {"file": "battle-setup.json", "source": SETUPS_SOURCE,
                         "bytes": len(setups_bytes),
                         "sha256": hashlib.sha256(setups_bytes).hexdigest()},
        "battleSettings": {"file": "battle-settings.json", "source": SETTINGS_SOURCE,
                           "bytes": len(settings_bytes),
                           "sha256": hashlib.sha256(settings_bytes).hexdigest()},
        "teamHeroIds": summary["inventoryHeroIds"],
        "teamHeroTypeIds": summary["heroTypeIds"],
        "activeEngineVersion": settings["ActiveEngineVersion"],
        "warmupBattleRandomCount": settings["WarmupBattleRandomCount"],
        "maxTurnsInBattle": settings["MaxTurnsInBattle"],
    }
    return provenance, setups_bytes, settings_bytes


class ReplaySourceCollector:
    """Keep the first opening decision and persist one verified slot publication."""

    def __init__(self, capture_directory: Path, expected_account_name: str):
        self.output = capture_directory / "replay-source"
        self.expected_account_name = expected_account_name
        self.account: dict[str, Any] | None = None
        self.opening: dict[str, Any] | None = None
        self.pending: dict[str, Any] | None = None
        self.sequence: int | None = None
        self.terminal = False
        self.status: dict[str, Any] = {"status": "not_seen", "captured": False}

    def _reject(self, reason: str) -> None:
        self.terminal = True
        self.status = {"status": "rejected", "captured": False, "reason": reason}

    def reject(self, reason: str) -> None:
        if not self.terminal:
            self._reject(reason)

    def observe_account(self, value: object) -> None:
        if self.terminal:
            return
        if not isinstance(value, dict):
            return
        if (value.get("accountName") != self.expected_account_name
                or not _integer(value.get("userId"), positive=True)):
            if self.account is not None:
                self._reject("game_account_changed")
            return
        if self.account is not None and (self.account.get("userId") != value.get("userId")
                                         or self.account.get("accountName") != value.get("accountName")):
            self._reject("game_account_changed")
            return
        self.account = value
        self._try_save()

    def observe_decision(self, value: object) -> None:
        if self.terminal or not isinstance(value, dict) or value.get("bossMode") != "hydra":
            return
        battle = value.get("battle")
        generation = value.get("battleGeneration")
        if (isinstance(battle, dict) and battle.get("playerTurnCount") in (0, 1)
                and _integer(generation, positive=True)):
            # The first decision can be published before all six native hero
            # models and their positions are present. Keep the first complete
            # opening identity, not merely the first early publication.
            try:
                _opening_identity(value)
            except ReplaySourceError:
                pass
            else:
                if self.opening is None or self.opening.get("battleGeneration") != generation:
                    self.opening = value
        self._try_save()

    def observe_slot(self, sequence: int, payload: str | None) -> None:
        if self.terminal:
            return
        if not payload:
            if sequence > 0:
                self._reject("replay_slot_empty")
            return
        self.sequence = sequence
        try:
            _require(len(payload.encode("utf-8")) <= MAX_SLOT_BYTES, "replay_slot_size_limit")
            value = _parse_json(payload, "replay_slot_json_invalid")
            _require(isinstance(value, dict), "replay_slot_not_object")
            if value.get("type") == "ipc_error":
                raise ReplaySourceError("replay_slot_overflow")
            _require(value.get("type") == "hydra_replay_source"
                     and type(value.get("schema")) is int and value["schema"] == 1,
                     "source_schema_or_status_invalid")
            _require(self.pending is None or value == self.pending,
                     "conflicting_source_publications")
            if value.get("status") == "unavailable":
                reason = value.get("reason")
                self.terminal = True
                self.status = {"status": "source_unavailable", "captured": False,
                               "reason": reason if isinstance(reason, str) and 0 < len(reason) <= 256
                               else "agent_source_unavailable"}
                return
            _require(value.get("status") == "captured", "source_schema_or_status_invalid")
            if self.pending is None:
                # Keep the exact bounded publication even if a later identity
                # check fails. It can be inspected offline without another
                # live battle; only the separately verified files are usable
                # as replay inputs.
                atomic_write_bytes(self.output.parent / "replay-source-candidate.json",
                                   payload.encode("utf-8"))
            self.pending = value
            self.status = {"status": "waiting_for_opening_identity", "captured": False,
                           "battleGeneration": value.get("battleGeneration")}
            self._try_save()
        except (ReplaySourceError, UnicodeError) as error:
            self._reject(str(error) if isinstance(error, ReplaySourceError)
                         else "replay_slot_utf8_invalid")
        except OSError as error:
            self._reject(f"save_error_{type(error).__name__}")

    def _try_save(self) -> None:
        if self.terminal or self.pending is None or self.account is None or self.opening is None:
            return
        if self.pending.get("battleGeneration") != self.opening.get("battleGeneration"):
            return
        try:
            provenance, setups, settings = validate_replay_source(
                self.pending, self.opening, self.account, self.expected_account_name)
            self.output.mkdir(parents=False, exist_ok=False)
            atomic_write_bytes(self.output / "battle-setup.json", setups)
            atomic_write_bytes(self.output / "battle-settings.json", settings)
            atomic_write_json(self.output / "capture-provenance.json", provenance)
        except ReplaySourceError as error:
            self._reject(str(error))
            return
        except OSError as error:
            self._reject(f"save_error_{type(error).__name__}")
            return
        self.terminal = True
        self.pending = None
        self.status = {"status": "verified_replay_source_saved", "captured": True,
                       "battleGeneration": provenance["battleGeneration"],
                       "battleSetupId": provenance["battleSetupId"],
                       "outputDirectory": str(self.output),
                       "battleSetupsBytes": provenance["battleSetups"]["bytes"],
                       "battleSettingsBytes": provenance["battleSettings"]["bytes"]}

    def finalize(self) -> None:
        if self.terminal or self.pending is None:
            return
        if self.account is None:
            self._reject("game_account_identity_missing")
        else:
            self._reject("matching_opening_decision_unavailable")
