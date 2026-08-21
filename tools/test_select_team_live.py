from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path
from typing import Any

from agent_ipc import AgentIpc
from chimera_controller import refresh_team_selection, select_team_heroes
from inject_probe import set_takeover
from reload_bound_agent import bound_state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--agent", required=True, type=Path)
    parser.add_argument("--hero-id", action="append", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    result: dict[str, Any] = {
        "ok": False,
        "pid": args.pid,
        "accountName": args.account_name,
        "userId": args.user_id,
        "requestedHeroIds": args.hero_id,
        "startedBattle": False,
    }
    session_id = secrets.randbits(63) or 1
    takeover_armed = False
    try:
        state = bound_state(args.pid, args.account_name, args.user_id)
        if state["lifecycle"].get("screen") != "team_selection":
            raise RuntimeError("live team selection test requires team_selection")
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
            refreshed = refresh_team_selection(
                ipc,
                pid=args.pid,
                agent=args.agent.resolve(),
                session_id=session_id,
                nonce=101,
            )
            existing = (refreshed.get("selection") or {}).get("heroIds", [])
            if existing and existing != args.hero_id:
                raise RuntimeError(f"refusing to replace existing team: {existing}")
            selected = select_team_heroes(
                ipc,
                pid=args.pid,
                agent=args.agent.resolve(),
                session_id=session_id,
                hero_ids=args.hero_id,
                nonce=102,
            )
            result.update(
                {
                    "ok": True,
                    "before": refreshed,
                    "after": selected,
                }
            )
        code = 0
    except Exception as error:
        result["error"] = str(error)
        code = 1
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
