#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import importlib
import io
import os
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from raid_processes import is_supported_raid_executable, raid_processes


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
RESOURCE_ROOT = Path(os.environ.get("CHIMERA_RESOURCE_ROOT", PROJECT_ROOT)).resolve()
CACHE_DIR = PROJECT_ROOT / "cache" / "chimera-icons"
ASSET_CACHE_DIR = PROJECT_ROOT / "cache" / "chimera-visual-assets"
GAME_ASSET_MANIFEST = ASSET_CACHE_DIR / "game-assets.json"
_GAME_ASSET_LOCK = threading.RLock()
_GAME_ASSET_MANIFEST_MEMORY: dict[str, Any] | None = None
_GAME_AVATAR_PATHS_MEMORY: dict[str, Path] = {}
_GAME_SKILL_PATHS_MEMORY: dict[str, Path] = {}
_GAME_REWARD_PATHS_MEMORY: dict[str, Path] = {}
_GAME_AVATAR_CACHE_CHECKED = False
_GAME_SKILL_CACHE_CHECKED: set[int] = set()
_GAME_REWARD_CACHE_CHECKED = False
_GAME_BUILD_MEMORY: Path | None = None
tk: Any = None


def game_build_directory() -> Path | None:
    """Resolve Plarium's current RAID build without assuming a drive letter."""
    global _GAME_BUILD_MEMORY
    if _GAME_BUILD_MEMORY is not None and _GAME_BUILD_MEMORY.is_dir():
        return _GAME_BUILD_MEMORY
    candidates: list[Path] = []
    configured = os.environ.get("CHIMERA_GAME_BUILD")
    if configured:
        candidates.append(Path(configured))
    try:
        candidates.extend(
            Path(str(process["path"])).resolve().parent
            for process in raid_processes().values()
            if isinstance(process.get("path"), str)
            and is_supported_raid_executable(str(process["path"]))
        )
    except OSError:
        pass
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data)
            / "PlariumPlay"
            / "StandAloneApps"
            / "raid-shadow-legends"
            / "build"
        )
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except (OSError, ValueError):
            continue
        if (resolved / "Raid.exe").is_file():
            _GAME_BUILD_MEMORY = resolved
            return resolved
    return None


def game_resource_directory() -> Path | None:
    build = game_build_directory()
    return build.parent / "resources" if build is not None else None


@lru_cache(maxsize=1)
def discover_game_hydra_heads() -> tuple[tuple[int, str], ...]:
    """Read every installed Hydra head identity without entering the game process."""
    resource_dir = game_resource_directory()
    if resource_dir is None or not resource_dir.is_dir():
        return ()
    pattern = re.compile(
        r"^Hydra_(?P<kind>[A-Za-z][A-Za-z0-9]*)_"
        r"(?P<type_id>\d+)_(?:\d+\.){2}\d+$",
        re.IGNORECASE,
    )
    discovered: dict[int, str] = {}
    try:
        directories = resource_dir.iterdir()
    except OSError:
        return ()
    for directory in directories:
        if not directory.is_dir():
            continue
        match = pattern.fullmatch(directory.name)
        if match is None:
            continue
        type_id = int(match.group("type_id"))
        if type_id > 0:
            discovered[type_id] = match.group("kind")
    return tuple(sorted(discovered.items()))


# Each visible choice stores one exact effect type.  The numeric value remains an
# implementation detail, but preserving it lets the strategy distinguish weak and
# strong variants that share the same runtime effect kind.
EFFECT_OPTIONS: tuple[dict[str, str], ...] = (
    {"token": "10", "icon": "Stun", "label": "眩晕", "group": "减益"},
    {"token": "20", "icon": "Freeze", "label": "冰冻", "group": "减益"},
    {"token": "30", "icon": "Sleep", "label": "睡眠", "group": "减益"},
    {"token": "40", "icon": "Provoke", "label": "挑衅", "group": "减益"},
    {"token": "70", "icon": "BlockHeal", "label": "封锁治疗 100%", "group": "减益"},
    {"token": "71", "icon": "BlockHeal2", "label": "封锁治疗 50%", "group": "减益"},
    {"token": "80", "icon": "ContinuousDamage", "label": "中毒 5%", "group": "减益"},
    {"token": "81", "icon": "ContinuousDamage2", "label": "中毒 2.5%", "group": "减益"},
    {"token": "110", "icon": "BlockBuffs", "label": "封锁增益", "group": "减益"},
    {"token": "130", "icon": "StatusReduceAttack", "label": "降低攻击 25%", "group": "减益"},
    {"token": "131", "icon": "StatusReduceAttack2", "label": "降低攻击 50%", "group": "减益"},
    {"token": "150", "icon": "StatusReduceDefence", "label": "降低防御 30%", "group": "减益"},
    {"token": "151", "icon": "StatusReduceDefence2", "label": "降低防御 60%", "group": "减益"},
    {"token": "170", "icon": "StatusReduceSpeed", "label": "降低速度 15%", "group": "减益"},
    {"token": "171", "icon": "StatusReduceSpeed2", "label": "降低速度 30%", "group": "减益"},
    {"token": "230", "icon": "StatusReduceAccuracy", "label": "降低精准 25%", "group": "减益"},
    {"token": "231", "icon": "StatusReduceAccuracy2", "label": "降低精准 50%", "group": "减益"},
    {"token": "250", "icon": "StatusReduceCriticalChance", "label": "降低暴击率 15%", "group": "减益"},
    {"token": "251", "icon": "StatusReduceCriticalChance2", "label": "降低暴击率 30%", "group": "减益"},
    {"token": "270", "icon": "StatusReduceCriticalDamage", "label": "降低暴击伤害 15%", "group": "减益"},
    {"token": "271", "icon": "StatusReduceCriticalDamage2", "label": "降低暴击伤害 25%", "group": "减益"},
    {"token": "290", "icon": "BlockActiveSkills", "label": "封锁主动技能", "group": "减益"},
    {"token": "350", "icon": "IncreaseDamageTaken", "label": "虚弱 25%", "group": "减益"},
    {"token": "351", "icon": "IncreaseDamageTaken2", "label": "虚弱 15%", "group": "减益"},
    {"token": "360", "icon": "BlockRevive", "label": "封锁复活", "group": "减益"},
    {"token": "470", "icon": "FireMark", "label": "生命值燃烧", "group": "减益"},
    {"token": "490", "icon": "Fear", "label": "恐惧", "group": "减益"},
    {"token": "491", "icon": "Fear2", "label": "真实恐惧", "group": "减益"},
    {"token": "500", "icon": "IncreasePoisoning", "label": "中毒敏感 25%", "group": "减益"},
    {"token": "501", "icon": "IncreasePoisoning2", "label": "中毒敏感 50%", "group": "减益"},
    {"token": "720", "icon": "StatusReduceResistance", "label": "降低抗性 25%", "group": "减益"},
    {"token": "721", "icon": "StatusReduceResistance2", "label": "降低抗性 50%", "group": "减益"},
    {"token": "740", "icon": "ElectricMark", "label": "重击（Smite）", "group": "减益"},
    {"token": "770", "icon": "Polymorph", "label": "变羊", "group": "减益"},
    {"token": "100", "icon": "BlockDebuff", "label": "封锁减益", "group": "增益"},
    {"token": "50", "icon": "StatusCounterattack", "label": "反击", "group": "增益"},
    {"token": "60", "icon": "BlockDamage", "label": "伤害免疫", "group": "增益"},
    {"token": "90", "icon": "ContinuousHeal", "label": "持续治疗 7.5%", "group": "增益"},
    {"token": "91", "icon": "ContinuousHeal2", "label": "持续治疗 15%", "group": "增益"},
    {"token": "120", "icon": "StatusIncreaseAttack", "label": "增加攻击 25%", "group": "增益"},
    {"token": "121", "icon": "StatusIncreaseAttack2", "label": "增加攻击 50%", "group": "增益"},
    {"token": "140", "icon": "StatusIncreaseDefence", "label": "增加防御 30%", "group": "增益"},
    {"token": "141", "icon": "StatusIncreaseDefence2", "label": "增加防御 60%", "group": "增益"},
    {"token": "160", "icon": "StatusIncreaseSpeed", "label": "增加速度 15%", "group": "增益"},
    {"token": "161", "icon": "StatusIncreaseSpeed2", "label": "增加速度 30%", "group": "增益"},
    {"token": "220", "icon": "StatusIncreaseAccuracy", "label": "增加精准 25%", "group": "增益"},
    {"token": "221", "icon": "StatusIncreaseAccuracy2", "label": "增加精准 50%", "group": "增益"},
    {"token": "240", "icon": "StatusIncreaseCriticalChance", "label": "增加暴击率 15%", "group": "增益"},
    {"token": "241", "icon": "StatusIncreaseCriticalChance2", "label": "增加暴击率 30%", "group": "增益"},
    {"token": "260", "icon": "StatusIncreaseCriticalDamage", "label": "增加暴击伤害 15%", "group": "增益"},
    {"token": "261", "icon": "StatusIncreaseCriticalDamage2", "label": "增加暴击伤害 30%", "group": "增益"},
    {"token": "280", "icon": "Shield", "label": "护盾", "group": "增益"},
    {"token": "300", "icon": "ReviveOnDeath", "label": "死亡后复活", "group": "增益"},
    {"token": "310", "icon": "ShareDamage", "label": "分担伤害 50%", "group": "增益"},
    {"token": "311", "icon": "ShareDamage2", "label": "分担伤害 25%", "group": "增益"},
    {"token": "320", "icon": "Unkillable", "label": "不死", "group": "增益"},
    {"token": "370", "icon": "Shield2", "label": "神器套装护盾", "group": "增益"},
    {"token": "410", "icon": "ReflectDamage", "label": "反射伤害 15%", "group": "增益"},
    {"token": "411", "icon": "ReflectDamage2", "label": "反射伤害 30%", "group": "增益"},
    {"token": "460", "icon": "LifeDrainOnDamage", "label": "吸血 10%", "group": "增益"},
    {"token": "480", "icon": "Invisible", "label": "隐身", "group": "增益"},
    {"token": "481", "icon": "Invisible2", "label": "完美隐身", "group": "增益"},
    {"token": "510", "icon": "ReduceDamageTaken", "label": "减少承受伤害 15%", "group": "增益"},
    {"token": "511", "icon": "ReduceDamageTaken2", "label": "减少承受伤害 25%", "group": "增益"},
    {"token": "620", "icon": "StoneSkin", "label": "石肤", "group": "增益"},
    {"token": "640", "icon": "MirrorDamage", "label": "伤害转移", "group": "增益"},
    {"token": "710", "icon": "StatusIncreaseResistance", "label": "增加抗性 25%", "group": "增益"},
    {"token": "711", "icon": "StatusIncreaseResistance2", "label": "增加抗性 50%", "group": "增益"},
    {"token": "840", "icon": "Negator", "label": "拦截 / 否定效果", "group": "特殊"},
    {"token": "860", "icon": "HuntersMark", "label": "猎人印记", "group": "特殊"},
    {"token": "870", "icon": "Inspiration", "label": "激励", "group": "特殊"},
    {"token": "910", "icon": "Duel", "label": "对决目标", "group": "奇美拉"},
    {"token": "920", "icon": "Duel", "label": "对决发起者", "group": "奇美拉"},
    {"token": "930", "icon": "Necrosis", "label": "死灵化来源", "group": "奇美拉"},
    {"token": "940", "icon": "Necrosis", "label": "死灵化", "group": "奇美拉"},
)

EFFECT_BY_TOKEN = {item["token"]: item for item in EFFECT_OPTIONS}

# Runtime names come from SharedModel.Battle.Effects.StatusEffectTypeId.  The
# game owns the numeric IDs; these labels only make the native names friendlier.
RUNTIME_EFFECT_LABELS: dict[str, str] = {
    "AoEContinuousDamage": "范围持续伤害",
    "BlockPassiveSkills": "封锁被动技能",
    "BloodRage": "血怒",
    "BoneShield": "骨盾",
    "BoneShield20": "骨盾 20%",
    "BoneShield30": "骨盾 30%",
    "Brutality": "残暴",
    "Brutality2": "残暴（强化）",
    "Brutality075": "残暴 7.5%",
    "Brutality15": "残暴 15%",
    "Chewing": "咀嚼",
    "Chrono": "时序",
    "Cocoon": "茧",
    "CrabShell": "蟹壳",
    "CritShield25": "暴击护盾 25%",
    "CritShield50": "暴击护盾 50%",
    "CritShield75": "暴击护盾 75%",
    "CritShield100": "暴击护盾 100%",
    "DamageCounter": "伤害计数",
    "DelayedDamage": "延迟伤害",
    "Digestion": "消化",
    "Devoured": "被吞噬",
    "ElectricMark": "电击标记",
    "Eclipse": "蚀",
    "Enfeeble": "衰弱",
    "Enrage": "狂怒",
    "Ensnare": "诱捕",
    "Ensnare2": "诱捕（强化）",
    "Ensnare50": "诱捕 50%",
    "Ensnare100": "诱捕 100%",
    "Entangle": "缠绕",
    "Fatigue": "疲劳",
    "Fortify": "强化",
    "Fortify2": "强化（强）",
    "Fortify15": "强化 15%",
    "Fortify25": "强化 25%",
    "GoldenArmor": "黄金护甲",
    "Grabbed": "被抓取",
    "GreaterSeal": "强力封印",
    "HitCounterShield": "次数护盾",
    "HungerCounter": "饥饿计数",
    "HydraHitCounter": "六头蛇受击计数",
    "HydraNeckIncreaseDamageTaken": "六头蛇断颈增伤",
    "IncreaseCritResistance": "增加暴击抗性",
    "IncreaseMaxHp": "增加最大生命",
    "IncreaseStamina": "增加耐力",
    "Infest": "寄生",
    "LightOrbs": "光球",
    "LesserSeal": "弱效封印",
    "MagmaShield": "熔岩护盾",
    "Mark": "标记",
    "MarkOfDeath": "死亡印记",
    "MarkOfMadness": "疯狂印记",
    "NewbieDefence": "新手保护",
    "Nullifier": "消除",
    "OnGuard": "警戒",
    "Petrification": "石化",
    "PoisonCloud": "毒云",
    "Rage": "怒气",
    "ReduceStamina": "降低耐力",
    "ReflectiveStoneSkin": "反射石肤",
    "Seal": "封印",
    "Seal2": "封印（强化）",
    "Selection": "选中",
    "SkyWrath": "天怒",
    "SleepCounter": "睡眠计数",
    "SoulCounter": "灵魂计数",
    "StatusBanish": "放逐",
    "SwapHealth": "交换生命",
    "Syphon": "虹吸",
    "Taunt": "嘲讽",
    "Thunder": "雷霆",
    "ThunderStunApplier": "雷霆眩晕",
    "TimeBomb": "定时炸弹",
    "VoidAbyss": "虚空深渊",
}

RUNTIME_DEBUFF_EFFECTS = {
    "AoEContinuousDamage", "BlockPassiveSkills", "Chewing", "DelayedDamage",
    "Digestion", "Devoured", "ElectricMark", "Enfeeble", "Ensnare", "Ensnare2",
    "Ensnare50", "Ensnare100", "Entangle", "Fatigue", "Grabbed", "GreaterSeal",
    "Infest", "LesserSeal", "Mark", "MarkOfDeath", "MarkOfMadness", "Petrification",
    "PoisonCloud", "ReduceStamina", "Seal", "Seal2", "StatusBanish", "Taunt",
    "ThunderStunApplier", "TimeBomb", "VoidAbyss",
}
RUNTIME_BUFF_EFFECTS = {
    "BloodRage", "BoneShield", "Brutality", "Brutality2", "Chrono", "Cocoon",
    "BoneShield20", "BoneShield30", "Brutality075", "Brutality15", "CrabShell",
    "CritShield25", "CritShield50", "CritShield75", "CritShield100", "Enrage",
    "Fortify", "Fortify2", "Fortify15", "Fortify25", "GoldenArmor", "HitCounterShield",
    "IncreaseCritResistance", "IncreaseMaxHp", "IncreaseStamina", "LightOrbs",
    "MagmaShield", "NewbieDefence", "OnGuard", "Rage", "ReflectiveStoneSkin",
    "SkyWrath", "Thunder",
}

RUNTIME_EFFECT_ICONS: dict[str, str] = {
    "BoneShield20": "BoneShield",
    "BoneShield30": "BoneShield",
    "Brutality075": "Brutality",
    "Brutality15": "Brutality2",
    "CrabShell": "Shield",
    "CritShield25": "Shield",
    "CritShield50": "Shield",
    "CritShield75": "Shield2",
    "CritShield100": "Shield2",
    "Devoured": "Digestion",
    "Ensnare50": "Ensnare",
    "Ensnare100": "Ensnare2",
    "Fortify15": "Fortify",
    "Fortify25": "Fortify2",
    "Grabbed": "Entangle",
    "GreaterSeal": "Seal2",
    "HitCounterShield": "Shield",
    "LesserSeal": "Seal",
    "ReflectiveStoneSkin": "StoneSkin",
    "SkyWrath": "Thunder",
    "SleepCounter": "Sleep",
    "StatusBanish": "Selection",
    "ThunderStunApplier": "Thunder",
    "VoidAbyss": "Eclipse",
}


@lru_cache(maxsize=1)
def native_effect_icon_names() -> frozenset[str]:
    try:
        payload = json.loads((CACHE_DIR / "manifest.json").read_text(encoding="utf-8"))
        icons = payload.get("icons", {})
        if isinstance(icons, dict):
            return frozenset(str(name) for name in icons)
    except (OSError, ValueError, TypeError):
        pass
    return frozenset()


def _readable_effect_name(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")


def runtime_effect_options(status_effects: Any) -> list[dict[str, str]]:
    """Merge the complete live StatusEffectTypeId enum with curated labels."""
    options = [dict(option) for option in EFFECT_OPTIONS]
    seen = {option["token"] for option in options}
    icons = native_effect_icon_names()
    if not isinstance(status_effects, list):
        return options
    additions: list[dict[str, str]] = []
    for raw in status_effects:
        if not isinstance(raw, dict):
            continue
        effect_id = raw.get("id")
        name = raw.get("name")
        if not isinstance(effect_id, int) or isinstance(effect_id, bool) or effect_id <= 0:
            continue
        if not isinstance(name, str) or not name or str(effect_id) in seen:
            continue
        preferred_icon = RUNTIME_EFFECT_ICONS.get(name, name)
        icon = preferred_icon if preferred_icon in icons else "Status_Effect_Temp"
        group = (
            "减益" if name in RUNTIME_DEBUFF_EFFECTS
            else "增益" if name in RUNTIME_BUFF_EFFECTS
            else "特殊"
        )
        additions.append({
            "token": str(effect_id),
            "icon": icon,
            "label": RUNTIME_EFFECT_LABELS.get(name, _readable_effect_name(name)),
            "group": group,
        })
        seen.add(str(effect_id))
    additions.sort(key=lambda option: (option["group"], option["label"], int(option["token"])))
    return options + additions

EFFECT_TOKEN_ALIASES = {
    "Fear1": "490", "Fear2": "491", "Invisible1": "480", "Invisible2": "481",
    "IncreaseAttack25": "120", "IncreaseAttack50": "121",
    "DecreaseAttack25": "130", "DecreaseAttack50": "131",
    "IncreaseDefence30": "140", "IncreaseDefence60": "141",
    "DecreaseDefence30": "150", "DecreaseDefence60": "151",
    "IncreaseSpeed15": "160", "IncreaseSpeed30": "161",
    "DecreaseSpeed15": "170", "DecreaseSpeed30": "171",
    "IncreaseAccuracy25": "220", "IncreaseAccuracy50": "221",
    "DecreaseAccuracy25": "230", "DecreaseAccuracy50": "231",
    "IncreaseCriticalChance15": "240", "IncreaseCriticalChance30": "241",
    "DecreaseCriticalChance15": "250", "DecreaseCriticalChance30": "251",
    "IncreaseCriticalDamage15": "260", "IncreaseCriticalDamage30": "261",
    "DecreaseCriticalDamage15p": "270", "DecreaseCriticalDamage25p": "271",
    "ContinuousDamage5p": "80", "ContinuousDamage025p": "81",
    "ContinuousHeal075p": "90", "ContinuousHeal15p": "91",
    "IncreaseDamageTaken25": "350", "IncreaseDamageTaken15": "351",
    "IncreasePoisoning25": "500", "IncreasePoisoning50": "501",
    "ReduceDamageTaken15": "510", "ReduceDamageTaken25": "511",
    "IncreaseResistance25": "710", "IncreaseResistance50": "711",
    "DecreaseResistance25": "720", "DecreaseResistance50": "721",
    "Counterattack": "50", "SimpleStoneSkin": "620", "Burn": "470",
    "FireMark": "740", "DuelTargetMark": "910", "DuelProducerMark": "920",
    "NecrosisSource": "930", "Necrosis": "940",
    # Earlier GUI versions saved these friendly aliases without a strength.
    "IncreaseAttack": "StatusIncreaseAttack",
    "IncreaseDefense": "StatusIncreaseDefence",
    "IncreaseDefence": "StatusIncreaseDefence",
    "IncreaseSpeed": "StatusIncreaseSpeed",
    "IncreaseAccuracy": "StatusIncreaseAccuracy",
    "IncreaseResistance": "StatusIncreaseResistance",
    "IncreaseCriticalChance": "StatusIncreaseCriticalChance",
    "IncreaseCriticalDamage": "StatusIncreaseCriticalDamage",
    "DecreaseAttack": "StatusReduceAttack",
    "DecreaseDefense": "StatusReduceDefence",
    "DecreaseDefence": "StatusReduceDefence",
    "DecreaseSpeed": "StatusReduceSpeed",
    "DecreaseAccuracy": "StatusReduceAccuracy",
    "DecreaseResistance": "StatusReduceResistance",
    "DecreaseCriticalChance": "StatusReduceCriticalChance",
    "DecreaseCriticalDamage": "StatusReduceCriticalDamage",
}

COMPAT_EFFECT_LABELS = {
    "Fear": "恐惧（旧策略：未区分恐惧类型）",
    "Invisible": "隐身（旧策略：未区分类型）",
    "StatusIncreaseAttack": "增加攻击（旧策略：未区分强度）",
    "StatusIncreaseDefence": "增加防御（旧策略：未区分强度）",
    "StatusIncreaseSpeed": "增加速度（旧策略：未区分强度）",
    "StatusIncreaseAccuracy": "增加精准（旧策略：未区分强度）",
    "StatusIncreaseResistance": "增加抗性（旧策略：未区分强度）",
    "StatusIncreaseCriticalChance": "增加暴击率（旧策略：未区分强度）",
    "StatusIncreaseCriticalDamage": "增加暴击伤害（旧策略：未区分强度）",
    "StatusReduceAttack": "降低攻击（旧策略：未区分强度）",
    "StatusReduceDefence": "降低防御（旧策略：未区分强度）",
    "StatusReduceSpeed": "降低速度（旧策略：未区分强度）",
    "StatusReduceAccuracy": "降低精准（旧策略：未区分强度）",
    "StatusReduceResistance": "降低抗性（旧策略：未区分强度）",
    "StatusReduceCriticalChance": "降低暴击率（旧策略：未区分强度）",
    "StatusReduceCriticalDamage": "降低暴击伤害（旧策略：未区分强度）",
    "IncreaseDamageTaken": "虚弱（旧策略：未区分强度）",
    "ContinuousDamage": "中毒（旧策略：未区分强度）",
    "ContinuousHeal": "持续治疗（旧策略：未区分强度）",
    "IncreasePoisoning": "中毒敏感（旧策略：未区分强度）",
    "ReduceDamageTaken": "减少承受伤害（旧策略：未区分强度）",
    "ShareDamage": "分担伤害（旧策略：未区分强度）",
    "ReflectDamage": "反射伤害（旧策略：未区分强度）",
    "Shield": "护盾（旧策略：按效果种类匹配）",
    "BlockDamage": "伤害免疫（旧策略：按效果种类匹配）",
    "BlockDebuff": "封锁减益（旧策略：按效果种类匹配）",
    "BlockBuffs": "封锁增益（旧策略：按效果种类匹配）",
    "BlockActiveSkills": "封锁主动技能（旧策略：按效果种类匹配）",
    "StatusCounterattack": "反击（旧策略：按效果种类匹配）",
    "Unkillable": "不死（旧策略：按效果种类匹配）",
    "ReviveOnDeath": "死亡后复活（旧策略：按效果种类匹配）",
    "LifeDrainOnDamage": "吸血（旧策略：按效果种类匹配）",
    "StoneSkin": "石肤（旧策略：按效果种类匹配）",
    "MirrorDamage": "伤害转移（旧策略：按效果种类匹配）",
    "Stun": "眩晕（旧策略：按效果种类匹配）",
    "Freeze": "冰冻（旧策略：按效果种类匹配）",
    "Sleep": "睡眠（旧策略：按效果种类匹配）",
    "Provoke": "挑衅（旧策略：按效果种类匹配）",
    "Burn": "生命值燃烧（旧策略：按效果种类匹配）",
    "Polymorph": "变羊（旧策略：按效果种类匹配）",
}


def canonical_effect_token(token: Any) -> str:
    text = str(token or "").strip()
    if text in EFFECT_BY_TOKEN:
        return text
    if text.isdigit():
        return text
    if text in EFFECT_TOKEN_ALIASES:
        return EFFECT_TOKEN_ALIASES[text]
    return text

# Concrete status type ID to its native Raid icon.  Unlike effectKind, these IDs
# preserve weak/strong variants and therefore are also safe for strategy choices.
EFFECT_TYPE_ICONS: dict[int, str] = {
    10: "Stun", 20: "Freeze", 30: "Sleep", 40: "Provoke",
    50: "StatusCounterattack", 60: "BlockDamage",
    70: "BlockHeal", 71: "BlockHeal2",
    80: "ContinuousDamage", 81: "ContinuousDamage2",
    90: "ContinuousHeal", 91: "ContinuousHeal2",
    100: "BlockDebuff", 110: "BlockBuffs",
    120: "StatusIncreaseAttack", 121: "StatusIncreaseAttack2",
    130: "StatusReduceAttack", 131: "StatusReduceAttack2",
    140: "StatusIncreaseDefence", 141: "StatusIncreaseDefence2",
    150: "StatusReduceDefence", 151: "StatusReduceDefence2",
    160: "StatusIncreaseSpeed", 161: "StatusIncreaseSpeed2",
    170: "StatusReduceSpeed", 171: "StatusReduceSpeed2",
    220: "StatusIncreaseAccuracy", 221: "StatusIncreaseAccuracy2",
    230: "StatusReduceAccuracy", 231: "StatusReduceAccuracy2",
    240: "StatusIncreaseCriticalChance", 241: "StatusIncreaseCriticalChance2",
    250: "StatusReduceCriticalChance", 251: "StatusReduceCriticalChance2",
    260: "StatusIncreaseCriticalDamage", 261: "StatusIncreaseCriticalDamage2",
    270: "StatusReduceCriticalDamage", 271: "StatusReduceCriticalDamage2",
    280: "Shield", 290: "BlockActiveSkills", 300: "ReviveOnDeath",
    310: "ShareDamage", 311: "ShareDamage2", 320: "Unkillable",
    350: "IncreaseDamageTaken", 351: "IncreaseDamageTaken2",
    360: "BlockRevive", 370: "Shield2",
    410: "ReflectDamage", 411: "ReflectDamage2", 460: "LifeDrainOnDamage",
    470: "FireMark", 480: "Invisible", 481: "Invisible2", 490: "Fear",
    491: "Fear2", 500: "IncreasePoisoning", 501: "IncreasePoisoning2",
    510: "ReduceDamageTaken", 511: "ReduceDamageTaken2", 620: "StoneSkin",
    640: "MirrorDamage", 710: "StatusIncreaseResistance",
    711: "StatusIncreaseResistance2", 720: "StatusReduceResistance",
    721: "StatusReduceResistance2", 740: "ElectricMark", 770: "Polymorph",
    840: "Negator", 860: "HuntersMark", 870: "Inspiration",
    910: "Duel", 920: "Duel", 930: "Necrosis", 940: "Necrosis",
}


def effect_label(token: Any) -> str:
    text = canonical_effect_token(token)
    if not text:
        return "未选择"
    option = EFFECT_BY_TOKEN.get(text)
    if option:
        return option["label"]
    compatibility_label = COMPAT_EFFECT_LABELS.get(text)
    if compatibility_label:
        return compatibility_label
    if text.isdigit():
        icon = EFFECT_TYPE_ICONS.get(int(text))
        for candidate in EFFECT_OPTIONS:
            if candidate["icon"] == icon:
                return candidate["label"]
    return "已保存的自定义效果"


@lru_cache(maxsize=1)
def _skill_capabilities() -> dict[int, list[dict[str, Any]]]:
    path = RESOURCE_ROOT / "data" / "chimera-skill-capabilities.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    result: dict[int, list[dict[str, Any]]] = {}
    for raw_key, capabilities in payload.get("skills", {}).items():
        if not str(raw_key).isdigit() or not isinstance(capabilities, list):
            continue
        result[int(raw_key)] = [
            dict(capability)
            for capability in capabilities
            if isinstance(capability, dict)
        ]
    return result


def skill_effect_details(skill_type_id: Any) -> list[dict[str, Any]]:
    """Return learned effects for details; these are never used as skill art."""
    try:
        type_id = int(skill_type_id)
    except (TypeError, ValueError):
        return []
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    scope_labels = {"boss": "奇美拉", "ally": "队友", "self": "自己"}
    for capability in _skill_capabilities().get(type_id, []):
        effect_id = capability.get("effectTypeId")
        token = str(effect_id if isinstance(effect_id, int) else capability.get("effectKind") or "")
        scope = str(capability.get("targetScope") or "unknown")
        key = (scope, token)
        if key in seen:
            continue
        seen.add(key)
        turns = capability.get("lifetime")
        result.append(
            {
                "token": token,
                "label": effect_label(token),
                "scope": scope_labels.get(scope, "目标"),
                "turns": turns if isinstance(turns, int) and turns > 0 else None,
            }
        )
    return result


def skill_effect_summary(skill_type_id: Any, *, limit: int = 5) -> str:
    details = skill_effect_details(skill_type_id)
    if not details:
        return "尚未学习到效果资料"
    values = [
        f"{item['scope']}：{item['label']}"
        + (f"（{item['turns']}回合）" if item.get("turns") else "")
        for item in details[:limit]
    ]
    if len(details) > limit:
        values.append(f"另有 {len(details) - limit} 项")
    return "；".join(values)


def _asset_cache_path(source: str, kind: str, identity: Any) -> Path:
    digest = hashlib.sha256(source.encode("utf-8", errors="ignore")).hexdigest()[:16]
    safe_identity = _safe_name(str(identity or "unknown"))
    return ASSET_CACHE_DIR / f"{kind}-{safe_identity}-{digest}.png"


def cache_visual_asset(
    source: Any,
    kind: str,
    identity: Any,
    *,
    allow_network: bool = False,
) -> Path | None:
    """Cache a game-provided image once. Relative Unity addresses stay unresolved."""
    value = str(source or "").strip()
    if not value:
        return None
    local = Path(value)
    if local.is_file():
        return local
    if value.startswith("//"):
        value = "https:" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return None
    target = _asset_cache_path(value, kind, identity)
    if target.is_file() and target.stat().st_size > 0:
        return target
    if not allow_network:
        return None
    try:
        request = Request(value, headers={"User-Agent": "RaidChimeraTool/1.0"})
        with urlopen(request, timeout=6) as response:
            payload = response.read(8 * 1024 * 1024 + 1)
        if not payload or len(payload) > 8 * 1024 * 1024:
            return None
        ASSET_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image  # type: ignore

            with Image.open(io.BytesIO(payload)) as image:
                image.convert("RGBA").save(target, format="PNG", optimize=True)
        except (ImportError, OSError):
            if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
                return None
            target.write_bytes(payload)
        return target
    except (OSError, ValueError):
        return None


def _read_game_asset_manifest() -> dict[str, Any]:
    global _GAME_ASSET_MANIFEST_MEMORY
    if _GAME_ASSET_MANIFEST_MEMORY is not None:
        return _GAME_ASSET_MANIFEST_MEMORY
    try:
        value = json.loads(GAME_ASSET_MANIFEST.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("version") == 1:
            _GAME_ASSET_MANIFEST_MEMORY = value
            return value
    except (OSError, ValueError, TypeError):
        pass
    _GAME_ASSET_MANIFEST_MEMORY = {
        "version": 1,
        "avatarSources": [],
        "avatars": {},
        "skillSources": {},
        "skills": {},
    }
    return _GAME_ASSET_MANIFEST_MEMORY


def _write_game_asset_manifest(manifest: dict[str, Any]) -> None:
    global _GAME_ASSET_MANIFEST_MEMORY
    ASSET_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = GAME_ASSET_MANIFEST.with_suffix(
        GAME_ASSET_MANIFEST.suffix + f".{os.getpid()}.tmp"
    )
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, GAME_ASSET_MANIFEST)
    _GAME_ASSET_MANIFEST_MEMORY = manifest


def _source_fingerprint(sources: list[Path]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in sources:
        try:
            stat = source.stat()
        except OSError:
            continue
        result.append(
            {
                "path": str(source),
                "size": stat.st_size,
                "mtimeNs": stat.st_mtime_ns,
            }
        )
    return result


def _hero_base_id(hero: Any, fallback: Any = None) -> int | None:
    if isinstance(hero, dict):
        source = str(hero.get("avatar") or hero.get("avatarUrl") or "")
        match = re.search(r"(?:^|/)HeroAvatars/(\d+)$", source, re.IGNORECASE)
        if match:
            return int(match.group(1))
    try:
        value = int(fallback)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _valid_manifest_paths(values: Any) -> dict[str, Path]:
    if not isinstance(values, dict):
        return {}
    return {
        str(key): ASSET_CACHE_DIR / str(filename)
        for key, filename in values.items()
        if isinstance(filename, str)
        and (ASSET_CACHE_DIR / filename).is_file()
        and (ASSET_CACHE_DIR / filename).stat().st_size > 0
    }


def _avatar_bundle_sources() -> list[Path]:
    resource_dir = game_resource_directory()
    if resource_dir is None or not resource_dir.is_dir():
        return []
    sources = [
        path
        for directory in resource_dir.glob("HeroAvatars*")
        if directory.is_dir()
        for path in directory.rglob("__data")
        if path.is_file()
    ]
    # Older bundles are read first, so a newer sprite with the same ID wins.
    return sorted(sources, key=lambda value: (value.stat().st_mtime_ns, str(value)))


def ensure_game_avatar_cache() -> dict[str, Path]:
    """Extract all lightweight hero portraits once at application startup."""
    global _GAME_AVATAR_CACHE_CHECKED, _GAME_AVATAR_PATHS_MEMORY
    with _GAME_ASSET_LOCK:
        if _GAME_AVATAR_CACHE_CHECKED:
            return dict(_GAME_AVATAR_PATHS_MEMORY)
        manifest = _read_game_asset_manifest()
        sources = _avatar_bundle_sources()
        fingerprint = _source_fingerprint(sources)
        cached = _valid_manifest_paths(manifest.get("avatars"))
        avatar_entries = manifest.get("avatars")
        avatar_count = len(avatar_entries) if isinstance(avatar_entries, dict) else 0
        if (
            fingerprint
            and manifest.get("avatarExtractorVersion") == 2
            and manifest.get("avatarSources") == fingerprint
            and cached
            and len(cached) == avatar_count
        ):
            _GAME_AVATAR_PATHS_MEMORY = cached
            _GAME_AVATAR_CACHE_CHECKED = True
            return cached
        if not sources:
            _GAME_AVATAR_PATHS_MEMORY = cached
            _GAME_AVATAR_CACHE_CHECKED = True
            return cached
        try:
            import UnityPy  # type: ignore
        except ImportError:
            return cached

        ASSET_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        avatars = dict(manifest.get("avatars", {})) if isinstance(manifest.get("avatars"), dict) else {}
        for source in sources:
            try:
                environment = UnityPy.load(str(source))
                for obj in environment.objects:
                    if obj.type.name != "Sprite":
                        continue
                    sprite = obj.read()
                    name = str(getattr(sprite, "m_Name", "") or "")
                    identity = re.fullmatch(r"(\d+)(?:-\d+)?", name)
                    if identity is None:
                        continue
                    base_name = identity.group(1)
                    filename = f"native-hero-{base_name}.png"
                    sprite.image.save(ASSET_CACHE_DIR / filename)
                    avatars[base_name] = filename
            except Exception:
                continue
        manifest["avatarExtractorVersion"] = 2
        manifest["avatarSources"] = fingerprint
        manifest["avatars"] = avatars
        _write_game_asset_manifest(manifest)
        _GAME_AVATAR_PATHS_MEMORY = _valid_manifest_paths(avatars)
        _GAME_AVATAR_CACHE_CHECKED = True
        return dict(_GAME_AVATAR_PATHS_MEMORY)


def _version_tuple(name: str) -> tuple[int, ...]:
    match = re.search(r"_(\d+)\.(\d+)\.(\d+)$", name)
    return tuple(map(int, match.groups())) if match else ()


def _hero_skill_bundle_sources(base_id: int) -> list[Path]:
    """Locate only the hero prefab bundles that can contain this hero's sprites."""
    resource_dir = game_resource_directory()
    if resource_dir is None or not resource_dir.is_dir():
        return []
    token = re.compile(rf"(?:id|_){base_id}(?:_|$)", re.IGNORECASE)
    candidates: list[Path] = []
    for directory in resource_dir.iterdir():
        name = directory.name
        if (
            not directory.is_dir()
            or not re.match(r"^\d+_", name)
            or not token.search(name)
            or re.search(r"_(?:LOD|Res|Boss)_", name, re.IGNORECASE)
        ):
            continue
        candidates.extend(path for path in directory.rglob("__data") if path.is_file())
    if not candidates:
        return []

    # Keep the newest downloaded version of every logical prefab/form bundle.
    newest: dict[str, Path] = {}
    for source in candidates:
        directory = source.parents[1]
        logical = re.sub(r"_\d+\.\d+\.\d+$", "", directory.name)
        previous = newest.get(logical)
        if previous is None or _version_tuple(directory.name) > _version_tuple(previous.parents[1].name):
            newest[logical] = source
    return sorted(newest.values(), key=str)


_NORMAL_SKILL_SPRITE = re.compile(r"^(\d+)_s(\d+)$", re.IGNORECASE)
_FORM_SKILL_SPRITE = re.compile(r"^(\d+)_f(\d+)_s(\d+)$", re.IGNORECASE)


def ensure_game_skill_cache(base_id: int) -> dict[str, Path]:
    """Extract one hero's skill sprites, including both mythical forms."""
    global _GAME_SKILL_PATHS_MEMORY
    with _GAME_ASSET_LOCK:
        if base_id in _GAME_SKILL_CACHE_CHECKED:
            return {
                key: path
                for key, path in _GAME_SKILL_PATHS_MEMORY.items()
                if key.startswith(f"{base_id}:")
            }
        manifest = _read_game_asset_manifest()
        sources = _hero_skill_bundle_sources(base_id)
        fingerprint = _source_fingerprint(sources)
        all_cached = _valid_manifest_paths(manifest.get("skills"))
        cached = {
            key: path for key, path in all_cached.items() if key.startswith(f"{base_id}:")
        }
        skill_sources = manifest.get("skillSources")
        if not isinstance(skill_sources, dict):
            skill_sources = {}
        skill_entries = manifest.get("skills")
        expected_count = (
            sum(1 for key in skill_entries if str(key).startswith(f"{base_id}:"))
            if isinstance(skill_entries, dict)
            else 0
        )
        if (
            fingerprint
            and skill_sources.get(str(base_id)) == fingerprint
            and cached
            and len(cached) == expected_count
        ):
            _GAME_SKILL_PATHS_MEMORY.update(cached)
            _GAME_SKILL_CACHE_CHECKED.add(base_id)
            return cached
        if not sources:
            _GAME_SKILL_PATHS_MEMORY.update(cached)
            _GAME_SKILL_CACHE_CHECKED.add(base_id)
            return cached
        try:
            import UnityPy  # type: ignore
        except ImportError:
            return cached

        ASSET_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        skills = dict(manifest.get("skills", {})) if isinstance(manifest.get("skills"), dict) else {}
        for source in sources:
            try:
                environment = UnityPy.load(str(source))
                for obj in environment.objects:
                    if obj.type.name != "Sprite":
                        continue
                    sprite = obj.read()
                    name = str(getattr(sprite, "m_Name", "") or "")
                    form_match = _FORM_SKILL_SPRITE.fullmatch(name)
                    normal_match = _NORMAL_SKILL_SPRITE.fullmatch(name)
                    if form_match and int(form_match.group(1)) == base_id:
                        form = int(form_match.group(2))
                        slot = int(form_match.group(3))
                    elif normal_match and int(normal_match.group(1)) == base_id:
                        form = 0
                        slot = int(normal_match.group(2))
                    else:
                        continue
                    key = f"{base_id}:{form}:{slot}"
                    filename = f"native-skill-{base_id}-f{form}-s{slot}.png"
                    sprite.image.save(ASSET_CACHE_DIR / filename)
                    skills[key] = filename
            except Exception:
                continue
        skill_sources[str(base_id)] = fingerprint
        manifest["skillSources"] = skill_sources
        manifest["skills"] = skills
        _write_game_asset_manifest(manifest)
        resolved = {
            key: path
            for key, path in _valid_manifest_paths(skills).items()
            if key.startswith(f"{base_id}:")
        }
        _GAME_SKILL_PATHS_MEMORY.update(resolved)
        _GAME_SKILL_CACHE_CHECKED.add(base_id)
        return resolved


def _latest_resource_bundle_source(prefix: str) -> Path | None:
    """Return the newest downloaded game-resource bundle for one UI family."""
    resource_dir = game_resource_directory()
    if resource_dir is None or not resource_dir.is_dir():
        return None
    directories = [
        directory
        for directory in resource_dir.glob(f"{prefix}_*")
        if directory.is_dir() and _version_tuple(directory.name)
    ]
    if not directories:
        return None
    latest = max(directories, key=lambda directory: _version_tuple(directory.name))
    return next((path for path in latest.rglob("__data") if path.is_file()), None)


def ensure_game_reward_cache() -> dict[str, Path]:
    """Extract the small set of native icons used by Chimera trial rewards."""
    global _GAME_REWARD_CACHE_CHECKED, _GAME_REWARD_PATHS_MEMORY
    with _GAME_ASSET_LOCK:
        if _GAME_REWARD_CACHE_CHECKED:
            return dict(_GAME_REWARD_PATHS_MEMORY)
        manifest = _read_game_asset_manifest()
        source = _latest_resource_bundle_source("Relics")
        sources = [source] if source is not None else []
        fingerprint = _source_fingerprint(sources)
        cached = _valid_manifest_paths(manifest.get("rewardSprites"))
        entries = manifest.get("rewardSprites")
        entry_count = len(entries) if isinstance(entries, dict) else 0
        if (
            fingerprint
            and manifest.get("rewardSources") == fingerprint
            and cached
            and len(cached) == entry_count
        ):
            _GAME_REWARD_PATHS_MEMORY = cached
            _GAME_REWARD_CACHE_CHECKED = True
            return cached
        if not sources:
            _GAME_REWARD_PATHS_MEMORY = cached
            _GAME_REWARD_CACHE_CHECKED = True
            return cached
        try:
            import UnityPy  # type: ignore
        except ImportError:
            return cached

        wanted = {
            "RelicCraftMaterial_Chimera",
            "Relics_Stones",
            "Relics_Stones_Chest",
            "Chimera",
        }
        ASSET_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        reward_sprites = (
            dict(entries) if isinstance(entries, dict) else {}
        )
        try:
            environment = UnityPy.load(str(source))
            for obj in environment.objects:
                if obj.type.name != "Sprite":
                    continue
                sprite = obj.read()
                name = str(getattr(sprite, "m_Name", "") or "")
                if name not in wanted or name in reward_sprites:
                    continue
                filename = f"native-reward-{_safe_name(name)}.png"
                sprite.image.save(ASSET_CACHE_DIR / filename)
                reward_sprites[name] = filename
        except Exception:
            pass
        manifest["rewardSources"] = fingerprint
        manifest["rewardSprites"] = reward_sprites
        _write_game_asset_manifest(manifest)
        _GAME_REWARD_PATHS_MEMORY = _valid_manifest_paths(reward_sprites)
        _GAME_REWARD_CACHE_CHECKED = True
        return dict(_GAME_REWARD_PATHS_MEMORY)


def game_reward_asset(identity: str) -> Path | None:
    """Resolve a UI reward identity to a cached native game sprite."""
    if identity.startswith("resource-410"):
        sprite_name = "RelicCraftMaterial_Chimera"
    elif identity == "relic-stones":
        sprite_name = "Relics_Stones"
    elif identity.startswith("bmi-1900"):
        sprite_name = "Relics_Stones_Chest"
    else:
        sprite_name = "Chimera"
    return ensure_game_reward_cache().get(sprite_name)


def game_hero_asset(hero_id: Any, hero: Any) -> Path | None:
    base_id = _hero_base_id(hero, hero_id)
    if base_id is None:
        return None
    with _GAME_ASSET_LOCK:
        path = _GAME_AVATAR_PATHS_MEMORY.get(str(base_id))
    if path is not None:
        return path
    return ensure_game_avatar_cache().get(str(base_id))


def game_skill_asset(hero_id: Any, hero: Any, skill: Any) -> Path | None:
    if not isinstance(skill, dict):
        return None
    base_id = _hero_base_id(hero, hero_id)
    if base_id is None:
        return None
    try:
        slot = int(skill.get("slot"))
    except (TypeError, ValueError):
        return None
    try:
        form = int(skill.get("formIndex", 0)) + 1
    except (TypeError, ValueError):
        form = 1
    cached = ensure_game_skill_cache(base_id)
    # Mythical bundles use f1/f2. Ordinary heroes omit the form marker.
    return cached.get(f"{base_id}:{form}:{slot}") or cached.get(f"{base_id}:0:{slot}")


def preload_game_visuals(
    catalog: dict[int, dict[str, Any]], hero_ids: Any = (),
) -> dict[str, int]:
    """Warm portraits globally and skills for the current five-hero team."""
    avatars = ensure_game_avatar_cache()
    rewards = ensure_game_reward_cache()
    skill_count = 0
    visited: set[int] = set()
    for raw_id in hero_ids or ():
        try:
            hero_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        hero = catalog.get(hero_id)
        if hero is None:
            hero = next(
                (
                    candidate
                    for candidate in catalog.values()
                    if isinstance(candidate, dict)
                    and hero_id in candidate.get("runtimeTypeIds", [])
                ),
                None,
            )
        base_id = _hero_base_id(hero, hero_id)
        if base_id is None or base_id in visited:
            continue
        visited.add(base_id)
        skill_count += len(ensure_game_skill_cache(base_id))
    return {
        "avatars": len(avatars),
        "skills": skill_count,
        "heroes": len(visited),
        "rewards": len(rewards),
    }


def _latest_bundle(category: str) -> Path | None:
    game_build = game_build_directory()
    if game_build is None:
        return None
    base = game_build / "Raid_Data" / "StreamingAssets" / "AssetBundles" / category
    candidates = list(base.glob("*/*/WindowsPlayer/*.unity3d")) if base.is_dir() else []
    return max(candidates, key=lambda value: value.stat().st_mtime_ns, default=None)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "icon"


def ensure_icon_cache() -> dict[str, Path]:
    """Extract native UI sprites once. A source fingerprint prevents repeated writes."""
    sources = [value for value in (_latest_bundle("StatusEffectIcons"), _latest_bundle("Challenges")) if value]
    fingerprint = [
        {"path": str(value), "size": value.stat().st_size, "mtimeNs": value.stat().st_mtime_ns}
        for value in sources
    ]
    manifest_path = CACHE_DIR / "manifest.json"
    cached_icons: dict[str, Path] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            cached_icons = {
                key: CACHE_DIR / filename
                for key, filename in manifest.get("icons", {}).items()
                if (CACHE_DIR / filename).is_file()
            }
            if manifest.get("sources") == fingerprint:
                return cached_icons
        except (OSError, ValueError, TypeError):
            pass
    if not sources:
        return {}
    try:
        import UnityPy  # type: ignore
    except ImportError:
        return cached_icons

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    icons: dict[str, str] = {}
    for source in sources:
        try:
            environment = UnityPy.load(str(source))
            for obj in environment.objects:
                if obj.type.name != "Sprite":
                    continue
                sprite = obj.read()
                name = str(getattr(sprite, "m_Name", "") or "")
                if not name or (source.parent.parent.parent.parent.name == "Challenges" and name != "alliance_chimera"):
                    continue
                filename = _safe_name(name) + ".png"
                sprite.image.save(CACHE_DIR / filename)
                icons[name] = filename
        except Exception:
            continue
    if not icons:
        return cached_icons
    manifest_path.write_text(
        json.dumps({"version": 1, "sources": fingerprint, "icons": icons}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {key: CACHE_DIR / filename for key, filename in icons.items()}


class ChimeraIconRepository:
    def __init__(self, master: tk.Misc) -> None:
        # The React desktop shell does not use Tk.  Resolve it only when the
        # retained legacy development UI explicitly constructs this class, so
        # PyInstaller does not bundle Tcl/Tk in the current release.
        global tk
        if tk is None:
            tk = importlib.import_module("tkinter")
        self.master = master
        self.paths = ensure_icon_cache()
        self._photos: dict[tuple[str, str], tk.PhotoImage] = {}

    @staticmethod
    def _edge(size: str) -> int:
        return {"small": 30, "medium": 46, "large": 64}.get(size, 30)

    def _path_photo(self, path: Path, key: tuple[str, str]) -> tk.PhotoImage | None:
        try:
            photo = tk.PhotoImage(master=self.master, file=str(path))
            edge = self._edge(key[1])
            largest = max(photo.width(), photo.height())
            if largest > edge:
                factor = max(1, (largest + edge - 1) // edge)
                photo = photo.subsample(factor, factor)
            self._photos[key] = photo
            return photo
        except tk.TclError:
            return None

    def _placeholder(self, key: tuple[str, str], kind: str) -> tk.PhotoImage:
        cached = self._photos.get(key)
        if cached is not None:
            return cached
        edge = self._edge(key[1])
        photo = tk.PhotoImage(master=self.master, width=edge, height=edge)
        background = "#263648" if kind == "hero" else "#302a47"
        foreground = "#60a6c8" if kind == "hero" else "#d9b65d"
        photo.put(background, to=(0, 0, edge, edge))
        photo.put(foreground, to=(3, 3, edge - 3, edge - 3))
        photo.put(background, to=(6, 6, edge - 6, edge - 6))
        if kind == "hero":
            radius = max(3, edge // 7)
            center = edge // 2
            photo.put(foreground, to=(center - radius, 7, center + radius, 7 + radius * 2))
            photo.put(foreground, to=(edge // 4, edge // 2, edge - edge // 4, edge - 7))
        else:
            center = edge // 2
            photo.put(foreground, to=(center - 2, 7, center + 2, edge - 7))
            photo.put(foreground, to=(edge // 3, center - 2, edge - edge // 3, center + 2))
        self._photos[key] = photo
        return photo

    def photo(self, name: str | None, size: str = "small") -> tk.PhotoImage:
        key = (str(name or "alliance_chimera"), size)
        cached = self._photos.get(key)
        if cached is not None:
            return cached
        path = self.paths.get(key[0]) or self.paths.get("alliance_chimera")
        if path and path.is_file():
            photo = tk.PhotoImage(master=self.master, file=str(path))
            if size == "small" and max(photo.width(), photo.height()) > 34:
                photo = photo.subsample(2, 2)
        else:
            edge = 26 if size == "small" else 46
            photo = tk.PhotoImage(master=self.master, width=edge, height=edge)
            photo.put("#263648", to=(0, 0, edge, edge))
            photo.put("#d9b65d", to=(3, 3, edge - 3, edge - 3))
            photo.put("#51657a", to=(6, 6, edge - 6, edge - 6))
        self._photos[key] = photo
        return photo

    def hero_photo(
        self, hero_id: int | None, hero: dict[str, Any] | None = None, size: str = "medium"
    ) -> tk.PhotoImage:
        hero = hero if isinstance(hero, dict) else {}
        source = hero.get("avatar") or hero.get("avatarUrl")
        key = (f"hero:{hero_id}:{source}", size)
        cached = self._photos.get(key)
        if cached is not None:
            return cached
        path = cache_visual_asset(source, "hero", hero_id)
        if path:
            photo = self._path_photo(path, key)
            if photo is not None:
                return photo
        return self._placeholder(
            ("placeholder:hero" if source else key[0], size), "hero"
        )

    def skill_photo(
        self,
        hero_id: int | None,
        skill: dict[str, Any] | None,
        size: str = "medium",
    ) -> tk.PhotoImage:
        skill = skill if isinstance(skill, dict) else {}
        source = skill.get("icon") or skill.get("iconUrl")
        identity = skill.get("typeId") or f"{hero_id}-{skill.get('slot', 'unknown')}"
        key = (f"skill:{identity}:{source}", size)
        cached = self._photos.get(key)
        if cached is not None:
            return cached
        path = cache_visual_asset(source, "skill", identity)
        if path:
            photo = self._path_photo(path, key)
            if photo is not None:
                return photo
        return self._placeholder(
            ("placeholder:skill" if source else key[0], size), "skill"
        )

    def prefetch_catalog(self, catalog: dict[int, dict[str, Any]]) -> int:
        """Download uncached game art away from Tk's UI thread."""
        cached = 0
        for hero_id, hero in catalog.items():
            if not isinstance(hero, dict):
                continue
            if cache_visual_asset(
                hero.get("avatar") or hero.get("avatarUrl"),
                "hero",
                hero_id,
                allow_network=True,
            ):
                cached += 1
            for skill in hero.get("skills", []):
                if not isinstance(skill, dict):
                    continue
                identity = skill.get("typeId") or f"{hero_id}-{skill.get('slot', 'unknown')}"
                if cache_visual_asset(
                    skill.get("icon") or skill.get("iconUrl"),
                    "skill",
                    identity,
                    allow_network=True,
                ):
                    cached += 1
        return cached

    def trial_photo(self, trial: dict[str, Any]) -> tk.PhotoImage:
        for effect in trial.get("effects", []):
            if isinstance(effect, dict) and isinstance(effect.get("id"), int):
                icon = EFFECT_TYPE_ICONS.get(effect["id"])
                if icon:
                    return self.photo(icon)
        form_fallback = {"Ram": "Duel", "Lion": "HungerCounter", "Snake": "ContinuousDamage"}
        return self.photo(form_fallback.get(str(trial.get("form")), "alliance_chimera"))

    def effect_photo(self, token: Any, size: str = "small") -> tk.PhotoImage:
        token = canonical_effect_token(token)
        option = EFFECT_BY_TOKEN.get(str(token))
        if option:
            return self.photo(option["icon"], size)
        text = str(token or "")
        return self.photo(EFFECT_TYPE_ICONS.get(int(text)) if text.isdigit() else text, size)
