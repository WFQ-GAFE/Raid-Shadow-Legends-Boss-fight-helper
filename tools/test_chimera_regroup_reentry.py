from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from agent_ipc import AgentIpc
from chimera_controller import (
    DEFAULT_ROTATION_ARCHIVE,
    TakeoverInterrupted,
    archive_rotation_catalog_if_changed,
    require_takeover_active,
    select_team_heroes,
    start_first_battle_if_ready,
    submit_free_regroup,
)
from inject_probe import seed_battle_context, seed_selection_context, set_takeover
from named_mutex import NamedMutex
from raid_processes import raid_processes


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AGENT = PROJECT_ROOT / "build" / "agent-1236" / "Release" / "RaidChimeraAgent.dll"


def resolve_account_pid(account_name: str) -> tuple[int, dict[str, Any]]:
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
        raise RuntimeError(
            f"游戏内用户名“{account_name}”匹配到 {len(matches)} 个就绪进程；"
            "测试不会退回使用不稳定的 PID 猜测"
        )
    return matches[0]


def wait_for_screen(
    ipc: AgentIpc,
    session_id: int,
    wanted: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        require_takeover_active(ipc, session_id)
        last = ipc.lifecycle()
        if isinstance(last, dict) and last.get("screen") == wanted:
            return last
        time.sleep(0.05)
    raise RuntimeError(f"等待进入 {wanted} 超时；最后状态：{last}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="显式测试：免费重整后使用原队伍再次进入奇美拉战斗"
    )
    parser.add_argument("--account", default="gafee", help="游戏内用户名")
    parser.add_argument("--agent", type=Path, default=DEFAULT_AGENT)
    parser.add_argument(
        "--seed-context",
        type=int,
        default=0,
        help="仅用于热更新后恢复同一场已验证战斗的界面上下文",
    )
    parser.add_argument(
        "--resume-after-regroup",
        action="store_true",
        help="从已经到达的队伍界面继续验证原队伍重新开战，不再发起重整",
    )
    parser.add_argument(
        "--seed-selection-context",
        type=int,
        default=0,
        help="仅用于热更新后恢复同一队伍界面实例",
    )
    parser.add_argument(
        "--rotation-archive", type=Path, default=DEFAULT_ROTATION_ARCHIVE
    )
    args = parser.parse_args()

    try:
        pid, account = resolve_account_pid(args.account)
    except Exception as error:
        print(f"闭环测试安全停止：{error}", flush=True)
        return 5
    agent = args.agent.resolve()
    mutex = NamedMutex(rf"Local\RaidChimeraController-{pid}")
    mutex.acquire()
    session_id = secrets.randbits(63) or 1
    takeover_armed = False
    try:
        with AgentIpc(pid) as ipc:
            if args.seed_context:
                seeded = seed_battle_context(pid, agent, args.seed_context)
                if not seeded.get("accepted"):
                    raise RuntimeError(f"热更新后的战斗上下文恢复失败：{seeded}")
            if args.seed_selection_context:
                seeded = seed_selection_context(
                    pid, agent, args.seed_selection_context
                )
                if not seeded.get("accepted"):
                    raise RuntimeError(f"热更新后的队伍上下文恢复失败：{seeded}")
            lifecycle = ipc.lifecycle() or {}
            if lifecycle.get("screen") not in {"battle", "team_selection"}:
                raise RuntimeError(
                    "当前注入代理还没有取得奇美拉队伍或战斗对象；"
                    f"当前页面标记为 {lifecycle.get('screen', 'unknown')}，未提交任何命令"
                )
            takeover = set_takeover(
                pid,
                agent,
                session_id=session_id,
                active=True,
                expected_user_id=int(account["userId"]),
            )
            if not takeover.get("accepted"):
                raise RuntimeError(f"代理拒绝接管测试：{takeover}")
            takeover_armed = True
            require_takeover_active(ipc, session_id)

            if args.resume_after_regroup:
                if lifecycle.get("screen") != "team_selection":
                    raise RuntimeError("续测要求当前停留在奇美拉队伍界面")
                start_first_battle_if_ready(
                    ipc,
                    pid=pid,
                    agent=agent,
                    session_id=session_id,
                )
                new_battle = wait_for_screen(
                    ipc, session_id, "battle", timeout_seconds=30.0
                )
                new_context = (new_battle.get("battle") or {}).get("context")
                if not isinstance(new_context, int) or new_context <= 0:
                    raise RuntimeError("重新开战后未取得新战斗实例")
                print(
                    "闭环续测通过：重整后的五人队伍已自动重新进入奇美拉；"
                    "未释放技能，也不会自动保存战果。",
                    flush=True,
                )
                return 0

            original_hero_ids: list[int] | None = None
            if lifecycle.get("screen") == "team_selection":
                selection = lifecycle.get("selection", {})
                if isinstance(selection, dict):
                    original_hero_ids = [
                        value
                        for value in selection.get("heroIds", [])
                        if isinstance(value, int)
                    ]
                start_first_battle_if_ready(
                    ipc,
                    pid=pid,
                    agent=agent,
                    session_id=session_id,
                )
                lifecycle = wait_for_screen(
                    ipc, session_id, "battle", timeout_seconds=30.0
                )
            else:
                battle_snapshot = lifecycle.get("battle")
                if isinstance(battle_snapshot, dict):
                    captured_heroes = [
                        value
                        for value in battle_snapshot.get("heroIds", [])
                        if isinstance(value, int)
                    ]
                    if len(captured_heroes) == 5:
                        original_hero_ids = captured_heroes

            battle_context = (lifecycle.get("battle") or {}).get("context")
            if not isinstance(battle_context, int) or battle_context <= 0:
                raise RuntimeError("战斗页面没有可验证的实例地址，未执行免费重整")

            print(
                f"账户 {account.get('accountName')}：提交免费重整（战斗实例 {battle_context}）。",
                flush=True,
            )
            submit_free_regroup(
                ipc,
                pid=pid,
                agent=agent,
                session_id=session_id,
            )
            selection_lifecycle = wait_for_screen(
                ipc, session_id, "team_selection", timeout_seconds=30.0
            )
            selection = selection_lifecycle.get("selection")
            if not isinstance(selection, dict):
                raise RuntimeError("已重整但没有读到队伍状态，停止在队伍界面")
            regrouped_hero_ids = [
                value
                for value in selection.get("heroIds", [])
                if isinstance(value, int)
            ]
            if (
                not regrouped_hero_ids
                and original_hero_ids is not None
                and len(original_hero_ids) == 5
                and selection.get("valid") is True
            ):
                selection_lifecycle = select_team_heroes(
                    ipc,
                    pid=pid,
                    agent=agent,
                    session_id=session_id,
                    hero_ids=original_hero_ids,
                    nonce=0x80002000,
                )
                selection = selection_lifecycle.get("selection") or {}
                regrouped_hero_ids = [
                    value
                    for value in selection.get("heroIds", [])
                    if isinstance(value, int)
                ]
            if (
                len(regrouped_hero_ids) != 5
                or selection.get("filled") is not True
                or selection.get("quickBattle") is True
            ):
                raise RuntimeError(
                    "已重整但队伍未满足再次开战守卫；"
                    f"英雄数 {len(regrouped_hero_ids)}，filled={selection.get('filled')}，"
                    f"quickBattle={selection.get('quickBattle')}"
                )
            if original_hero_ids is not None and regrouped_hero_ids != original_hero_ids:
                raise RuntimeError(
                    "免费重整后队伍发生变化；测试停在队伍界面，不自动开战"
                )
            archive_rotation_catalog_if_changed(
                ipc, args.rotation_archive.resolve()
            )

            print("免费重整已确认；仅本次显式测试继续用当前五人队伍开战。", flush=True)
            start_first_battle_if_ready(
                ipc,
                pid=pid,
                agent=agent,
                session_id=session_id,
            )
            new_battle = wait_for_screen(
                ipc, session_id, "battle", timeout_seconds=30.0
            )
            new_context = (new_battle.get("battle") or {}).get("context")
            if not isinstance(new_context, int) or new_context <= 0:
                raise RuntimeError("再次开战后未取得新战斗实例")
            if new_context == battle_context:
                raise RuntimeError("再次开战仍是旧战斗实例，测试结果无效")
            archive_rotation_catalog_if_changed(
                ipc, args.rotation_archive.resolve(), state=ipc.decision()
            )
            print(
                "闭环测试通过：免费重整已回到队伍界面，并已创建新的奇美拉战斗实例；"
                "未执行英雄技能，也不会自动保存战果。",
                flush=True,
            )
            return 0
    except TakeoverInterrupted as error:
        print(str(error), flush=True)
        return 6
    except Exception as error:
        print(f"闭环测试安全停止：{error}", flush=True)
        return 5
    finally:
        if takeover_armed:
            try:
                set_takeover(pid, agent, session_id=session_id, active=False)
            except Exception as error:
                print(f"测试接管清理失败：{error}", flush=True)
        mutex.close()


if __name__ == "__main__":
    sys.exit(main())
