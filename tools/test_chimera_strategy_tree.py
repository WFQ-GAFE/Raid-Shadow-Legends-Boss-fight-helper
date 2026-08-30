from __future__ import annotations

import copy
import io
import json
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from chimera_controller import (
    GamePaused,
    SkillCapabilityMemory,
    annotate_chimera_form_first_turn,
    annotate_team_positions,
    archive_rotation_catalog_if_changed,
    buff_count,
    debuff_count,
    effect_target_has_capacity,
    evaluate,
    evaluate_objectives,
    is_battle_decision_state,
    load_trial_recipes,
    recoverable_command_rejection,
    matches,
    next_chimera_form,
    pending_mythic_followup_decision,
    process_state,
    require_takeover_active,
    safety_reason,
    select_target,
    turns_until_form_change,
    trial_recipe_for_id,
    validate_strategy_config,
    wait_for_turn_advance,
)


def sample_state() -> dict:
    return {
        "pid": 1,
        "battle": {
            "areaTypeId": 13,
            "kindId": 8,
            "round": 1,
            "turn": 7,
            "playerTurnCount": 3,
            "currentDamage": 1_500_000,
            "currentCompetitionPoints": 42,
        },
        "activeHeroId": 0,
        "activeHeroTypeId": 8896,
        "activeHeroTurnCount": 2,
        "activeHeroFormIndex": 0,
        "activeHeroIsMetamorph": True,
        "activeHeroIsTransformed": False,
        "skills": [
            {
                "slot": 1,
                "skillId": 0,
                "typeId": 88961,
                "ready": True,
                "validTargetIds": [5],
            },
            {
                "slot": 2,
                "skillId": 1,
                "typeId": 88962,
                "ready": True,
                "validTargetIds": [0],
            },
            {
                "slot": 4,
                "skillId": 3,
                "typeId": 88964,
                "ready": True,
                "passive": False,
                "blocked": False,
                "validTargetIds": [0],
            },
        ],
        "heroes": [
            {
                "id": 0,
                "typeId": 8896,
                "healthPct": 80,
                "effects": [],
                "challenges": [],
            },
            {
                "id": 1,
                "typeId": 12345,
                "healthPct": 65,
                "dead": False,
                "effects": [
                    {
                        "effectKind": "IncreaseDefense",
                        "effectKindId": 101,
                        "turnsLeft": 3,
                    },
                    {
                        "effectKind": "Shield",
                        "effectKindId": 2004,
                        "turnsLeft": 2,
                    },
                ],
                "challenges": [],
            },
        ],
        "bosses": [
            {
                "id": 5,
                "typeId": 26866,
                "healthPct": 90,
                "effects": [
                    {
                        "effectTypeId": 490,
                        "effectKindId": 490,
                        "effectKind": "Fear",
                        "turnsLeft": 2,
                    }
                ],
                "challenges": [
                    {
                        "id": 8000501,
                        "started": True,
                        "completed": True,
                        "progressRatio": 1.0,
                    },
                    {
                        "id": 8000502,
                        "started": True,
                        "completed": False,
                        "possible": False,
                        "progressRatio": 0.4,
                    },
                ],
            }
        ],
        "chimera": {
            "id": 5,
            "currentForm": "Ram",
            "turnCount": 7,
        },
        "allianceChimeraDifficultyId": 5,
        "chimeraStageId": 1302,
        "rotationIdentity": {
            "catalogFingerprint": "fnv1a64:test-catalog",
            "trialDefinitionFingerprint": "fnv1a64:test-definitions",
            "rewardRotationFingerprint": "fnv1a64:test-rewards",
            "attributeRotationFingerprint": "fnv1a64:test-attributes",
            "metadata": {
                "turnsBetweenForms": 5,
                "formSequence": ["Ultimate", "Ram", "Lion", "Snake"],
            },
        },
        "trialCatalog": {
            "available": True,
            "difficulties": [
                {
                    "difficultyId": 5,
                    "stageIds": [1301, 1302, 1303, 1304],
                    "trials": [
                        {
                            "id": 8000501,
                            "name": "stable-trial",
                            "reward": {
                                "rollCount": 1,
                                "entries": [{"typeId": 1, "minCount": 1}],
                            },
                        }
                    ],
                }
            ],
        },
    }


class FakeIpc:
    def __init__(self, state: dict) -> None:
        self.state = state

    def rotation_catalog(self) -> dict:
        return {
            "identity": self.state["rotationIdentity"],
            "catalog": self.state["trialCatalog"],
        }

    def lifecycle(self) -> dict:
        return {
            "screen": "team_selection",
            "selection": {"stageId": self.state["chimeraStageId"]},
        }


class ResultIpc:
    def lifecycle(self) -> dict:
        return {
            "screen": "result",
            "takeoverState": "active",
            "sessionId": 123,
        }

    def decision(self) -> dict:
        raise AssertionError("result screen must stop before reading another decision")


class ActiveIpc:
    def lifecycle(self) -> dict:
        return {
            "screen": "battle",
            "takeoverState": "active",
            "sessionId": 123,
        }


class GamePausedIpc:
    def lifecycle(self) -> dict:
        return {
            "screen": "battle",
            "takeoverState": "interrupted",
            "sessionId": 123,
            "reason": "game_pause_clicked",
            "inputSource": "battle_hud_pause_button",
        }


def main() -> int:
    try:
        require_takeover_active(GamePausedIpc(), 123)
    except GamePaused:
        pass
    else:
        raise AssertionError("game pause did not end the takeover session")
    for reason in ("guard_failed", "battle_guard_changed", "duplicate_turn"):
        assert recoverable_command_rejection(
            {"status": "rejected", "reason": reason}
        )
    for acknowledgement in (
        None,
        {"status": "submitted"},
        {"status": "rejected", "reason": "account_changed"},
        {"status": "rejected", "reason": "invalid_action"},
    ):
        assert not recoverable_command_rejection(acknowledgement)
    state = sample_state()
    positioned = copy.deepcopy(state)
    positioned["heroes"] = [
        {"id": index, "typeId": type_id, "name": f"hero-{index}", "healthPct": 100}
        for index, type_id in enumerate((8896, 12345, 23456, 34567, 45678))
    ]
    positioned["skills"][1]["validTargetIds"] = [0, 1, 2, 3, 4]
    annotate_team_positions(
        positioned,
        {
            "screen": "battle",
            "battle": {
                "heroIds": [101, 102, 103, 104, 105],
                "heroTypeIds": [8896, 12345, 23456, 34567, 45678],
            },
        },
    )
    assert [hero.get("teamPosition") for hero in positioned["heroes"]] == [1, 2, 3, 4, 5]
    position_rule = {
        "rules": [
            {
                "name": "position-two",
                "when": {"activeHeroTypeId": [8890, 8896]},
                "action": {
                    "type": "cast",
                    "skillTypeId": 88962,
                    "target": {"type": "allyPosition", "position": 2},
                },
            }
        ]
    }
    positioned_decision = evaluate(position_rule, positioned)
    assert positioned_decision is not None
    assert positioned_decision.target_id == 1
    assert positioned_decision.target_label.startswith("2号位")
    strict_and_default = {
        "rules": [
            {
                "name": "strict-reserved-s2",
                "when": {
                    "form": "Ram",
                    "activeHeroTypeId": [8890, 8896],
                    "bossHasEffect": {"kind": "Poison"},
                },
                "action": {
                    "type": "cast",
                    "skillTypeId": 88962,
                    "target": "self",
                },
            },
            {
                "name": "default-priority",
                "when": {
                    "form": "Ram",
                    "activeHeroTypeId": [8890, 8896],
                },
                "action": {
                    "type": "defaultSkillPriority",
                    "prioritySkills": [
                        {"skillTypeId": 88962, "skillSlot": 2},
                        {"skillTypeId": 88961, "skillSlot": 1},
                    ],
                    "blockedSkillTypeIds": [],
                },
            },
        ]
    }
    default_decision = evaluate(strict_and_default, positioned)
    assert default_decision is not None
    assert default_decision.rule == "default-priority"
    assert default_decision.skill["typeId"] == 88961
    strict_ready = copy.deepcopy(strict_and_default)
    strict_ready["rules"][0]["when"]["bossHasEffect"] = {"kind": "Fear"}
    strict_decision = evaluate(strict_ready, positioned)
    assert strict_decision is not None
    assert strict_decision.rule == "strict-reserved-s2"
    assert strict_decision.skill["typeId"] == 88962
    explicit_opener = copy.deepcopy(strict_ready)
    explicit_opener["rules"][1]["action"]["firstTurnSkill"] = {
        "skillTypeId": 88961,
        "skillSlot": 1,
        "target": "boss",
    }
    explicit_opener["rules"][1]["action"]["blockedSkillTypeIds"] = [88961]
    first_hero_turn = copy.deepcopy(positioned)
    first_hero_turn["activeHeroTurnCount"] = 0
    opener_decision = evaluate(explicit_opener, first_hero_turn)
    assert opener_decision is not None
    assert opener_decision.rule.endswith("首回合技能")
    assert opener_decision.skill["typeId"] == 88961
    first_hero_turn["activeHeroTurnCount"] = 1
    one_based_opener_decision = evaluate(explicit_opener, first_hero_turn)
    assert one_based_opener_decision is not None
    assert one_based_opener_decision.rule.endswith("首回合技能")
    assert one_based_opener_decision.skill["typeId"] == 88961
    first_hero_turn["activeHeroTurnCount"] = 2
    later_hero_turn_decision = evaluate(explicit_opener, first_hero_turn)
    assert later_hero_turn_decision is not None
    assert later_hero_turn_decision.rule == "strict-reserved-s2"
    validate_strategy_config({"mode": "execute", **explicit_opener})
    strict_listed_after_default = copy.deepcopy(strict_ready)
    strict_listed_after_default["rules"].reverse()
    reordered_strict_decision = evaluate(strict_listed_after_default, positioned)
    assert reordered_strict_decision is not None
    assert reordered_strict_decision.rule == "strict-reserved-s2"
    reserved_listed_after_default = copy.deepcopy(strict_and_default)
    reserved_listed_after_default["rules"].reverse()
    reordered_default_decision = evaluate(reserved_listed_after_default, positioned)
    assert reordered_default_decision is not None
    assert reordered_default_decision.skill["typeId"] == 88961
    form_switch_a1_config = {
        "rules": [
            {
                "name": "default-a1-while-waiting",
                "when": {
                    "activeHeroTypeId": [8896],
                    "form": ["Ultimate"],
                },
                "action": {
                    "type": "defaultSkillPriority",
                    "prioritySkills": [
                        {
                            "skillTypeId": 88961,
                            "skillSlot": 1,
                            "target": {"type": "boss"},
                        }
                    ],
                    "blockedSkillTypeIds": [],
                },
            },
            {
                "name": "a1-on-form-switch-boundary",
                "when": {
                    "activeHeroTypeId": [8896],
                    "form": ["Ultimate"],
                    "nextForm": "Snake",
                    "turnsUntilFormChangeAtMost": 0,
                },
                "action": {
                    "type": "cast",
                    "skillTypeId": 88961,
                    "skillSlot": 1,
                    "target": {"type": "boss"},
                },
            },
        ]
    }
    before_switch = copy.deepcopy(positioned)
    before_switch["chimera"].update({"currentForm": "Ultimate", "turnCount": 21})
    before_switch["skills"] = [copy.deepcopy(positioned["skills"][0])]
    assert turns_until_form_change(before_switch) == 4
    assert not matches(form_switch_a1_config["rules"][1]["when"], before_switch)
    waiting_decision = evaluate(form_switch_a1_config, before_switch)
    assert waiting_decision is not None
    assert waiting_decision.rule == "default-a1-while-waiting"
    switch_boundary = copy.deepcopy(before_switch)
    switch_boundary["chimera"]["turnCount"] = 25
    assert turns_until_form_change(switch_boundary) == 0
    assert matches(form_switch_a1_config["rules"][1]["when"], switch_boundary)
    boundary_decision = evaluate(form_switch_a1_config, switch_boundary)
    assert boundary_decision is not None
    assert boundary_decision.rule == "a1-on-form-switch-boundary"
    unreserved_default = {"rules": [copy.deepcopy(strict_and_default["rules"][1])]}
    unreserved_decision = evaluate(unreserved_default, positioned)
    assert unreserved_decision is not None
    assert unreserved_decision.skill["typeId"] == 88962
    form_specific_default = copy.deepcopy(unreserved_default)
    form_specific_default["rules"][0]["when"]["form"] = [
        "Ultimate", "Ram", "Lion", "Snake"
    ]
    form_specific_action = form_specific_default["rules"][0]["action"]
    form_specific_action["prioritySkills"] = [
        {"skillTypeId": 88961, "skillSlot": 1, "target": {"type": "boss"}}
    ]
    # The editor mirrors Ultimate at the top level for compatibility. Other
    # form policies must not inherit this opener when they omit it.
    form_specific_action["firstTurnSkill"] = {
        "skillTypeId": 88961,
        "skillSlot": 1,
        "target": {"type": "boss"},
    }
    form_specific_action["formPolicies"] = {
        "Ultimate": {
            "firstTurnSkill": {
                "skillTypeId": 88961,
                "skillSlot": 1,
                "target": {"type": "boss"},
            },
            "prioritySkills": [
                {"skillTypeId": 88961, "skillSlot": 1, "target": {"type": "boss"}}
            ],
            "blockedSkillTypeIds": [],
        },
        "Ram": {
            "firstTurnSkill": {
                "skillTypeId": 88962,
                "skillSlot": 2,
                "target": {"type": "self"},
            },
            "prioritySkills": [
                {"skillTypeId": 88962, "skillSlot": 2, "target": {"type": "self"}},
                {"skillTypeId": 88961, "skillSlot": 1, "target": {"type": "boss"}},
            ],
            "blockedSkillTypeIds": [],
        },
        "Lion": {
            "prioritySkills": [
                {"skillTypeId": 88961, "skillSlot": 1, "target": {"type": "boss"}}
            ],
            "blockedSkillTypeIds": [88962],
        },
        "Snake": {
            "prioritySkills": [
                {"skillTypeId": 88961, "skillSlot": 1, "target": {"type": "boss"}}
            ],
            "blockedSkillTypeIds": [],
        },
    }
    validate_strategy_config({"mode": "execute", **form_specific_default})
    ram_form_decision = evaluate(form_specific_default, positioned)
    assert ram_form_decision is not None
    assert ram_form_decision.skill["typeId"] == 88962
    lion_positioned = copy.deepcopy(positioned)
    lion_positioned["chimera"]["currentForm"] = "Lion"
    lion_form_decision = evaluate(form_specific_default, lion_positioned)
    assert lion_form_decision is not None
    assert lion_form_decision.skill["typeId"] == 88961
    ram_first_turn = copy.deepcopy(positioned)
    ram_first_turn["activeHeroTurnCount"] = 8
    ram_first_turn["_chimeraFormHeroFirstTurn"] = True
    ram_first_turn["_chimeraFormPhase"] = "Ram"
    ram_first_turn_decision = evaluate(form_specific_default, ram_first_turn)
    assert ram_first_turn_decision is not None
    assert ram_first_turn_decision.rule.endswith("首回合技能")
    assert ram_first_turn_decision.skill["typeId"] == 88962
    ultimate_first_turn = copy.deepcopy(ram_first_turn)
    ultimate_first_turn["chimera"]["currentForm"] = "Ultimate"
    ultimate_first_turn["_chimeraFormPhase"] = "Ultimate"
    ultimate_first_turn_decision = evaluate(
        form_specific_default, ultimate_first_turn
    )
    assert ultimate_first_turn_decision is not None
    assert ultimate_first_turn_decision.rule.endswith("首回合技能")
    assert ultimate_first_turn_decision.skill["typeId"] == 88961
    lion_first_turn = copy.deepcopy(ram_first_turn)
    lion_first_turn["chimera"]["currentForm"] = "Lion"
    lion_first_turn["_chimeraFormPhase"] = "Lion"
    lion_first_turn_decision = evaluate(form_specific_default, lion_first_turn)
    assert lion_first_turn_decision is not None
    assert not lion_first_turn_decision.rule.endswith("首回合技能")
    assert lion_first_turn_decision.skill["typeId"] == 88961
    ram_later_turn = copy.deepcopy(ram_first_turn)
    ram_later_turn["_chimeraFormHeroFirstTurn"] = False
    ram_later_turn_decision = evaluate(form_specific_default, ram_later_turn)
    assert ram_later_turn_decision is not None
    assert not ram_later_turn_decision.rule.endswith("首回合技能")

    form_turn_runtime: dict[str, object] = {}
    mid_ram = copy.deepcopy(positioned)
    annotate_chimera_form_first_turn(mid_ram, form_turn_runtime)
    assert mid_ram["_chimeraFormHeroFirstTurn"] is False
    first_lion_action = copy.deepcopy(mid_ram)
    first_lion_action["chimera"]["currentForm"] = "Lion"
    first_lion_action["chimera"]["turnCount"] = 10
    first_lion_action["battle"]["playerTurnCount"] = 4
    first_lion_action["activeHeroTurnCount"] = 3
    annotate_chimera_form_first_turn(first_lion_action, form_turn_runtime)
    assert first_lion_action["_chimeraFormHeroFirstTurn"] is True
    refreshed_lion_action = copy.deepcopy(first_lion_action)
    refreshed_lion_action["activeHeroSkillsUpdateCounter"] = 12
    annotate_chimera_form_first_turn(refreshed_lion_action, form_turn_runtime)
    assert refreshed_lion_action["_chimeraFormHeroFirstTurn"] is True
    lion_extra_turn = copy.deepcopy(first_lion_action)
    lion_extra_turn["battle"]["playerTurnCount"] = 5
    lion_extra_turn["activeHeroTurnCount"] = 4
    annotate_chimera_form_first_turn(lion_extra_turn, form_turn_runtime)
    assert lion_extra_turn["_chimeraFormHeroFirstTurn"] is False
    another_hero_lion = copy.deepcopy(lion_extra_turn)
    another_hero_lion["activeHeroId"] = 1
    another_hero_lion["activeHeroTypeId"] = 12345
    another_hero_lion["activeHeroTurnCount"] = 2
    another_hero_lion["battle"]["playerTurnCount"] = 6
    annotate_chimera_form_first_turn(another_hero_lion, form_turn_runtime)
    assert another_hero_lion["_chimeraFormHeroFirstTurn"] is True
    second_ram_phase = copy.deepcopy(another_hero_lion)
    second_ram_phase["chimera"]["currentForm"] = "Ram"
    second_ram_phase["chimera"]["turnCount"] = 15
    second_ram_phase["activeHeroId"] = 0
    second_ram_phase["activeHeroTypeId"] = 8896
    second_ram_phase["activeHeroTurnCount"] = 5
    second_ram_phase["battle"]["playerTurnCount"] = 7
    annotate_chimera_form_first_turn(second_ram_phase, form_turn_runtime)
    assert second_ram_phase["_chimeraFormHeroFirstTurn"] is True
    blocked_default = copy.deepcopy(unreserved_default)
    blocked_default["rules"][0]["action"]["blockedSkillTypeIds"] = [88962]
    blocked_decision = evaluate(blocked_default, positioned)
    assert blocked_decision is not None
    assert blocked_decision.skill["typeId"] == 88961
    validate_strategy_config({"mode": "execute", **strict_and_default})
    transform_rule = {
        "rules": [
            {
                "name": "exact-transform",
                "when": {"activeHeroFormIndex": 0},
                "action": {
                    "type": "transform",
                    "skillSlot": 4,
                    "skillTypeId": 88964,
                    "toFormIndex": 1,
                },
            }
        ]
    }
    transform_decision = evaluate(transform_rule, positioned)
    assert transform_decision is not None
    assert transform_decision.skill["typeId"] == 88964
    validate_strategy_config({"mode": "execute", **position_rule})
    validate_strategy_config({"mode": "execute", **transform_rule})
    ten_slot_state = json.loads(json.dumps(state))
    ten_slot_state["bosses"][0]["effects"] = [
        {
            "effectTypeId": 1000 + index,
            "effectKindId": 2000 + index,
            "effectKind": f"Effect{index}",
            "turnsLeft": 1 if index == 0 else 3,
        }
        for index in range(10)
    ]
    assert matches({"bossEffectSlotsAtLeast": 10}, ten_slot_state)
    assert matches({"bossEffectSlotsAtMost": 10}, ten_slot_state)
    assert not matches({"bossEffectSlotsAtMost": 9}, ten_slot_state)
    assert matches(
        {
            "bossMissingEffect": {
                "kind": "Effect0",
                "turnsAtLeast": 2,
            }
        },
        ten_slot_state,
    )
    unknown_trial_state = json.loads(json.dumps(state))
    unknown_trial_state["bosses"][0]["challenges"][1].pop("possible")
    unknown_report = evaluate_objectives(
        {"objectives": {"mandatoryTrialIds": [8000502]}},
        unknown_trial_state,
    )
    assert not unknown_report.mandatory_impossible
    expired_trial_state = json.loads(json.dumps(unknown_trial_state))
    expired_trial_state["bosses"][0]["challenges"][1][
        "lastEligibleBossTurn"
    ] = 5
    expired_trial_state["chimera"]["turnCount"] = 6
    expired_report = evaluate_objectives(
        {"objectives": {"mandatoryTrialIds": [8000502]}},
        expired_trial_state,
    )
    assert expired_report.impossible_trial_ids == (8000502,)
    assert expired_report.mandatory_impossible
    completed_objective_state = json.loads(json.dumps(state))
    completed_objective_state["bosses"][0]["challenges"][1].update(
        {"completed": True, "possible": False, "progressRatio": 1.0}
    )
    completed_report = evaluate_objectives(
        {
            "objectives": {
                "mandatoryTrialIds": [8000501, 8000502],
                "minimumDamage": 1_000_000,
            }
        },
        completed_objective_state,
    )
    assert completed_report.all_met
    assert not completed_report.mandatory_impossible

    chained_trial_state = json.loads(json.dumps(state))
    chained_trial_state["bosses"][0]["challenges"] = [
        {
            "id": 8000501,
            "completed": True,
            "activeInChain": False,
            "eligibleNow": False,
            "chainState": "completed",
            "requiredPrerequisiteTrialIds": [],
            "possible": True,
        },
        {
            "id": 8000502,
            "completed": False,
            "activeInChain": True,
            "eligibleNow": True,
            "chainState": "active",
            "requiredPrerequisiteTrialIds": [8000501],
            "possible": True,
        },
        {
            "id": 8000503,
            "completed": False,
            "activeInChain": False,
            "eligibleNow": False,
            "chainState": "locked",
            "requiredPrerequisiteTrialIds": [8000501, 8000502],
            "possible": True,
        },
    ]
    chained_report = evaluate_objectives(
        {"objectives": {"mandatoryTrialIds": [8000503]}},
        chained_trial_state,
    )
    assert chained_report.mandatory_trial_ids == (8000501, 8000502, 8000503)
    assert chained_report.completed_trial_ids == (8000501,)
    assert chained_report.missing_trial_ids == (8000502, 8000503)
    assert matches({"activeTrialsAny": [8000502]}, chained_trial_state)
    assert matches({"eligibleTrialsAll": [8000502]}, chained_trial_state)
    assert matches({"lockedTrialsAny": [8000503]}, chained_trial_state)
    assert not matches({"eligibleTrialsAny": [8000503]}, chained_trial_state)
    trial_gated_strict_config = {
        "rules": [
            {
                "name": "strict-only-while-trial-active",
                "when": {
                    "activeHeroTypeId": [8896],
                    "form": ["Ram"],
                    "eligibleTrialsAny": [8000502],
                },
                "action": {
                    "type": "cast",
                    "skillTypeId": 88962,
                    "skillSlot": 2,
                    "target": {"type": "self"},
                },
            },
            {
                "name": "trial-gate-default",
                "when": {"activeHeroTypeId": [8896], "form": ["Ram"]},
                "action": {
                    "type": "defaultSkillPriority",
                    "prioritySkills": [
                        {
                            "skillTypeId": 88962,
                            "skillSlot": 2,
                            "target": {"type": "self"},
                        },
                        {
                            "skillTypeId": 88961,
                            "skillSlot": 1,
                            "target": {"type": "boss"},
                        },
                    ],
                    "blockedSkillTypeIds": [],
                },
            },
        ]
    }
    validate_strategy_config({"mode": "execute", **trial_gated_strict_config})
    active_trial_decision = evaluate(trial_gated_strict_config, chained_trial_state)
    assert active_trial_decision is not None
    assert active_trial_decision.rule == "strict-only-while-trial-active"
    completed_trial_gate_state = copy.deepcopy(chained_trial_state)
    completed_trial_gate_state["bosses"][0]["challenges"][1]["completed"] = True
    completed_trial_decision = evaluate(
        trial_gated_strict_config, completed_trial_gate_state
    )
    assert completed_trial_decision is not None
    assert completed_trial_decision.rule == "trial-gate-default"
    unselected_trial_gate_state = copy.deepcopy(chained_trial_state)
    unselected_trial_gate_state["bosses"][0]["challenges"] = [
        trial
        for trial in unselected_trial_gate_state["bosses"][0]["challenges"]
        if trial.get("id") != 8000502
    ]
    unselected_trial_decision = evaluate(
        trial_gated_strict_config, unselected_trial_gate_state
    )
    assert unselected_trial_decision is not None
    assert unselected_trial_decision.rule == "trial-gate-default"

    executable_state = json.loads(json.dumps(state))
    executable_state["battle"].update(
        {
            "chimeraPreset": True,
            "finished": False,
            "autoMode": False,
            "waitingForManualCommand": True,
        }
    )
    executable_state["pointers"] = {"context": 10, "generator": 20, "mode": 30}
    executable_state["skills"][0]["skillDataPtr"] = 40
    executable_config = {
        "mode": "execute",
        "rules": [
            {
                "name": "recoverable-manual-race",
                "when": {"form": "Ram"},
                "action": {"type": "cast", "skillSlot": 1, "target": "boss"},
            }
        ],
    }
    with (
        patch("chimera_controller.queue_command", return_value={"queued": True}),
        patch(
            "chimera_controller.wait_for_command_ack",
            return_value={"status": "rejected", "reason": "guard_failed"},
        ),
    ):
        assert not process_state(
            executable_config,
            executable_state,
            agent=Path("agent.dll"),
            ipc=ActiveIpc(),
            session_id=123,
            execute_requested=True,
            nonce=1,
            ignore_freshness=True,
        )
    with (
        patch("chimera_controller.queue_command", return_value={"queued": True}),
        patch(
            "chimera_controller.wait_for_command_ack",
            return_value={"status": "submitted"},
        ),
        patch("chimera_controller.wait_for_turn_advance", return_value=None),
    ):
        assert process_state(
            executable_config,
            executable_state,
            agent=Path("agent.dll"),
            ipc=ActiveIpc(),
            session_id=123,
            execute_requested=True,
            nonce=2,
            ignore_freshness=True,
        )

    no_match_output = io.StringIO()
    executable_state["activeHeroName"] = "测试英雄"
    with redirect_stdout(no_match_output):
        assert not process_state(
            {"mode": "execute", "rules": []},
            executable_state,
            agent=Path("agent.dll"),
            ipc=ActiveIpc(),
            session_id=123,
            execute_requested=True,
            nonce=3,
            ignore_freshness=True,
        )
    assert "未行动：当前英雄“测试英雄”没有匹配且可安全执行的规则" in no_match_output.getvalue()
    assert "没有为当前英雄配置规则" in no_match_output.getvalue()

    assert not is_battle_decision_state({"type": "hero_catalog_state", "heroes": []})
    assert is_battle_decision_state({"type": "decision_state", "sequence": 1})
    identified_hydra = json.loads(json.dumps(executable_state))
    identified_hydra["bossMode"] = "hydra"
    identified_hydra["battle"]["areaTypeId"] = 999
    identified_hydra["battle"]["kindId"] = 999
    with patch("chimera_controller.ACTIVE_BOSS_MODE", "hydra"):
        assert safety_reason(identified_hydra, 1500, ignore_freshness=True) is None
    unidentified_hydra = json.loads(json.dumps(identified_hydra))
    unidentified_hydra.pop("bossMode")
    for boss in unidentified_hydra["bosses"]:
        boss.pop("isHydraHead", None)
        boss.pop("isHydraNeck", None)
    with patch("chimera_controller.ACTIVE_BOSS_MODE", "hydra"):
        assert safety_reason(unidentified_hydra, 1500, ignore_freshness=True) == "当前回合快照尚未确认是六头蛇战斗"

    hydra_state = json.loads(json.dumps(executable_state))
    hydra_state["skills"][0]["name"] = "测试技能"
    hydra_state["hydra"] = {"turnCount": 4}
    hydra_advanced = json.loads(json.dumps(hydra_state))
    hydra_advanced["hydra"]["turnCount"] = 5
    hydra_log_output = io.StringIO()
    with (
        patch("chimera_controller.ACTIVE_BOSS_MODE", "hydra"),
        patch("chimera_controller.safety_reason", return_value=None),
        patch("chimera_controller.queue_command", return_value={"queued": True}),
        patch(
            "chimera_controller.wait_for_command_ack",
            return_value={"status": "submitted"},
        ),
        patch("chimera_controller.wait_for_turn_advance", return_value=hydra_advanced),
        redirect_stdout(hydra_log_output),
    ):
        assert process_state(
            executable_config,
            hydra_state,
            agent=Path("agent.dll"),
            ipc=ActiveIpc(),
            session_id=123,
            execute_requested=True,
            nonce=4,
            ignore_freshness=True,
        )
    hydra_log = hydra_log_output.getvalue()
    assert "英雄“测试英雄”命中规则“recoverable-manual-race”" in hydra_log
    assert "执行成功：英雄“测试英雄”已按规则“recoverable-manual-race”" in hydra_log
    assert "六头蛇回合 4→5" in hydra_log
    assert wait_for_turn_advance(
        ResultIpc(), state, 123, timeout_seconds=0.1
    ) is None
    validate_strategy_config(
        {
            "mode": "observe",
            "objectives": {
                "mandatoryTrialIds": [8000501],
                "minimumDamage": 0,
                "maxRegroupRetries": 10,
                "onMandatoryTrialImpossible": "free_regroup_and_retry_manual",
                "onAllMetAtResult": "hold_for_user",
            },
            "safety": {
                "requireFreshSnapshotMs": 500,
                "maxCommandsPerTurn": 1,
                "onUnknownState": "pause",
                "onNoMatchingRule": "pause",
            },
            "rules": [],
        }
    )
    for invalid_config in (
        {"mode": "execute", "objectives": {"maxRegroupRetries": -1}, "rules": []},
        {"mode": "execute", "objectives": {"minimumDamage": True}, "rules": []},
        {"mode": "execute", "objectives": {"mandatoryTrialIds": [1, 1]}, "rules": []},
        {"mode": "execute", "objectives": {"onAllMetAtResult": "save"}, "rules": []},
        {"mode": "execute", "safety": {"maxCommandsPerTurn": 2}, "rules": []},
        {"mode": "typo", "rules": []},
    ):
        try:
            validate_strategy_config(invalid_config)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid strategy config accepted: {invalid_config}")
    assert matches(
        {
            "form": "Ram",
            "activeHeroFormIndex": 0,
            "activeHeroIsMetamorph": True,
            "activeHeroIsTransformed": False,
            "transformationReady": True,
            "bossHasEffect": {"kind": "Fear", "turnsAtLeast": 2},
            "completedTrialsAll": [8000501],
            "incompleteTrialsAll": [8000502],
            "trialProgressAtLeast": {"8000502": 0.4},
            "currentDamageAtLeast": 1_000_000,
            "bossEffectSlotsAtMost": 10,
            "chimeraStageId": 1302,
            "stageRotationIndex": 1,
            "catalogFingerprint": "fnv1a64:test-catalog",
            "trialDefinitionFingerprint": "fnv1a64:test-definitions",
            "rewardRotationFingerprint": "fnv1a64:test-rewards",
            "attributeRotationFingerprint": "fnv1a64:test-attributes",
            "nextForm": "Ultimate",
            "turnsUntilFormChangeAtMost": 3,
            "turnsUntilFormChangeAtLeast": 3,
            "chimeraTurnAtLeast": 7,
            "chimeraTurnAtMost": 7,
            "anyAllyHasEffect": {
                "kind": "IncreaseDefense",
                "turnsAtLeast": 2,
            },
            "anyAllyMissingEffect": {"kind": "Shield"},
            "allyHasEffect": {
                "heroTypeId": 12345,
                "effect": {"kind": "Shield", "turnsAtLeast": 2},
            },
            "allyEffectSlotsAtLeast": {"heroTypeId": 12345, "count": 2},
            "allAlliesEffectSlotsAtMost": 2,
            "livingAlliesAtLeast": 2,
            "possibleTrialsAll": [8000501],
            "impossibleTrialsAny": [8000502],
        },
        state,
    )
    assert not matches(
        {"allAlliesHaveEffect": {"kind": "IncreaseDefense"}}, state
    )
    assert matches(
        {
            "effectConditions": [
                {
                    "target": "boss",
                    "presence": "has",
                    "effect": {"kind": "Fear", "turnsAtLeast": 2},
                },
                {
                    "target": "ally",
                    "heroTypeId": 12345,
                    "presence": "has",
                    "effect": {"kind": "Shield", "turnsAtLeast": 2},
                },
                {
                    "target": "ally",
                    "heroTypeId": 12345,
                    "presence": "missing",
                    "effect": {"kind": "Stun"},
                },
            ]
        },
        state,
    )
    assert not matches(
        {
            "effectConditions": [
                {
                    "target": "boss",
                    "presence": "missing",
                    "effect": {"kind": "Fear"},
                }
            ]
        },
        state,
    )
    assert matches(
        {
            "effectConditionsMode": "any",
            "effectConditions": [
                {
                    "target": "boss",
                    "presence": "missing",
                    "effect": {"kind": "Fear"},
                },
                {
                    "target": "ally",
                    "heroTypeId": 12345,
                    "presence": "has",
                    "effect": {"kind": "Shield", "turnsAtLeast": 2},
                },
            ],
        },
        state,
    )
    assert not matches(
        {
            "effectConditionsMode": "all",
            "effectConditions": [
                {
                    "target": "boss",
                    "presence": "missing",
                    "effect": {"kind": "Fear"},
                },
                {
                    "target": "ally",
                    "heroTypeId": 12345,
                    "presence": "has",
                    "effect": {"kind": "Shield"},
                },
            ],
        },
        state,
    )
    assert not matches({"effectConditionsMode": "any"}, state)
    assert not matches({"effectConditionsMode": "xor", "effectConditions": [{}]}, state)
    nested_condition = {
        "conditionTree": {
            "type": "group",
            "operator": "any",
            "children": [
                {
                    "type": "group",
                    "operator": "all",
                    "children": [
                        {
                            "type": "effect",
                            "target": "boss",
                            "presence": "has",
                            "effect": {"kind": "Fear"},
                        },
                        {
                            "type": "effect",
                            "target": "ally",
                            "heroTypeId": 12345,
                            "presence": "has",
                            "effect": {"kind": "Shield"},
                        },
                    ],
                },
                {
                    "type": "effect",
                    "target": "boss",
                    "presence": "has",
                    "effect": {"kind": "Stun"},
                },
            ],
        }
    }
    assert matches(nested_condition, state)
    nested_condition["conditionTree"]["children"][0]["negate"] = True
    assert not matches(nested_condition, state)
    nested_condition["conditionTree"]["children"][1]["negate"] = True
    assert matches(nested_condition, state)
    validate_strategy_config(
        {
            "mode": "execute",
            "rules": [
                {
                    "name": "nested-condition",
                    "when": nested_condition,
                    "action": {
                        "type": "cast",
                        "skillSlot": 1,
                        "target": {"type": "boss"},
                    },
                }
            ],
        }
    )

    dead_target_state = copy.deepcopy(state)
    dead_target_state["heroes"][1].update({"dead": True, "teamPosition": 2})
    strict_dead_skill = {"validTargetIds": [1]}
    assert select_target(
        {"type": "allyHeroTypeId", "heroTypeId": 12345},
        strict_dead_skill,
        dead_target_state,
    ) is None
    assert select_target(
        {"type": "allyPosition", "position": 2},
        strict_dead_skill,
        dead_target_state,
    ) is None
    revive_target = select_target(
        {"type": "auto"}, strict_dead_skill, dead_target_state
    )
    assert revive_target is not None and revive_target[0] == 1
    dead_boss_state = copy.deepcopy(state)
    dead_boss_state["bossMode"] = "hydra"
    dead_boss_state["hydra"] = {
        "active": True,
        "devouringHeadIds": [5],
    }
    dead_boss_state["bosses"][0]["dead"] = True
    assert select_target(
        {"type": "devouringHead"},
        {"validTargetIds": [5]},
        dead_boss_state,
    ) is None

    learned_state = json.loads(json.dumps(state))
    learned_state["skills"].append(
        {
            "slot": 3,
            "skillId": 2,
            "typeId": 104303,
            "name": "friendly-veil",
            "ready": True,
            "blocked": False,
            "passive": False,
            "validTargetIds": [0, 1],
        }
    )
    learned_state["heroes"][1]["effects"].append(
        {
            "skillTypeId": 104303,
            "producerId": 0,
            "effectTypeId": 481,
            "effectKindId": 481,
            "effectKind": "Invisible",
            "lifetime": 2,
            "turnsLeft": 2,
        }
    )
    capability_memory = SkillCapabilityMemory()
    assert capability_memory.observe_state(learned_state) == 1
    maintain_config = {
        "rules": [
            {
                "name": "maintain-active-veil",
                "when": {},
                "action": {
                    "type": "maintainEffects",
                    "requirements": [
                        {
                            "scope": "activeHero",
                            "effect": {"kind": "Invisible"},
                            "keepTurnsAtLeast": 2,
                        }
                    ],
                    "probeUnknownSkills": True,
                },
            }
        ]
    }
    validate_strategy_config({"mode": "execute", **maintain_config})
    maintain_decision = evaluate(
        maintain_config, learned_state, capability_memory
    )
    assert maintain_decision is not None
    assert maintain_decision.skill["typeId"] == 104303
    assert maintain_decision.target_id == learned_state["activeHeroId"]
    assert maintain_decision.capability_probe is False

    probe_state = json.loads(json.dumps(state))
    probe_state["skills"].append(
        {
            "slot": 3,
            "skillId": 2,
            "typeId": 47103,
            "name": "unknown-boss-skill",
            "ready": True,
            "blocked": False,
            "passive": False,
            "validTargetIds": [5],
        }
    )
    probe_config = {
        "rules": [
            {
                "name": "discover-defence-down",
                "when": {},
                "action": {
                    "type": "maintainEffects",
                    "requirements": [
                        {
                            "scope": "boss",
                            "effect": {"kind": "StatusReduceDefence"},
                        }
                    ],
                    "probeUnknownSkills": True,
                },
            }
        ]
    }
    probe_memory = SkillCapabilityMemory()
    probe_decision = evaluate(probe_config, probe_state, probe_memory)
    assert probe_decision is not None
    assert probe_decision.skill["typeId"] == 47103
    assert probe_decision.capability_probe is True
    probe_memory.mark_probed(47103)
    assert evaluate(probe_config, probe_state, probe_memory) is None

    with tempfile.TemporaryDirectory() as directory:
        capability_path = Path(directory) / "capabilities.json"
        assert capability_memory.save_if_changed(capability_path)
        loaded_memory = SkillCapabilityMemory.load(capability_path)
        loaded_decision = evaluate(maintain_config, learned_state, loaded_memory)
        assert loaded_decision is not None
        assert loaded_decision.skill["typeId"] == 104303

        seed_path = Path(directory) / "bundled-seed.json"
        overlay_path = Path(directory) / "persistent-overlay.json"
        seed_path.write_text(
            json.dumps(
                {
                    "skills": {
                        "111": [
                            {
                                "targetScope": "boss",
                                "effectTypeId": 131,
                                "effectKind": "StatusReduceAttack",
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        overlay_path.write_text(
            json.dumps(
                {
                    "skills": {
                        "222": [
                            {
                                "targetScope": "ally",
                                "effectTypeId": 280,
                                "effectKind": "Shield",
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        merged_memory = SkillCapabilityMemory.load(overlay_path, seed_path)
        assert merged_memory.capabilities_for(111)[0]["effectTypeId"] == 131
        assert merged_memory.capabilities_for(222)[0]["effectTypeId"] == 280

    recipes = load_trial_recipes()
    assert len(recipes) == 27
    assert recipes[8000501]["automation"] == "automatic"
    assert recipes[8000503]["automation"] == "manual"
    nightmare_snake_wing = trial_recipe_for_id(recipes, 8000521)
    ultimate_nightmare_snake_wing = trial_recipe_for_id(recipes, 8000621)
    assert nightmare_snake_wing is not None
    assert ultimate_nightmare_snake_wing is not None
    assert nightmare_snake_wing["minimumBossDebuffs"] == 9
    assert ultimate_nightmare_snake_wing["trialId"] == 8000621
    assert ultimate_nightmare_snake_wing["allianceDifficultyId"] == 6
    assert ultimate_nightmare_snake_wing["minimumBossDebuffs"] == 10

    distinct_progress_state = copy.deepcopy(state)
    distinct_progress_state["chimera"]["currentForm"] = "Ram"
    distinct_progress_state["bosses"][0]["challenges"] = [
        {
            "id": 8000604,
            "completed": False,
            "eligibleNow": True,
            "activeInChain": True,
            "current": 6,
            "target": 7,
        }
    ]
    distinct_progress_state["heroes"][0]["effects"] = [
        {
            "effectTypeId": 220,
            "effectKindId": 2106,
            "effectKind": "StatusIncreaseAccuracy",
            "turnsLeft": 2,
        }
    ]
    distinct_progress_state["bosses"][0]["effects"] = [
        {
            "effectTypeId": effect_type_id,
            "effectKindId": 3100 + index,
            "effectKind": f"Debuff{index}",
            "turnsLeft": 2,
        }
        for index, effect_type_id in enumerate((10, 20, 30, 40, 70, 80), 1)
    ]
    distinct_progress_state["skills"].append(
        {
            "slot": 3,
            "skillId": 2,
            "typeId": 99041,
            "ready": True,
            "blocked": False,
            "passive": False,
            "validTargetIds": [5],
        }
    )
    distinct_memory = SkillCapabilityMemory()
    for effect_type_id in (110, 131, 151):
        distinct_memory._remember(
            99041,
            {
                "targetScope": "boss",
                "effectTypeId": effect_type_id,
                "effectKindId": 3101,
            },
        )
    trial_overlay = {
        "rules": [
            {
                "name": "team-trial-planner",
                "when": {},
                "action": {
                    "type": "executeTrialRecipe",
                    "trialIds": [8000604],
                },
            }
        ]
    }
    distinct_progress_decision = evaluate(
        trial_overlay, distinct_progress_state, distinct_memory
    )
    assert distinct_progress_decision is not None
    assert distinct_progress_decision.skill["typeId"] == 99041
    assert distinct_progress_decision.trial_id == 8000604
    assert "6/7" in distinct_progress_decision.rule

    forbidden_effect_state = copy.deepcopy(distinct_progress_state)
    forbidden_effect_state["chimera"]["currentForm"] = "Snake"
    forbidden_effect_state["bosses"][0]["challenges"] = [
        {
            "id": 8000621,
            "completed": False,
            "eligibleNow": True,
            "activeInChain": True,
            "current": 8,
            "target": 10,
        }
    ]
    forbidden_effect_state["bosses"][0]["effects"] = forbidden_effect_state[
        "bosses"
    ][0]["effects"][:8]
    forbidden_effect_state["skills"] = [
        {
            "slot": 2,
            "skillId": 1,
            "typeId": 99042,
            "ready": True,
            "blocked": False,
            "passive": False,
            "validTargetIds": [5],
        },
        {
            "slot": 3,
            "skillId": 2,
            "typeId": 99043,
            "ready": True,
            "blocked": False,
            "passive": False,
            "validTargetIds": [5],
        },
    ]
    forbidden_memory = SkillCapabilityMemory()
    for effect_type_id in (110, 151, 290):
        forbidden_memory._remember(
            99042,
            {
                "targetScope": "boss",
                "effectTypeId": effect_type_id,
                "effectKindId": 3101,
            },
        )
    forbidden_memory._remember(
        99043,
        {
            "targetScope": "boss",
            "effectTypeId": 350,
            "effectKindId": 3105,
        },
    )
    forbidden_trial_overlay = copy.deepcopy(trial_overlay)
    forbidden_trial_overlay["rules"][0]["action"]["trialIds"] = [8000621]
    forbidden_decision = evaluate(
        forbidden_trial_overlay, forbidden_effect_state, forbidden_memory
    )
    assert forbidden_decision is not None
    assert forbidden_decision.skill["typeId"] == 99043
    assert forbidden_decision.trial_id == 8000621

    ally_attack_state = copy.deepcopy(state)
    ally_attack_state["chimera"]["currentForm"] = "Lion"
    ally_attack_state["bosses"][0]["challenges"] = [
        {
            "id": 8000615,
            "completed": False,
            "eligibleNow": True,
            "activeInChain": True,
            "current": 0,
            "target": 1,
        }
    ]
    ally_attack_state["skills"] = [
        {
            "slot": 3,
            "skillId": 2,
            "typeId": 82503,
            "name": "Mikage ally attack",
            "ready": True,
            "blocked": False,
            "passive": False,
            "validTargetIds": [0],
        }
    ]
    ally_attack_overlay = copy.deepcopy(trial_overlay)
    ally_attack_overlay["rules"][0]["action"]["trialIds"] = [8000615]
    ally_attack_decision = evaluate(
        ally_attack_overlay, ally_attack_state, SkillCapabilityMemory()
    )
    assert ally_attack_decision is not None
    assert ally_attack_decision.skill["typeId"] == 82503
    assert ally_attack_decision.trial_id == 8000615
    mixed_effect_target = {
        "effects": [
            *[
                {
                    "effectTypeId": 131,
                    "effectKindId": 3101,
                    "effectKind": "StatusReduceAttack",
                }
                for _ in range(8)
            ],
            {
                "effectTypeId": 141,
                "effectKindId": 2102,
                "effectKind": "StatusIncreaseDefence",
            },
            {
                "effectTypeId": 161,
                "effectKindId": 2103,
                "effectKind": "StatusIncreaseSpeed",
            },
        ]
    }
    assert debuff_count(mixed_effect_target) == 8
    assert buff_count(mixed_effect_target) == 2
    capacity_state = {
        "heroes": [],
        "bosses": [{"id": 5, **mixed_effect_target}],
    }
    assert effect_target_has_capacity(
        {"scope": "boss", "effect": {"effectTypeId": 151}},
        5,
        capacity_state,
        capability={"effectTypeId": 151, "effectKindId": 3102},
    )
    ten_buff_target = {
        "effects": [
            {
                "effectTypeId": 141,
                "effectKindId": 2102,
                "effectKind": "StatusIncreaseDefence",
            }
            for _ in range(10)
        ]
    }
    assert not effect_target_has_capacity(
        {"scope": "activeHero", "effect": {"effectTypeId": 161}},
        0,
        {"heroes": [{"id": 0, **ten_buff_target}], "bosses": []},
        capability={"effectTypeId": 161, "effectKindId": 2103},
    )
    recipe_state = json.loads(json.dumps(state))
    recipe_state["bosses"][0]["challenges"] = [
        {
            "id": 8000501,
            "completed": False,
            "eligibleNow": True,
            "activeInChain": True,
            "progressRatio": 0.0,
        }
    ]
    recipe_state["bosses"][0]["effects"] = [
        {
            "effectTypeId": 490,
            "effectKindId": 3001,
            "effectKind": "Fear",
            "turnsLeft": 2,
        }
    ]
    recipe_config = {
        "rules": [
            {
                "name": "automatic-current-trial",
                "when": {"eligibleTrialsAny": [8000501]},
                "action": {
                    "type": "executeTrialRecipe",
                    "trialIds": [8000501],
                    "probeUnknownSkills": True,
                },
            }
        ]
    }
    validate_strategy_config({"mode": "execute", **recipe_config})
    recipe_decision = evaluate(recipe_config, recipe_state)
    assert recipe_decision is not None
    assert recipe_decision.target_id == 5
    assert recipe_decision.skill["slot"] == 1
    assert recipe_decision.trial_id == 8000501

    boss_buff_gate_state = copy.deepcopy(recipe_state)
    boss_buff_gate_state["chimera"]["currentForm"] = "Snake"
    boss_buff_gate_state["bosses"][0]["challenges"] = [
        {
            "id": 8000520,
            "completed": False,
            "eligibleNow": True,
            "activeInChain": True,
            "progressRatio": 0.0,
        }
    ]
    boss_buff_gate_state["bosses"][0]["effects"] = [
        {"effectTypeId": 110, "effectKindId": 3008, "turnsLeft": 2},
        {"effectTypeId": 470, "effectKindId": 3014, "turnsLeft": 2},
        {"effectTypeId": 141, "effectKindId": 2102, "turnsLeft": 2},
    ]
    boss_buff_gate_decision = evaluate(
        {
            "rules": [
                {
                    "name": "boss-must-have-no-buffs",
                    "when": {"eligibleTrialsAny": [8000520]},
                    "action": {
                        "type": "executeTrialRecipe",
                        "trialIds": [8000520],
                    },
                }
            ]
        },
        boss_buff_gate_state,
    )
    assert boss_buff_gate_decision is not None
    assert boss_buff_gate_decision.trial_id is None
    assert "当前无可执行试炼专用动作" in boss_buff_gate_decision.rule

    support_damage_state = copy.deepcopy(recipe_state)
    support_damage_state["chimera"]["currentForm"] = "Snake"
    support_damage_state["bosses"][0]["challenges"] = [
        {
            "id": 8000525,
            "completed": False,
            "eligibleNow": True,
            "activeInChain": True,
            "progressRatio": 0.0,
        }
    ]
    support_damage_state["bosses"][0]["effects"] = [
        {
            "effectTypeId": 131,
            "effectKindId": 3101,
            "effectKind": "StatusReduceAttack",
            "turnsLeft": 2,
        }
    ]
    support_damage_state["heroes"][0]["effects"] = [
        {
            "effectTypeId": 161,
            "effectKindId": 2103,
            "effectKind": "StatusIncreaseSpeed",
            "turnsLeft": 2,
        }
    ]
    support_damage_state["skills"][1]["validTargetIds"] = [5]
    support_damage_memory = SkillCapabilityMemory()
    support_damage_memory._performance = {
        88961: {"samples": 20, "emaDamage": 50_000.0},
        88962: {"samples": 20, "emaDamage": 2_000.0},
    }
    support_damage_memory._remember(
        88962,
        {
            "targetScope": "ally",
            "effectTypeId": 161,
            "effectKind": "StatusIncreaseSpeed",
        },
    )
    support_damage_memory._remember(
        88962,
        {
            "targetScope": "boss",
            "effectTypeId": 151,
            "effectKind": "StatusReduceDefence",
        },
    )
    support_damage_config = {
        "rules": [
            {
                "name": "support-trial-overlay",
                "when": {"form": ["Snake"], "activeHeroTypeId": [8896]},
                "action": {
                    "type": "executeTrialRecipe",
                    "trialIds": [8000525],
                },
            },
            {
                "name": "support-current-form-default",
                "when": {
                    "form": ["Ultimate", "Ram", "Lion", "Snake"],
                    "activeHeroTypeId": [8896],
                },
                "action": {
                    "type": "defaultSkillPriority",
                    "prioritySkills": [
                        {
                            "skillTypeId": 88961,
                            "skillSlot": 1,
                            "target": {"type": "boss"},
                        }
                    ],
                    "blockedSkillTypeIds": [],
                    "formPolicies": {
                        "Snake": {
                            "prioritySkills": [
                                {
                                    "skillTypeId": 88962,
                                    "skillSlot": 2,
                                    "target": {"type": "boss"},
                                },
                                {
                                    "skillTypeId": 88961,
                                    "skillSlot": 1,
                                    "target": {"type": "boss"},
                                },
                            ],
                            "blockedSkillTypeIds": [],
                        }
                    },
                },
            },
        ]
    }
    support_damage_decision = evaluate(
        support_damage_config,
        support_damage_state,
        support_damage_memory,
    )
    assert support_damage_decision is not None
    assert support_damage_decision.skill["typeId"] == 88962

    strict_after_trial = copy.deepcopy(support_damage_config)
    strict_after_trial["rules"].append(
        {
            "name": "explicit-strict-after-trial",
            "when": {
                "form": ["Snake"],
                "activeHeroTypeId": [8896],
                "effectConditions": [
                    {
                        "target": "boss",
                        "presence": "has",
                        "effect": {"effectTypeId": 131},
                    }
                ],
            },
            "action": {
                "type": "cast",
                "skillTypeId": 88961,
                "skillSlot": 1,
                "target": {"type": "boss"},
            },
        }
    )
    strict_after_trial_decision = evaluate(
        strict_after_trial,
        support_damage_state,
        support_damage_memory,
    )
    assert strict_after_trial_decision is not None
    assert strict_after_trial_decision.rule == "explicit-strict-after-trial"

    reserved_strict_skill_config = copy.deepcopy(support_damage_config)
    reserved_strict_skill_config["rules"][1]["action"]["formPolicies"]["Snake"][
        "prioritySkills"
    ].insert(
        0,
        {
            "skillTypeId": 88963,
            "skillSlot": 3,
            "target": {"type": "boss"},
        },
    )
    reserved_strict_skill_config["rules"].append(
        {
            "name": "conditional-strict-reserved-s3",
            "when": {
                "form": ["Snake"],
                "activeHeroTypeId": [8896],
                "effectConditions": [
                    {
                        "target": "boss",
                        "presence": "missing",
                        "effect": {"effectTypeId": 100},
                    }
                ],
            },
            "action": {
                "type": "cast",
                "skillTypeId": 88963,
                "skillSlot": 3,
                "target": {"type": "boss"},
            },
        }
    )
    blocked_debuff_state = copy.deepcopy(support_damage_state)
    blocked_debuff_state["skills"].append(
        {
            "slot": 3,
            "skillId": 2,
            "typeId": 88963,
            "ready": True,
            "blocked": False,
            "passive": False,
            "validTargetIds": [5],
        }
    )
    blocked_debuff_state["bosses"][0]["effects"].append(
        {
            "effectTypeId": 100,
            "effectKindId": 2002,
            "effectKind": "BlockDebuff",
            "turnsLeft": 2,
        }
    )
    reserved_trial_decision = evaluate(
        reserved_strict_skill_config,
        blocked_debuff_state,
        support_damage_memory,
    )
    assert reserved_trial_decision is not None
    assert reserved_trial_decision.rule.startswith("support-trial-overlay")
    assert reserved_trial_decision.skill["typeId"] == 88962

    no_trial_window = json.loads(json.dumps(recipe_state))
    no_trial_window["bosses"][0]["challenges"] = []
    no_trial_fallback = evaluate(
        {
            "rules": [
                {
                    "name": "automatic-trial-fallback",
                    "when": {"activeHeroTypeId": [8890, 8896]},
                    "action": {"type": "executeTrialRecipe"},
                }
            ]
        },
        no_trial_window,
    )
    assert no_trial_fallback is not None
    assert no_trial_fallback.skill["slot"] == 2
    assert "当前无可执行试炼专用动作" in no_trial_fallback.rule
    assert "按常规技能优先级" in no_trial_fallback.rule

    explicit_default = evaluate(
        {
            "rules": [
                {
                    "name": "trial-overlay",
                    "when": {"activeHeroTypeId": [8890, 8896]},
                    "action": {"type": "executeTrialRecipe"},
                },
                {
                    "name": "hero-default",
                    "when": {"activeHeroTypeId": [8890, 8896]},
                    "action": {
                        "type": "defaultSkillPriority",
                        "prioritySkills": [
                            {
                                "skillTypeId": 88961,
                                "skillSlot": 1,
                                "target": {"type": "boss"},
                            }
                        ],
                    },
                },
            ]
        },
        no_trial_window,
    )
    assert explicit_default is not None
    assert explicit_default.rule == "hero-default"
    assert explicit_default.skill["typeId"] == 88961

    lower_difficulty_state = json.loads(json.dumps(recipe_state))
    lower_difficulty_state["bosses"][0]["challenges"][0]["id"] = 8000401
    lower_difficulty_decision = evaluate(
        {
            "rules": [
                {
                    "name": "difficulty-independent-recipe",
                    "when": {"eligibleTrialsAny": [8000401]},
                    "action": {
                        "type": "executeTrialRecipe",
                        "trialIds": [8000401],
                    },
                }
            ]
        },
        lower_difficulty_state,
    )
    assert lower_difficulty_decision is not None
    assert lower_difficulty_decision.trial_id == 8000401
    assert "恐惧下技能伤害" in lower_difficulty_decision.rule

    missing_provider_state = json.loads(json.dumps(recipe_state))
    missing_provider_state["bosses"][0]["effects"] = []
    missing_provider_decision = evaluate(
        {
            "rules": [
                recipe_config["rules"][0],
                {
                    "name": "missing-provider-default",
                    "when": {"activeHeroTypeId": [8890, 8896]},
                    "action": {
                        "type": "defaultSkillPriority",
                        "prioritySkills": [
                            {
                                "skillTypeId": 88962,
                                "skillSlot": 2,
                                "target": {"type": "self"},
                            }
                        ],
                    },
                },
            ]
        },
        missing_provider_state,
        SkillCapabilityMemory(),
    )
    assert missing_provider_decision is not None
    assert missing_provider_decision.rule == "missing-provider-default"
    assert missing_provider_decision.skill["typeId"] == 88962

    survival_state = json.loads(json.dumps(recipe_state))
    survival_state["bosses"][0]["challenges"] = [
        {
            "id": 8000523,
            "completed": False,
            "eligibleNow": True,
            "activeInChain": True,
            "progressRatio": 0.0,
        }
    ]
    for hero_id in range(2, 5):
        survival_state["heroes"].append(
            {
                "id": hero_id,
                "typeId": 12000 + hero_id,
                "healthPct": 100,
                "dead": False,
                "effects": [],
            }
        )
    survival_decision = evaluate(
        {
            "rules": [
                {
                    "name": "survival-trial-overlay",
                    "when": {"eligibleTrialsAny": [8000523]},
                    "action": {
                        "type": "executeTrialRecipe",
                        "trialIds": [8000523],
                    },
                },
                {
                    "name": "survival-hero-default",
                    "when": {"activeHeroTypeId": [8890, 8896]},
                    "action": {
                        "type": "defaultSkillPriority",
                        "prioritySkills": [
                            {
                                "skillTypeId": 88962,
                                "skillSlot": 2,
                                "target": {"type": "self"},
                            }
                        ],
                    },
                },
            ]
        },
        survival_state,
        SkillCapabilityMemory(),
    )
    assert survival_decision is not None
    assert survival_decision.rule == "survival-hero-default"
    assert survival_decision.skill["typeId"] == 88962

    measured_state = json.loads(json.dumps(recipe_state))
    measured_state["skills"].append(
        {
            "slot": 3,
            "skillId": 2,
            "typeId": 99003,
            "ready": True,
            "blocked": False,
            "passive": False,
            "validTargetIds": [5],
        }
    )
    measured_memory = SkillCapabilityMemory()
    low_before = json.loads(json.dumps(recipe_state))
    low_after = json.loads(json.dumps(recipe_state))
    low_after["battle"]["currentDamage"] += 10_000
    low_after["bosses"][0]["challenges"][0]["current"] = 10_000
    low_before["bosses"][0]["challenges"][0]["current"] = 0
    measured_memory.observe_action_damage(
        99003, low_before, low_after, trial_id=8000501
    )
    high_after = json.loads(json.dumps(recipe_state))
    high_after["battle"]["currentDamage"] += 100_000
    high_after["bosses"][0]["challenges"][0]["current"] = 100_000
    measured_memory.observe_action_damage(
        88961, low_before, high_after, trial_id=8000501
    )
    measured_decision = evaluate(recipe_config, measured_state, measured_memory)
    assert measured_decision is not None
    assert measured_decision.skill["typeId"] == 88961
    assert measured_memory.damage_score(88961, 8000501) == 100_000
    exploration_state = json.loads(json.dumps(measured_state))
    exploration_state["skills"].append(
        {
            "slot": 2,
            "skillId": 3,
            "typeId": 99002,
            "ready": True,
            "blocked": False,
            "passive": False,
            "validTargetIds": [5],
        }
    )
    exploration_decision = evaluate(
        recipe_config, exploration_state, measured_memory
    )
    assert exploration_decision is not None
    assert exploration_decision.skill["typeId"] == 99002

    missing_recipe_state = json.loads(json.dumps(recipe_state))
    missing_recipe_state["bosses"][0]["effects"] = []
    missing_recipe_state["skills"].append(
        {
            "slot": 3,
            "skillId": 2,
            "typeId": 47101,
            "name": "fear-provider",
            "ready": True,
            "blocked": False,
            "passive": False,
            "validTargetIds": [5],
        }
    )
    fear_memory = SkillCapabilityMemory()
    learned_fear_state = json.loads(json.dumps(missing_recipe_state))
    learned_fear_state["bosses"][0]["effects"] = [
        {
            "skillTypeId": 47101,
            "producerId": 0,
            "effectTypeId": 490,
            "effectKindId": 3001,
            "effectKind": "Fear",
            "turnsLeft": 2,
        }
    ]
    assert fear_memory.observe_state(learned_fear_state) == 1
    fear_decision = evaluate(recipe_config, missing_recipe_state, fear_memory)
    assert fear_decision is not None
    assert fear_decision.skill["typeId"] == 47101
    assert "维持必要效果" in fear_decision.rule

    objective_scoped_state = json.loads(json.dumps(missing_recipe_state))
    objective_scoped_state["bosses"][0]["challenges"] = [
        {
            "id": 8000601,
            "completed": False,
            "eligibleNow": True,
            "activeInChain": True,
            "requiredPrerequisiteTrialIds": [],
        },
        {
            "id": 8000607,
            "completed": False,
            "eligibleNow": True,
            "activeInChain": True,
            "requiredPrerequisiteTrialIds": [],
        },
        {
            "id": 8000608,
            "completed": False,
            "eligibleNow": False,
            "activeInChain": False,
            "requiredPrerequisiteTrialIds": [8000607],
        },
        {
            "id": 8000609,
            "completed": False,
            "eligibleNow": False,
            "activeInChain": False,
            "requiredPrerequisiteTrialIds": [8000607, 8000608],
        },
    ]
    objective_scoped_config = {
        "objectives": {"mandatoryTrialIds": [8000609]},
        "rules": [
            {
                "name": "selected-trial-overlay",
                "when": {"activeHeroTypeId": [8890, 8896]},
                "action": {"type": "executeTrialRecipe"},
            },
            {
                "name": "selected-trial-default",
                "when": {"activeHeroTypeId": [8890, 8896]},
                "action": {
                    "type": "defaultSkillPriority",
                    "prioritySkills": [
                        {
                            "skillTypeId": 88962,
                            "skillSlot": 2,
                            "target": {"type": "self"},
                        }
                    ],
                },
            },
        ],
    }
    objective_scoped_decision = evaluate(
        objective_scoped_config, objective_scoped_state, fear_memory
    )
    assert objective_scoped_decision is not None
    assert objective_scoped_decision.skill["typeId"] == 88962
    assert "保留试炼" in objective_scoped_decision.rule

    explicitly_requested_config = json.loads(json.dumps(objective_scoped_config))
    explicitly_requested_config["rules"][0]["action"]["trialIds"] = [8000601]
    explicitly_requested_decision = evaluate(
        explicitly_requested_config, objective_scoped_state, fear_memory
    )
    assert explicitly_requested_decision is not None
    assert explicitly_requested_decision.skill["typeId"] == 47101
    assert "恐惧下技能伤害" in explicitly_requested_decision.rule

    preparation_state = json.loads(json.dumps(recipe_state))
    preparation_state["chimera"].update(
        {"currentForm": "Ultimate", "turnCount": 3}
    )
    preparation_state["bosses"][0]["challenges"][0].update(
        {"eligibleNow": False, "activeInChain": True}
    )
    preparation_config = json.loads(json.dumps(recipe_config))
    preparation_config["rules"][0]["when"] = {
        "activeTrialsAny": [8000501]
    }
    preparation_config["rules"][0]["action"]["prepareWithinBossTurns"] = 3
    preparation_decision = evaluate(preparation_config, preparation_state)
    assert preparation_decision is not None
    assert preparation_decision.skill["slot"] == 2
    assert "准备" in preparation_decision.rule
    protective_memory = SkillCapabilityMemory()
    learned_protection = json.loads(json.dumps(preparation_state))
    learned_protection["heroes"][0]["effects"].append(
        {
            "skillTypeId": 88962,
            "producerId": 0,
            "effectTypeId": 280,
            "effectKindId": 2004,
            "effectKind": "Shield",
            "lifetime": 2,
            "turnsLeft": 2,
        }
    )
    assert protective_memory.observe_state(learned_protection) == 1
    protective_preparation = evaluate(
        preparation_config, preparation_state, protective_memory
    )
    assert protective_preparation is not None
    assert protective_preparation.skill["slot"] == 2
    assert "先建立保护" in protective_preparation.rule
    repeated_rotation = json.loads(json.dumps(state))
    repeated_rotation["chimera"] = {
        "id": 5,
        "currentForm": "Ultimate",
        "turnCount": 21,
    }
    repeated_rotation["rotationIdentity"]["metadata"]["formSequence"] = [
        {"sequenceIndex": 5, "form": "Ultimate"},
        {"sequenceIndex": 10, "form": "Ram"},
        {"sequenceIndex": 15, "form": "Ultimate"},
        {"sequenceIndex": 20, "form": "Lion"},
        {"sequenceIndex": 25, "form": "Ultimate"},
        {"sequenceIndex": 30, "form": "Snake"},
    ]
    assert matches({"nextForm": "Snake"}, repeated_rotation)
    assert matches({"nextForm": "Viper"}, repeated_rotation)
    assert not matches({"nextForm": "Ram"}, repeated_rotation)
    for turn_count, current_form, expected_next in (
        (0, "Ultimate", "Ram"),
        (5, "Ultimate", "Ram"),
        (6, "Ram", "Ultimate"),
        (11, "Ultimate", "Lion"),
        (16, "Lion", "Ultimate"),
        (21, "Ultimate", "Snake"),
        (26, "Snake", "Ultimate"),
        (31, "Ultimate", "Ram"),
        (41, "Ultimate", "Lion"),
        (51, "Ultimate", "Snake"),
    ):
        cycle_state = json.loads(json.dumps(state))
        cycle_state["chimera"].update(
            {"turnCount": turn_count, "currentForm": current_form}
        )
        assert next_chimera_form(cycle_state) == expected_next
    boundary_state = json.loads(json.dumps(state))
    boundary_state["chimera"].update(
        {"turnCount": 5, "currentForm": "Ultimate"}
    )
    assert turns_until_form_change(boundary_state) == 0
    boundary_state["chimera"].update({"turnCount": 6, "currentForm": "Ram"})
    assert turns_until_form_change(boundary_state) == 4
    boundary_state["chimera"].update({"turnCount": 26, "currentForm": "Snake"})
    assert matches({"form": "Viper"}, boundary_state)
    assert not matches(
        {
            "allyHasEffect": {
                "heroTypeId": 8896,
                "effect": {"kind": "Shield"},
            }
        },
        state,
    )
    assert not matches(
        {"allyEffectSlotsAtMost": {"heroTypeId": 12345, "count": 1}},
        state,
    )
    assert not matches({"bossEffectSlotsAtMost": 0}, state)
    state_without_form_timing = json.loads(json.dumps(state))
    del state_without_form_timing["rotationIdentity"]["metadata"][
        "turnsBetweenForms"
    ]
    assert matches({"turnsUntilFormChangeAtMost": 3}, state_without_form_timing)
    assert not matches({"bossEffectSlotAtMost": 10}, state)
    for malformed_condition in (
        {"turnAtLeast": "7"},
        {"anyAllyHpPctBelow": []},
        {"bossHasEffects": []},
        {"bossHasEffect": {}},
        {"bossHasEffect": {"kind": "Fear", "turnsAtLeast": "2"}},
        {"completedTrialsAll": []},
        {"completedTrialsAll": [True]},
        {"trialProgressAtLeast": {}},
        {"trialProgressAtLeast": {"8000502": "0.4"}},
        {"allyHasEffect": {"effect": {"kind": "Shield"}}},
        {"effectConditions": []},
        {"effectConditions": [{"target": "boss", "presence": "has", "effect": {}}]},
        {"effectConditions": [{"target": "ally", "presence": "has", "effect": {"kind": "Shield"}}]},
        {"allyEffectSlotsAtMost": {"count": 10}},
    ):
        assert not matches(malformed_condition, state)
    for invalid_tree in (
        {
            "type": "rule",
            "when": {"bossEffectSlotAtMost": 10},
            "action": {
                "type": "cast",
                "skillSlot": 1,
                "target": "boss",
            },
        },
        {
            "type": "typo-priority",
            "action": {
                "type": "cast",
                "skillSlot": 1,
                "target": "boss",
            },
        },
        {"type": "cast", "target": "boss"},
        {"type": "cast", "skillSlot": 1},
        {"type": "priority", "children": None},
    ):
        assert evaluate({"strategyTree": invalid_tree}, state) is None
    assert evaluate({"rules": {}}, state) is None
    transform = evaluate(
        {
            "strategyTree": {
                "type": "rule",
                "name": "mythical-transform",
                "when": {
                    "activeHeroFormIndex": 0,
                    "activeHeroIsMetamorph": True,
                    "transformationReady": True,
                },
                "action": {"type": "transform"},
            }
        },
        state,
    )
    assert transform is not None
    assert transform.skill["slot"] == 4
    assert transform.target_id == state["activeHeroId"]
    transformed_state = json.loads(json.dumps(state))
    transformed_state["activeHeroFormIndex"] = 1
    transformed_state["activeHeroIsTransformed"] = True
    transformed_state["skills"] = [
        {
            "slot": 3,
            "skillId": 2,
            "typeId": 99993,
            "ready": True,
            "validTargetIds": [5],
        },
        {
            "slot": 4,
            "skillId": 3,
            "typeId": 99994,
            "ready": True,
            "passive": False,
            "blocked": False,
            "validTargetIds": [0],
        },
    ]
    transformed_cast = evaluate(
        {
            "strategyTree": {
                "type": "rule",
                "when": {
                    "activeHeroFormIndex": 1,
                    "activeHeroIsTransformed": True,
                },
                "action": {
                    "type": "cast",
                    "skillSlot": 3,
                    "target": "boss",
                },
            }
        },
        transformed_state,
    )
    assert transformed_cast is not None
    assert transformed_cast.skill["typeId"] == 99993
    cross_form_rule = {
        "rules": [
            {
                "name": "alternate-form-a3",
                "when": {
                    "activeHeroTypeId": 8896,
                    "activeHeroFormIndex": 1,
                },
                "action": {
                    "type": "cast",
                    "skillTypeId": 99993,
                    "skillSlot": 3,
                    "target": "boss",
                },
            },
            {
                "name": "same-form-fallback",
                "when": {"activeHeroTypeId": 8896},
                "action": {
                    "type": "cast",
                    "skillTypeId": 88961,
                    "skillSlot": 1,
                    "target": "boss",
                },
            },
        ]
    }
    cross_form_switch = evaluate(cross_form_rule, state)
    assert cross_form_switch is not None
    assert cross_form_switch.skill["typeId"] == 88964
    assert cross_form_switch.mythic_followup_rule == "alternate-form-a3"
    assert cross_form_switch.mythic_followup_action == {
        "type": "cast",
        "skillTypeId": 99993,
        "skillSlot": 3,
        "target": "boss",
        "formIndex": 1,
    }
    unavailable_switch_state = copy.deepcopy(state)
    unavailable_switch_state["skills"][2]["ready"] = False
    unavailable_switch_state["skills"][2]["validTargetIds"] = []
    skipped_cross_form = evaluate(cross_form_rule, unavailable_switch_state)
    assert skipped_cross_form is not None
    assert skipped_cross_form.rule == "same-form-fallback"
    assert skipped_cross_form.skill["typeId"] == 88961
    cross_form_default = {
        "rules": [
            {
                "name": "mythical-default",
                "when": {"activeHeroTypeId": 8896},
                "action": {
                    "type": "defaultSkillPriority",
                    "prioritySkills": [
                        {
                            "skillTypeId": 99993,
                            "skillSlot": 3,
                            "formIndex": 1,
                            "target": {"type": "boss"},
                        },
                        {
                            "skillTypeId": 88961,
                            "skillSlot": 1,
                            "formIndex": 0,
                            "target": {"type": "boss"},
                        },
                    ],
                    "blockedSkillTypeIds": [],
                    "reserveStrictRuleSkills": True,
                },
            }
        ]
    }
    default_form_switch = evaluate(cross_form_default, state)
    assert default_form_switch is not None
    assert default_form_switch.skill["typeId"] == 88964
    default_without_switch = evaluate(cross_form_default, unavailable_switch_state)
    assert default_without_switch is not None
    assert default_without_switch.skill["typeId"] == 88961
    followup_runtime = {
        "mythicSkillFollowup": {
            "activeHeroId": transformed_state["activeHeroId"],
            "formIndex": 1,
            "rule": "alternate-form-a3",
            "action": {
                "type": "cast",
                "skillTypeId": 99993,
                "skillSlot": 3,
                "formIndex": 1,
                "target": "boss",
            },
        }
    }
    followup = pending_mythic_followup_decision(
        followup_runtime, transformed_state
    )
    assert followup is not None
    assert followup.skill["typeId"] == 99993
    assert followup.consumes_mythic_followup
    config = {
        "objectives": {
            "mandatoryTrialIds": [8000501, 8000502],
            "minimumDamage": 1_000_000,
        },
        "strategyTree": {
            "type": "priority",
            "children": [
                {
                    "type": "branch",
                    "when": {"form": "Ram"},
                    "then": {
                        "type": "rule",
                        "name": "ram-a1",
                        "when": {"bossEffectSlotsAtMost": 9},
                        "action": {
                            "type": "cast",
                            "skillSlot": 1,
                            "target": "boss",
                        },
                    },
                    "else": {"type": "pause"},
                }
            ],
        },
    }
    report = evaluate_objectives(config, state)
    assert report.completed_trial_ids == (8000501,)
    assert report.missing_trial_ids == (8000502,)
    assert report.impossible_trial_ids == (8000502,)
    assert report.mandatory_impossible
    assert not report.all_met
    decision = evaluate(config, state)
    assert decision is not None
    assert decision.rule == "ram-a1"
    assert decision.target_id == 5
    assert decision.skill["typeId"] == 88961
    with tempfile.TemporaryDirectory() as directory:
        archive_path = Path(directory) / "rotations.json"
        ipc = FakeIpc(state)
        first_key = archive_rotation_catalog_if_changed(
            ipc, archive_path, state=state
        )
        first_mtime = archive_path.stat().st_mtime_ns
        time.sleep(0.01)
        with patch(
            "chimera_controller.NamedMutex",
            side_effect=AssertionError("unchanged archive reached disk path"),
        ):
            second_key = archive_rotation_catalog_if_changed(
                ipc, archive_path, state=state
            )
        second_mtime = archive_path.stat().st_mtime_ns
        state["rotationIdentity"]["rewardRotationFingerprint"] = (
            "fnv1a64:test-rewards-v2"
        )
        state["trialCatalog"]["difficulties"][0]["trials"][0]["reward"] = {
            "rollCount": 1,
            "entries": [{"typeId": 1, "minCount": 2}],
        }
        third_key = archive_rotation_catalog_if_changed(
            ipc, archive_path, state=state
        )
        archived = json.loads(archive_path.read_text(encoding="utf-8"))
        assert first_key == second_key == ("fnv1a64:test-rewards", 1302, 5)
        assert first_mtime == second_mtime
        assert third_key == ("fnv1a64:test-rewards-v2", 1302, 5)
        assert len(archived["trialDefinitions"]) == 1
        assert len(archived["rewardRotations"]) == 2
        record = archived["attributeRotations"]["fnv1a64:test-attributes"]
        assert len(record["observations"]) == 1
        assert record["observations"][0]["stageRotationIndex"] == 1
    print("chimera-strategy-tree-tests-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
