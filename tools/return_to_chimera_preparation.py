from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

from agent_ipc import AgentIpc
from chimera_controller import (
    account_binding,
    require_account_binding,
    submit_free_regroup,
    wait_for_lifecycle_screen,
)
from inject_probe import set_takeover


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Return one exact in-game account to Chimera team selection"
    )
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--agent", required=True, type=Path)
    args = parser.parse_args()

    session_id = secrets.randbits(63) or 1
    armed = False
    agent = args.agent.resolve()
    try:
        with AgentIpc(args.pid) as ipc:
            expected = account_binding(ipc.account(), args.pid)
            if (
                expected.account_name != args.account_name
                or expected.user_id != args.user_id
            ):
                raise RuntimeError("the selected in-game account identity changed")
            takeover = set_takeover(
                args.pid,
                agent,
                session_id=session_id,
                active=True,
                expected_user_id=expected.user_id,
            )
            if takeover.get("accepted") is not True:
                raise RuntimeError(f"takeover rejected: {takeover}")
            armed = True
            require_account_binding(ipc, expected)
            submit_free_regroup(
                ipc,
                pid=args.pid,
                agent=agent,
                session_id=session_id,
            )
            lifecycle = wait_for_lifecycle_screen(
                ipc, session_id, "team_selection", timeout_seconds=30.0
            )
            selection = lifecycle.get("selection") or {}
            if (
                lifecycle.get("screen") != "team_selection"
                or selection.get("areaTypeId") != 13
            ):
                raise RuntimeError("the verified Chimera preparation screen was not reached")
            print(
                f"returned {expected.account_name} ({expected.user_id}) to Chimera preparation; "
                "no result was saved"
            )
        return 0
    finally:
        if armed:
            set_takeover(
                args.pid,
                agent,
                session_id=session_id,
                active=False,
            )


if __name__ == "__main__":
    sys.exit(main())
