from __future__ import annotations

import argparse
import secrets
import time
from pathlib import Path
from typing import Any

from agent_ipc import AgentIpc
from inject_probe import queue_command, set_takeover
from raid_processes import raid_processes


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AGENT = (
    PROJECT_ROOT / "build" / "agent-1236" / "Release" / "RaidChimeraAgent.dll"
)


def resolve_account(account_name: str) -> tuple[int, dict[str, Any]]:
    matches: list[tuple[int, dict[str, Any]]] = []
    for pid in raid_processes():
        try:
            with AgentIpc(pid) as ipc:
                if not ipc.header().get("ready"):
                    continue
                account = ipc.account()
        except (FileNotFoundError, ValueError):
            continue
        if (
            isinstance(account, dict)
            and str(account.get("accountName", "")).casefold()
            == account_name.casefold()
        ):
            matches.append((pid, account))
    if len(matches) != 1:
        raise RuntimeError(f"账户匹配数量不是 1：{len(matches)}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="验证旧英雄形态命令会被进程内守卫拒绝")
    parser.add_argument("--account", default="gafee")
    parser.add_argument("--agent", type=Path, default=DEFAULT_AGENT)
    args = parser.parse_args()
    pid, account = resolve_account(args.account)
    agent = args.agent.resolve()
    session_id = secrets.randbits(63) or 1
    armed = False
    try:
        with AgentIpc(pid) as ipc:
            lifecycle = ipc.lifecycle() or {}
            state = ipc.decision() or {}
            if lifecycle.get("screen") != "battle":
                raise RuntimeError("当前不在奇美拉战斗")
            battle = state.get("battle", {})
            if battle.get("waitingForManualCommand") is not True:
                raise RuntimeError("当前没有等待英雄行动")
            skill = next(
                (
                    item
                    for item in state.get("skills", [])
                    if isinstance(item, dict)
                    and item.get("ready") is True
                    and item.get("validTargetIds")
                ),
                None,
            )
            if skill is None:
                raise RuntimeError("当前没有可用于只验证守卫的技能")
            takeover = set_takeover(
                pid,
                agent,
                session_id=session_id,
                active=True,
                expected_user_id=int(account["userId"]),
            )
            if not takeover.get("accepted"):
                raise RuntimeError(f"接管守卫拒绝测试：{takeover}")
            armed = True
            current_form = int(state["activeHeroFormIndex"])
            stale_form = 1 if current_form == 0 else 0
            nonce = secrets.randbelow(0x7FFFFFFE) + 1
            pointers = state["pointers"]
            queued = queue_command(
                pid,
                agent,
                session_id=session_id,
                context=int(pointers.get("context", 0)),
                generator=int(pointers["generator"]),
                mode=int(pointers["mode"]),
                skill_data=int(skill["skillDataPtr"]),
                target_id=int(skill["validTargetIds"][0]),
                skill_id=int(skill["skillId"]),
                verified_skill_type_id=int(skill["typeId"]),
                expected_area_id=int(battle["areaTypeId"]),
                expected_region_id=int(battle["regionTypeId"]),
                expected_round=int(battle["round"]),
                expected_turn=int(battle["turn"]),
                expected_player_turn_count=int(battle["playerTurnCount"]),
                expected_active_hero_id=int(state["activeHeroId"]),
                expected_active_hero_turn_count=int(state["activeHeroTurnCount"]),
                expected_active_hero_form_index=stale_form,
                execute=False,
                nonce=nonce,
            )
            if not queued.get("queued"):
                raise RuntimeError(f"测试请求未进入主线程队列：{queued}")
            deadline = time.monotonic() + 3.0
            acknowledgement: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                current = ipc.acknowledgement()
                if current and current.get("nonce") == nonce:
                    acknowledgement = current
                    break
                time.sleep(0.05)
            if not acknowledgement:
                raise RuntimeError("等待形态守卫回执超时")
            if acknowledgement.get("status") != "rejected":
                raise RuntimeError(f"旧形态请求未被拒绝：{acknowledgement}")
            print(
                f"形态守卫通过：当前形态 {current_form}，伪造旧形态 {stale_form} 的请求"
                f"被拒绝；未执行技能。",
                flush=True,
            )
            return 0
    finally:
        if armed:
            set_takeover(pid, agent, session_id=session_id, active=False)


if __name__ == "__main__":
    raise SystemExit(main())
