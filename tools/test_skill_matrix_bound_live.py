from __future__ import annotations

import argparse
import json
import secrets
import time
from pathlib import Path
from typing import Any

from agent_ipc import AgentIpc
from chimera_controller import (
    decision_from_action,
    process_state,
    turn_key,
)
from inject_probe import set_takeover
from reload_bound_agent import bound_state


EXPECTED_HERO_IDS = [39104, 21597, 34700, 21826, 26679]


def target_selector(skill: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    valid = {
        value for value in skill.get("validTargetIds", []) if isinstance(value, int)
    }
    if not valid:
        return None
    chimera_id = (state.get("chimera") or {}).get("id")
    boss_ids = {
        item.get("id")
        for item in state.get("bosses", [])
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    }
    if (isinstance(chimera_id, int) and chimera_id in valid) or valid & boss_ids:
        return {"type": "boss"}
    active_id = state.get("activeHeroId")
    if isinstance(active_id, int) and active_id in valid:
        return {"type": "self"}
    for hero in state.get("heroes", []):
        if (
            isinstance(hero, dict)
            and hero.get("id") in valid
            and hero.get("dead") is not True
            and isinstance(hero.get("typeId"), int)
        ):
            return {"type": "allyHeroTypeId", "heroTypeId": hero["typeId"]}
    return None


def choose_action(
    state: dict[str, Any], *, prefer_non_basic: bool
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    skills = [
        skill
        for skill in state.get("skills", [])
        if isinstance(skill, dict)
        and skill.get("passive") is not True
        and skill.get("ready") is True
        and skill.get("blocked") is not True
    ]
    ordered = sorted(
        skills,
        key=lambda skill: (
            0 if prefer_non_basic and int(skill.get("slot", 0)) > 1 else 1,
            int(skill.get("slot", 999)),
        ),
    )
    for skill in ordered:
        if prefer_non_basic and int(skill.get("slot", 0)) <= 1:
            continue
        selector = target_selector(skill, state)
        if selector is not None:
            return (
                {
                    "type": "cast",
                    "skillTypeId": skill.get("typeId"),
                    "skillSlot": skill.get("slot"),
                    "target": selector,
                },
                skill,
            )
    if prefer_non_basic:
        return None
    for skill in ordered:
        selector = target_selector(skill, state)
        if selector is not None:
            return (
                {
                    "type": "cast",
                    "skillTypeId": skill.get("typeId"),
                    "skillSlot": skill.get("slot"),
                    "target": selector,
                },
                skill,
            )
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--agent", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-commands", type=int, default=15)
    parser.add_argument("--target-hero-count", type=int, default=5)
    args = parser.parse_args()

    result: dict[str, Any] = {
        "ok": False,
        "pid": args.pid,
        "accountName": args.account_name,
        "userId": args.user_id,
        "savedResult": False,
        "actions": [],
        "testedHeroTypeIds": [],
    }
    session_id = secrets.randbits(63) or 1
    takeover_armed = False
    code = 1
    try:
        initial = bound_state(args.pid, args.account_name, args.user_id)
        lifecycle = initial["lifecycle"]
        if lifecycle.get("screen") != "battle":
            raise RuntimeError(f"skill matrix requires battle screen: {lifecycle.get('screen')}")
        if (lifecycle.get("battle") or {}).get("heroIds") != EXPECTED_HERO_IDS:
            raise RuntimeError("battle team does not match the bound five-hero team")

        with AgentIpc(args.pid) as ipc:
            takeover = set_takeover(
                args.pid,
                args.agent.resolve(),
                session_id=session_id,
                active=True,
                expected_user_id=args.user_id,
            )
            if takeover.get("accepted") is not True:
                raise RuntimeError(f"takeover rejected: {takeover}")
            takeover_armed = True

            tested: set[int] = set()
            commands = 0
            nonce = secrets.randbits(30) or 1
            overall_deadline = time.monotonic() + 180.0
            while (
                time.monotonic() < overall_deadline
                and commands < args.max_commands
                and len(tested) < args.target_hero_count
            ):
                account = ipc.account() or {}
                if (
                    account.get("accountName") != args.account_name
                    or account.get("userId") != args.user_id
                ):
                    raise RuntimeError("bound game account changed during skill matrix")
                current_lifecycle = ipc.lifecycle() or {}
                if current_lifecycle.get("screen") == "result":
                    result["stoppedAtResult"] = True
                    break
                if current_lifecycle.get("screen") != "battle":
                    raise RuntimeError(
                        f"battle screen changed during skill matrix: {current_lifecycle.get('screen')}"
                    )

                state = ipc.decision() or {}
                battle = state.get("battle") or {}
                hero_type_id = state.get("activeHeroTypeId")
                if (
                    battle.get("waitingForManualCommand") is not True
                    or not isinstance(hero_type_id, int)
                    or hero_type_id <= 0
                ):
                    time.sleep(0.02)
                    continue

                baseline_key = turn_key(state)
                prefer_non_basic = hero_type_id not in tested
                chosen = choose_action(state, prefer_non_basic=prefer_non_basic)
                if chosen is None and prefer_non_basic:
                    settle_deadline = time.monotonic() + 2.0
                    while time.monotonic() < settle_deadline:
                        refreshed = ipc.decision() or {}
                        if turn_key(refreshed) != baseline_key:
                            state = refreshed
                            break
                        state = refreshed
                        chosen = choose_action(state, prefer_non_basic=True)
                        if chosen is not None:
                            break
                        time.sleep(0.02)

                is_matrix_action = chosen is not None and prefer_non_basic
                if chosen is None:
                    chosen = choose_action(state, prefer_non_basic=False)
                if chosen is None:
                    time.sleep(0.05)
                    continue
                action, skill = chosen
                preview = decision_from_action("live skill matrix", action, state)
                if preview is None:
                    raise RuntimeError("selected skill or target became invalid before submit")

                config = {
                    "mode": "execute",
                    "objectives": {
                        "mandatoryTrialIds": [],
                        "minimumDamage": 0,
                        "minimumCompetitionPoints": 0,
                    },
                    "safety": {"requireFreshSnapshotMs": 1500},
                    "rules": [
                        {
                            "name": "live skill matrix",
                            "when": {"activeHeroTypeId": hero_type_id},
                            "action": action,
                        }
                    ],
                }
                nonce += 1
                executed = process_state(
                    config,
                    state,
                    agent=args.agent.resolve(),
                    ipc=ipc,
                    session_id=session_id,
                    execute_requested=True,
                    nonce=nonce,
                    ignore_freshness=commands == 0,
                )
                if not executed:
                    time.sleep(0.05)
                    continue
                commands += 1
                record = {
                    "heroId": state.get("activeHeroId"),
                    "heroTypeId": hero_type_id,
                    "heroName": state.get("activeHeroName"),
                    "skillSlot": skill.get("slot"),
                    "skillTypeId": skill.get("typeId"),
                    "skillName": skill.get("name"),
                    "validTargetIds": skill.get("validTargetIds"),
                    "selectedTargetId": preview.target_id,
                    "selectedTarget": preview.target_label,
                    "nonBasicCoverage": is_matrix_action,
                }
                result["actions"].append(record)
                if is_matrix_action:
                    tested.add(hero_type_id)

            result["testedHeroTypeIds"] = sorted(tested)
            result["commandCount"] = commands
            result["ok"] = len(tested) >= args.target_hero_count
            if not result["ok"] and "stoppedAtResult" not in result:
                result["error"] = (
                    f"covered {len(tested)}/{args.target_hero_count} heroes "
                    f"within {commands}/{args.max_commands} commands"
                )
            code = 0 if result["ok"] else 1
    except Exception as error:
        result["error"] = str(error)
    finally:
        if takeover_armed:
            try:
                set_takeover(
                    args.pid,
                    args.agent.resolve(),
                    session_id=session_id,
                    active=False,
                )
            except Exception as error:
                result["cleanupError"] = str(error)
                result["ok"] = False
                code = 1
        encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(args.output)
        print(encoded, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
