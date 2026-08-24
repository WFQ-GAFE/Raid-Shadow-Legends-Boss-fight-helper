#!/usr/bin/env python3
"""Shared non-UI runtime helpers for Chimera front ends."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agent_ipc import AgentIpc
from boss_modes import STRATEGY_STORE, strategy_template as boss_strategy_template
from chimera_catalog_cache import load_hero_catalog, save_hero_catalog
from chimera_controller import latest_account_state


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
RESOURCE_ROOT = Path(
    os.environ.get("CHIMERA_RESOURCE_ROOT", getattr(sys, "_MEIPASS", PROJECT_ROOT))
).resolve()
CONTROLLER = PROJECT_ROOT / "tools" / "chimera_controller.py"
INJECTOR = PROJECT_ROOT / "tools" / "inject_probe.py"
AGENT = next(
    (
        candidate
        for candidate in (
            RESOURCE_ROOT / "agent" / "RaidChimeraAgent.dll",
            PROJECT_ROOT / "build" / "agent-1236" / "Release" / "RaidChimeraAgent.dll",
        )
        if candidate.is_file()
    ),
    PROJECT_ROOT / "build" / "agent-1236" / "Release" / "RaidChimeraAgent.dll",
)
USER_STRATEGY = STRATEGY_STORE


def worker_command(worker: str) -> list[str]:
    """Launch a source script or dispatch through the frozen desktop exe."""
    scripts = {"injector": INJECTOR, "controller": CONTROLLER}
    if worker not in scripts:
        raise ValueError(f"未知内部工作进程：{worker}")
    if getattr(sys, "frozen", False):
        return [sys.executable, "--internal-worker", worker]
    return [sys.executable, str(scripts[worker])]


def strategy_template(mode: str = "chimera") -> dict[str, Any]:
    return boss_strategy_template(mode)


def lifecycle_team_ids(lifecycle: Any) -> list[int]:
    if not isinstance(lifecycle, dict):
        return []
    screen = lifecycle.get("screen")
    section = lifecycle.get("selection" if screen == "team_selection" else "battle")
    if not isinstance(section, dict):
        return []
    has_type_ids = "heroTypeIds" in section
    values = section.get("heroTypeIds")
    if not has_type_ids:
        values = section.get("heroIds")
    if not isinstance(values, list):
        return []
    hero_ids = [
        value
        for value in values
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    ]
    return hero_ids if len(hero_ids) in {5, 6} and len(set(hero_ids)) == len(hero_ids) else []


def account_from_probe(payload: dict[str, Any]) -> dict[str, Any] | None:
    probe = payload.get("probe")
    if not isinstance(probe, dict):
        return None
    snapshot = probe.get("accountModelSnapshot")
    if not isinstance(snapshot, dict):
        return None
    name = snapshot.get("accountName")
    user_id = snapshot.get("userId")
    if not isinstance(name, str) or not name or not isinstance(user_id, int):
        return None
    return {"type": "account_state", "accountName": name, "userId": user_id}


def usable_account_state(pid: int, value: Any) -> dict[str, Any] | None:
    """Return a complete account identity for this process, or ``None``.

    The agent can publish an initializing snapshot before the game finishes
    populating the user model.  Treat that as pending instead of presenting it
    as a recognized account.
    """
    if not isinstance(value, dict):
        return None
    name = value.get("accountName")
    user_id = value.get("userId")
    state_pid = value.get("pid", pid)
    if (
        state_pid != pid
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(user_id, int)
        or isinstance(user_id, bool)
        or user_id <= 0
    ):
        return None
    result = dict(value)
    result["pid"] = pid
    return result


def wait_for_account_state(
    pid: int,
    probe_payload: dict[str, Any] | None = None,
    *,
    timeout: float = 8.0,
) -> dict[str, Any] | None:
    """Wait briefly for a newly loaded agent to publish the account model."""
    if isinstance(probe_payload, dict):
        account = usable_account_state(pid, account_from_probe(probe_payload))
        if account is not None:
            return account
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        account = usable_account_state(pid, latest_account_state(pid))
        if account is not None:
            return account
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.2)


def subprocess_failure(result: subprocess.CompletedProcess[str], fallback: str) -> str:
    """Expose the worker's concrete JSON error instead of hiding it."""
    if result.stdout.strip():
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            detail = payload.get("error") or payload.get("reason")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()
    if result.stderr.strip():
        return result.stderr.strip()
    return fallback


def cache_probe_hero_catalog(payload: dict[str, Any]) -> int:
    """Persist one complete static hero catalog produced during agent startup."""
    probe = payload.get("probe")
    raw_heroes = probe.get("heroCatalog") if isinstance(probe, dict) else None
    if not isinstance(raw_heroes, list) or not raw_heroes:
        return 0

    previous_catalog = load_hero_catalog({})
    catalog: dict[int, dict[str, Any]] = {}
    for raw_hero in raw_heroes:
        if not isinstance(raw_hero, dict):
            continue
        hero_id = raw_hero.get("typeId")
        if not isinstance(hero_id, int) or isinstance(hero_id, bool) or hero_id <= 0:
            continue
        previous = previous_catalog.get(hero_id, {})
        previous_skills = [
            skill
            for skill in previous.get("skills", [])
            if isinstance(skill, dict)
        ]
        previous_by_form_and_type = {
            (skill.get("formIndex", 0), skill.get("typeId")): skill
            for skill in previous_skills
            if isinstance(skill.get("typeId"), int)
        }
        previous_by_type = {
            skill.get("typeId"): skill
            for skill in previous_skills
            if isinstance(skill.get("typeId"), int)
        }
        skills: list[dict[str, Any]] = []
        for raw_skill in raw_hero.get("skills", []):
            if not isinstance(raw_skill, dict):
                continue
            skill_type_id = raw_skill.get("typeId")
            form_index = raw_skill.get("formIndex", 0)
            cached = previous_by_form_and_type.get(
                (form_index, skill_type_id),
                previous_by_type.get(skill_type_id, {}),
            )
            merged_skill = dict(cached) if isinstance(cached, dict) else {}
            merged_skill.update(
                {
                    key: value
                    for key, value in raw_skill.items()
                    if value not in (None, "")
                }
            )
            skills.append(merged_skill)
        merged_hero = dict(previous) if isinstance(previous, dict) else {}
        merged_hero.update(
            {
                key: value
                for key, value in raw_hero.items()
                if key != "skills" and value not in (None, "")
            }
        )
        merged_hero["skills"] = skills
        catalog[hero_id] = merged_hero

    # A partially initialized static model must never replace a healthy cache.
    if len(catalog) < 50:
        return 0
    save_hero_catalog(catalog)
    return len(catalog)


def account_matches(
    account: dict[str, Any] | None,
    *,
    pid: int,
    account_name: str,
    user_id: int,
) -> bool:
    return bool(
        isinstance(account, dict)
        and account.get("pid", pid) == pid
        and account.get("accountName") == account_name
        and account.get("userId") == user_id
    )


def require_expected_account(pid: int, account_name: str, user_id: int) -> None:
    if not account_matches(
        latest_account_state(pid),
        pid=pid,
        account_name=account_name,
        user_id=user_id,
    ):
        raise RuntimeError(
            f"所选游戏内账户已变化；预期 {account_name}（玩家 ID {user_id}），"
            "已拒绝按进程编号继续"
        )


def identify_account(pid: int) -> dict[str, Any] | None:
    state = usable_account_state(pid, latest_account_state(pid))
    common = [*worker_command("injector"), "--pid", str(pid), "--agent", str(AGENT)]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    check = subprocess.run(
        [*common, "--check-only"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=creation_flags,
    )
    if check.returncode:
        raise RuntimeError(
            subprocess_failure(check, f"账户读取检查失败（进程 {pid}）")
        )
    check_payload = json.loads(check.stdout)
    if check_payload.get("agentLoaded"):
        if not check_payload.get("agentCompatible") or not check_payload.get("agentReady"):
            lifecycle: dict[str, Any] = {}
            try:
                with AgentIpc(pid) as ipc:
                    lifecycle = ipc.lifecycle() or {}
            except (FileNotFoundError, ValueError, OSError):
                pass
            screen = lifecycle.get("screen")
            if screen == "result":
                raise RuntimeError("当前停留在战绩结算画面，暂不更新读取组件")
            section = lifecycle.get(
                "selection" if screen == "team_selection" else "battle"
            )
            context = section.get("context") if isinstance(section, dict) else None
            reloaded = subprocess.run(
                [*common, "--reload"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                creationflags=creation_flags,
            )
            reload_payload = json.loads(reloaded.stdout) if reloaded.stdout.strip() else {}
            if reloaded.returncode or reload_payload.get("ok") is not True:
                raise RuntimeError(
                    str(reload_payload.get("reason") or reloaded.stderr.strip() or "读取组件更新失败")
                )
            cache_probe_hero_catalog(reload_payload)
            if isinstance(context, int) and context > 0 and screen in {"team_selection", "battle"}:
                seed_option = (
                    "--seed-selection-context"
                    if screen == "team_selection"
                    else "--seed-battle-context"
                )
                seeded = subprocess.run(
                    [*common, seed_option, "--context", str(context)],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    creationflags=creation_flags,
                )
                seed_payload = json.loads(seeded.stdout) if seeded.stdout.strip() else {}
                if seeded.returncode or seed_payload.get("ok") is not True:
                    raise RuntimeError(
                        str(seed_payload.get("reason") or seeded.stderr.strip() or "准备界面恢复失败")
                    )
            state = usable_account_state(pid, latest_account_state(pid))
        return state or wait_for_account_state(pid, timeout=3.0)

    loaded = subprocess.run(
        common,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        creationflags=creation_flags,
    )
    if loaded.returncode:
        raise RuntimeError(
            subprocess_failure(loaded, f"账户名称读取失败（进程 {pid}）")
        )
    payload = json.loads(loaded.stdout)
    cache_probe_hero_catalog(payload)
    return wait_for_account_state(pid, payload)
