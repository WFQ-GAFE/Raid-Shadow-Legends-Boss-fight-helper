from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_ipc import AgentIpc
from inject_probe import seed_battle_context, seed_selection_context


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INJECTOR = PROJECT_ROOT / "tools" / "inject_probe.py"


def bound_state(pid: int, account_name: str, user_id: int) -> dict[str, Any]:
    with AgentIpc(pid) as ipc:
        account = ipc.account() or {}
        if (
            account.get("pid") != pid
            or account.get("accountName") != account_name
            or account.get("userId") != user_id
        ):
            raise RuntimeError(
                f"bound account mismatch: expected {account_name}/{user_id}, got {account}"
            )
        return {
            "header": ipc.header(),
            "account": account,
            "lifecycle": ipc.lifecycle() or {},
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--agent", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result: dict[str, Any] = {
        "ok": False,
        "pid": args.pid,
        "accountName": args.account_name,
        "userId": args.user_id,
    }
    try:
        previous = bound_state(args.pid, args.account_name, args.user_id)
        lifecycle = previous["lifecycle"]
        screen = lifecycle.get("screen")
        if screen == "result":
            raise RuntimeError("refusing to reload agent on result screen")
        context: int | None = None
        if screen == "battle":
            context = (lifecycle.get("battle") or {}).get("context")
        elif screen == "team_selection":
            context = (lifecycle.get("selection") or {}).get("context")

        command = [
            sys.executable,
            str(INJECTOR),
            "--pid",
            str(args.pid),
            "--agent",
            str(args.agent.resolve()),
            "--reload",
        ]
        loaded = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
        payload = json.loads(loaded.stdout) if loaded.stdout.strip() else {}
        if loaded.returncode != 0 or payload.get("ok") is not True:
            raise RuntimeError(
                payload.get("reason") or loaded.stderr.strip() or "agent reload failed"
            )

        seeded: dict[str, Any] | None = None
        if isinstance(context, int) and context > 0:
            if screen == "battle":
                seeded = seed_battle_context(args.pid, args.agent.resolve(), context)
            elif screen == "team_selection":
                seeded = seed_selection_context(
                    args.pid, args.agent.resolve(), context
                )
            if seeded is not None and seeded.get("accepted") is not True:
                raise RuntimeError(f"context recovery rejected: {seeded}")

        current = bound_state(args.pid, args.account_name, args.user_id)
        result.update(
            {
                "ok": True,
                "previous": previous,
                "load": payload,
                "seed": seeded,
                "current": current,
            }
        )
        code = 0
    except Exception as error:
        result["error"] = str(error)
        code = 1

    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(args.output)
    print(encoded, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
