#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Callable


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
VENDORED_PYTHON = PROJECT_ROOT / "third_party" / "python"
if VENDORED_PYTHON.is_dir() and str(VENDORED_PYTHON) not in sys.path:
    sys.path.insert(0, str(VENDORED_PYTHON))
try:
    import ttkbootstrap as ttk_bootstrap
except ImportError:  # The classic theme remains a safe offline fallback.
    ttk_bootstrap = None

from agent_ipc import AgentIpc
from controller_pause import signal_controller_pause
from chimera_controller import (
    latest_account_state,
    validate_strategy_config,
)
from chimera_catalog_cache import (
    cache_live_rewards,
    cached_trials,
    ensure_ui_catalog_cache,
    infer_trial_difficulty_id,
    load_hero_catalog,
    save_hero_catalog,
)
from chimera_icons import (
    EFFECT_OPTIONS,
    ChimeraIconRepository,
    canonical_effect_token,
    effect_label,
    skill_effect_details,
    skill_effect_summary,
)
from inject_probe import seed_battle_context, seed_selection_context
from named_mutex import NamedMutex
from raid_processes import attach_windows, is_supported_raid_executable, raid_processes


CONTROLLER = PROJECT_ROOT / "tools" / "chimera_controller.py"
INJECTOR = PROJECT_ROOT / "tools" / "inject_probe.py"
AGENT = PROJECT_ROOT / "build" / "agent-1236" / "Release" / "RaidChimeraAgent.dll"
DEFAULT_STRATEGY = PROJECT_ROOT / "config" / "chimera-strategy.a1-test.json"
USER_STRATEGY = PROJECT_ROOT / "config" / "chimera-strategy.user.json"

KNOWN_HEROES = {
    8896: "地窖守卫威克斯维尔",
    4716: "死亡女妖莉迪亚",
    10436: "蛛网占卜师玛瓦拉",
    9906: "黑羽缇塔斯",
    8256: "御影夫人",
}
FORM_LABELS = {
    "Ultimate": "终极形态",
    "Ram": "公羊形态",
    "Lion": "狮子形态",
    "Snake": "毒蛇形态（Viper）",
}
TARGET_LABELS = {
    "boss": "奇美拉",
    "self": "自己",
    "lowestHpAlly": "生命最低的友方",
}
NEXT_FORM_LABELS = {"不限": None, **{label: form for form, label in FORM_LABELS.items()}}
HERO_FORM_LABELS = {"不限": None, "原形态": 0, "变形形态": 1}
TRANSFORM_ACTION_SLOT = -1
ACTION_MODE_LABELS = {
    "施放指定技能": "cast",
    "自动学习并维持必要效果": "maintainEffects",
    "按当前试炼配方自动决策": "executeTrialRecipe",
}
PART_LABELS = {"Wing": "翅膀", "Tail": "尾巴", "Paw": "爪子"}
DIFFICULTY_LABELS = {
    "Easy": "简单",
    "Normal": "普通",
    "Hard": "困难",
    "Brutal": "残暴",
    "Nightmare": "噩梦",
    "UltraNightmare": "终极噩梦",
}
BOSS_DIFFICULTY_BY_LABEL = {label: index for index, label in enumerate(DIFFICULTY_LABELS.values(), 1)}
REWARD_RESOURCE_LABELS = {
    "RelicCraftMaterial_Chimera_Rare": "稀有遗物锻造材料",
    "RelicCraftMaterial_Chimera_Epic": "史诗遗物锻造材料",
    "RelicCraftMaterial_Chimera_Legendary": "传说遗物锻造材料",
    "RelicCraftMaterial_Chimera_Mythical": "神话遗物锻造材料",
}


def plain_game_text(value: Any) -> str:
    text = str(value or "").replace("\\n", "\n")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()
EDITABLE_WHEN_KEYS = {
    "form",
    "activeHeroTypeId",
    "activeHeroFormIndex",
    "activeHeroIsMetamorph",
    "transformationReady",
    "activeHeroHpPctBelow",
    "anyAllyHpPctBelow",
    "bossHpPctBelow",
    "bossHasEffects",
    "bossMissingEffects",
    "turnAtLeast",
    "turnAtMost",
    "chimeraTurnAtLeast",
    "chimeraTurnAtMost",
    "playerTurnCount",
    "chimeraTurnCount",
    "turnsUntilFormChangeAtMost",
    "nextForm",
    "currentDamageAtLeast",
    "currentDamageBelow",
    "currentCompetitionPointsAtLeast",
    "bossEffectSlotsAtLeast",
    "bossEffectSlotsAtMost",
    "activeHeroEffectSlotsAtMost",
    "allAlliesEffectSlotsAtMost",
    "bossHasEffect",
    "bossMissingEffect",
    "activeHeroHasEffect",
    "activeHeroMissingEffect",
    "anyAllyHasEffect",
    "anyAllyMissingEffect",
    "allyHasEffect",
    "allyMissingEffect",
    "allyEffectSlotsAtLeast",
    "allyEffectSlotsAtMost",
    "completedTrialsAll",
    "incompleteTrialsAll",
    "activeTrialsAny",
    "eligibleTrialsAny",
    "lockedTrialsAny",
    "impossibleTrialsAny",
    "trialProgressAtLeast",
}


def strategy_template() -> dict[str, Any]:
    return {
        "name": "chimera-user-strategy",
        "mode": "observe",
        "scope": {"battleKind": "AllianceChimera"},
        "objectives": {
            "mandatoryTrialIds": [],
            "minimumDamage": 0,
            "minimumCompetitionPoints": 0,
            "onMandatoryTrialImpossible": "free_regroup_and_retry_manual",
            "maxRegroupRetries": 10,
            "onAllMetAtResult": "hold_for_user",
        },
        "safety": {
            "requireManualCommandWindow": True,
            "requireAcceptableTargetSet": True,
            "requireFreshSnapshotMs": 1500,
            "maxCommandsPerTurn": 1,
            "onUnknownState": "pause",
            "onNoMatchingRule": "pause",
        },
        "rules": [],
    }


def read_strategy(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("rules"), list):
        raise ValueError("策略文件格式无效")
    return value


def split_effects(value: str) -> list[str]:
    return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]


def split_positive_ids(value: str, label: str) -> list[int]:
    items = [
        int(item.strip())
        for item in value.replace("，", ",").split(",")
        if item.strip()
    ]
    if len(items) != len(set(items)) or any(item <= 0 for item in items):
        raise ValueError(f"{label}必须是互不重复的正整数")
    return items


def parse_trial_progress(value: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in value.replace("，", ",").split(","):
        text = item.strip()
        if not text:
            continue
        if ":" not in text and "：" not in text:
            raise ValueError("保存的试炼进度格式无效")
        raw_id, raw_progress = text.replace("：", ":").split(":", 1)
        trial_id = int(raw_id.strip())
        progress_text = raw_progress.strip()
        if progress_text.endswith("%"):
            progress = float(progress_text[:-1]) / 100
        else:
            progress = float(progress_text)
        if trial_id <= 0 or not 0 <= progress <= 1:
            raise ValueError("保存的试炼或进度比例无效")
        result[str(trial_id)] = progress
    return result


def format_trial_progress(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return ", ".join(f"{key}:{float(progress):g}" for key, progress in value.items())


def reward_summary(trial: dict[str, Any]) -> str:
    reward = trial.get("reward")
    if not isinstance(reward, dict):
        return "未读取"
    entries = []
    for entry in reward.get("entries", []):
        if not isinstance(entry, dict):
            continue
        name = entry.get("resourceType") or entry.get("type") or entry.get("typeId")
        name = REWARD_RESOURCE_LABELS.get(name, name)
        minimum = entry.get("minCount")
        maximum = entry.get("maxCount")
        count = str(minimum) if minimum == maximum or not maximum else f"{minimum}-{maximum}"
        probability = entry.get("probability")
        probability_text = f" {probability}%" if isinstance(probability, (int, float)) else ""
        entries.append(f"{name} ×{count}{probability_text}")
    return "；".join(entries) or "无明细"


def readable_game_text(value: Any) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def trial_display_name(trial: dict[str, Any], *, compact: bool = False) -> str:
    description = readable_game_text(trial.get("description"))
    if not description:
        return "未读取到试炼说明"
    # The trial table is the user's primary way to understand a trial.  Keep the
    # complete localized description here; form/part/status have their own UI.
    return description


def selected_trial_summary(trials: list[dict[str, Any]], selected_ids: list[int]) -> str:
    selected = [trial_display_name(trial, compact=True) for trial in trials if trial.get("id") in selected_ids]
    if not selected_ids:
        return "未选择"
    if not selected:
        return f"已保存 {len(selected_ids)} 项试炼（进入对应难度后显示名称）"
    if len(selected) == 1:
        return selected[0]
    return f"{selected[0]}；另有 {len(selected) - 1} 项"


def current_trial_catalog(
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    catalog = state.get("trialCatalog")
    if not isinstance(catalog, dict) or catalog.get("available") is not True:
        return [], None, None
    difficulties = catalog.get("difficulties")
    if not isinstance(difficulties, list):
        return [], None, None

    difficulty_id = state.get("allianceChimeraDifficultyId")
    stage_id = state.get("chimeraStageId")
    selected: dict[str, Any] | None = None
    for difficulty in difficulties:
        if not isinstance(difficulty, dict):
            continue
        if isinstance(difficulty_id, int) and difficulty.get("difficultyId") == difficulty_id:
            selected = difficulty
            break
        stage_ids = difficulty.get("stageIds")
        if isinstance(stage_ids, list) and stage_id in stage_ids:
            selected = difficulty
    if selected is None:
        return [], None, None

    trials = [dict(value) for value in selected.get("trials", []) if isinstance(value, dict)]
    runtime_by_id: dict[int, dict[str, Any]] = {}
    for boss in state.get("bosses", []):
        if not isinstance(boss, dict):
            continue
        for challenge in boss.get("challenges", []):
            if isinstance(challenge, dict) and isinstance(challenge.get("id"), int):
                runtime_by_id[challenge["id"]] = challenge
    for trial in trials:
        runtime = runtime_by_id.get(trial.get("id"))
        if runtime:
            trial.update(runtime)
    difficulty_name = selected.get("difficulty")
    if not isinstance(difficulty_name, str) or not difficulty_name:
        raw_id = selected.get("difficultyId")
        difficulty_name = f"难度 {raw_id}" if isinstance(raw_id, int) else "当前难度"
    identity = state.get("rotationIdentity")
    fingerprint = (
        identity.get("rewardRotationFingerprint") if isinstance(identity, dict) else None
    )
    return trials, difficulty_name, fingerprint if isinstance(fingerprint, str) else None


def trial_runtime_status_summary(trial: dict[str, Any]) -> str:
    if trial.get("completed") is True:
        return "已完成"
    if trial.get("impossible") is True or trial.get("possible") is False:
        return "已不可能"
    if trial.get("eligibleNow") is True:
        return "当前可推进"
    if trial.get("activeInChain") is True:
        return "等待对应形态"
    if trial.get("chainState") == "locked":
        return "前置未完成"
    return "尚未读取"


def lifecycle_team_ids(lifecycle: Any) -> list[int]:
    if not isinstance(lifecycle, dict):
        return []
    screen = lifecycle.get("screen")
    section = lifecycle.get("selection" if screen == "team_selection" else "battle")
    if not isinstance(section, dict):
        return []
    values = section.get("heroTypeIds")
    if not isinstance(values, list) or not values:
        values = section.get("heroIds")
    if not isinstance(values, list):
        return []
    hero_ids = [
        value
        for value in values
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    ]
    return hero_ids if len(hero_ids) == 5 and len(set(hero_ids)) == 5 else []


def compose_strategy_config(
    *,
    rules: list[dict[str, Any]],
    mode: str,
    mandatory_trials: str,
    minimum_damage: str,
    minimum_points: str,
    max_retries: str,
    team_hero_ids: list[int] | None = None,
) -> dict[str, Any]:
    if not rules:
        raise ValueError("至少需要一条规则")
    trial_ids = split_positive_ids(mandatory_trials, "试炼设置")
    try:
        damage = int(minimum_damage)
        points = int(minimum_points)
        retries = int(max_retries)
    except ValueError as error:
        raise ValueError("伤害、积分和重试次数必须是整数") from error
    if min(damage, points, retries) < 0:
        raise ValueError("目标值和重试次数不能小于 0")

    config = strategy_template()
    config["mode"] = mode
    config["rules"] = rules
    config["objectives"] = {
        "mandatoryTrialIds": trial_ids,
        "minimumDamage": damage,
        "minimumCompetitionPoints": points,
        "onMandatoryTrialImpossible": "free_regroup_and_retry_manual",
        "maxRegroupRetries": retries,
        "onAllMetAtResult": "hold_for_user",
    }
    if team_hero_ids:
        config["team"] = {"heroIds": list(team_hero_ids)}
    validate_strategy_config(config)
    return config


def process_label(item: dict[str, Any]) -> str:
    pid = int(item["pid"])
    account = item.get("account", {})
    name = account.get("accountName") if isinstance(account, dict) else None
    user_id = account.get("userId") if isinstance(account, dict) else None
    if isinstance(name, str) and name and isinstance(user_id, int):
        return f"{name}（玩家 ID {user_id}）"
    if isinstance(name, str) and name:
        return name
    return f"未识别账户（进程 {pid}）"


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
            "已拒绝按 PID 继续"
        )


def identify_account(pid: int) -> dict[str, Any] | None:
    state = latest_account_state(pid)
    if (
        isinstance(state, dict)
        and state.get("pid") == pid
        and isinstance(state.get("accountName"), str)
        and state.get("accountName")
    ):
        return state
    common = [sys.executable, str(INJECTOR), "--pid", str(pid), "--agent", str(AGENT)]
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
        raise RuntimeError(f"账户读取检查失败（进程 {pid}）")
    check_payload = json.loads(check.stdout)
    if check_payload.get("agentLoaded"):
        if not check_payload.get("agentCompatible") or not check_payload.get("agentReady"):
            raise RuntimeError(f"进程 {pid} 中的代理版本不匹配或尚未就绪")
        return state if state and state.get("pid") == pid else latest_account_state(pid)

    loaded = subprocess.run(
        common,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=creation_flags,
    )
    if loaded.returncode:
        raise RuntimeError(f"账户名称读取失败（进程 {pid}）")
    payload = json.loads(loaded.stdout)
    return latest_account_state(pid) or account_from_probe(payload)


class EffectPickerDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        icons: ChimeraIconRepository,
        selected_tokens: list[str],
        on_save: Callable[[list[str]], None],
        *,
        multiple: bool,
        title: str = "选择增益或减益",
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("720x620")
        self.minsize(620, 480)
        self.transient(parent)
        self.grab_set()
        self.icons = icons
        self.on_save = on_save
        self.multiple = multiple
        self.selected_tokens = set(selected_tokens)
        self.search_var = tk.StringVar()

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        search = ttk.Frame(frame)
        search.pack(fill="x", pady=(0, 8))
        ttk.Label(search, text="搜索名称").pack(side="left")
        entry = ttk.Entry(search, textvariable=self.search_var)
        entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.search_var.trace_add("write", lambda *_args: self.refresh())

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            list_frame,
            columns=("group",),
            show="tree headings",
            selectmode="extended" if multiple else "browse",
            height=17,
        )
        self.tree.heading("#0", text="游戏图标与效果名称")
        self.tree.column("#0", width=500, minwidth=320, stretch=True)
        self.tree.heading("group", text="类别")
        self.tree.column("group", width=90, anchor="center", stretch=False)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(fill="both", expand=True, side="left")
        scrollbar.pack(fill="y", side="left")
        if not multiple:
            self.tree.bind("<Double-1>", lambda _event: self.save())

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="清空", command=lambda: self.finish([])).pack(side="left")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="使用所选效果", command=self.save).pack(side="right", padx=(0, 8))
        self.refresh()
        entry.focus_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def refresh(self) -> None:
        query = self.search_var.get().strip().casefold()
        self.tree.delete(*self.tree.get_children())
        for option in EFFECT_OPTIONS:
            if query and query not in option["label"].casefold() and query not in option["token"].casefold():
                continue
            token = option["token"]
            self.tree.insert(
                "",
                "end",
                iid=token,
                text=option["label"],
                image=self.icons.effect_photo(token),
                values=(option["group"],),
            )
            if token in self.selected_tokens:
                self.tree.selection_add(token)

    def save(self) -> None:
        values = list(self.tree.selection())
        if not values:
            messagebox.showinfo("选择效果", "请先选择一个效果。", parent=self)
            return
        self.finish(values if self.multiple else values[:1])

    def finish(self, values: list[str]) -> None:
        self.on_save(values)
        self.destroy()


class SkillPickerDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        icons: ChimeraIconRepository,
        hero_id: int | None,
        skills: list[dict[str, Any]],
        selected_slot: int | None,
        on_save: Callable[[str, int], None],
    ) -> None:
        super().__init__(parent)
        self.title("选择英雄技能")
        self.geometry("860x650")
        self.minsize(700, 520)
        self.transient(parent)
        self.grab_set()
        self.icons = icons
        self.hero_id = hero_id
        self.skills = skills
        self.on_save = on_save

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="这里显示游戏中的技能名称与原始图标；Buff / Debuff 图标只出现在下方效果资料中。",
            foreground="#666666",
        ).pack(fill="x", pady=(0, 10))
        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True)
        ttk.Style(self).configure("SkillPicker.Treeview", rowheight=52)
        self.tree = ttk.Treeview(
            list_frame,
            columns=("slot", "cooldown", "effects"),
            show="tree headings",
            selectmode="browse",
            height=8,
            style="SkillPicker.Treeview",
        )
        self.tree.heading("#0", text="具体技能")
        self.tree.column("#0", width=245, minwidth=190, stretch=True)
        self.tree.heading("slot", text="位置")
        self.tree.column("slot", width=70, anchor="center", stretch=False)
        self.tree.heading("cooldown", text="冷却")
        self.tree.column("cooldown", width=70, anchor="center", stretch=False)
        self.tree.heading("effects", text="已学习效果摘要")
        self.tree.column("effects", width=390, minwidth=260, stretch=True)
        self.tree.pack(fill="both", expand=True)
        for index, skill in enumerate(skills):
            slot = int(skill.get("slot", 0))
            label = str(skill.get("label") or skill.get("name") or f"技能{slot}")
            transform = slot == TRANSFORM_ACTION_SLOT
            cooldown = skill.get("defaultCooldown")
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                text=label,
                image=(
                    self.icons.photo("Polymorph", "medium")
                    if transform
                    else self.icons.skill_photo(hero_id, skill, "medium")
                ),
                values=(
                    "变形" if transform else f"技能{slot}",
                    "—" if transform or not isinstance(cooldown, int) else f"{cooldown}回合",
                    "切换神话英雄形态" if transform else skill_effect_summary(skill.get("typeId"), limit=3),
                ),
            )
            if slot == selected_slot:
                self.tree.selection_set(str(index))
        if not self.tree.selection() and skills:
            self.tree.selection_set("0")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.refresh_detail())
        self.tree.bind("<Double-1>", lambda _event: self.save())

        detail_frame = ttk.LabelFrame(frame, text="技能详情", padding=8)
        detail_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.detail = ScrolledText(detail_frame, height=10, wrap="word")
        self.detail.pack(fill="both", expand=True)
        self.detail.configure(state="disabled")
        self.refresh_detail()
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="使用这个技能", command=self.save).pack(side="right", padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def save(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        skill = self.skills[int(selected[0])]
        slot = int(skill.get("slot", 0))
        label = str(skill.get("label") or skill.get("name") or f"技能{slot}")
        self.on_save(label, slot)
        self.destroy()

    def refresh_detail(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        skill = self.skills[int(selected[0])]
        slot = int(skill.get("slot", 0))
        name = str(skill.get("name") or skill.get("label") or f"技能{slot}")
        description = plain_game_text(skill.get("description"))
        lines = [f"{name}（{'切换形态' if slot == TRANSFORM_ACTION_SLOT else f'技能{slot}'}）"]
        type_id = skill.get("typeId")
        if isinstance(type_id, int):
            lines.append(f"技能资料编号：{type_id}")
        cooldown = skill.get("defaultCooldown")
        if isinstance(cooldown, int):
            lines.append(f"基础冷却：{cooldown} 回合")
        lines.append("")
        lines.append(description or "游戏尚未把这项技能的完整说明缓存到工具。进入战斗并轮到该英雄时会自动补全。")
        effects = skill_effect_details(type_id)
        if effects:
            lines.extend(("", "已学习到的实际效果："))
            for effect in effects:
                turns = f"，{effect['turns']} 回合" if effect.get("turns") else ""
                lines.append(f"• {effect['scope']}：{effect['label']}{turns}")
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", "\n".join(lines))
        self.detail.configure(state="disabled")


class HeroPickerDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        icons: ChimeraIconRepository,
        catalog: dict[int, dict[str, Any]],
        selected_id: int | None,
        on_save: Callable[[int | None], None],
        *,
        title: str,
        allow_any: bool = False,
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("650x520")
        self.minsize(520, 400)
        self.transient(parent)
        self.grab_set()
        self.catalog = catalog
        self.icons = icons
        self.selected_id = selected_id
        self.on_save = on_save
        self.allow_any = allow_any
        self.search_var = tk.StringVar()

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=f"已有英雄库 {len(catalog)} 名，可按英雄名称或已缓存技能名称检索。",
            foreground="#666666",
        ).pack(fill="x", pady=(0, 8))
        search = ttk.Frame(frame)
        search.pack(fill="x", pady=(0, 8))
        ttk.Label(search, text="搜索英雄").pack(side="left")
        entry = ttk.Entry(search, textvariable=self.search_var)
        entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.search_var.trace_add("write", lambda *_args: self.refresh())

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True)
        ttk.Style(self).configure("HeroPicker.Treeview", rowheight=52)
        self.tree = ttk.Treeview(
            list_frame,
            columns=("skills",),
            show="tree headings",
            selectmode="browse",
            height=15,
            style="HeroPicker.Treeview",
        )
        self.tree.heading("#0", text="英雄名称")
        self.tree.column("#0", width=260, minwidth=180, stretch=True)
        self.tree.heading("skills", text="已有技能资料")
        self.tree.column("skills", width=310, minwidth=180, stretch=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.save())

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", side="bottom", pady=(10, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="使用所选英雄", command=self.save).pack(
            side="right", padx=(0, 8)
        )
        ttk.Button(
            buttons,
            text="任意英雄（不限制）" if allow_any else "清除选择",
            command=lambda: self.finish(None),
        ).pack(side="left")

        self.refresh()
        entry.focus_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def refresh(self) -> None:
        query = self.search_var.get().strip().casefold()
        self.tree.delete(*self.tree.get_children())
        for hero_id, hero in sorted(
            self.catalog.items(),
            key=lambda value: str(value[1].get("name", "")).casefold(),
        ):
            name = str(hero.get("name") or "未识别英雄")
            skills = [
                str(skill.get("name") or f"技能{skill.get('slot', '')}")
                for skill in hero.get("skills", [])
                if isinstance(skill, dict)
            ]
            searchable = " ".join((name, *skills)).casefold()
            if query and query not in searchable:
                continue
            iid = str(hero_id)
            self.tree.insert(
                "",
                "end",
                iid=iid,
                text=name,
                image=self.icons.hero_photo(hero_id, hero, "medium"),
                values=("、".join(skills) or "尚未缓存技能名称",),
            )
            if hero_id == self.selected_id:
                self.tree.selection_set(iid)
                self.tree.see(iid)

    def save(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("选择英雄", "请先从英雄库选择一名英雄。", parent=self)
            return
        self.finish(int(selected[0]))

    def finish(self, hero_id: int | None) -> None:
        self.on_save(hero_id)
        self.destroy()


class RuleDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        catalog: dict[int, dict[str, Any]],
        trials: list[dict[str, Any]],
        icons: ChimeraIconRepository,
        rule: dict[str, Any] | None,
        on_save: Callable[[dict[str, Any]], None],
    ) -> None:
        super().__init__(parent)
        self.title("编辑策略规则" if rule else "添加策略规则")
        self.resizable(True, True)
        self.minsize(920, 620)
        self.geometry("1040x860")
        self.transient(parent)
        self.grab_set()
        self.catalog = catalog
        self.trials = trials
        self.icons = icons
        self.on_save = on_save
        self.hero_labels: dict[str, list[int]] = {
            "全部已知英雄": list(catalog),
            **{
                str(data["name"]): [hero_id]
                for hero_id, data in catalog.items()
            },
        }
        self.target_labels: dict[str, dict[str, Any]] = {
            "奇美拉": {"type": "boss"},
            "自己": {"type": "self"},
            "生命最低的友方": {"type": "lowestHpAlly"},
            **{
                f"指定友方：{data['name']}": {
                    "type": "allyHeroTypeId",
                    "heroTypeId": hero_id,
                }
                for hero_id, data in catalog.items()
            },
        }
        self.condition_ally_labels: dict[str, int] = {
            str(data["name"]): hero_id
            for hero_id, data in catalog.items()
        }

        self.name_var = tk.StringVar()
        self.action_mode_var = tk.StringVar(value=next(iter(ACTION_MODE_LABELS)))
        self.hero_var = tk.StringVar(value=next(iter(self.hero_labels), ""))
        self.skill_var = tk.StringVar()
        self.target_var = tk.StringVar(value="奇美拉")
        self.maintain_boss_effect_var = tk.StringVar()
        self.maintain_boss_turns_var = tk.StringVar(value="1")
        self.maintain_active_effect_var = tk.StringVar()
        self.maintain_active_turns_var = tk.StringVar(value="1")
        self.maintain_ally_effect_var = tk.StringVar()
        self.maintain_ally_turns_var = tk.StringVar(value="1")
        self.probe_unknown_skills_var = tk.BooleanVar(value=True)
        self.probe_transforms_var = tk.BooleanVar(value=False)
        self.form_vars = {
            form: tk.BooleanVar(value=True) for form in FORM_LABELS
        }
        self.active_hp_var = tk.StringVar()
        self.ally_hp_var = tk.StringVar()
        self.boss_hp_var = tk.StringVar()
        self.boss_has_var = tk.StringVar()
        self.boss_missing_var = tk.StringVar()
        self.turn_at_least_var = tk.StringVar()
        self.turn_at_most_var = tk.StringVar()
        self.player_turn_var = tk.StringVar()
        self.chimera_turn_var = tk.StringVar()
        self.turns_until_change_var = tk.StringVar()
        self.next_form_var = tk.StringVar(value="不限")
        self.hero_form_var = tk.StringVar(value="不限")
        self.damage_at_least_var = tk.StringVar()
        self.damage_below_var = tk.StringVar()
        self.points_at_least_var = tk.StringVar()
        self.boss_slots_min_var = tk.StringVar()
        self.boss_slots_max_var = tk.StringVar()
        self.active_slots_max_var = tk.StringVar()
        self.allies_slots_max_var = tk.StringVar()
        self.boss_effect_var = tk.StringVar()
        self.boss_effect_turns_min_var = tk.StringVar()
        self.boss_effect_turns_max_var = tk.StringVar()
        self.boss_missing_effect_var = tk.StringVar()
        self.boss_missing_effect_turns_min_var = tk.StringVar()
        self.boss_missing_effect_turns_max_var = tk.StringVar()
        self.active_effect_var = tk.StringVar()
        self.active_effect_turns_min_var = tk.StringVar()
        self.active_effect_turns_max_var = tk.StringVar()
        self.active_missing_effect_var = tk.StringVar()
        self.active_missing_effect_turns_min_var = tk.StringVar()
        self.active_missing_effect_turns_max_var = tk.StringVar()
        self.ally_effect_var = tk.StringVar()
        self.ally_effect_turns_min_var = tk.StringVar()
        self.ally_effect_turns_max_var = tk.StringVar()
        self.ally_missing_effect_var = tk.StringVar()
        self.ally_missing_effect_turns_min_var = tk.StringVar()
        self.ally_missing_effect_turns_max_var = tk.StringVar()
        self.condition_ally_var = tk.StringVar(value="未选择队友")
        self.selected_ally_effect_var = tk.StringVar()
        self.selected_ally_effect_turns_min_var = tk.StringVar()
        self.selected_ally_effect_turns_max_var = tk.StringVar()
        self.selected_ally_missing_effect_var = tk.StringVar()
        self.selected_ally_missing_turns_min_var = tk.StringVar()
        self.selected_ally_missing_turns_max_var = tk.StringVar()
        self.selected_ally_slots_min_var = tk.StringVar()
        self.selected_ally_slots_max_var = tk.StringVar()
        self.completed_trials_var = tk.StringVar()
        self.incomplete_trials_var = tk.StringVar()
        self.active_trials_var = tk.StringVar()
        self.eligible_trials_var = tk.StringVar()
        self.locked_trials_var = tk.StringVar()
        self.impossible_trials_var = tk.StringVar()
        self.trial_progress_var = tk.StringVar()
        self.preserved_when: dict[str, Any] = {}
        self.skill_labels: dict[str, int] = {}
        self.skill_by_label: dict[str, dict[str, Any]] = {}
        self.skill_options: list[dict[str, Any]] = []
        self.skill_detail_var = tk.StringVar(value="选择英雄后可查看具体技能与效果。")
        self.effect_widgets: dict[int, tuple[ttk.Button, bool]] = {}
        self.trial_display_vars: dict[int, tk.StringVar] = {}
        self.trial_progress_display_var = tk.StringVar(value="未设置最低进度")

        viewport = ttk.Frame(self)
        viewport.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        canvas = tk.Canvas(viewport, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        viewport.rowconfigure(0, weight=1)
        viewport.columnconfigure(0, weight=1)
        body = ttk.Frame(canvas, padding=14)
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=1)
        body.columnconfigure(4, weight=1)
        body.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(body_window, width=event.width),
        )
        self.bind(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"),
        )
        row = 0
        ttk.Label(body, text="规则名称").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.name_var, width=48).grid(
            row=row, column=1, columnspan=4, sticky="ew", pady=4
        )
        row += 1
        ttk.Label(body, text="动作方式").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(
            body,
            textvariable=self.action_mode_var,
            values=list(ACTION_MODE_LABELS),
            state="readonly",
            width=34,
        ).grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(
            body,
            text="自动维护不会绑定英雄或固定技能槽",
            foreground="#666666",
        ).grid(row=row, column=3, columnspan=2, sticky="w", padx=(14, 0))
        row += 1
        ttk.Label(body, text="行动英雄").grid(row=row, column=0, sticky="w", pady=4)
        self.hero_box = ttk.Button(
            body,
            textvariable=self.hero_var,
            command=self.pick_action_hero,
        )
        self.hero_box.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(
            body,
            text="从已有英雄库搜索选择",
            foreground="#666666",
        ).grid(row=row, column=3, columnspan=2, sticky="w", padx=(14, 0))
        row += 1
        ttk.Label(body, text="奇美拉形态").grid(row=row, column=0, sticky="nw", pady=4)
        forms = ttk.Frame(body)
        forms.grid(row=row, column=1, columnspan=4, sticky="w")
        for column, (form, label) in enumerate(FORM_LABELS.items()):
            ttk.Checkbutton(forms, text=label, variable=self.form_vars[form]).grid(
                row=0, column=column, padx=(0, 10)
            )
        row += 1
        ttk.Label(body, text="使用技能").grid(row=row, column=0, sticky="w", pady=4)
        self.skill_box = ttk.Button(body, command=self.pick_skill, compound="left")
        self.skill_box.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(body, text="目标").grid(row=row, column=3, sticky="e", padx=(14, 4))
        ttk.Combobox(
            body,
            textvariable=self.target_var,
            values=list(self.target_labels),
            state="readonly",
            width=23,
        ).grid(row=row, column=4, sticky="ew")
        row += 1
        ttk.Label(
            body,
            textvariable=self.skill_detail_var,
            foreground="#5f6670",
            wraplength=840,
            justify="left",
        ).grid(row=row, column=1, columnspan=4, sticky="ew", pady=(0, 6))
        row += 1

        maintenance = ttk.LabelFrame(
            body, text="自动维护效果（动作方式选择自动维护时生效）", padding=8
        )
        maintenance.grid(row=row, column=0, columnspan=5, sticky="ew", pady=(6, 4))
        ttk.Label(maintenance, text="对象").grid(row=0, column=0, sticky="w")
        ttk.Label(maintenance, text="必须维持的效果").grid(row=0, column=1, sticky="w")
        ttk.Label(maintenance, text="至少剩余回合").grid(row=0, column=2, sticky="w")
        maintenance_rows = (
            ("Boss", self.maintain_boss_effect_var, self.maintain_boss_turns_var),
            ("当前行动英雄", self.maintain_active_effect_var, self.maintain_active_turns_var),
            ("任一存活友方", self.maintain_ally_effect_var, self.maintain_ally_turns_var),
        )
        for maintenance_row, (label, effect_var, turns_var) in enumerate(
            maintenance_rows, 1
        ):
            ttk.Label(maintenance, text=label).grid(
                row=maintenance_row, column=0, sticky="w", pady=(5, 0)
            )
            self.effect_button(maintenance, effect_var, row=maintenance_row, column=1, padx=(6, 14), pady=(5, 0))
            ttk.Entry(maintenance, textvariable=turns_var, width=8).grid(
                row=maintenance_row, column=2, sticky="w", pady=(5, 0)
            )
        ttk.Checkbutton(
            maintenance,
            text="找不到提供者时，安全试用尚未学习的非一技能",
            variable=self.probe_unknown_skills_var,
        ).grid(row=1, column=3, sticky="w", padx=(18, 0))
        ttk.Checkbutton(
            maintenance,
            text="学习时允许主动切换神话形态",
            variable=self.probe_transforms_var,
        ).grid(row=2, column=3, sticky="w", padx=(18, 0))
        maintenance.columnconfigure(1, weight=1)
        row += 1

        condition_box = ttk.LabelFrame(body, text="可选条件（留空表示不限制）", padding=10)
        condition_box.grid(row=row, column=0, columnspan=5, sticky="ew", pady=(10, 4))
        ttk.Label(condition_box, text="当前英雄生命低于 %").grid(row=0, column=0, sticky="w")
        ttk.Entry(condition_box, textvariable=self.active_hp_var, width=8).grid(row=0, column=1, padx=(4, 16))
        ttk.Label(condition_box, text="任一友方生命低于 %").grid(row=0, column=2, sticky="w")
        ttk.Entry(condition_box, textvariable=self.ally_hp_var, width=8).grid(row=0, column=3, padx=(4, 16))
        ttk.Label(condition_box, text="Boss 生命低于 %").grid(row=0, column=4, sticky="w")
        ttk.Entry(condition_box, textvariable=self.boss_hp_var, width=8).grid(row=0, column=5, padx=(4, 0))
        ttk.Label(condition_box, text="Boss 必须已有的效果").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.effect_button(condition_box, self.boss_has_var, row=1, column=1, columnspan=2, multiple=True, padx=(4, 16), pady=(8, 0))
        ttk.Label(condition_box, text="Boss 必须缺少的效果").grid(row=1, column=3, sticky="w", pady=(8, 0))
        self.effect_button(condition_box, self.boss_missing_var, row=1, column=4, columnspan=2, multiple=True, padx=(4, 0), pady=(8, 0))
        ttk.Label(condition_box, text="点击图标按钮选择，可同时选择多个效果。", foreground="#666666").grid(row=2, column=0, columnspan=6, sticky="w", pady=(7, 0))
        row += 1

        advanced = ttk.Notebook(body)
        advanced.grid(row=row, column=0, columnspan=5, sticky="ew", pady=(8, 4))

        timing_tab = ttk.Frame(advanced, padding=10)
        advanced.add(timing_tab, text="回合、形态与目标")
        ttk.Label(timing_tab, text="奇美拉回合从").grid(row=0, column=0, sticky="w")
        ttk.Entry(timing_tab, textvariable=self.turn_at_least_var, width=8).grid(row=0, column=1, padx=(4, 12))
        ttk.Label(timing_tab, text="到").grid(row=0, column=2, sticky="w")
        ttk.Entry(timing_tab, textvariable=self.turn_at_most_var, width=8).grid(row=0, column=3, padx=(4, 18))
        ttk.Label(timing_tab, text="玩家行动计数 =").grid(row=0, column=4, sticky="w")
        ttk.Entry(timing_tab, textvariable=self.player_turn_var, width=8).grid(row=0, column=5, padx=(4, 18))
        ttk.Label(timing_tab, text="奇美拉行动计数 =").grid(row=0, column=6, sticky="w")
        ttk.Entry(timing_tab, textvariable=self.chimera_turn_var, width=8).grid(row=0, column=7, padx=(4, 0))
        ttk.Label(timing_tab, text="距离换形态最多").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(timing_tab, textvariable=self.turns_until_change_var, width=8).grid(row=1, column=1, padx=(4, 12), pady=(8, 0))
        ttk.Label(timing_tab, text="回合；下一形态").grid(row=1, column=2, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Combobox(timing_tab, textvariable=self.next_form_var, values=list(NEXT_FORM_LABELS), state="readonly", width=13).grid(row=1, column=4, padx=(4, 18), pady=(8, 0), sticky="w")
        ttk.Label(timing_tab, text="当前英雄形态").grid(row=1, column=5, sticky="w", pady=(8, 0))
        ttk.Combobox(timing_tab, textvariable=self.hero_form_var, values=list(HERO_FORM_LABELS), state="readonly", width=11).grid(row=1, column=6, columnspan=2, padx=(4, 0), pady=(8, 0), sticky="w")
        ttk.Label(timing_tab, text="当前伤害 ≥").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(timing_tab, textvariable=self.damage_at_least_var, width=12).grid(row=2, column=1, padx=(4, 12), pady=(8, 0))
        ttk.Label(timing_tab, text="当前伤害 <").grid(row=2, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(timing_tab, textvariable=self.damage_below_var, width=12).grid(row=2, column=3, padx=(4, 18), pady=(8, 0))
        ttk.Label(timing_tab, text="当前积分 ≥").grid(row=2, column=4, sticky="w", pady=(8, 0))
        ttk.Entry(timing_tab, textvariable=self.points_at_least_var, width=12).grid(row=2, column=5, padx=(4, 0), pady=(8, 0))

        effects_tab = ttk.Frame(advanced, padding=10)
        advanced.add(effects_tab, text="增益、减益与十格上限")
        ttk.Label(effects_tab, text="对象").grid(row=0, column=0, sticky="w")
        ttk.Label(effects_tab, text="必须存在的效果").grid(row=0, column=1, sticky="w")
        ttk.Label(effects_tab, text="剩余至少").grid(row=0, column=2, sticky="w")
        ttk.Label(effects_tab, text="剩余最多").grid(row=0, column=3, sticky="w")
        effect_rows = (
            ("Boss 必须存在", self.boss_effect_var, self.boss_effect_turns_min_var, self.boss_effect_turns_max_var),
            ("Boss 缺少/不足", self.boss_missing_effect_var, self.boss_missing_effect_turns_min_var, self.boss_missing_effect_turns_max_var),
            ("行动英雄必须存在", self.active_effect_var, self.active_effect_turns_min_var, self.active_effect_turns_max_var),
            ("行动英雄缺少/不足", self.active_missing_effect_var, self.active_missing_effect_turns_min_var, self.active_missing_effect_turns_max_var),
            ("任一友方必须存在", self.ally_effect_var, self.ally_effect_turns_min_var, self.ally_effect_turns_max_var),
            ("任一友方缺少/不足", self.ally_missing_effect_var, self.ally_missing_effect_turns_min_var, self.ally_missing_effect_turns_max_var),
        )
        for effect_row, (label, effect_var, minimum_var, maximum_var) in enumerate(effect_rows, 1):
            ttk.Label(effects_tab, text=label).grid(row=effect_row, column=0, sticky="w", pady=(6, 0))
            self.effect_button(effects_tab, effect_var, row=effect_row, column=1, padx=(5, 14), pady=(6, 0))
            ttk.Entry(effects_tab, textvariable=minimum_var, width=8).grid(row=effect_row, column=2, padx=(4, 14), pady=(6, 0))
            ttk.Entry(effects_tab, textvariable=maximum_var, width=8).grid(row=effect_row, column=3, padx=(4, 0), pady=(6, 0))
        slot_row = len(effect_rows) + 1
        ttk.Label(effects_tab, text="Boss 效果格数从").grid(row=slot_row, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(effects_tab, textvariable=self.boss_slots_min_var, width=8).grid(row=slot_row, column=1, sticky="w", padx=(5, 0), pady=(10, 0))
        ttk.Label(effects_tab, text="Boss 最多").grid(row=slot_row, column=1, sticky="w", padx=(80, 0), pady=(10, 0))
        ttk.Entry(effects_tab, textvariable=self.boss_slots_max_var, width=8).grid(row=slot_row, column=1, sticky="w", padx=(145, 0), pady=(10, 0))
        ttk.Label(effects_tab, text="行动英雄最多").grid(row=slot_row, column=2, sticky="e", pady=(10, 0))
        ttk.Entry(effects_tab, textvariable=self.active_slots_max_var, width=8).grid(row=slot_row, column=3, sticky="w", padx=(4, 14), pady=(10, 0))
        ttk.Label(effects_tab, text="所有友方最多").grid(row=slot_row, column=4, sticky="e", pady=(10, 0))
        ttk.Entry(effects_tab, textvariable=self.allies_slots_max_var, width=8).grid(row=slot_row, column=5, sticky="w", padx=(4, 0), pady=(10, 0))
        ttk.Label(effects_tab, text="“缺少/不足”可用于效果不存在或剩余回合不够时补效果；格数条件可防止第 11 个效果挤掉关键效果。", foreground="#666666").grid(row=slot_row + 1, column=0, columnspan=6, sticky="w", pady=(8, 0))
        effects_tab.columnconfigure(1, weight=1)

        ally_tab = ttk.Frame(advanced, padding=10)
        advanced.add(ally_tab, text="指定队友状态")
        ttk.Label(ally_tab, text="指定队友").grid(row=0, column=0, sticky="w")
        self.condition_ally_button = ttk.Button(
            ally_tab,
            textvariable=self.condition_ally_var,
            command=self.pick_condition_ally,
        )
        self.condition_ally_button.grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=(6, 0)
        )
        ttk.Label(ally_tab, text="条件").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Label(ally_tab, text="效果").grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Label(ally_tab, text="剩余至少").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Label(ally_tab, text="剩余最多").grid(row=1, column=3, sticky="w", pady=(8, 0))
        for ally_row, values in enumerate(
            (
                (
                    "必须存在",
                    self.selected_ally_effect_var,
                    self.selected_ally_effect_turns_min_var,
                    self.selected_ally_effect_turns_max_var,
                ),
                (
                    "缺少/不足",
                    self.selected_ally_missing_effect_var,
                    self.selected_ally_missing_turns_min_var,
                    self.selected_ally_missing_turns_max_var,
                ),
            ),
            2,
        ):
            label, effect_var, minimum_var, maximum_var = values
            ttk.Label(ally_tab, text=label).grid(row=ally_row, column=0, sticky="w", pady=(6, 0))
            self.effect_button(ally_tab, effect_var, row=ally_row, column=1, padx=(6, 14), pady=(6, 0))
            ttk.Entry(ally_tab, textvariable=minimum_var, width=8).grid(row=ally_row, column=2, padx=(4, 14), pady=(6, 0))
            ttk.Entry(ally_tab, textvariable=maximum_var, width=8).grid(row=ally_row, column=3, padx=(4, 0), pady=(6, 0))
        ttk.Label(ally_tab, text="效果格数从").grid(row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(ally_tab, textvariable=self.selected_ally_slots_min_var, width=8).grid(row=4, column=1, sticky="w", padx=(6, 0), pady=(10, 0))
        ttk.Label(ally_tab, text="到").grid(row=4, column=1, sticky="w", padx=(80, 0), pady=(10, 0))
        ttk.Entry(ally_tab, textvariable=self.selected_ally_slots_max_var, width=8).grid(row=4, column=1, sticky="w", padx=(105, 0), pady=(10, 0))
        ttk.Label(ally_tab, text="用于只针对某一名队友的增益、减益和十格容量做决策。", foreground="#666666").grid(row=5, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ally_tab.columnconfigure(1, weight=1)

        trials_tab = ttk.Frame(advanced, padding=10)
        advanced.add(trials_tab, text="试炼状态")
        ttk.Label(trials_tab, text="必须已完成").grid(row=0, column=0, sticky="w")
        self.trial_button(trials_tab, self.completed_trials_var, "选择已完成试炼", row=0, column=1, padx=(6, 18))
        ttk.Label(trials_tab, text="必须未完成").grid(row=0, column=2, sticky="w")
        self.trial_button(trials_tab, self.incomplete_trials_var, "选择未完成试炼", row=0, column=3, padx=(6, 0))
        ttk.Label(trials_tab, text="任一已不可能完成").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.trial_button(trials_tab, self.impossible_trials_var, "选择不可能试炼", row=1, column=1, padx=(6, 18), pady=(8, 0))
        ttk.Label(trials_tab, text="最低进度").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Button(trials_tab, textvariable=self.trial_progress_display_var, command=self.pick_trial_progress).grid(row=1, column=3, sticky="ew", padx=(6, 0), pady=(8, 0))
        ttk.Label(trials_tab, text="任一为分支当前试炼").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.trial_button(trials_tab, self.active_trials_var, "选择分支当前试炼", row=2, column=1, padx=(6, 18), pady=(8, 0))
        ttk.Label(trials_tab, text="任一此刻可推进").grid(row=2, column=2, sticky="w", pady=(8, 0))
        self.trial_button(trials_tab, self.eligible_trials_var, "选择可推进试炼", row=2, column=3, padx=(6, 0), pady=(8, 0))
        ttk.Label(trials_tab, text="任一被前置锁定").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.trial_button(trials_tab, self.locked_trials_var, "选择前置锁定试炼", row=3, column=1, padx=(6, 18), pady=(8, 0))
        ttk.Label(trials_tab, text="Wing、Tail、Paw 三条链同时进行；每条按简单→普通→苦难。", foreground="#666666").grid(row=3, column=2, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(trials_tab, text="所有试炼都按图标、形态、部位和说明选择，编号仅保存在策略文件内部。", foreground="#666666").grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))
        trials_tab.columnconfigure(1, weight=1)
        trials_tab.columnconfigure(3, weight=1)
        row += 1

        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, columnspan=5, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存规则", command=self.save).pack(side="right", padx=(0, 8))

        self.refresh_skills()
        if rule:
            self.populate(rule)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.wait_visibility()
        self.focus_set()

    def effect_button(
        self,
        parent: tk.Misc,
        variable: tk.StringVar,
        *,
        row: int,
        column: int,
        columnspan: int = 1,
        multiple: bool = False,
        padx: Any = 0,
        pady: Any = 0,
    ) -> ttk.Button:
        button = ttk.Button(
            parent,
            text="选择效果…",
            image=self.icons.photo("alliance_chimera"),
            compound="left",
            command=lambda: self.pick_effect(variable, multiple),
        )
        button.grid(
            row=row,
            column=column,
            columnspan=columnspan,
            sticky="ew",
            padx=padx,
            pady=pady,
        )
        self.effect_widgets[id(variable)] = (button, multiple)
        self.refresh_effect_button(variable)
        return button

    def pick_effect(self, variable: tk.StringVar, multiple: bool) -> None:
        selected = [canonical_effect_token(value) for value in split_effects(variable.get())]
        EffectPickerDialog(
            self,
            self.icons,
            selected,
            lambda values: self.apply_effect(variable, values),
            multiple=multiple,
        )

    def apply_effect(self, variable: tk.StringVar, values: list[str]) -> None:
        variable.set(", ".join(values))
        self.refresh_effect_button(variable)

    def refresh_effect_button(self, variable: tk.StringVar) -> None:
        widget = self.effect_widgets.get(id(variable))
        if not widget:
            return
        button, _multiple = widget
        values = split_effects(variable.get())
        if not values:
            button.configure(text="选择效果…", image=self.icons.photo("alliance_chimera"))
            return
        labels = [effect_label(value) for value in values]
        text = "、".join(labels[:2])
        if len(labels) > 2:
            text += f" 等 {len(labels)} 项"
        button.configure(text=text, image=self.icons.effect_photo(values[0]))

    def trial_button(
        self,
        parent: tk.Misc,
        variable: tk.StringVar,
        title: str,
        *,
        row: int,
        column: int,
        padx: Any = 0,
        pady: Any = 0,
    ) -> ttk.Button:
        display = tk.StringVar(value="选择试炼…")
        self.trial_display_vars[id(variable)] = display
        button = ttk.Button(
            parent,
            textvariable=display,
            command=lambda: self.pick_rule_trials(variable, title),
        )
        button.grid(row=row, column=column, sticky="ew", padx=padx, pady=pady)
        self.refresh_trial_button(variable)
        return button

    def refresh_trial_button(self, variable: tk.StringVar) -> None:
        display = self.trial_display_vars.get(id(variable))
        if display is None:
            return
        try:
            selected_ids = split_positive_ids(variable.get(), "试炼")
        except ValueError:
            display.set("已保存的试炼设置")
            return
        display.set(selected_trial_summary(self.trials, selected_ids))

    def pick_rule_trials(self, variable: tk.StringVar, title: str) -> None:
        if not self.trials:
            messagebox.showinfo(
                "试炼资源不可用",
                "没有找到本地试炼缓存，请重新启动工具以重建资源库。",
                parent=self,
            )
            return
        try:
            selected_ids = split_positive_ids(variable.get(), "试炼")
        except ValueError:
            selected_ids = []
        TrialPickerDialog(
            self,
            self.trials,
            selected_ids,
            lambda values: self.apply_rule_trials(variable, values),
            icons=self.icons,
            title=title,
            action_text="使用所选试炼",
        )

    def apply_rule_trials(self, variable: tk.StringVar, values: list[int]) -> None:
        variable.set(", ".join(map(str, values)))
        self.refresh_trial_button(variable)

    def pick_trial_progress(self) -> None:
        if not self.trials:
            messagebox.showinfo("试炼资源不可用", "请重新启动工具以重建本地资源库。", parent=self)
            return
        try:
            current = parse_trial_progress(self.trial_progress_var.get())
        except ValueError:
            current = {}
        TrialProgressDialog(self, self.trials, current, self.icons, self.apply_trial_progress)

    def apply_trial_progress(self, values: dict[str, float]) -> None:
        self.trial_progress_var.set(format_trial_progress(values))
        self.refresh_trial_progress_display()

    def refresh_trial_progress_display(self) -> None:
        try:
            values = parse_trial_progress(self.trial_progress_var.get())
        except ValueError:
            self.trial_progress_display_var.set("已保存的进度条件")
            return
        if not values:
            self.trial_progress_display_var.set("设置最低进度…")
            return
        names = selected_trial_summary(self.trials, [int(value) for value in values])
        self.trial_progress_display_var.set(f"{names}（{len(values)} 项）")

    def pick_action_hero(self) -> None:
        selected = self.selected_hero_ids()
        HeroPickerDialog(
            self,
            self.icons,
            self.catalog,
            selected[0] if len(selected) == 1 else None,
            self.apply_action_hero,
            title="从英雄库选择行动英雄",
            allow_any=True,
        )

    def apply_action_hero(self, hero_id: int | None) -> None:
        if hero_id is None:
            self.hero_var.set("全部已知英雄")
        else:
            label = next(
                (
                    name
                    for name, values in self.hero_labels.items()
                    if values == [hero_id]
                ),
                "",
            )
            if not label:
                label = str(self.catalog.get(hero_id, {}).get("name") or "未识别英雄")
                self.hero_labels[label] = [hero_id]
            self.hero_var.set(label)
        selected = self.selected_hero_ids()
        hero = self.catalog.get(selected[0], {}) if len(selected) == 1 else {}
        self.hero_box.configure(
            image=self.icons.hero_photo(
                selected[0] if len(selected) == 1 else None, hero, "small"
            ),
            compound="left",
        )
        self.refresh_skills()

    def pick_condition_ally(self) -> None:
        HeroPickerDialog(
            self,
            self.icons,
            self.catalog,
            self.condition_ally_labels.get(self.condition_ally_var.get()),
            self.apply_condition_ally,
            title="从英雄库选择指定队友",
        )

    def apply_condition_ally(self, hero_id: int | None) -> None:
        if hero_id is None:
            self.condition_ally_var.set("未选择队友")
            return
        label = next(
            (
                name
                for name, saved_id in self.condition_ally_labels.items()
                if saved_id == hero_id
            ),
            "",
        )
        if not label:
            label = str(self.catalog.get(hero_id, {}).get("name") or "未识别英雄")
            self.condition_ally_labels[label] = hero_id
        self.condition_ally_var.set(label)

    def selected_hero_ids(self) -> list[int]:
        return self.hero_labels.get(self.hero_var.get(), [])

    def refresh_skills(self) -> None:
        hero_ids = self.selected_hero_ids()
        hero = self.catalog.get(hero_ids[0], {}) if len(hero_ids) == 1 else {}
        self.hero_box.configure(
            image=self.icons.hero_photo(
                hero_ids[0] if len(hero_ids) == 1 else None, hero, "small"
            ),
            compound="left",
        )
        previous_slot = self.skill_labels.get(self.skill_var.get())
        self.skill_options = []
        if len(hero_ids) == 1:
            for skill in hero.get("skills", []):
                slot = int(skill.get("slot", 0))
                if slot > 0:
                    label = f"技能{slot} · {skill.get('name', f'Skill {slot}')}"
                    option = dict(skill)
                    option.update({"slot": slot, "label": label})
                    self.skill_options.append(option)
        if not self.skill_options:
            self.skill_options.extend(
                {"slot": slot, "label": f"技能{slot}", "name": f"技能{slot}"}
                for slot in range(1, 5)
            )
        self.skill_options.append(
            {
                "slot": TRANSFORM_ACTION_SLOT,
                "label": "神话英雄：切换形态",
                "name": "切换形态",
                "description": "神话英雄主动切换到另一形态，并使用另一组技能。",
            }
        )
        self.skill_labels = {
            str(option["label"]): int(option["slot"])
            for option in self.skill_options
        }
        self.skill_by_label = {
            str(option["label"]): option for option in self.skill_options
        }
        wanted = next(
            (
                label
                for label, slot in self.skill_labels.items()
                if previous_slot is not None and slot == previous_slot
            ),
            next(iter(self.skill_labels)),
        )
        self.skill_var.set(wanted)
        self.refresh_skill_button()

    def refresh_skill_button(self) -> None:
        label = self.skill_var.get() or "选择技能…"
        slot = self.skill_labels.get(label, 1)
        hero_ids = self.selected_hero_ids()
        skill = self.skill_by_label.get(label, {"slot": slot, "name": label})
        photo = (
            self.icons.photo("Polymorph", "medium")
            if slot == TRANSFORM_ACTION_SLOT
            else self.icons.skill_photo(
                hero_ids[0] if len(hero_ids) == 1 else None, skill, "medium"
            )
        )
        self.skill_box.configure(text=label, image=photo, compound="left")
        description = plain_game_text(skill.get("description"))
        effect_summary = (
            "切换后将使用该英雄另一形态的三项技能。"
            if slot == TRANSFORM_ACTION_SLOT
            else skill_effect_summary(skill.get("typeId"), limit=4)
        )
        summary = description or effect_summary
        if description and effect_summary != "尚未学习到效果资料":
            summary = f"{description}　｜　{effect_summary}"
        self.skill_detail_var.set(summary[:520])

    def pick_skill(self) -> None:
        hero_ids = self.selected_hero_ids()
        selected_slot = self.skill_labels.get(self.skill_var.get())
        SkillPickerDialog(
            self,
            self.icons,
            hero_ids[0] if len(hero_ids) == 1 else None,
            list(self.skill_options),
            selected_slot,
            self.apply_skill,
        )

    def apply_skill(self, label: str, _slot: int) -> None:
        self.skill_var.set(label)
        self.refresh_skill_button()

    def populate(self, rule: dict[str, Any]) -> None:
        self.name_var.set(str(rule.get("name", "")))
        when = rule.get("when", {})
        self.preserved_when = {
            key: value
            for key, value in when.items()
            if key not in EDITABLE_WHEN_KEYS
        }
        hero_value = when.get("activeHeroTypeId")
        hero_ids = hero_value if isinstance(hero_value, list) else [hero_value]
        hero_ids = [value for value in hero_ids if isinstance(value, int)]
        all_ids = list(self.catalog)
        if set(hero_ids) == set(all_ids):
            self.hero_var.set("全部已知英雄")
        elif hero_ids:
            wanted = hero_ids[0]
            label = next(
                (key for key, values in self.hero_labels.items() if values == [wanted]),
                "全部已知英雄",
            )
            self.hero_var.set(label)
        self.refresh_skills()
        action = rule.get("action", {})
        action_type = action.get("type")
        self.action_mode_var.set(
            next(
                (
                    label
                    for label, value in ACTION_MODE_LABELS.items()
                    if value == action_type
                ),
                "施放指定技能",
            )
        )
        if action_type == "maintainEffects":
            for requirement in action.get("requirements", []):
                if not isinstance(requirement, dict):
                    continue
                effect = requirement.get("effect")
                if not isinstance(effect, dict):
                    continue
                effect_value = effect.get("kind", effect.get("effectTypeId", ""))
                turns_value = requirement.get("keepTurnsAtLeast", 1)
                scope = requirement.get("scope")
                if scope == "boss":
                    self.maintain_boss_effect_var.set(str(effect_value))
                    self.maintain_boss_turns_var.set(str(turns_value))
                elif scope == "activeHero":
                    self.maintain_active_effect_var.set(str(effect_value))
                    self.maintain_active_turns_var.set(str(turns_value))
                elif scope == "anyAlly":
                    self.maintain_ally_effect_var.set(str(effect_value))
                    self.maintain_ally_turns_var.set(str(turns_value))
            self.probe_unknown_skills_var.set(
                action.get("probeUnknownSkills") is True
            )
            self.probe_transforms_var.set(action.get("probeTransforms") is True)
        slot = (
            TRANSFORM_ACTION_SLOT
            if action.get("type") == "transform"
            else action.get("skillSlot")
        )
        if not isinstance(slot, int) and isinstance(action.get("skillTypeId"), int):
            slot = 1
        skill_label = next(
            (label for label, value in self.skill_labels.items() if value == slot),
            next(iter(self.skill_labels)),
        )
        self.skill_var.set(skill_label)
        forms = when.get("form", list(FORM_LABELS))
        forms = forms if isinstance(forms, list) else [forms]
        for form, variable in self.form_vars.items():
            variable.set(form in forms)
        target = action.get("target", {"type": "boss"})
        if isinstance(target, str):
            target = {"type": target}
        target_label = next(
            (label for label, selector in self.target_labels.items() if selector == target),
            "奇美拉",
        )
        self.target_var.set(target_label)
        self.active_hp_var.set(str(when.get("activeHeroHpPctBelow", "")))
        self.ally_hp_var.set(str(when.get("anyAllyHpPctBelow", "")))
        self.boss_hp_var.set(str(when.get("bossHpPctBelow", "")))
        self.boss_has_var.set(", ".join(map(str, when.get("bossHasEffects", []))))
        self.boss_missing_var.set(", ".join(map(str, when.get("bossMissingEffects", []))))
        field_variables = (
            ("chimeraTurnAtLeast", self.turn_at_least_var),
            ("chimeraTurnAtMost", self.turn_at_most_var),
            ("playerTurnCount", self.player_turn_var),
            ("chimeraTurnCount", self.chimera_turn_var),
            ("turnsUntilFormChangeAtMost", self.turns_until_change_var),
            ("currentDamageAtLeast", self.damage_at_least_var),
            ("currentDamageBelow", self.damage_below_var),
            ("currentCompetitionPointsAtLeast", self.points_at_least_var),
            ("bossEffectSlotsAtLeast", self.boss_slots_min_var),
            ("bossEffectSlotsAtMost", self.boss_slots_max_var),
            ("activeHeroEffectSlotsAtMost", self.active_slots_max_var),
            ("allAlliesEffectSlotsAtMost", self.allies_slots_max_var),
        )
        for key, variable in field_variables:
            variable.set(str(when.get(key, "")))
        if not self.turn_at_least_var.get() and "turnAtLeast" in when:
            self.turn_at_least_var.set(str(when["turnAtLeast"]))
        if not self.turn_at_most_var.get() and "turnAtMost" in when:
            self.turn_at_most_var.set(str(when["turnAtMost"]))
        next_form = when.get("nextForm")
        self.next_form_var.set(
            next(
                (label for label, value in NEXT_FORM_LABELS.items() if value == next_form),
                "不限",
            )
        )
        hero_form_index = when.get("activeHeroFormIndex")
        self.hero_form_var.set(
            next(
                (
                    label
                    for label, value in HERO_FORM_LABELS.items()
                    if value == hero_form_index
                ),
                "不限",
            )
        )
        self.populate_effect_selector(
            when.get("bossHasEffect"),
            self.boss_effect_var,
            self.boss_effect_turns_min_var,
            self.boss_effect_turns_max_var,
        )
        self.populate_effect_selector(
            when.get("bossMissingEffect"),
            self.boss_missing_effect_var,
            self.boss_missing_effect_turns_min_var,
            self.boss_missing_effect_turns_max_var,
        )
        self.populate_effect_selector(
            when.get("activeHeroHasEffect"),
            self.active_effect_var,
            self.active_effect_turns_min_var,
            self.active_effect_turns_max_var,
        )
        self.populate_effect_selector(
            when.get("activeHeroMissingEffect"),
            self.active_missing_effect_var,
            self.active_missing_effect_turns_min_var,
            self.active_missing_effect_turns_max_var,
        )
        self.populate_effect_selector(
            when.get("anyAllyHasEffect"),
            self.ally_effect_var,
            self.ally_effect_turns_min_var,
            self.ally_effect_turns_max_var,
        )
        self.populate_effect_selector(
            when.get("anyAllyMissingEffect"),
            self.ally_missing_effect_var,
            self.ally_missing_effect_turns_min_var,
            self.ally_missing_effect_turns_max_var,
        )
        selected_ally_conditions = [
            when.get(key)
            for key in (
                "allyHasEffect",
                "allyMissingEffect",
                "allyEffectSlotsAtLeast",
                "allyEffectSlotsAtMost",
            )
            if isinstance(when.get(key), dict)
        ]
        selected_ally_id = next(
            (
                value.get("heroTypeId")
                for value in selected_ally_conditions
                if isinstance(value.get("heroTypeId"), int)
            ),
            None,
        )
        if isinstance(selected_ally_id, int):
            selected_label = next(
                (
                    label
                    for label, hero_id in self.condition_ally_labels.items()
                    if hero_id == selected_ally_id
                ),
                None,
            )
            if selected_label:
                self.condition_ally_var.set(selected_label)
        ally_has = when.get("allyHasEffect")
        if isinstance(ally_has, dict):
            self.populate_effect_selector(
                ally_has.get("effect"),
                self.selected_ally_effect_var,
                self.selected_ally_effect_turns_min_var,
                self.selected_ally_effect_turns_max_var,
            )
        ally_missing = when.get("allyMissingEffect")
        if isinstance(ally_missing, dict):
            self.populate_effect_selector(
                ally_missing.get("effect"),
                self.selected_ally_missing_effect_var,
                self.selected_ally_missing_turns_min_var,
                self.selected_ally_missing_turns_max_var,
            )
        ally_slots_min = when.get("allyEffectSlotsAtLeast")
        if isinstance(ally_slots_min, dict):
            self.selected_ally_slots_min_var.set(str(ally_slots_min.get("count", "")))
        ally_slots_max = when.get("allyEffectSlotsAtMost")
        if isinstance(ally_slots_max, dict):
            self.selected_ally_slots_max_var.set(str(ally_slots_max.get("count", "")))
        self.completed_trials_var.set(", ".join(map(str, when.get("completedTrialsAll", []))))
        self.incomplete_trials_var.set(", ".join(map(str, when.get("incompleteTrialsAll", []))))
        self.active_trials_var.set(", ".join(map(str, when.get("activeTrialsAny", []))))
        self.eligible_trials_var.set(", ".join(map(str, when.get("eligibleTrialsAny", []))))
        self.locked_trials_var.set(", ".join(map(str, when.get("lockedTrialsAny", []))))
        self.impossible_trials_var.set(", ".join(map(str, when.get("impossibleTrialsAny", []))))
        self.trial_progress_var.set(format_trial_progress(when.get("trialProgressAtLeast")))
        self.refresh_skill_button()
        for effect_variable in (
            self.maintain_boss_effect_var,
            self.maintain_active_effect_var,
            self.maintain_ally_effect_var,
            self.boss_has_var,
            self.boss_missing_var,
            self.boss_effect_var,
            self.boss_missing_effect_var,
            self.active_effect_var,
            self.active_missing_effect_var,
            self.ally_effect_var,
            self.ally_missing_effect_var,
            self.selected_ally_effect_var,
            self.selected_ally_missing_effect_var,
        ):
            self.refresh_effect_button(effect_variable)
        for trial_variable in (
            self.completed_trials_var,
            self.incomplete_trials_var,
            self.active_trials_var,
            self.eligible_trials_var,
            self.locked_trials_var,
            self.impossible_trials_var,
        ):
            self.refresh_trial_button(trial_variable)
        self.refresh_trial_progress_display()

    @staticmethod
    def populate_effect_selector(
        selector: Any,
        effect_var: tk.StringVar,
        minimum_var: tk.StringVar,
        maximum_var: tk.StringVar,
    ) -> None:
        if isinstance(selector, dict):
            effect_var.set(str(selector.get("kind", selector.get("effectTypeId", ""))))
            minimum_var.set(str(selector.get("turnsAtLeast", "")))
            maximum_var.set(str(selector.get("turnsAtMost", "")))
        elif selector is not None:
            effect_var.set(str(selector))

    @staticmethod
    def optional_nonnegative_number(value: str, label: str) -> float | int | None:
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError as error:
            raise ValueError(f"{label}必须是数字") from error
        if number < 0:
            raise ValueError(f"{label}不能小于 0")
        return int(number) if number.is_integer() else number

    @staticmethod
    def optional_nonnegative_int(value: str, label: str) -> int | None:
        text = value.strip()
        if not text:
            return None
        try:
            number = int(text)
        except ValueError as error:
            raise ValueError(f"{label}必须是整数") from error
        if number < 0:
            raise ValueError(f"{label}不能小于 0")
        return number

    @classmethod
    def effect_selector(
        cls,
        effect_text: str,
        minimum_text: str,
        maximum_text: str,
        label: str,
    ) -> dict[str, Any] | None:
        effect = effect_text.strip()
        minimum = cls.optional_nonnegative_int(minimum_text, f"{label}最少剩余回合")
        maximum = cls.optional_nonnegative_int(maximum_text, f"{label}最多剩余回合")
        if not effect:
            if minimum is not None or maximum is not None:
                raise ValueError(f"{label}填写了持续回合，但没有填写效果")
            return None
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"{label}最少剩余回合不能大于最多剩余回合")
        selector: dict[str, Any] = (
            {"effectTypeId": int(effect)} if effect.isdigit() else {"kind": effect}
        )
        if minimum is not None:
            selector["turnsAtLeast"] = minimum
        if maximum is not None:
            selector["turnsAtMost"] = maximum
        return selector

    @staticmethod
    def optional_number(value: str, label: str) -> float | None:
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError as error:
            raise ValueError(f"{label}必须是数字") from error
        if not 0 <= number <= 100:
            raise ValueError(f"{label}必须在 0 到 100 之间")
        return number

    def save(self) -> None:
        try:
            hero_ids = self.selected_hero_ids()
            forms = [form for form, value in self.form_vars.items() if value.get()]
            if not hero_ids:
                raise ValueError("请选择行动英雄")
            if not forms:
                raise ValueError("至少选择一种奇美拉形态")
            action_mode = ACTION_MODE_LABELS.get(self.action_mode_var.get())
            if action_mode is None:
                raise ValueError("请选择动作方式")
            slot = self.skill_labels.get(self.skill_var.get())
            if action_mode == "cast" and slot is None:
                raise ValueError("请选择技能")
            target = self.target_labels.get(self.target_var.get())
            if action_mode == "cast" and not target:
                raise ValueError("请选择目标")
            when: dict[str, Any] = dict(self.preserved_when)
            when["form"] = forms
            if action_mode == "cast":
                when["activeHeroTypeId"] = (
                    hero_ids if len(hero_ids) > 1 else hero_ids[0]
                )
            else:
                when.pop("activeHeroTypeId", None)
            hero_form_index = HERO_FORM_LABELS.get(self.hero_form_var.get())
            if hero_form_index is not None:
                when["activeHeroFormIndex"] = hero_form_index
            if action_mode == "cast" and slot == TRANSFORM_ACTION_SLOT:
                if hero_form_index is None:
                    when["activeHeroFormIndex"] = 0
                when["activeHeroIsMetamorph"] = True
                when["transformationReady"] = True
            numbers = (
                ("activeHeroHpPctBelow", self.active_hp_var.get(), "当前英雄生命"),
                ("anyAllyHpPctBelow", self.ally_hp_var.get(), "任一友方生命"),
                ("bossHpPctBelow", self.boss_hp_var.get(), "Boss 生命"),
            )
            for key, text, label in numbers:
                number = self.optional_number(text, label)
                if number is not None:
                    when[key] = number
            boss_has = split_effects(self.boss_has_var.get())
            boss_missing = split_effects(self.boss_missing_var.get())
            if boss_has:
                when["bossHasEffects"] = boss_has
            if boss_missing:
                when["bossMissingEffects"] = boss_missing
            integer_fields = (
                ("chimeraTurnAtLeast", self.turn_at_least_var, "最早奇美拉回合"),
                ("chimeraTurnAtMost", self.turn_at_most_var, "最晚奇美拉回合"),
                ("playerTurnCount", self.player_turn_var, "玩家行动计数"),
                ("chimeraTurnCount", self.chimera_turn_var, "奇美拉行动计数"),
                ("turnsUntilFormChangeAtMost", self.turns_until_change_var, "距离换形态回合"),
                ("bossEffectSlotsAtLeast", self.boss_slots_min_var, "Boss 最少效果格数"),
                ("bossEffectSlotsAtMost", self.boss_slots_max_var, "Boss 最多效果格数"),
                ("activeHeroEffectSlotsAtMost", self.active_slots_max_var, "行动英雄最多效果格数"),
                ("allAlliesEffectSlotsAtMost", self.allies_slots_max_var, "友方最多效果格数"),
            )
            for key, variable, label in integer_fields:
                value = self.optional_nonnegative_int(variable.get(), label)
                if value is not None:
                    when[key] = value
            if (
                "chimeraTurnAtLeast" in when
                and "chimeraTurnAtMost" in when
                and when["chimeraTurnAtLeast"] > when["chimeraTurnAtMost"]
            ):
                raise ValueError("最早奇美拉回合不能大于最晚奇美拉回合")
            numeric_fields = (
                ("currentDamageAtLeast", self.damage_at_least_var, "最低当前伤害"),
                ("currentDamageBelow", self.damage_below_var, "最高当前伤害"),
                ("currentCompetitionPointsAtLeast", self.points_at_least_var, "最低当前积分"),
            )
            for key, variable, label in numeric_fields:
                value = self.optional_nonnegative_number(variable.get(), label)
                if value is not None:
                    when[key] = value
            if (
                "currentDamageAtLeast" in when
                and "currentDamageBelow" in when
                and when["currentDamageAtLeast"] >= when["currentDamageBelow"]
            ):
                raise ValueError("最低当前伤害必须小于最高当前伤害")
            next_form = NEXT_FORM_LABELS.get(self.next_form_var.get())
            if next_form:
                when["nextForm"] = next_form
            detailed_effects = (
                (
                    "bossHasEffect",
                    self.boss_effect_var,
                    self.boss_effect_turns_min_var,
                    self.boss_effect_turns_max_var,
                    "Boss 效果",
                ),
                (
                    "activeHeroHasEffect",
                    self.active_effect_var,
                    self.active_effect_turns_min_var,
                    self.active_effect_turns_max_var,
                    "行动英雄效果",
                ),
                (
                    "bossMissingEffect",
                    self.boss_missing_effect_var,
                    self.boss_missing_effect_turns_min_var,
                    self.boss_missing_effect_turns_max_var,
                    "Boss 缺少效果",
                ),
                (
                    "activeHeroMissingEffect",
                    self.active_missing_effect_var,
                    self.active_missing_effect_turns_min_var,
                    self.active_missing_effect_turns_max_var,
                    "行动英雄缺少效果",
                ),
                (
                    "anyAllyHasEffect",
                    self.ally_effect_var,
                    self.ally_effect_turns_min_var,
                    self.ally_effect_turns_max_var,
                    "友方效果",
                ),
                (
                    "anyAllyMissingEffect",
                    self.ally_missing_effect_var,
                    self.ally_missing_effect_turns_min_var,
                    self.ally_missing_effect_turns_max_var,
                    "友方缺少效果",
                ),
            )
            for key, effect_var, minimum_var, maximum_var, label in detailed_effects:
                selector = self.effect_selector(
                    effect_var.get(), minimum_var.get(), maximum_var.get(), label
                )
                if selector is not None:
                    when[key] = selector
            selected_ally_id = self.condition_ally_labels.get(
                self.condition_ally_var.get()
            )
            selected_ally_has = self.effect_selector(
                self.selected_ally_effect_var.get(),
                self.selected_ally_effect_turns_min_var.get(),
                self.selected_ally_effect_turns_max_var.get(),
                "指定队友效果",
            )
            selected_ally_missing = self.effect_selector(
                self.selected_ally_missing_effect_var.get(),
                self.selected_ally_missing_turns_min_var.get(),
                self.selected_ally_missing_turns_max_var.get(),
                "指定队友缺少效果",
            )
            selected_ally_slots_min = self.optional_nonnegative_int(
                self.selected_ally_slots_min_var.get(), "指定队友最少效果格数"
            )
            selected_ally_slots_max = self.optional_nonnegative_int(
                self.selected_ally_slots_max_var.get(), "指定队友最多效果格数"
            )
            if (
                selected_ally_has is not None
                or selected_ally_missing is not None
                or selected_ally_slots_min is not None
                or selected_ally_slots_max is not None
            ) and selected_ally_id is None:
                raise ValueError("请为指定队友条件选择一名英雄")
            if (
                selected_ally_slots_min is not None
                and selected_ally_slots_max is not None
                and selected_ally_slots_min > selected_ally_slots_max
            ):
                raise ValueError("指定队友最少效果格数不能大于最多效果格数")
            if selected_ally_has is not None:
                when["allyHasEffect"] = {
                    "heroTypeId": selected_ally_id,
                    "effect": selected_ally_has,
                }
            if selected_ally_missing is not None:
                when["allyMissingEffect"] = {
                    "heroTypeId": selected_ally_id,
                    "effect": selected_ally_missing,
                }
            if selected_ally_slots_min is not None:
                when["allyEffectSlotsAtLeast"] = {
                    "heroTypeId": selected_ally_id,
                    "count": selected_ally_slots_min,
                }
            if selected_ally_slots_max is not None:
                when["allyEffectSlotsAtMost"] = {
                    "heroTypeId": selected_ally_id,
                    "count": selected_ally_slots_max,
                }
            trial_sets = (
                ("completedTrialsAll", self.completed_trials_var, "已完成试炼"),
                ("incompleteTrialsAll", self.incomplete_trials_var, "未完成试炼"),
                ("activeTrialsAny", self.active_trials_var, "分支当前试炼"),
                ("eligibleTrialsAny", self.eligible_trials_var, "此刻可推进试炼"),
                ("lockedTrialsAny", self.locked_trials_var, "前置锁定试炼"),
                ("impossibleTrialsAny", self.impossible_trials_var, "不可能试炼"),
            )
            for key, variable, label in trial_sets:
                values = split_positive_ids(variable.get(), label)
                if values:
                    when[key] = values
            progress = parse_trial_progress(self.trial_progress_var.get())
            if progress:
                when["trialProgressAtLeast"] = progress
            if action_mode == "maintainEffects":
                requirements: list[dict[str, Any]] = []
                maintenance_fields = (
                    (
                        "boss",
                        self.maintain_boss_effect_var,
                        self.maintain_boss_turns_var,
                        "Boss 维护效果",
                    ),
                    (
                        "activeHero",
                        self.maintain_active_effect_var,
                        self.maintain_active_turns_var,
                        "行动英雄维护效果",
                    ),
                    (
                        "anyAlly",
                        self.maintain_ally_effect_var,
                        self.maintain_ally_turns_var,
                        "友方维护效果",
                    ),
                )
                for scope, effect_var, turns_var, label in maintenance_fields:
                    effect = effect_var.get().strip()
                    if not effect:
                        continue
                    keep_turns = self.optional_nonnegative_int(
                        turns_var.get(), f"{label}最少剩余回合"
                    )
                    requirements.append(
                        {
                            "scope": scope,
                            "effect": (
                                {"effectTypeId": int(effect)}
                                if effect.isdigit()
                                else {"kind": effect}
                            ),
                            "keepTurnsAtLeast": (
                                keep_turns if keep_turns is not None else 1
                            ),
                        }
                    )
                if not requirements:
                    raise ValueError("自动维护动作至少要填写一个必要效果")
                action = {
                    "type": "maintainEffects",
                    "requirements": requirements,
                    "probeUnknownSkills": self.probe_unknown_skills_var.get(),
                    "probeTransforms": self.probe_transforms_var.get(),
                }
                default_name = "自动学习并维持必要效果"
            elif action_mode == "executeTrialRecipe":
                configured_recipe_trial_ids = when.get(
                    "eligibleTrialsAny", when.get("activeTrialsAny", [])
                )
                action = {
                    "type": "executeTrialRecipe",
                    "probeUnknownSkills": self.probe_unknown_skills_var.get(),
                    "probeTransforms": self.probe_transforms_var.get(),
                }
                if isinstance(configured_recipe_trial_ids, list) and configured_recipe_trial_ids:
                    action["trialIds"] = list(configured_recipe_trial_ids)
                default_name = "按当前试炼配方自动决策"
            else:
                default_target = (
                    "自己" if slot == TRANSFORM_ACTION_SLOT else self.target_var.get()
                )
                default_name = (
                    f"{self.hero_var.get()} · {self.skill_var.get()} → {default_target}"
                )
                action = (
                    {"type": "transform"}
                    if slot == TRANSFORM_ACTION_SLOT
                    else {
                        "type": "cast",
                        "skillSlot": slot,
                        "target": dict(target),
                    }
                )
                selected_skill = self.skill_by_label.get(self.skill_var.get(), {})
                if (
                    isinstance(action, dict)
                    and action.get("type") == "cast"
                    and isinstance(selected_skill.get("typeId"), int)
                ):
                    action["skillTypeId"] = selected_skill["typeId"]
            result = {
                "name": self.name_var.get().strip() or default_name,
                "when": when,
                "action": action,
            }
        except ValueError as error:
            messagebox.showerror("无法保存规则", str(error), parent=self)
            return
        self.on_save(result)
        self.destroy()


class TrialPickerDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        trials: list[dict[str, Any]],
        selected_ids: list[int],
        on_save: Callable[[list[int]], None],
        *,
        icons: ChimeraIconRepository,
        title: str = "选择当前轮换的必做试炼",
        action_text: str = "使用所选试炼",
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.geometry("1180x650")
        self.minsize(820, 500)
        self.transient(parent)
        self.grab_set()
        self.trials = trials
        self.on_save = on_save
        self.icons = icons

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="按 Ctrl/Shift 可多选；“试炼内容”显示完整说明，部位、读取状态和前置条件在下方详情中查看。",
            foreground="#666666",
        ).pack(fill="x", pady=(0, 8))

        columns = ("form", "difficulty", "reward")
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="tree headings",
            selectmode="extended",
            height=14,
        )
        headings = {
            "form": "形态",
            "difficulty": "试炼等级",
            "reward": "当前奖励",
        }
        self.tree.heading("#0", text="试炼内容")
        self.tree.column("#0", width=580, minwidth=330, stretch=True)
        widths = {"form": 90, "difficulty": 90, "reward": 360}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                minwidth=60,
                stretch=column == "reward",
            )
        vertical = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.show_description)

        for index, trial in enumerate(self.trials):
            trial_id = trial.get("id")
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                text=trial_display_name(trial, compact=True),
                image=self.icons.trial_photo(trial),
                values=(
                    FORM_LABELS.get(trial.get("form"), trial.get("form", "未知")),
                    DIFFICULTY_LABELS.get(
                        trial.get("difficulty"),
                        trial.get("difficulty", trial.get("difficultyId", "未知")),
                    ),
                    reward_summary(trial),
                ),
            )
            if trial_id in selected_ids:
                self.tree.selection_add(str(index))

        detail_box = ttk.LabelFrame(frame, text="选中试炼的详细内容", padding=8)
        detail_box.pack(fill="x", pady=(10, 0))
        self.description = ScrolledText(
            detail_box,
            height=5,
            wrap="word",
            state="disabled",
            font=("Microsoft YaHei UI", 9),
        )
        self.description.pack(fill="x")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text=action_text, command=self.save).pack(
            side="right", padx=(0, 8)
        )
        self.show_description()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def show_description(self, _event: tk.Event | None = None) -> None:
        selected = self.tree.selection()
        text = "请选择一条试炼查看说明。"
        if selected:
            trial = self.trials[int(selected[0])]
            text = readable_game_text(trial.get("description")) or "未读取到试炼说明。"
            text += "\n\n部位：" + PART_LABELS.get(
                trial.get("part"), str(trial.get("part") or "未知")
            )
            text += "　　读取状态：" + trial_runtime_status_summary(trial)
            prerequisites = trial.get("requiredPrerequisiteTrialIds")
            if isinstance(prerequisites, list) and prerequisites:
                by_id = {value.get("id"): value for value in self.trials}
                names = [
                    trial_display_name(by_id[value], compact=True)
                    for value in prerequisites
                    if value in by_id
                ]
                text += "\n必须先完成：" + ("；".join(names) or "同一部位的前一级试炼")
            text += "\n\n当前奖励：" + reward_summary(trial)
        self.description.configure(state="normal")
        self.description.delete("1.0", "end")
        self.description.insert("1.0", text)
        self.description.configure(state="disabled")

    def save(self) -> None:
        ids = [
            int(self.trials[int(index)]["id"])
            for index in self.tree.selection()
            if isinstance(self.trials[int(index)].get("id"), int)
        ]
        self.on_save(ids)
        self.destroy()


class TrialProgressDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        trials: list[dict[str, Any]],
        current: dict[str, float],
        icons: ChimeraIconRepository,
        on_save: Callable[[dict[str, float]], None],
    ) -> None:
        super().__init__(parent)
        self.title("设置试炼最低进度")
        self.geometry("1060x650")
        self.minsize(760, 480)
        self.transient(parent)
        self.grab_set()
        self.trials = trials
        self.values = dict(current)
        self.icons = icons
        self.on_save = on_save
        self.progress_var = tk.IntVar(value=50)
        self.detail_var = tk.StringVar(value="选择一条试炼后在这里查看部位和读取状态。")

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="选择一项或多项试炼，设定达到多少百分比后规则才生效。",
            foreground="#666666",
        ).pack(fill="x", pady=(0, 8))
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("form", "difficulty", "progress"),
            show="tree headings",
            selectmode="extended",
            height=16,
        )
        self.tree.heading("#0", text="试炼内容")
        self.tree.column("#0", width=690, minwidth=380, stretch=True)
        for key, title, width in (
            ("form", "形态", 90),
            ("difficulty", "等级", 75),
            ("progress", "最低进度", 90),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="center", stretch=False)
        vertical = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.selection_changed)
        self.refresh()

        detail = ttk.LabelFrame(frame, text="选中试炼的详细内容", padding=(8, 6))
        detail.pack(fill="x", pady=(8, 0))
        ttk.Label(detail, textvariable=self.detail_var).pack(fill="x")

        editor = ttk.Frame(frame)
        editor.pack(fill="x", pady=(10, 0))
        ttk.Label(editor, text="最低进度").pack(side="left")
        ttk.Spinbox(editor, from_=0, to=100, textvariable=self.progress_var, width=8).pack(side="left", padx=(6, 3))
        ttk.Label(editor, text="%").pack(side="left")
        ttk.Button(editor, text="应用到选中项", command=self.apply_selected).pack(side="left", padx=(12, 0))
        ttk.Button(editor, text="移除选中项的进度条件", command=self.remove_selected).pack(side="left", padx=(8, 0))
        ttk.Button(editor, text="清空全部", command=self.clear_all).pack(side="left", padx=(8, 0))
        ttk.Button(editor, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(editor, text="保存进度条件", command=self.save).pack(side="right", padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def refresh(self) -> None:
        selected = set(self.tree.selection()) if hasattr(self, "tree") else set()
        self.tree.delete(*self.tree.get_children())
        for index, trial in enumerate(self.trials):
            trial_id = trial.get("id")
            value = self.values.get(str(trial_id))
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                text=trial_display_name(trial, compact=True),
                image=self.icons.trial_photo(trial),
                values=(
                    FORM_LABELS.get(trial.get("form"), "未知"),
                    DIFFICULTY_LABELS.get(trial.get("difficulty"), "未知"),
                    "未设置" if value is None else f"{value * 100:g}%",
                ),
            )
            if str(index) in selected:
                self.tree.selection_add(str(index))

    def selection_changed(self, _event: tk.Event | None = None) -> None:
        selected = self.tree.selection()
        if len(selected) != 1:
            self.detail_var.set(
                f"已选择 {len(selected)} 项试炼。" if selected else "选择一条试炼后在这里查看部位和读取状态。"
            )
            return
        trial = self.trials[int(selected[0])]
        trial_id = trial.get("id")
        value = self.values.get(str(trial_id))
        if value is not None:
            self.progress_var.set(round(value * 100))
        self.detail_var.set(
            "部位："
            + PART_LABELS.get(trial.get("part"), str(trial.get("part") or "未知"))
            + "　　读取状态："
            + trial_runtime_status_summary(trial)
        )

    def apply_selected(self) -> None:
        try:
            progress = int(self.progress_var.get())
        except (ValueError, tk.TclError):
            progress = -1
        if not 0 <= progress <= 100:
            messagebox.showerror("进度无效", "最低进度必须在 0% 到 100% 之间。", parent=self)
            return
        for index in self.tree.selection():
            trial_id = self.trials[int(index)].get("id")
            if isinstance(trial_id, int):
                self.values[str(trial_id)] = progress / 100
        self.refresh()

    def remove_selected(self) -> None:
        for index in self.tree.selection():
            trial_id = self.trials[int(index)].get("id")
            self.values.pop(str(trial_id), None)
        self.refresh()

    def clear_all(self) -> None:
        self.values.clear()
        self.refresh()

    def save(self) -> None:
        self.on_save(self.values)
        self.destroy()


class ChimeraToolApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("奇美拉策略控制器")
        self.root.geometry("1280x800")
        self.root.minsize(1024, 640)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.icons = ChimeraIconRepository(self.root)
        self.ui_catalog = ensure_ui_catalog_cache()

        self.process_items: dict[str, dict[str, Any]] = {}
        default_heroes: dict[int, dict[str, Any]] = {
            hero_id: {
                "name": name,
                "skills": [
                    {"slot": slot, "name": f"技能{slot}"} for slot in range(1, 5)
                ],
            }
            for hero_id, name in KNOWN_HEROES.items()
        }
        self.catalog = load_hero_catalog(default_heroes)
        save_hero_catalog(self.catalog)
        self.prefetch_catalog_assets()
        self.rules: list[dict[str, Any]] = []
        self.trials: list[dict[str, Any]] = []
        self.selected_difficulty_id = 5
        self.team_hero_ids: list[int] = []
        self.process: subprocess.Popen[str] | None = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stop_requested = False
        self.refresh_in_progress = False

        self.pid_var = tk.StringVar()
        self.difficulty_var = tk.StringVar(value=DIFFICULTY_LABELS["Nightmare"])
        self.mandatory_trials_var = tk.StringVar()
        self.mandatory_trials_display_var = tk.StringVar(value="未选择必做试炼")
        self.minimum_damage_var = tk.StringVar(value="0")
        self.minimum_points_var = tk.StringVar(value="0")
        self.max_regroup_retries_var = tk.StringVar(value="10")
        self.trial_catalog_var = tk.StringVar(value="正在载入本地奇美拉资源库…")
        self.team_var = tk.StringVar(value="尚未读取当前奇美拉五人队伍")
        self.state_var = tk.StringVar(value="尚未读取战斗状态")
        self.run_var = tk.StringVar(value="已停止")

        self.build_ui()
        self.load_initial_strategy()
        self.refresh_processes()
        self.root.after(100, self.poll_events)

    def prefetch_catalog_assets(self) -> None:
        snapshot = {
            hero_id: {
                **hero,
                "skills": [
                    dict(skill)
                    for skill in hero.get("skills", [])
                    if isinstance(skill, dict)
                ],
            }
            for hero_id, hero in self.catalog.items()
            if isinstance(hero, dict)
        }
        threading.Thread(
            target=self.icons.prefetch_catalog,
            args=(snapshot,),
            daemon=True,
            name="chimera-art-cache",
        ).start()

    def build_ui(self) -> None:
        style = ttk.Style(self.root)
        style.configure("AppTitle.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("SectionTitle.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Muted.TLabel", font=("Microsoft YaHei UI", 9))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 9))
        style.configure("Toolbar.TButton", padding=(10, 6))
        style.configure("ChimeraRules.Treeview", rowheight=36, font=("Microsoft YaHei UI", 9))
        style.configure("ChimeraRules.Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

        root_frame = ttk.Frame(self.root, padding=(16, 14))
        root_frame.pack(fill="both", expand=True)
        root_frame.columnconfigure(0, weight=1)
        root_frame.rowconfigure(1, weight=1)

        header = ttk.Frame(root_frame, padding=(14, 10), relief="ridge")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        header.columnconfigure(1, weight=2)
        ttk.Label(header, text="奇美拉策略中心", style="AppTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="目标、策略与执行状态集中在一个工作区",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        account_actions = ttk.Frame(header)
        account_actions.grid(row=0, column=1, sticky="ew", padx=(18, 0))
        account_actions.columnconfigure(0, weight=1)
        self.pid_box = ttk.Combobox(
            account_actions, textvariable=self.pid_var, state="readonly", width=46
        )
        self.pid_box.grid(row=0, column=0, sticky="ew")
        self.pid_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh_snapshot())
        self.refresh_button = ttk.Button(
            account_actions,
            text="↻ 刷新账户",
            command=self.refresh_processes,
            style="Toolbar.TButton",
        )
        self.refresh_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(header, textvariable=self.state_var, style="SectionTitle.TLabel").grid(
            row=1, column=1, sticky="w", padx=(18, 0), pady=(2, 0)
        )
        ttk.Label(header, textvariable=self.team_var, style="Muted.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(7, 0)
        )

        self.main_pane = ttk.Panedwindow(root_frame, orient="horizontal")
        self.main_pane.grid(row=1, column=0, sticky="nsew")
        left = ttk.Frame(self.main_pane, padding=(0, 0, 8, 0), width=350)
        right = ttk.Frame(self.main_pane, padding=(8, 0, 0, 0))
        self.main_pane.add(left, weight=0)
        self.main_pane.add(right, weight=1)

        run = ttk.LabelFrame(left, text="执行控制", padding=12)
        run.pack(side="bottom", fill="x")
        ttk.Label(run, textvariable=self.run_var, style="SectionTitle.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            run,
            text="普通点击不会打断接管；游戏内暂停或这里的暂停按钮会立即停止。",
            wraplength=310,
            justify="left",
            style="Muted.TLabel",
        ).pack(fill="x", pady=(3, 10))
        run_buttons = ttk.Frame(run)
        run_buttons.pack(fill="x")
        self.stop_button = ttk.Button(
            run_buttons,
            text="暂停接管",
            command=self.stop,
            state="disabled",
            style="Toolbar.TButton",
        )
        self.stop_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.start_button = ttk.Button(
            run_buttons,
            text="开始执行",
            command=self.start,
            style="Primary.TButton",
        )
        self.start_button.pack(side="left", fill="x", expand=True, padx=(5, 0))

        objectives = ttk.LabelFrame(left, text="战斗目标", padding=12)
        objectives.pack(side="top", fill="both", expand=True, pady=(0, 10))
        objectives.columnconfigure(0, weight=1)
        objectives.columnconfigure(1, weight=1)
        ttk.Label(objectives, text="Boss 难度", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(objectives, text="最多免费重整", style="Muted.TLabel").grid(
            row=0, column=1, sticky="w", padx=(10, 0)
        )
        difficulty_box = ttk.Combobox(
            objectives,
            textvariable=self.difficulty_var,
            values=list(BOSS_DIFFICULTY_BY_LABEL),
            state="readonly",
        )
        difficulty_box.grid(row=1, column=0, sticky="ew", pady=(3, 10))
        difficulty_box.bind("<<ComboboxSelected>>", lambda _event: self.difficulty_changed())
        ttk.Spinbox(
            objectives,
            from_=0,
            to=999,
            textvariable=self.max_regroup_retries_var,
        ).grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(3, 10))
        ttk.Label(objectives, text="最低伤害", style="Muted.TLabel").grid(
            row=2, column=0, sticky="w"
        )
        ttk.Label(objectives, text="最低积分", style="Muted.TLabel").grid(
            row=2, column=1, sticky="w", padx=(10, 0)
        )
        ttk.Entry(objectives, textvariable=self.minimum_damage_var).grid(
            row=3, column=0, sticky="ew", pady=(3, 10)
        )
        ttk.Entry(objectives, textvariable=self.minimum_points_var).grid(
            row=3, column=1, sticky="ew", padx=(10, 0), pady=(3, 10)
        )
        ttk.Label(objectives, text="必做试炼", style="SectionTitle.TLabel").grid(
            row=4, column=0, columnspan=2, sticky="w"
        )
        self.mandatory_trials_button = ttk.Button(
            objectives,
            textvariable=self.mandatory_trials_display_var,
            image=self.icons.photo("alliance_chimera"),
            compound="left",
            command=self.pick_mandatory_trials,
            style="Toolbar.TButton",
        )
        self.mandatory_trials_button.grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(5, 10)
        )
        ttk.Label(
            objectives,
            textvariable=self.trial_catalog_var,
            wraplength=310,
            justify="left",
            style="Muted.TLabel",
        ).grid(row=6, column=0, columnspan=2, sticky="ew")
        ttk.Label(
            objectives,
            text="必要试炼失败时自动免费重整；目标完成后停留在结算页，由你决定是否保留。",
            wraplength=310,
            justify="left",
            style="Muted.TLabel",
        ).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        strategy = ttk.LabelFrame(right, text="策略规则", padding=10)
        strategy.grid(row=0, column=0, sticky="nsew")
        strategy.columnconfigure(0, weight=1)
        strategy.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(strategy)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(
            toolbar, text="越靠上的规则优先级越高", style="Muted.TLabel"
        ).pack(side="left")
        for text, command in (
            ("＋ 添加", self.add_rule),
            ("编辑", self.edit_rule),
            ("删除", self.delete_rule),
            ("↑", lambda: self.move_rule(-1)),
            ("↓", lambda: self.move_rule(1)),
            ("保存策略", self.save_strategy),
        ):
            ttk.Button(
                toolbar, text=text, command=command, style="Toolbar.TButton"
            ).pack(side="right", padx=(6, 0))

        tree_area = ttk.Frame(strategy)
        tree_area.grid(row=1, column=0, sticky="nsew")
        tree_area.columnconfigure(0, weight=1)
        tree_area.rowconfigure(0, weight=1)
        columns = ("order", "name", "hero", "form", "skill", "target", "condition")
        self.tree = ttk.Treeview(
            tree_area,
            columns=columns,
            show="tree headings",
            style="ChimeraRules.Treeview",
        )
        self.tree.heading("#0", text="头像")
        self.tree.column("#0", width=54, minwidth=54, stretch=False, anchor="center")
        headings = {
            "order": "顺序",
            "name": "规则",
            "hero": "英雄",
            "form": "形态",
            "skill": "具体技能",
            "target": "目标",
            "condition": "附加条件",
        }
        widths = {
            "order": 46,
            "name": 170,
            "hero": 130,
            "form": 120,
            "skill": 135,
            "target": 110,
            "condition": 250,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                minwidth=45,
                stretch=column in {"name", "condition"},
            )
        vertical = ttk.Scrollbar(tree_area, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(tree_area, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<Double-1>", lambda _event: self.edit_rule())

        activity_bar = ttk.Frame(right, padding=(0, 8, 0, 0))
        activity_bar.grid(row=1, column=0, sticky="ew")
        self.log_toggle_button = ttk.Button(
            activity_bar,
            text="显示运行记录 ▾",
            command=self.toggle_log,
            style="Toolbar.TButton",
        )
        self.log_toggle_button.pack(side="right")
        ttk.Label(
            activity_bar,
            text="开始控制始终位于左下角，不再随策略表高度移动",
            style="Muted.TLabel",
        ).pack(side="left")

        self.log_box = ttk.LabelFrame(right, text="运行记录", padding=8)
        self.log_box.grid(row=2, column=0, sticky="ew", pady=(0, 0))
        self.log = ScrolledText(
            self.log_box,
            height=6,
            wrap="word",
            state="disabled",
            font=("Microsoft YaHei UI", 9),
        )
        self.log.pack(fill="both", expand=True)
        self.log_box.grid_remove()
        self.log_visible = False
        self.root.after_idle(lambda: self.main_pane.sashpos(0, 350))

    def toggle_log(self) -> None:
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_box.grid()
            self.log_toggle_button.configure(text="隐藏运行记录 ▴")
        else:
            self.log_box.grid_remove()
            self.log_toggle_button.configure(text="显示运行记录 ▾")

    def append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        line_count = int(self.log.index("end-1c").split(".", 1)[0])
        if line_count > 2000:
            self.log.delete("1.0", f"{line_count - 2000 + 1}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def load_initial_strategy(self) -> None:
        source = USER_STRATEGY if USER_STRATEGY.is_file() else DEFAULT_STRATEGY
        try:
            config = read_strategy(source)
            self.rules = [dict(rule) for rule in config.get("rules", []) if isinstance(rule, dict)]
            objectives = config.get("objectives", {})
            if not isinstance(objectives, dict):
                objectives = {}
            trial_ids = objectives.get("mandatoryTrialIds", [])
            self.set_trial_difficulty(
                infer_trial_difficulty_id(config),
                translate_existing=False,
            )
            self.mandatory_trials_var.set(
                ", ".join(str(value) for value in trial_ids if isinstance(value, int))
            )
            self.update_mandatory_trial_display()
            self.minimum_damage_var.set(str(objectives.get("minimumDamage", 0)))
            self.minimum_points_var.set(
                str(objectives.get("minimumCompetitionPoints", 0))
            )
            self.max_regroup_retries_var.set(
                str(objectives.get("maxRegroupRetries", 10))
            )
            team = config.get("team")
            loaded_team = team.get("heroIds") if isinstance(team, dict) else None
            self.team_hero_ids = (
                list(loaded_team)
                if isinstance(loaded_team, list) and len(loaded_team) == 5
                else []
            )
            self.update_team_label()
            self.append_log(f"已载入策略：{source.name}")
        except Exception as error:
            self.rules = []
            self.append_log(f"策略载入失败：{error}")
        self.refresh_tree()

    @staticmethod
    def translated_trial_id(trial_id: int, difficulty_id: int) -> int:
        offset = trial_id - 8_000_000
        source_difficulty, ordinal = divmod(offset, 100)
        if 1 <= source_difficulty <= 6 and 1 <= ordinal <= 27:
            return 8_000_000 + difficulty_id * 100 + ordinal
        return trial_id

    def set_trial_difficulty(
        self,
        difficulty_id: int,
        *,
        translate_existing: bool,
    ) -> None:
        if not 1 <= difficulty_id <= 6:
            difficulty_id = 5
        if translate_existing and difficulty_id != self.selected_difficulty_id:
            translator = lambda value: self.translated_trial_id(value, difficulty_id)
            try:
                mandatory = split_positive_ids(self.mandatory_trials_var.get(), "试炼")
            except ValueError:
                mandatory = []
            self.mandatory_trials_var.set(", ".join(map(str, (translator(value) for value in mandatory))))
            for rule in self.rules:
                if not isinstance(rule, dict):
                    continue
                when = rule.get("when")
                if isinstance(when, dict):
                    for key in (
                        "completedTrialsAll",
                        "incompleteTrialsAll",
                        "activeTrialsAny",
                        "eligibleTrialsAny",
                        "lockedTrialsAny",
                        "impossibleTrialsAny",
                    ):
                        if isinstance(when.get(key), list):
                            when[key] = [
                                translator(value) if isinstance(value, int) else value
                                for value in when[key]
                            ]
                    progress = when.get("trialProgressAtLeast")
                    if isinstance(progress, dict):
                        when["trialProgressAtLeast"] = {
                            str(translator(int(key))) if str(key).isdigit() else str(key): value
                            for key, value in progress.items()
                        }
                action = rule.get("action")
                if isinstance(action, dict) and isinstance(action.get("trialIds"), list):
                    action["trialIds"] = [
                        translator(value) if isinstance(value, int) else value
                        for value in action["trialIds"]
                    ]
        self.selected_difficulty_id = difficulty_id
        difficulty_name = list(DIFFICULTY_LABELS)[difficulty_id - 1]
        self.difficulty_var.set(DIFFICULTY_LABELS[difficulty_name])
        self.trials = cached_trials(self.ui_catalog, difficulty_id)
        reward_fingerprint = self.ui_catalog.get("rewardRotationFingerprint") or "尚无奖励缓存"
        if self.trials:
            self.trial_catalog_var.set(
                f"启动缓存：{len(self.trials)} 项试炼 · "
                + ("最近奖励已缓存" if reward_fingerprint != "尚无奖励缓存" else reward_fingerprint)
            )
        else:
            self.trial_catalog_var.set("本地试炼缓存不可用")
        self.update_mandatory_trial_display()
        self.refresh_tree()

    def difficulty_changed(self) -> None:
        difficulty_id = BOSS_DIFFICULTY_BY_LABEL.get(self.difficulty_var.get(), 5)
        self.set_trial_difficulty(difficulty_id, translate_existing=True)

    def refresh_processes(self) -> None:
        if self.refresh_in_progress:
            return
        current_item = self.process_items.get(self.pid_var.get())
        current_pid = int(current_item["pid"]) if current_item else None
        self.refresh_in_progress = True
        self.refresh_button.configure(state="disabled")
        self.state_var.set("正在读取账户…")
        threading.Thread(
            target=self.load_processes,
            args=(current_pid,),
            daemon=True,
        ).start()

    def load_processes(self, current_pid: int | None) -> None:
        try:
            processes = raid_processes()
            attach_windows(processes)
            valid = []
            for item in processes.values():
                path = item.get("path")
                if isinstance(path, str) and is_supported_raid_executable(path):
                    item["account"] = identify_account(int(item["pid"]))
                    valid.append(item)
            self.events.put(("processes_refreshed", (valid, current_pid)))
        except Exception as error:
            self.events.put(("processes_error", str(error)))

    def apply_processes(self, valid: list[dict[str, Any]], current_pid: int | None) -> None:
        self.refresh_in_progress = False
        self.refresh_button.configure(state="normal")
        self.process_items = {
            process_label(item): item
            for item in sorted(valid, key=lambda value: int(value["pid"]))
        }
        labels = list(self.process_items)
        self.pid_box.configure(values=labels)
        preferred = next(
            (
                label
                for label, item in self.process_items.items()
                if current_pid is not None and int(item["pid"]) == current_pid
            ),
            labels[0] if labels else "",
        )
        self.pid_var.set(preferred)
        recognized = sum(
            1 for item in valid if isinstance(item.get("account"), dict)
        )
        self.append_log(
            f"发现 {len(labels)} 个 Raid 账户，已识别 {recognized} 个账户名称。"
        )
        self.refresh_snapshot()

    def selected_pid(self) -> int | None:
        item = self.process_items.get(self.pid_var.get())
        return int(item["pid"]) if item else None

    def refresh_snapshot(self) -> None:
        pid = self.selected_pid()
        if pid is None:
            self.state_var.set("没有可用账户")
            self.clear_trial_catalog()
            return
        try:
            with AgentIpc(pid) as ipc:
                state = ipc.decision()
                lifecycle = ipc.lifecycle() or {}
                rotation = ipc.rotation_catalog() or {}
        except Exception as error:
            self.state_var.set("状态读取失败")
            self.clear_trial_catalog()
            self.append_log(f"PID {pid} 状态读取失败：{error}")
            return
        if isinstance(state, dict) and state.get("type") == "hero_catalog_state":
            self.update_catalog(state)
            state = None
        self.update_team_binding(lifecycle)
        lifecycle_section = lifecycle.get(
            "selection" if lifecycle.get("screen") == "team_selection" else "battle"
        )
        if isinstance(lifecycle_section, dict) and isinstance(
            lifecycle_section.get("heroCatalog"), list
        ):
            self.update_catalog({"heroes": lifecycle_section["heroCatalog"]})

        catalog_state = dict(state) if isinstance(state, dict) else {}
        if isinstance(rotation, dict):
            if isinstance(rotation.get("catalog"), dict):
                catalog_state.setdefault("trialCatalog", rotation["catalog"])
            if isinstance(rotation.get("identity"), dict):
                catalog_state.setdefault("rotationIdentity", rotation["identity"])
        if isinstance(lifecycle_section, dict):
            stage_id = lifecycle_section.get("stageId")
            if isinstance(stage_id, int):
                catalog_state.setdefault("chimeraStageId", stage_id)
        self.update_trial_catalog(catalog_state)
        if not state:
            if lifecycle.get("screen") == "team_selection":
                self.state_var.set(f"{self.pid_var.get()} · 奇美拉队伍界面")
            else:
                self.state_var.set(f"{self.pid_var.get()} · 尚无已验证奇美拉战斗状态")
            return
        self.update_catalog(state)
        form = FORM_LABELS.get(state.get("chimera", {}).get("currentForm"), "未知形态")
        hero = state.get("activeHeroName", "未知英雄")
        boss = next(iter(state.get("bosses", [])), {})
        hp = boss.get("healthPct")
        hp_text = f"{hp:.4f}%" if isinstance(hp, (int, float)) else "未知"
        waiting = "等待手动指令" if state.get("battle", {}).get("waitingForManualCommand") else "非等待状态"
        self.state_var.set(
            f"{self.pid_var.get()} · {form} · {hero} · Boss {hp_text} · {waiting}"
        )
        self.refresh_tree()

    def update_catalog(self, state: dict[str, Any]) -> None:
        updated: dict[int, dict[str, Any]] = {}
        active_hero_type_id = state.get("activeHeroTypeId")
        live_skills = {
            int(skill["typeId"]): skill
            for skill in state.get("skills", [])
            if isinstance(skill, dict) and isinstance(skill.get("typeId"), int)
        }
        for hero in state.get("heroes", []):
            if not isinstance(hero, dict) or not isinstance(hero.get("typeId"), int):
                continue
            hero_id = int(hero["typeId"])
            previous = self.catalog.get(hero_id, {})
            previous_by_type = {
                int(skill["typeId"]): skill
                for skill in previous.get("skills", [])
                if isinstance(skill, dict) and isinstance(skill.get("typeId"), int)
            }
            previous_by_slot = {
                int(skill["slot"]): skill
                for skill in previous.get("skills", [])
                if isinstance(skill, dict) and isinstance(skill.get("slot"), int)
            }
            skills = []
            slot = 0
            for skill in hero.get("skills", []):
                if not isinstance(skill, dict) or not skill.get("activeSkill") or skill.get("hiddenOnHud"):
                    continue
                slot += 1
                type_id = skill.get("typeId")
                cached = previous_by_type.get(type_id, previous_by_slot.get(slot, {}))
                merged = dict(cached) if isinstance(cached, dict) else {}
                merged.update(
                    {
                        "slot": slot,
                        "name": skill.get("name") or merged.get("name") or f"技能{slot}",
                        "typeId": type_id,
                        "defaultCooldown": skill.get("maxCooldown", merged.get("defaultCooldown")),
                    }
                )
                if hero_id == active_hero_type_id and isinstance(type_id, int):
                    live = live_skills.get(type_id)
                    if live:
                        merged.update(
                            {
                                key: value
                                for key, value in live.items()
                                if key
                                in {
                                    "name",
                                    "description",
                                    "icon",
                                    "defaultCooldown",
                                    "typeId",
                                    "slot",
                                }
                                and value not in (None, "")
                            }
                        )
                skills.append(merged)
            if not skills:
                skills = [
                    dict(previous_by_slot.get(value, {"slot": value, "name": f"技能{value}"}))
                    for value in range(1, 5)
                ]
            updated[hero_id] = {
                **previous,
                "name": hero.get("name")
                or previous.get("name")
                or KNOWN_HEROES.get(hero_id, "未识别英雄"),
                "avatar": hero.get("avatar") or previous.get("avatar") or "",
                "skills": skills,
            }
        if updated:
            self.catalog.update(updated)
            if save_hero_catalog(self.catalog):
                self.prefetch_catalog_assets()
            self.update_team_label()

    def clear_trial_catalog(self) -> None:
        self.set_trial_difficulty(
            self.selected_difficulty_id,
            translate_existing=False,
        )

    def update_team_label(self) -> None:
        if self.team_hero_ids:
            self.team_var.set(
                "已绑定当前五人队伍："
                + "、".join(
                    self.catalog.get(hero_id, {"name": "未识别英雄"})["name"]
                    for hero_id in self.team_hero_ids
                )
            )
        else:
            self.team_var.set("尚未读取当前奇美拉五人队伍")

    def update_team_binding(self, lifecycle: dict[str, Any]) -> None:
        current = lifecycle_team_ids(lifecycle)
        if current:
            self.team_hero_ids = current
            self.update_team_label()

    def update_trial_catalog(self, state: dict[str, Any]) -> None:
        trials, difficulty_name, fingerprint = current_trial_catalog(state)
        if not trials:
            self.clear_trial_catalog()
            return
        live_difficulty_id = next(
            (
                index
                for index, name in enumerate(DIFFICULTY_LABELS, 1)
                if name == difficulty_name
            ),
            self.selected_difficulty_id,
        )
        if live_difficulty_id != self.selected_difficulty_id:
            self.set_trial_difficulty(live_difficulty_id, translate_existing=True)
        self.ui_catalog = cache_live_rewards(
            self.ui_catalog,
            live_difficulty_id,
            trials,
            fingerprint,
        )
        self.trials = trials
        self.update_mandatory_trial_display()
        self.trial_catalog_var.set(
            f"{DIFFICULTY_LABELS.get(difficulty_name, difficulty_name)}："
            f"已读取 {len(trials)} 项试炼 · 当前奖励已同步到缓存"
        )

    def pick_mandatory_trials(self) -> None:
        if not self.trials:
            messagebox.showinfo(
                "试炼资源不可用",
                "没有找到本地试炼缓存，请重新启动工具以重建资源库。",
                parent=self.root,
            )
            return
        try:
            selected_ids = split_positive_ids(
                self.mandatory_trials_var.get(), "试炼设置"
            )
        except ValueError as error:
            messagebox.showerror("试炼设置无效", str(error), parent=self.root)
            return
        TrialPickerDialog(
            self.root,
            self.trials,
            selected_ids,
            self.apply_mandatory_trials,
            icons=self.icons,
            title="选择当前轮换的必做试炼",
            action_text="设为必做试炼",
        )

    def apply_mandatory_trials(self, trial_ids: list[int]) -> None:
        self.mandatory_trials_var.set(", ".join(map(str, trial_ids)))
        self.update_mandatory_trial_display()

    def update_mandatory_trial_display(self) -> None:
        try:
            ids = split_positive_ids(self.mandatory_trials_var.get(), "试炼")
        except ValueError:
            self.mandatory_trials_display_var.set("已保存必做试炼设置 · 点击查看或修改")
            return
        if not ids:
            self.mandatory_trials_display_var.set("尚未选择 · 点击此处选择必做试炼")
            return
        self.mandatory_trials_display_var.set(
            f"已选择 {len(ids)} 项必做试炼 · 点击查看完整内容或修改"
        )

    def human_rule(self, index: int, rule: dict[str, Any]) -> tuple[Any, ...]:
        when = rule.get("when", {})
        action = rule.get("action", {})
        hero_value = when.get("activeHeroTypeId")
        hero_ids = hero_value if isinstance(hero_value, list) else [hero_value]
        hero_ids = [value for value in hero_ids if isinstance(value, int)]
        if set(hero_ids) == set(self.catalog):
            hero_text = "全部已知英雄"
        else:
            hero_text = "、".join(self.catalog.get(value, {"name": "未识别英雄"})["name"] for value in hero_ids) or "未指定"
        forms = when.get("form", list(FORM_LABELS))
        forms = forms if isinstance(forms, list) else [forms]
        form_text = "、".join(FORM_LABELS.get(value, str(value)) for value in forms)
        if action.get("type") == "executeTrialRecipe":
            hero_text = "任意行动英雄"
            skill_text = "当前试炼配方"
            recipe_ids = action.get("trialIds", [])
            target_text = (
                selected_trial_summary(self.trials, recipe_ids)
                if isinstance(recipe_ids, list) and recipe_ids
                else "当前可推进试炼"
            )
        elif action.get("type") == "maintainEffects":
            hero_text = "任意行动英雄"
            skill_text = "自动学习并维护"
            requirements = [
                requirement
                for requirement in action.get("requirements", [])
                if isinstance(requirement, dict)
            ]
            scope_labels = {
                "boss": "Boss",
                "activeHero": "行动者",
                "anyAlly": "友方",
            }
            target_text = "；".join(
                f"{scope_labels.get(requirement.get('scope'), requirement.get('scope'))}:"
                f"{effect_label((requirement.get('effect') or {}).get('kind', (requirement.get('effect') or {}).get('effectTypeId', '')))}"
                for requirement in requirements
            )
        else:
            slot = action.get("skillSlot")
            skill_text = "切换形态"
            if action.get("type") != "transform":
                skill_text = f"技能{slot}" if slot else "已保存技能"
                if len(hero_ids) == 1 and isinstance(slot, int):
                    skill_text = next(
                        (
                            str(skill.get("name"))
                            for skill in self.catalog.get(hero_ids[0], {}).get("skills", [])
                            if skill.get("slot") == slot and skill.get("name")
                        ),
                        skill_text,
                    )
            target = action.get(
                "target",
                {"type": "self" if action.get("type") == "transform" else "boss"},
            )
            target = {"type": target} if isinstance(target, str) else target
            target_type = target.get("type", "boss") if isinstance(target, dict) else "boss"
            if target_type == "allyHeroTypeId":
                wanted = target.get("heroTypeId")
                target_text = self.catalog.get(wanted, {"name": "未识别队友"})["name"]
            else:
                target_text = TARGET_LABELS.get(target_type, target_type)
        conditions = []
        labels = (
            ("activeHeroHpPctBelow", "自己生命<"),
            ("anyAllyHpPctBelow", "任一友方生命<"),
            ("bossHpPctBelow", "Boss生命<"),
        )
        for key, label in labels:
            if key in when:
                conditions.append(f"{label}{when[key]}%")
        if when.get("bossHasEffects"):
            conditions.append("Boss已有:" + "、".join(effect_label(value) for value in when["bossHasEffects"]))
        if when.get("bossMissingEffects"):
            conditions.append("Boss缺少:" + "、".join(effect_label(value) for value in when["bossMissingEffects"]))
        advanced_labels = (
            ("chimeraTurnAtLeast", "奇美拉回合≥"),
            ("chimeraTurnAtMost", "奇美拉回合≤"),
            ("playerTurnCount", "玩家行动="),
            ("chimeraTurnCount", "Boss行动="),
            ("turnsUntilFormChangeAtMost", "换形态≤"),
            ("currentDamageAtLeast", "伤害≥"),
            ("currentDamageBelow", "伤害<"),
            ("currentCompetitionPointsAtLeast", "积分≥"),
            ("bossEffectSlotsAtLeast", "Boss效果格≥"),
            ("bossEffectSlotsAtMost", "Boss效果格≤"),
            ("activeHeroEffectSlotsAtMost", "自己效果格≤"),
            ("allAlliesEffectSlotsAtMost", "友方效果格≤"),
        )
        for key, label in advanced_labels:
            if key in when:
                conditions.append(f"{label}{when[key]}")
        if when.get("nextForm"):
            conditions.append(
                "下一形态=" + FORM_LABELS.get(when["nextForm"], str(when["nextForm"]))
            )
        for key, label in (
            ("bossHasEffect", "Boss效果"),
            ("bossMissingEffect", "Boss缺少/不足"),
            ("activeHeroHasEffect", "自己效果"),
            ("activeHeroMissingEffect", "自己缺少/不足"),
            ("anyAllyHasEffect", "友方效果"),
            ("anyAllyMissingEffect", "友方缺少/不足"),
        ):
            selector = when.get(key)
            if isinstance(selector, dict):
                text = effect_label(selector.get("kind", selector.get("effectTypeId", "")))
                if "turnsAtLeast" in selector:
                    text += f"剩余≥{selector['turnsAtLeast']}"
                if "turnsAtMost" in selector:
                    text += f"剩余≤{selector['turnsAtMost']}"
                conditions.append(f"{label}:{text}")
        for key, label in (
            ("allyHasEffect", "指定队友效果"),
            ("allyMissingEffect", "指定队友缺少/不足"),
        ):
            requested = when.get(key)
            selector = requested.get("effect") if isinstance(requested, dict) else None
            if isinstance(requested, dict) and isinstance(selector, dict):
                hero_id = requested.get("heroTypeId")
                hero_name = self.catalog.get(hero_id, {"name": "未识别队友"})["name"]
                text = effect_label(selector.get("kind", selector.get("effectTypeId", "")))
                if "turnsAtLeast" in selector:
                    text += f"剩余≥{selector['turnsAtLeast']}"
                if "turnsAtMost" in selector:
                    text += f"剩余≤{selector['turnsAtMost']}"
                conditions.append(f"{label}:{hero_name}/{text}")
        for key, label in (
            ("allyEffectSlotsAtLeast", "指定队友效果格≥"),
            ("allyEffectSlotsAtMost", "指定队友效果格≤"),
        ):
            requested = when.get(key)
            if isinstance(requested, dict):
                hero_id = requested.get("heroTypeId")
                hero_name = self.catalog.get(hero_id, {"name": "未识别队友"})["name"]
                conditions.append(f"{hero_name}{label}{requested.get('count', '?')}")
        for key, label in (
            ("completedTrialsAll", "已完成试炼"),
            ("incompleteTrialsAll", "未完成试炼"),
            ("activeTrialsAny", "分支当前试炼"),
            ("eligibleTrialsAny", "此刻可推进试炼"),
            ("lockedTrialsAny", "前置锁定试炼"),
            ("impossibleTrialsAny", "不可能试炼"),
        ):
            if when.get(key):
                conditions.append(f"{label}:" + selected_trial_summary(self.trials, list(when[key])))
        if when.get("trialProgressAtLeast"):
            progress_items = []
            for trial_id, progress in when["trialProgressAtLeast"].items():
                summary = selected_trial_summary(self.trials, [int(trial_id)])
                progress_items.append(f"{summary} {float(progress) * 100:g}%")
            conditions.append("试炼进度≥" + "；".join(progress_items))
        return (
            index + 1,
            rule.get("name", "未命名规则"),
            hero_text,
            form_text,
            skill_text,
            target_text,
            "；".join(conditions) or "无",
        )

    def refresh_tree(self) -> None:
        selected = self.tree.selection()
        selected_index = int(selected[0]) if selected else None
        self.tree.delete(*self.tree.get_children())
        for index, rule in enumerate(self.rules):
            when = rule.get("when", {}) if isinstance(rule, dict) else {}
            hero_value = when.get("activeHeroTypeId") if isinstance(when, dict) else None
            hero_ids = hero_value if isinstance(hero_value, list) else [hero_value]
            hero_ids = [value for value in hero_ids if isinstance(value, int)]
            hero_id = hero_ids[0] if len(hero_ids) == 1 else None
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                image=self.icons.hero_photo(
                    hero_id, self.catalog.get(hero_id, {}) if hero_id else {}, "small"
                ),
                values=self.human_rule(index, rule),
            )
        if selected_index is not None and selected_index < len(self.rules):
            self.tree.selection_set(str(selected_index))

    def selected_rule_index(self) -> int | None:
        selected = self.tree.selection()
        return int(selected[0]) if selected else None

    def add_rule(self) -> None:
        RuleDialog(self.root, self.catalog, self.trials, self.icons, None, self.add_rule_result)

    def add_rule_result(self, rule: dict[str, Any]) -> None:
        self.rules.append(rule)
        self.refresh_tree()

    def edit_rule(self) -> None:
        index = self.selected_rule_index()
        if index is None:
            messagebox.showinfo("编辑规则", "请先选择一条规则。", parent=self.root)
            return
        RuleDialog(
            self.root,
            self.catalog,
            self.trials,
            self.icons,
            self.rules[index],
            lambda rule: self.replace_rule(index, rule),
        )

    def replace_rule(self, index: int, rule: dict[str, Any]) -> None:
        self.rules[index] = rule
        self.refresh_tree()
        self.tree.selection_set(str(index))

    def delete_rule(self) -> None:
        index = self.selected_rule_index()
        if index is None:
            return
        if messagebox.askyesno("删除规则", f"确定删除“{self.rules[index].get('name', '未命名规则')}”吗？", parent=self.root):
            del self.rules[index]
            self.refresh_tree()

    def move_rule(self, offset: int) -> None:
        index = self.selected_rule_index()
        if index is None:
            return
        target = index + offset
        if not 0 <= target < len(self.rules):
            return
        self.rules[index], self.rules[target] = self.rules[target], self.rules[index]
        self.refresh_tree()
        self.tree.selection_set(str(target))

    def save_strategy(self, *, quiet: bool = False) -> bool:
        try:
            config = compose_strategy_config(
                rules=self.rules,
                mode="execute",
                mandatory_trials=self.mandatory_trials_var.get(),
                minimum_damage=self.minimum_damage_var.get(),
                minimum_points=self.minimum_points_var.get(),
                max_retries=self.max_regroup_retries_var.get(),
                team_hero_ids=self.team_hero_ids,
            )
        except ValueError as error:
            if not quiet:
                messagebox.showerror("策略设置无效", str(error), parent=self.root)
            return False
        USER_STRATEGY.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not quiet:
            self.append_log(f"策略已保存：{USER_STRATEGY.name}")
        return True

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        pid = self.selected_pid()
        if pid is None:
            messagebox.showerror("无法启动", "没有选择可用的 Raid 账户。", parent=self.root)
            return
        selected_item = self.process_items.get(self.pid_var.get(), {})
        selected_account = selected_item.get("account")
        account_name = (
            selected_account.get("accountName")
            if isinstance(selected_account, dict)
            else None
        )
        user_id = (
            selected_account.get("userId")
            if isinstance(selected_account, dict)
            else None
        )
        if (
            not isinstance(account_name, str)
            or not account_name
            or not isinstance(user_id, int)
            or isinstance(user_id, bool)
            or user_id <= 0
        ):
            messagebox.showerror(
                "无法启动",
                "必须先读取到游戏内用户名和玩家 ID，不能只按 PID 启动。",
                parent=self.root,
            )
            return
        if not self.save_strategy(quiet=False):
            return
        self.stop_requested = False
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.pid_box.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.run_var.set("正在准备代理…")
        self.append_log(f"正在为账户 {account_name} 准备接管。")
        threading.Thread(
            target=self.prepare_and_start,
            args=(pid, account_name, user_id),
            daemon=True,
        ).start()

    def prepare_and_start(
        self,
        pid: int,
        account_name: str,
        user_id: int,
    ) -> None:
        try:
            require_expected_account(pid, account_name, user_id)
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            check = subprocess.run(
                [sys.executable, str(INJECTOR), "--pid", str(pid), "--agent", str(AGENT), "--check-only"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                creationflags=creation_flags,
            )
            check_payload = json.loads(check.stdout) if check.stdout.strip() else {}
            if check.returncode != 0:
                raise RuntimeError(check_payload.get("reason") or check.stderr.strip() or "代理检查失败")
            if check_payload.get("agentLoaded") and (
                not check_payload.get("agentCompatible")
                or not check_payload.get("agentReady")
            ):
                previous_lifecycle: dict[str, Any] = {}
                try:
                    with AgentIpc(pid) as previous_ipc:
                        previous_lifecycle = previous_ipc.lifecycle() or {}
                except (FileNotFoundError, ValueError):
                    pass
                if previous_lifecycle.get("screen") == "result":
                    raise RuntimeError("当前停留在战绩结算画面，不会在此时更新代理")
                self.events.put(("log", "正在只更新所选游戏账户的代理版本…"))
                reload_result = subprocess.run(
                    [
                        sys.executable,
                        str(INJECTOR),
                        "--pid",
                        str(pid),
                        "--agent",
                        str(AGENT),
                        "--reload",
                    ],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=35,
                    creationflags=creation_flags,
                )
                reload_payload = (
                    json.loads(reload_result.stdout)
                    if reload_result.stdout.strip()
                    else {}
                )
                if reload_result.returncode != 0:
                    raise RuntimeError(
                        reload_payload.get("reason")
                        or reload_result.stderr.strip()
                        or "所选账户代理更新失败"
                    )
                screen = previous_lifecycle.get("screen")
                if screen == "battle":
                    context = (previous_lifecycle.get("battle") or {}).get("context")
                    if isinstance(context, int) and context > 0:
                        seeded = seed_battle_context(pid, AGENT, context)
                        if not seeded.get("accepted"):
                            raise RuntimeError("更新后恢复当前奇美拉战斗失败")
                elif screen == "team_selection":
                    context = (previous_lifecycle.get("selection") or {}).get("context")
                    if isinstance(context, int) and context > 0:
                        seeded = seed_selection_context(pid, AGENT, context)
                        if not seeded.get("accepted"):
                            raise RuntimeError("更新后恢复奇美拉队伍界面失败")
                require_expected_account(pid, account_name, user_id)
                check_payload = {
                    "agentLoaded": True,
                    "agentCompatible": True,
                    "agentReady": True,
                }
            if not check_payload.get("agentLoaded"):
                self.events.put(("log", "代理尚未载入，正在载入到所选 PID…"))
                load = subprocess.run(
                    [sys.executable, str(INJECTOR), "--pid", str(pid), "--agent", str(AGENT)],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=35,
                    creationflags=creation_flags,
                )
                load_payload = json.loads(load.stdout) if load.stdout.strip() else {}
                if load.returncode != 0:
                    raise RuntimeError(load_payload.get("reason") or load.stderr.strip() or "代理载入失败")
                require_expected_account(pid, account_name, user_id)
            require_expected_account(pid, account_name, user_id)
            if self.stop_requested:
                self.events.put(("stopped", None))
                return
            arguments = [
                sys.executable,
                str(CONTROLLER),
                "--parent-pid",
                str(os.getpid()),
                "--pid",
                str(pid),
                "--account-name",
                account_name,
                "--account-user-id",
                str(user_id),
                "--config",
                str(USER_STRATEGY),
                "--agent",
                str(AGENT),
                "--bootstrap-current",
                "--execute",
                "--auto-start",
            ]
            self.process = subprocess.Popen(
                arguments,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
            )
            self.events.put(("running", pid))
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.events.put(("log", line.rstrip()))
            code = self.process.wait()
            self.events.put(("exited", code))
        except Exception as error:
            self.events.put(("error", str(error)))

    def stop(self) -> None:
        self.stop_requested = True
        process = self.process
        if process is not None and process.poll() is None:
            pid = self.selected_pid()
            if isinstance(pid, int) and signal_controller_pause(pid):
                self.run_var.set("正在暂停接管…")
                self.stop_button.configure(state="disabled")
                self.append_log("已请求暂停；正在等待控制器清理接管会话。")
                return
            self.append_log("暂停信号发送失败；控制器仍保持运行。")
            self.stop_requested = False
            return
        self.events.put(("stopped", None))

    def set_stopped_ui(self, label: str = "已停止") -> None:
        self.run_var.set(label)
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.pid_box.configure(state="readonly")
        if not self.refresh_in_progress:
            self.refresh_button.configure(state="normal")
        self.process = None

    def poll_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.append_log(str(value))
                elif kind == "running":
                    self.run_var.set(f"正在运行 · PID {value}")
                    self.append_log("控制器已启动。")
                elif kind == "exited":
                    self.append_log(f"控制器已退出，代码 {value}。")
                    if value == 6:
                        self.set_stopped_ui("安全中断")
                    elif value == 7:
                        self.set_stopped_ui("已免费重整")
                    elif value == 8:
                        self.set_stopped_ui("游戏内暂停")
                    elif value == 0 and self.stop_requested:
                        self.set_stopped_ui("已暂停")
                    else:
                        self.set_stopped_ui("已完成" if value == 0 else "异常停止")
                    self.stop_requested = False
                    self.refresh_snapshot()
                elif kind == "error":
                    self.append_log(f"启动失败：{value}")
                    self.set_stopped_ui("启动失败")
                    messagebox.showerror("控制器启动失败", str(value), parent=self.root)
                elif kind == "stopped":
                    self.append_log("控制器已停止。")
                    self.set_stopped_ui()
                elif kind == "processes_refreshed":
                    valid, current_pid = value
                    self.apply_processes(valid, current_pid)
                elif kind == "processes_error":
                    self.refresh_in_progress = False
                    self.refresh_button.configure(state="normal")
                    self.state_var.set("账户读取失败")
                    self.append_log(f"账户刷新失败：{value}")
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            if not messagebox.askyesno("退出", "控制器仍在运行。停止并退出吗？", parent=self.root):
                return
            self.stop()
        self.root.destroy()


def self_test() -> int:
    startup_catalog = ensure_ui_catalog_cache()
    assert len(startup_catalog.get("difficulties", [])) == 6
    assert all(
        len(difficulty.get("trials", [])) == 27
        for difficulty in startup_catalog["difficulties"]
    )
    config = read_strategy(DEFAULT_STRATEGY)
    assert config["rules"]
    template = strategy_template()
    template["rules"] = config["rules"]
    encoded = json.dumps(template, ensure_ascii=False)
    assert "奇美拉" in encoded
    assert template["objectives"]["onMandatoryTrialImpossible"] == (
        "free_regroup_and_retry_manual"
    )
    assert template["objectives"]["maxRegroupRetries"] == 10
    assert account_matches(
        {"pid": 123, "accountName": "gafee", "userId": 95815853},
        pid=123,
        account_name="gafee",
        user_id=95815853,
    )
    assert not account_matches(
        {"pid": 123, "accountName": "other", "userId": 95815853},
        pid=123,
        account_name="gafee",
        user_id=95815853,
    )
    assert split_positive_ids("8000501，8000502", "试炼") == [8000501, 8000502]
    assert ChimeraToolApp.translated_trial_id(8000506, 4) == 8000406
    assert parse_trial_progress("8000501:50%, 8000502:0.8") == {
        "8000501": 0.5,
        "8000502": 0.8,
    }
    trial_state = {
        "allianceChimeraDifficultyId": 5,
        "chimeraStageId": 1302,
        "rotationIdentity": {"rewardRotationFingerprint": "fnv1a64:rewards"},
        "trialCatalog": {
            "available": True,
            "difficulties": [
                {"difficultyId": 4, "difficulty": "Brutal", "trials": []},
                {
                    "difficultyId": 5,
                    "difficulty": "Nightmare",
                    "stageIds": [1301, 1302, 1303, 1304],
                    "trials": [
                        {
                            "id": 8000501,
                            "reward": {
                                "entries": [
                                    {
                                        "resourceType": "RelicCraftMaterial_Chimera_Rare",
                                        "minCount": 7,
                                        "maxCount": 7,
                                        "probability": 100,
                                    }
                                ]
                            },
                        }
                    ],
                },
            ],
        },
    }
    trials, difficulty_name, fingerprint = current_trial_catalog(trial_state)
    assert [trial["id"] for trial in trials] == [8000501]
    assert difficulty_name == "Nightmare"
    assert fingerprint == "fnv1a64:rewards"
    assert reward_summary(trials[0]) == "稀有遗物锻造材料 ×7 100%"
    assert readable_game_text("在<color=#fff>奇美拉</color>上施加效果") == "在奇美拉上施加效果"
    trial_name = trial_display_name(
        {
            "id": 8000501,
            "form": "Ram",
            "part": "Wing",
            "difficulty": "Easy",
            "description": "在奇美拉受到恐惧时造成伤害。",
        }
    )
    assert trial_name == "在奇美拉受到恐惧时造成伤害。"
    assert all(value not in trial_name for value in ("8000501", "公羊", "翅膀", "简单"))
    assert canonical_effect_token("DecreaseDefence60") == "151"
    assert effect_label("150") == "降低防御 30%"
    assert effect_label("151") == "降低防御 60%"
    assert effect_label("StatusReduceDefence") == "降低防御（旧策略：未区分强度）"
    team_selection_catalog = dict(trial_state)
    team_selection_catalog.pop("allianceChimeraDifficultyId")
    stage_trials, stage_difficulty, _ = current_trial_catalog(team_selection_catalog)
    assert [trial["id"] for trial in stage_trials] == [8000501]
    assert stage_difficulty == "Nightmare"
    assert RuleDialog.effect_selector("Shield", "2", "", "护盾") == {
        "kind": "Shield",
        "turnsAtLeast": 2,
    }
    assert RuleDialog.effect_selector("151", "1", "", "降低防御") == {
        "effectTypeId": 151,
        "turnsAtLeast": 1,
    }
    assert lifecycle_team_ids(
        {"screen": "battle", "battle": {"heroIds": [11, 12, 13, 14, 15]}}
    ) == [11, 12, 13, 14, 15]
    assert not lifecycle_team_ids(
        {"screen": "battle", "battle": {"heroIds": [11, 12, 13, 14]}}
    )
    advanced = strategy_template()
    advanced["rules"] = [
        {
            "name": "advanced-ui-roundtrip",
            "when": {
                "form": ["Ultimate", "Ram"],
                "activeHeroTypeId": 4716,
                "chimeraTurnAtLeast": 5,
                "chimeraTurnAtMost": 20,
                "nextForm": "Lion",
                "turnsUntilFormChangeAtMost": 2,
                "currentDamageAtLeast": 100000,
                "bossEffectSlotsAtMost": 9,
                "bossHasEffect": {
                    "kind": "DecreaseDefence60",
                    "turnsAtLeast": 2,
                },
                "completedTrialsAll": [8000501],
                "activeTrialsAny": [8000502],
                "eligibleTrialsAny": [8000502],
                "lockedTrialsAny": [8000503],
                "trialProgressAtLeast": {"8000502": 0.5},
            },
            "action": {"type": "cast", "skillSlot": 2, "target": {"type": "boss"}},
        }
    ]
    validate_strategy_config(advanced)
    roundtrip = compose_strategy_config(
        rules=advanced["rules"],
        mode="execute",
        mandatory_trials="8000501, 8000502",
        minimum_damage="1000000",
        minimum_points="200",
        max_retries="8",
        team_hero_ids=[11, 12, 13, 14, 15],
    )
    decoded = json.loads(json.dumps(roundtrip, ensure_ascii=False))
    validate_strategy_config(decoded)
    assert decoded["team"]["heroIds"] == [11, 12, 13, 14, 15]
    assert decoded["objectives"]["mandatoryTrialIds"] == [8000501, 8000502]
    print("chimera-gui-selftest-ok")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    mutex = NamedMutex(r"Local\RaidChimeraTool.Singleton")
    try:
        mutex.acquire()
    except RuntimeError:
        notice = tk.Tk()
        notice.withdraw()
        messagebox.showinfo("奇美拉工具", "工具已经在运行，请使用现有窗口。", parent=notice)
        notice.destroy()
        return 4
    try:
        if ttk_bootstrap is not None:
            root = ttk_bootstrap.Window(themename="superhero", high_dpi=True)
        else:
            root = tk.Tk()
            style = ttk.Style(root)
            if "vista" in style.theme_names():
                style.theme_use("vista")
        ChimeraToolApp(root)
        root.mainloop()
        return 0
    finally:
        mutex.close()


if __name__ == "__main__":
    raise SystemExit(main())
