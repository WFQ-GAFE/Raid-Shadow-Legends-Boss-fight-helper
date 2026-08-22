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
    annotate_team_positions,
    archive_rotation_catalog_if_changed,
    evaluate,
    evaluate_objectives,
    is_battle_decision_state,
    load_trial_recipes,
    recoverable_command_rejection,
    matches,
    next_chimera_form,
    process_state,
    require_takeover_active,
    safety_reason,
    turns_until_form_change,
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
    unreserved_default = {"rules": [copy.deepcopy(strict_and_default["rules"][1])]}
    unreserved_decision = evaluate(unreserved_default, positioned)
    assert unreserved_decision is not None
    assert unreserved_decision.skill["typeId"] == 88962
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

    recipes = load_trial_recipes()
    assert len(recipes) == 27
    assert recipes[8000501]["automation"] == "automatic"
    assert recipes[8000503]["automation"] == "manual"
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
    assert no_trial_fallback.skill["slot"] == 1
    assert "无需专用动作" in no_trial_fallback.rule

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
    assert preparation_decision.skill["slot"] == 1
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
