from __future__ import annotations

import argparse
import json
import secrets
import time
from pathlib import Path
from typing import Any

from agent_ipc import AgentIpc
from chimera_controller import free_regroup_and_retry_manual
from inject_probe import seed_battle_context, set_takeover
from reload_bound_agent import bound_state


EXPECTED_HERO_IDS = [39104, 21597, 34700, 21826, 26679]


def _lifecycle_summary(value: dict[str, Any]) -> dict[str, Any]:
    battle = value.get("battle") or {}
    selection = value.get("selection") or {}
    return {
        "screen": value.get("screen"),
        "reason": value.get("reason"),
        "battleContext": battle.get("context"),
        "battleHeroIds": battle.get("heroIds"),
        "selectionContext": selection.get("context"),
        "selectionHeroIds": selection.get("heroIds"),
        "selectionFilled": selection.get("filled"),
        "autoBattle": selection.get("autoBattle"),
        "quickBattle": selection.get("quickBattle"),
        "areaTypeId": selection.get("areaTypeId"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--agent", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed-context", type=int, default=0)
    args = parser.parse_args()

    result: dict[str, Any] = {
        "ok": False,
        "pid": args.pid,
        "accountName": args.account_name,
        "userId": args.user_id,
        "savedResult": False,
    }
    session_id = secrets.randbits(63) or 1
    takeover_armed = False
    code = 1
    try:
        initial = bound_state(args.pid, args.account_name, args.user_id)
        if args.seed_context:
            seeded = seed_battle_context(
                args.pid, args.agent.resolve(), args.seed_context
            )
            if seeded.get("accepted") is not True:
                raise RuntimeError(f"battle context recovery rejected: {seeded}")
            deadline = time.monotonic() + 3.0
            while True:
                initial = bound_state(args.pid, args.account_name, args.user_id)
                if initial["lifecycle"].get("screen") == "battle":
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            result["seed"] = seeded
        before = initial["lifecycle"]
        if before.get("screen") == "result":
            raise RuntimeError("refusing regroup on result screen")
        if before.get("screen") != "battle":
            raise RuntimeError(f"regroup test requires battle screen: {before.get('screen')}")
        old_context = (before.get("battle") or {}).get("context")
        old_hero_ids = (before.get("battle") or {}).get("heroIds")
        if not isinstance(old_context, int) or old_context <= 0:
            raise RuntimeError("battle context is unavailable")
        if old_hero_ids is None:
            old_hero_ids = list(EXPECTED_HERO_IDS)
        if old_hero_ids != EXPECTED_HERO_IDS:
            raise RuntimeError(f"battle team does not match the bound team: {old_hero_ids}")

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
            free_regroup_and_retry_manual(
                ipc,
                pid=args.pid,
                agent=args.agent.resolve(),
                session_id=session_id,
                nonce=secrets.randbits(31) or 1,
                desired_hero_ids=EXPECTED_HERO_IDS,
            )

        final = bound_state(args.pid, args.account_name, args.user_id)
        after = final["lifecycle"]
        new_context = (after.get("battle") or {}).get("context")
        new_hero_ids = (after.get("battle") or {}).get("heroIds")
        if after.get("screen") != "battle":
            raise RuntimeError(f"new battle was not reached: {after.get('screen')}")
        if not isinstance(new_context, int) or new_context <= 0:
            raise RuntimeError("new battle context is unavailable")
        if new_context == old_context:
            raise RuntimeError("new battle reused the old battle context")
        if new_hero_ids != old_hero_ids:
            raise RuntimeError(f"team changed across regroup: {old_hero_ids} -> {new_hero_ids}")

        result.update(
            {
                "ok": True,
                "sessionId": session_id,
                "oldContext": old_context,
                "newContext": new_context,
                "heroIds": new_hero_ids,
                "before": _lifecycle_summary(before),
                "after": _lifecycle_summary(after),
                "lastAck": final.get("acknowledgement"),
            }
        )
        code = 0
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
