from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from boss_modes import (
    canonical_hydra_head_type_id,
    default_store,
    hydra_head_is_exposed_neck,
    normalize_hydra_head,
    strategy_for_mode,
    update_mode_strategy,
)
from chimera_controller import (
    evaluate,
    select_target,
    start_first_battle_if_ready,
    validate_strategy_config,
)


class HydraSelectionIpc:
    def __init__(self) -> None:
        self.lifecycle_reads = 0

    def lifecycle(self) -> dict[str, object]:
        self.lifecycle_reads += 1
        if self.lifecycle_reads == 1:
            return {
                "screen": "team_selection",
                "selection": {
                    "bossMode": "hydra",
                    "filled": True,
                    "context": 1234,
                    "heroIds": [1, 2, 3, 4, 5, 6],
                },
            }
        return {"screen": "battle"}

    def decision(self) -> None:
        return None


def main() -> None:
    live_wrath_neck = {
        "typeId": 26260,
        "avatar": "HeroAvatars/26240-1",
        "isHydraHead": False,
        "isHydraNeck": False,
        "skills": [
            {"typeId": 260009},
            {"typeId": 260010},
            {"typeId": 260011},
        ],
    }
    assert canonical_hydra_head_type_id(live_wrath_neck) == 26240
    assert hydra_head_is_exposed_neck(live_wrath_neck)
    normalized_neck = normalize_hydra_head(live_wrath_neck)
    assert normalized_neck["canonicalTypeId"] == 26240
    assert normalized_neck["isHydraNeck"] is True
    assert normalized_neck["headState"] == "exposed_neck"
    assert canonical_hydra_head_type_id(
        {"typeId": 26130, "skills": [{"typeId": 261201}]}
    ) == 26120
    assert canonical_hydra_head_type_id(
        {"typeId": 26310, "avatar": "HeroAvatars/26300"}
    ) == 26300

    store = default_store()
    chimera = strategy_for_mode(store, "chimera")
    hydra = strategy_for_mode(store, "hydra")
    assert chimera["scope"]["battleKind"] == "AllianceChimera"
    assert hydra["scope"]["battleKind"] == "AllianceHydra"
    assert "mandatoryTrialIds" in chimera["objectives"]
    assert "mandatoryTrialIds" not in hydra["objectives"]

    hydra["rules"] = [
        {
            "name": "rescue",
            "when": {"activeHeroTypeId": [100]},
            "action": {
                "type": "cast",
                "skillSlot": 2,
                "target": {"type": "devouringHead"},
            },
        }
    ]
    hydra["team"] = {"heroIds": [1, 2, 3, 4, 5, 6]}
    validate_strategy_config(hydra, boss_mode="hydra")
    updated = update_mode_strategy(store, "hydra", hydra)
    assert strategy_for_mode(updated, "chimera") == chimera
    assert strategy_for_mode(updated, "hydra")["team"]["heroIds"][-1] == 6

    hydra_with_turn_counters = strategy_for_mode(updated, "hydra")
    hydra_with_turn_counters["rules"][0]["when"].update(
        {
            "round": 2,
            "turnAtLeast": 5,
            "turnAtMost": 9,
            "playerTurnCount": 4,
            "activeHeroTurnCount": 3,
            "skillCooldownConditions": [
                {"heroTypeId": 100, "skillTypeId": 1002, "turnsAtMost": 2}
            ],
        }
    )
    cleaned_store = update_mode_strategy(updated, "hydra", hydra_with_turn_counters)
    cleaned_hydra = strategy_for_mode(cleaned_store, "hydra")
    cleaned_when = cleaned_hydra["rules"][0]["when"]
    assert "round" not in cleaned_when
    assert "turnAtLeast" not in cleaned_when
    assert "turnAtMost" not in cleaned_when
    assert "playerTurnCount" not in cleaned_when
    assert "activeHeroTurnCount" not in cleaned_when
    assert cleaned_when["skillCooldownConditions"][0]["turnsAtMost"] == 2

    state = {
        "activeHeroId": 10,
        "heroes": [
            {
                "id": 10,
                "typeId": 100,
                "skills": [{"typeId": 1002, "cooldown": 0}],
            },
            {
                "id": 11,
                "typeId": 200,
                "skills": [{"typeId": 2003, "cooldown": 2}],
            },
        ],
        "bosses": [
            {
                "id": 21,
                "typeId": 26040,
                "name": "head-1",
                "healthPct": 70,
                "effects": [],
            },
            {
                "id": 22,
                "typeId": 26120,
                "name": "head-2",
                "healthPct": 90,
                "devouredHeroId": 11,
                "isDevouring": True,
                "effects": [{"effectKind": "PoisonCloud"}],
            },
            {
                "id": 23,
                "typeId": 26200,
                "name": "neck",
                "healthPct": 40,
                "isHydraNeck": True,
                "effects": [],
            },
        ],
    }
    skill = {"validTargetIds": [21, 22, 23]}
    assert select_target({"type": "devouringHead"}, skill, state)[0] == 22
    assert select_target({"type": "exposedNeck"}, skill, state)[0] == 23
    assert select_target({"type": "lowestHpBoss"}, skill, state)[0] == 23
    # The old positional selector is deliberately migrated to a safe fallback.
    assert select_target({"type": "hydraHeadSlot", "position": 2}, skill, state)[0] == 23
    priority = {
        "type": "hydraHeadPriority",
        "headTypeIds": [26120, 26040],
        "fallback": "lowestHp",
    }
    assert select_target(priority, skill, state)[0] == 22
    # Runtime aliases still match the stable identities stored by the editor.
    state["bosses"][1].update(
        {"typeId": 26130, "avatar": "HeroAvatars/26120", "isHydraHead": False}
    )
    assert select_target(priority, skill, state)[0] == 22
    state["bosses"][2].update(
        {
            "typeId": 26260,
            "avatar": "HeroAvatars/26240-1",
            "isHydraNeck": False,
            "skills": [{"typeId": 260009}],
        }
    )
    assert select_target({"type": "exposedNeck"}, skill, state)[0] == 23
    # A missing preferred identity falls through to the next identity.
    priority["headTypeIds"] = [99999, 26040]
    assert select_target(priority, skill, state)[0] == 21
    # A preferred head that is not a legal skill target is skipped as well.
    priority["headTypeIds"] = [26120, 26040]
    assert select_target(priority, {"validTargetIds": [21, 23]}, state)[0] == 21
    # With no preferred identity present, choose the deterministic lowest-HP target.
    priority["headTypeIds"] = [99998, 99999]
    assert select_target(priority, skill, state)[0] == 23
    assert select_target({"type": "auto"}, skill, state)[0] == 22
    state["activeHeroTypeId"] = 100
    state["skills"] = [
        {"slot": 2, "typeId": 1002, "ready": True, "validTargetIds": [21, 22, 23]}
    ]
    scoped_rule = {
        "rules": [
            {
                "name": "target-scoped-effect",
                "when": {
                    "activeHeroTypeId": [100],
                    "bossHasEffects": ["PoisonCloud"],
                },
                "action": {
                    "type": "cast",
                    "skillTypeId": 1002,
                    "target": {"type": "devouringHead"},
                },
            }
        ]
    }
    decision = evaluate(scoped_rule, state)
    assert decision is not None and decision.target_id == 22

    priority_scoped_rule = {
        "rules": [
            {
                "name": "condition-must-follow-legal-priority-target",
                "when": {
                    "activeHeroTypeId": [100],
                    "bossHasEffects": ["PoisonCloud"],
                },
                "action": {
                    "type": "cast",
                    "skillTypeId": 1002,
                    "target": {
                        "type": "hydraHeadPriority",
                        "headTypeIds": [26120, 26040],
                        "fallback": "lowestHp",
                    },
                },
            }
        ]
    }
    state["skills"][0]["validTargetIds"] = [21, 23]
    assert evaluate(priority_scoped_rule, state) is None
    state["skills"][0]["validTargetIds"] = [21, 22, 23]

    cooldown_rule = {
        "rules": [
            {
                "name": "wait-for-specific-hero-skill",
                "when": {
                    "activeHeroTypeId": [100],
                    "skillCooldownConditions": [
                        {
                            "heroTypeId": 200,
                            "skillTypeId": 2003,
                            "turnsAtLeast": 2,
                            "turnsAtMost": 3,
                        }
                    ],
                },
                "action": {
                    "type": "cast",
                    "skillTypeId": 1002,
                    "target": {"type": "lowestHpBoss"},
                },
            }
        ]
    }
    validate_strategy_config(cooldown_rule, boss_mode="hydra")
    decision = evaluate(cooldown_rule, state)
    assert decision is not None and decision.target_id == 23
    state["heroes"][1]["skills"][0]["cooldown"] = 1
    assert evaluate(cooldown_rule, state) is None

    hydra_ipc = HydraSelectionIpc()
    output = io.StringIO()
    with (
        patch(
            "chimera_controller.queue_lifecycle_command",
            return_value={"queued": True},
        ) as queued,
        patch(
            "chimera_controller.wait_for_command_ack",
            return_value={"status": "submitted"},
        ),
        patch("chimera_controller.require_takeover_active"),
        redirect_stdout(output),
    ):
        assert start_first_battle_if_ready(
            hydra_ipc,
            pid=99,
            agent=Path("agent.dll"),
            session_id=77,
            boss_mode="hydra",
        )
    assert queued.call_args.kwargs["context"] == 1234
    assert "6名英雄开始首场六头蛇战斗" in output.getvalue()
    print("boss-mode-tests-ok")


if __name__ == "__main__":
    main()
