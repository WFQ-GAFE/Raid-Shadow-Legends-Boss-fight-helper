from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from agent_ipc import AgentIpc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--target-form-count", type=int, default=2)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    transitions: list[dict[str, Any]] = []
    last_form: tuple[Any, Any] | None = None
    error: str | None = None
    deadline = time.monotonic() + args.timeout
    try:
        with AgentIpc(args.pid) as ipc:
            while time.monotonic() < deadline:
                account = ipc.account() or {}
                if (
                    account.get("accountName") != args.account_name
                    or account.get("userId") != args.user_id
                ):
                    raise RuntimeError("bound account changed during form sampling")
                lifecycle = ipc.lifecycle() or {}
                if lifecycle.get("screen") == "result":
                    break
                decision = ipc.decision() or {}
                chimera = decision.get("chimera") or {}
                form_key = (chimera.get("currentFormIndex"), chimera.get("currentForm"))
                if form_key[0] is not None and form_key != last_form:
                    battle = decision.get("battle") or {}
                    transitions.append(
                        {
                            "formIndex": form_key[0],
                            "form": form_key[1],
                            "chimeraTurnCount": chimera.get("turnCount"),
                            "battleTurn": battle.get("turn"),
                            "playerTurnCount": battle.get("playerTurnCount"),
                            "damage": battle.get("currentDamage"),
                            "competitionPoints": battle.get("currentCompetitionPoints"),
                        }
                    )
                    last_form = form_key
                    if len(transitions) >= args.target_form_count:
                        break
                time.sleep(0.01)
    except Exception as exc:
        error = str(exc)

    result = {
        "ok": error is None and len(transitions) >= args.target_form_count,
        "pid": args.pid,
        "accountName": args.account_name,
        "userId": args.user_id,
        "transitions": transitions,
        "error": error,
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(args.output)
    print(encoded, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
