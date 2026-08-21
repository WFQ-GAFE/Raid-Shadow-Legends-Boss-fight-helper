from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from agent_ipc import AgentIpc, SHARED_STATE_VERSION
from raid_processes import raid_processes


class AccountIdentityChanged(RuntimeError):
    pass


def resolve_account(account_name: str) -> tuple[int, AgentIpc] | None:
    matches: list[tuple[int, AgentIpc]] = []
    for pid in raid_processes():
        ipc: AgentIpc | None = None
        try:
            ipc = AgentIpc(pid).open()
            header = ipc.header()
            account = ipc.account() or {}
            if (
                header.get("sharedStateVersion") == SHARED_STATE_VERSION
                and str(account.get("accountName", "")).casefold()
                == account_name.casefold()
                and isinstance(account.get("userId"), int)
                and not isinstance(account.get("userId"), bool)
                and account["userId"] > 0
            ):
                matches.append((pid, ipc))
                ipc = None
        except (FileNotFoundError, ValueError, OSError):
            pass
        finally:
            if ipc is not None:
                ipc.close()
    if len(matches) != 1:
        for _, ipc in matches:
            ipc.close()
        return None
    return matches[0]


def compact_snapshot(
    pid: int, ipc: AgentIpc, expected_account_name: str
) -> dict[str, Any]:
    header = ipc.header()
    account = ipc.account() or {}
    if (
        str(account.get("accountName", "")).casefold()
        != expected_account_name.casefold()
        or not isinstance(account.get("userId"), int)
        or isinstance(account.get("userId"), bool)
        or account["userId"] <= 0
    ):
        raise AccountIdentityChanged("monitored game account identity changed")
    lifecycle = ipc.lifecycle() or {}
    decision = ipc.decision() or {}
    battle = decision.get("battle") if isinstance(decision, dict) else {}
    chimera = decision.get("chimera") if isinstance(decision, dict) else {}
    return {
        "pid": pid,
        "gameAccountName": account.get("accountName"),
        "gameUserId": account.get("userId"),
        "agentBuildId": header.get("buildId"),
        "agentReadyState": header.get("readyState"),
        "hooksReady": header.get("hooksReady"),
        "screen": lifecycle.get("screen"),
        "takeoverState": lifecycle.get("takeoverState"),
        "lifecycleReason": lifecycle.get("reason"),
        "battleContext": (lifecycle.get("battle") or {}).get("context"),
        "decisionSequence": decision.get("sequence"),
        "round": (battle or {}).get("round"),
        "turn": (battle or {}).get("turn"),
        "playerTurnCount": (battle or {}).get("playerTurnCount"),
        "autoMode": (battle or {}).get("autoMode"),
        "finished": (battle or {}).get("finished"),
        "activeHeroId": decision.get("activeHeroId"),
        "chimeraForm": (chimera or {}).get("currentForm"),
        "chimeraTurnCount": (chimera or {}).get("turnCount"),
    }


def write_checkpoint(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="只读监测指定游戏内账户")
    parser.add_argument("--account", default="gafee")
    parser.add_argument("--duration-hours", type=float, default=8.0)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--checkpoint-minutes", type=float, default=15.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    started_wall = time.time()
    deadline = time.monotonic() + max(0.0, args.duration_hours) * 3600.0
    checkpoint_interval = max(60.0, args.checkpoint_minutes * 60.0)
    next_checkpoint = time.monotonic() + checkpoint_interval
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "accountName": args.account,
        "startedAtUnix": started_wall,
        "durationHours": args.duration_hours,
        "readOnly": True,
        "monitorPid": os.getpid(),
        "samples": 0,
        "resolutionMisses": 0,
        "identityMismatches": 0,
        "readErrors": 0,
        "events": [],
        "completed": False,
    }
    write_checkpoint(args.output, report)
    current: tuple[int, AgentIpc] | None = None
    last_snapshot: dict[str, Any] | None = None
    last_resolve_attempt = 0.0
    dirty = True
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if current is None and now - last_resolve_attempt >= 10.0:
                last_resolve_attempt = now
                current = resolve_account(args.account)
                if current is None:
                    report["resolutionMisses"] += 1
            if current is not None:
                pid, ipc = current
                try:
                    snapshot = compact_snapshot(pid, ipc, args.account)
                    report["samples"] += 1
                    report["lastSnapshot"] = snapshot
                    if last_snapshot != snapshot:
                        event = {
                            "observedAtUnix": time.time(),
                            "state": snapshot,
                        }
                        events = report["events"]
                        events.append(event)
                        if len(events) > 512:
                            del events[: len(events) - 512]
                        last_snapshot = snapshot
                        dirty = True
                except AccountIdentityChanged:
                    report["identityMismatches"] += 1
                    ipc.close()
                    current = None
                    dirty = True
                except (FileNotFoundError, ValueError, OSError):
                    report["readErrors"] += 1
                    ipc.close()
                    current = None
                    dirty = True
            if dirty and now >= next_checkpoint:
                write_checkpoint(args.output, report)
                dirty = False
                next_checkpoint = now + checkpoint_interval
            time.sleep(max(0.25, args.interval_seconds))
    finally:
        if current is not None:
            current[1].close()
        report["completed"] = time.monotonic() >= deadline
        report["finishedAtUnix"] = time.time()
        write_checkpoint(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
