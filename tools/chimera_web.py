#!/usr/bin/env python3
"""Local React shell for the Chimera controller.

The HTTP server binds to loopback only. State-changing requests also require a
random token that is generated for each launch and never written to disk.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from desktop_lifecycle import WindowStartup


def desktop_log(message: str) -> None:
    """Write optional diagnostics without breaking a windowed executable."""
    stream = sys.stdout
    if stream is None:
        return
    try:
        print(message, file=stream, flush=True)
    except (OSError, ValueError):
        pass


def enable_per_monitor_dpi_awareness() -> None:
    """Prevent Windows from bitmap-scaling the WebView on high-DPI displays."""
    if os.name != "nt":
        return
    try:
        set_context = ctypes.windll.user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_bool
        if set_context(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass
    try:
        set_awareness = ctypes.windll.shcore.SetProcessDpiAwareness
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_long
        if set_awareness(2) in (0, 0x80070005):  # success or already configured
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


enable_per_monitor_dpi_awareness()


def _runtime_project_root() -> Path:
    configured = os.environ.get("CHIMERA_PROJECT_ROOT")
    if configured:
        return Path(configured).resolve()

    if getattr(sys, "frozen", False):
        # A one-file PyInstaller application is extracted into a temporary
        # directory on every launch. User strategies and caches must never be
        # stored there or beside an EXE that may live in a read-only folder.
        # Earlier versions' data is copied in by main() (data_root).
        from data_root import data_root
        root = data_root()
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()

    anchor = Path(__file__).resolve().parent
    for candidate in (anchor, *anchor.parents):
        if (
            (candidate / "tools" / "chimera_controller.py").is_file()
            and (candidate / "config").is_dir()
        ):
            return candidate
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _runtime_project_root()
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)).resolve()
os.environ.setdefault("CHIMERA_PROJECT_ROOT", str(PROJECT_ROOT))
os.environ.setdefault("CHIMERA_RESOURCE_ROOT", str(BUNDLE_ROOT))
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
VENDORED_PYTHON = PROJECT_ROOT / "third_party" / "python"
if VENDORED_PYTHON.is_dir() and str(VENDORED_PYTHON) not in sys.path:
    sys.path.insert(0, str(VENDORED_PYTHON))

from controller_manager import ControllerManager
from strategy_storage import atomic_write_json, write_strategy_store, storage_health, recover_strategy_store, restore_strategy_value, revision
from catalog_stream import static_entity, merge_skill_catalog
from hydra_state import hydra_devouring_head_ids
from hydra_forecast_live import recent_forecasts as recent_hydra_forecasts
from chimera_simulation_service import (BATTLE_FORECAST_ROOT, SimulationService,
                                        list_captures as list_chimera_captures,
                                        recent_simulations as recent_chimera_simulations)
from hydra_simulation_service import (HydraSimulationService,
                                      recent_simulations as recent_hydra_simulations)
from team_setups import (STRATEGY_TEAM_ROOT, TEAM_SIZE, StrategyTeamStore, TeamSetupError,  # noqa: E402
                         check_against_captures,
                         decode_account_bonuses, decode_roster, exported_team, latest_check, preview_team,
                         remember_check, same_team, simulation_team, team_sources)
from inject_probe import (REQUEST_BONUSES_BY_TYPE, REQUEST_HYDRA, REQUEST_ROSTER,  # noqa: E402
                          REQUEST_SKILL_TYPES, REQUEST_TEAM_DATA, request_account_bonuses)
from boss_skills import BossSkillCatalog, enemy_skill_ids, listed_skills  # noqa: E402
from account_stores import AccountStores, StrategyAccount  # noqa: E402
from team_preview import (TeamSnapshotStore, decode_preview, display_preview, portable_snapshot,  # noqa: E402
                          public_preview, sanitize_reference_team)
from agent_ipc import AgentIpc, read_agent_status  # noqa: E402
from boss_modes import (  # noqa: E402
    HYDRA_HEAD_TYPE_IDS,
    HYDRA_RESERVED_HEAD_TYPE_IDS,
    MODE_SPECS,
    STRATEGY_STORE,
    active_strategy_id,
    default_store,
    canonical_hydra_head_type_id,
    delete_mode_strategy,
    hydra_head_is_exposed_neck,
    load_strategy_store,
    mode_spec,
    normalize_hydra_head,
    normalize_mode,
    sanitize_strategy_for_mode,
    select_mode_strategy,
    strategy_profiles_for_mode,
    strategy_for_mode,
    update_mode_strategy,
)
from chimera_catalog_cache import (  # noqa: E402
    cache_live_rotation_catalog,
    ensure_ui_catalog_cache,
    is_unresolved_skill_name,
    load_hero_catalog,
    save_hero_catalog,
    skill_display_name,
)
from chimera_controller import latest_account_state, validate_strategy_config  # noqa: E402
from chimera_runtime import usable_account_state  # noqa: E402
from chimera_runtime import (  # noqa: E402
    identify_account,
    lifecycle_team_ids,
    strategy_template,
)
from chimera_icons import (  # noqa: E402
    cache_visual_asset,
    discover_game_hydra_heads,
    ensure_icon_cache,
    game_hero_asset,
    game_reward_asset,
    game_named_sprite,
    TEAM_ICON_BUNDLES,
    game_skill_asset,
    preload_game_visuals,
    runtime_effect_options,
    skill_effect_summary,
)
from named_mutex import NamedMutex  # noqa: E402
from raid_processes import (  # noqa: E402
    attach_windows,
    is_supported_raid_executable,
    raid_processes,
)


BUNDLED_DIST_DIR = BUNDLE_ROOT / "ui" / "dist"
DIST_DIR = (
    BUNDLED_DIST_DIR
    if (BUNDLED_DIST_DIR / "index.html").is_file()
    else PROJECT_ROOT / "ui" / "dist"
)
APP_NAME = "RSL-Boss-helper"
# The packaged build carries VERSION and the icon next to the interface.
APP_VERSION = next(
    (
        (root / "VERSION").read_text(encoding="utf-8").strip()
        for root in (BUNDLE_ROOT, PROJECT_ROOT)
        if (root / "VERSION").is_file()
    ),
    "",
)
APP_ICON = next(
    (
        root / "branding" / "alliance-boss-strategy-icon-v3.ico"
        for root in (BUNDLE_ROOT, PROJECT_ROOT)
        if (root / "branding" / "alliance-boss-strategy-icon-v3.ico").is_file()
    ),
    BUNDLE_ROOT / "branding" / "alliance-boss-strategy-icon-v3.ico",
)
HERO_CATALOG = PROJECT_ROOT / "cache" / "chimera-hero-catalog.json"
BOSS_SKILLS = PROJECT_ROOT / "cache" / "boss-skills.json"
UI_PREFERENCES = PROJECT_ROOT / "config" / "raid-boss-ui-preferences.user.json"
def snapshot_requested(body: dict[str, Any]) -> bool:
    """An explicit save: snapshot the strategy group's team (capturePreparedTeam: interfaces before 1.1.1)."""
    return body.get("snapshotTeam") is True or body.get("capturePreparedTeam") is True


def team_hero_ids(team: Any) -> list[int]:
    """The account's hero ids a strategy group's team names (empty for an imported, unbound team)."""
    values = team.get("heroInstanceIds") if isinstance(team, dict) else None
    type_ids = team.get("heroTypeIds") if isinstance(team, dict) else None
    if (not isinstance(values, list) or not isinstance(type_ids, list) or len(values) != len(type_ids)
            or not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in values)):
        return []
    return list(values)


EXPECTED_RULE_KEYS = {
    "name",
    "when",
    "action",
    "children",
    "fallback",
    "branches",
}


def hero_catalog_identity(hero_id: int, hero: dict[str, Any]) -> int:
    source = str(hero.get("avatar") or hero.get("avatarUrl") or "")
    tail = source.rsplit("/", 1)[-1].split("-", 1)[0]
    return int(tail) if tail.isdigit() else hero_id


def canonicalize_hero_catalog(
    catalog: dict[int, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Merge live rank/ascension TypeIds into one user-facing hero identity."""
    grouped: dict[int, dict[str, Any]] = {}
    for runtime_id, raw in sorted(catalog.items()):
        if not isinstance(raw, dict):
            continue
        canonical_id = hero_catalog_identity(runtime_id, raw)
        previous = grouped.get(canonical_id, {})
        merged = {**previous, **raw}
        merged["typeId"] = canonical_id
        previous_aliases = previous.get("runtimeTypeIds", [])
        raw_aliases = raw.get("runtimeTypeIds", [])
        aliases = {
            value
            for value in (
                *(previous_aliases if isinstance(previous_aliases, list) else []),
                *(raw_aliases if isinstance(raw_aliases, list) else []),
                canonical_id,
                runtime_id,
            )
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
        }
        merged["runtimeTypeIds"] = sorted(aliases)

        merged["skills"] = merge_skill_catalog(raw.get("skills", []), previous.get("skills", []))
        grouped[canonical_id] = merged
    return grouped


def is_transform_skill(hero: dict[str, Any], skill: dict[str, Any]) -> bool:
    if hero.get("isMetamorph") is not True:
        return False
    try:
        slot = int(skill.get("slot", 0))
    except (TypeError, ValueError):
        return False
    text = f"{skill.get('name', '')} {skill.get('description', '')}".lower()
    markers = ("变身", "变形", "蜕变", "交替形态", "基础形态", "alternate form", "base form")
    return slot >= 4 and any(marker in text for marker in markers)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default


def process_display(item: dict[str, Any]) -> str:
    account = item.get("account")
    pid = int(item["pid"])
    if isinstance(account, dict):
        name = account.get("accountName")
        user_id = account.get("userId")
        if isinstance(name, str) and name:
            return f"{name}（玩家 ID {user_id}）" if isinstance(user_id, int) else name
    return f"未识别账户（进程 {pid}）"


def default_strategy(boss_mode: str = "chimera") -> dict[str, Any]:
    config = strategy_template(boss_mode)
    config["mode"] = "execute"
    return config


def normalized_strategy(
    value: Any, previous: dict[str, Any], boss_mode: str = "chimera"
) -> dict[str, Any]:
    boss_mode = normalize_mode(boss_mode)
    spec = mode_spec(boss_mode)
    if not isinstance(value, dict):
        raise ValueError("策略必须是一个对象")
    rules = value.get("rules")
    if not isinstance(rules, list):
        raise ValueError("策略规则必须是一个数组")
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"第 {index + 1} 条策略规则格式无效")
        unexpected = set(rule) - EXPECTED_RULE_KEYS
        if unexpected:
            # Do not discard controller-supported extension keys; this catches
            # only values that cannot be represented as a rule at all.
            pass

    baseline = default_strategy(boss_mode)
    result = {
        **baseline,
        **previous,
        **value,
        "name": str(value.get("name") or f"{boss_mode}-user-strategy"),
        "mode": "execute",
        "bossMode": boss_mode,
        "scope": {"battleKind": spec["battleKind"]},
        "rules": rules,
    }
    objectives = {
        **baseline["objectives"],
        **(previous.get("objectives") if isinstance(previous.get("objectives"), dict) else {}),
        **(value.get("objectives") if isinstance(value.get("objectives"), dict) else {}),
        "onAllMetAtResult": "hold_for_user",
    }
    if spec["hasTrials"]:
        objectives["onMandatoryTrialImpossible"] = "free_regroup_and_retry_manual"
    else:
        objectives.pop("mandatoryTrialIds", None)
        objectives.pop("onMandatoryTrialImpossible", None)
    # Replaced by the opening whole-battle forecast in 1.0.6.
    objectives.pop("earlyRetryConditions", None)
    objectives.pop("minimumCompetitionPoints", None)
    for key in ("minimumDamage", "maxRegroupRetries"):
        raw = objectives.get(key, 0)
        if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw < 0:
            raise ValueError(f"{key} 必须是非负数")
        objectives[key] = int(raw)
    if spec["hasTrials"]:
        trial_ids = objectives.get("mandatoryTrialIds", [])
        if not isinstance(trial_ids, list) or any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in trial_ids
        ):
            raise ValueError("必做试炼设置无效")
        objectives["mandatoryTrialIds"] = list(dict.fromkeys(trial_ids))
        # On unless the player turned it off: strategies saved before 1.0.6
        # have no such key.
        objectives["battleForecast"] = objectives.get("battleForecast", True) is not False
    else:
        devour_conditions = objectives.get("devourOrderRetryConditions", [])
        if not isinstance(devour_conditions, list):
            raise ValueError("六头蛇吞噬顺序重整条件设置无效")
        normalized_devour_conditions: list[dict[str, Any]] = []
        for condition in devour_conditions[:20]:
            if not isinstance(condition, dict):
                continue
            never_marked = condition.get("relation") == "neverMarked"
            mark_index = condition.get("markIndex")
            mark_limit = condition.get("markLimit")
            if never_marked:
                if not (
                    isinstance(mark_limit, int)
                    and not isinstance(mark_limit, bool)
                    and 1 <= mark_limit <= 100
                ):
                    mark_limit = None
            elif (
                not isinstance(mark_index, int)
                or isinstance(mark_index, bool)
                or not 1 <= mark_index <= 100
            ):
                continue
            hero_type_ids = condition.get("heroTypeIds", [])
            if not isinstance(hero_type_ids, list):
                continue
            filtered_hero_type_ids = list(dict.fromkeys(
                hero_type_id
                for hero_type_id in hero_type_ids
                if isinstance(hero_type_id, int)
                and not isinstance(hero_type_id, bool)
                and hero_type_id > 0
            ))
            if not filtered_hero_type_ids:
                continue
            if never_marked:
                normalized_devour_conditions.append({
                    "relation": "neverMarked",
                    "heroTypeIds": filtered_hero_type_ids,
                    **({"markLimit": mark_limit} if mark_limit is not None else {}),
                })
                continue
            normalized_devour_conditions.append({
                "markIndex": mark_index,
                "relation": (
                    "isAnyOf"
                    if condition.get("relation") == "isAnyOf"
                    else "isNoneOf"
                ),
                "heroTypeIds": filtered_hero_type_ids,
            })
        objectives["devourOrderRetryConditions"] = normalized_devour_conditions
        objectives["devourOrderForecast"] = objectives.get("devourOrderForecast") is True
    result["objectives"] = objectives
    team = result.get("team")
    if isinstance(team, dict):
        normalized_team = dict(team)
        # Earlier UI builds stored stable hero TypeIds under the ambiguous
        # heroIds key.  Keep the data, but give it an explicit identity field
        # so it is never compared with per-account hero instance IDs again.
        if (
            not isinstance(normalized_team.get("heroTypeIds"), list)
            and isinstance(normalized_team.get("heroIds"), list)
        ):
            normalized_team["heroTypeIds"] = list(normalized_team["heroIds"])
        normalized_team.pop("heroIds", None)
        result["team"] = normalized_team
    result = sanitize_strategy_for_mode(result, boss_mode)
    validate_strategy_config(result, boss_mode=boss_mode)
    return result


class ChimeraService:
    def __init__(self) -> None:
        self.stopping = threading.Event()
        self.lock = threading.RLock()
        self.visual_preload_lock = threading.RLock()
        self.visual_preload_global_started = False
        self.visual_preload_requested_heroes: set[int] = set()
        self.client_lock = threading.RLock()
        self.first_client = threading.Event()
        self.active_clients = 0
        self.controller = ControllerManager()
        self.simulations = SimulationService()
        self.hydra_simulations = HydraSimulationService()
        self.team_snapshots = TeamSnapshotStore()
        # Strategy groups of each game account (config/accounts/<userId>); the account
        # of the current request is kept per thread (see use_strategy_account).
        self.account_stores = AccountStores()
        self._store_context = threading.local()
        self.team_previews: dict[int, dict[str, Any]] = {}
        self.catalog_epoch = 0
        self.catalog_session = secrets.token_hex(8)
        self.processes: dict[int, dict[str, Any]] = {}
        self.last_process_refresh = 0.0
        loaded_hero_catalog = load_hero_catalog({})
        self.hero_catalog = {key: static_entity(value) for key, value in canonicalize_hero_catalog(loaded_hero_catalog).items()}
        if self.hero_catalog != loaded_hero_catalog:
            save_hero_catalog(self.hero_catalog)
        try:
            self.hero_catalog_mtime_ns = HERO_CATALOG.stat().st_mtime_ns
        except OSError:
            self.hero_catalog_mtime_ns = 0
        self.ui_catalog = ensure_ui_catalog_cache()
        self.status_effect_catalog: list[dict[str, Any]] = []
        self.effect_options = runtime_effect_options([])
        installed_hydra_heads = {
            type_id: kind
            for type_id, kind in discover_game_hydra_heads()
            if type_id not in HYDRA_RESERVED_HEAD_TYPE_IDS
        }
        hydra_head_type_ids = sorted(
            set(HYDRA_HEAD_TYPE_IDS) | set(installed_hydra_heads)
        )
        self.boss_skills = BossSkillCatalog(BOSS_SKILLS)
        self.boss_skill_retry_at = 0.0
        self.hydra_head_catalog: dict[int, dict[str, Any]] = {
            type_id: {
                "typeId": type_id,
                "name": (
                    f"蛇头 {type_id} · {installed_hydra_heads[type_id]}"
                    if type_id in installed_hydra_heads
                    else f"蛇头 {type_id}"
                ),
                "avatar": f"HeroAvatars/{type_id}",
                **(
                    {"resourceKind": installed_hydra_heads[type_id]}
                    if type_id in installed_hydra_heads
                    else {}
                ),
            }
            for type_id in hydra_head_type_ids
        }
        self.cached_rotation_keys: set[tuple[str, int]] = set()
        # The cache is only a fallback for snapshots that omit the slot array.
        # Normal preparation updates preserve all slots, including empty ones,
        # so changing a single champion is visible before the team is full.
        self.live_team_cache: dict[tuple[int, str], list[int]] = {}
        self.visual_cache_status = {
            "effects": 0,
            "avatars": 0,
            "skills": 0,
            "heroes": 0,
            "rewards": 0,
        }
        # Decoding every installed portrait can take over a minute on an empty
        # cache. It is useful work, but it must not delay the first window.
        self.start_visual_preload()

    def start_visual_preload(self, hero_ids: Any = ()) -> None:
        if self.stopping.is_set():
            return
        requested: set[int] = set()
        for raw in hero_ids or ():
            if not isinstance(raw, (int, str)) or isinstance(raw, bool):
                continue
            try:
                value = int(raw)
            except ValueError:
                continue
            if value > 0:
                requested.add(value)
        with self.visual_preload_lock:
            if requested:
                pending = requested - self.visual_preload_requested_heroes
                if not pending:
                    return
                self.visual_preload_requested_heroes.update(pending)
            else:
                if self.visual_preload_global_started:
                    return
                self.visual_preload_global_started = True
                pending = set()
        catalog = dict(self.hero_catalog)

        def worker() -> None:
            try:
                status = preload_game_visuals(catalog, sorted(pending), cancelled=self.stopping.is_set)
                if self.stopping.is_set():
                    return
                with self.lock:
                    self.visual_cache_status = status
                    self._catalog_changed()
                    self.effect_options = runtime_effect_options(
                        self.status_effect_catalog
                    )
                if pending:
                    desktop_log(
                        f"[assets] current team: {status['heroes']} heroes, "
                        f"{status['skills']} skill icons ready"
                    )
                else:
                    desktop_log(
                        f"[assets] cached {status['effects']} effect icons and "
                        f"{status['avatars']} hero portraits"
                    )
            except Exception as error:
                desktop_log(f"[assets] visual preload failed: {error}")

        threading.Thread(
            target=worker,
            daemon=True,
            name=(
                "raid-team-visual-preload"
                if pending
                else "raid-global-visual-preload"
            ),
        ).start()

    def reload_hero_catalog_if_changed(self) -> None:
        try:
            modified = HERO_CATALOG.stat().st_mtime_ns
        except OSError:
            modified = 0
        with self.lock:
            if modified == self.hero_catalog_mtime_ns:
                return
        catalog = canonicalize_hero_catalog(load_hero_catalog({}))
        with self.lock:
            self.hero_catalog = {key: static_entity(value) for key, value in catalog.items()}
            self.hero_catalog_mtime_ns = modified
            self._catalog_changed()

    def client_opened(self) -> None:
        with self.client_lock:
            self.active_clients += 1
            self.first_client.set()

    def client_closed(self) -> None:
        with self.client_lock:
            self.active_clients = max(0, self.active_clients - 1)

    def client_count(self) -> int:
        with self.client_lock:
            return self.active_clients

    def shutdown(self) -> None:
        self.stopping.set()
        self.controller.shutdown()

    def use_strategy_account(self, pid: Any = None, account: Any = None) -> StrategyAccount:
        """Strategy groups of the account logged in to this client (else the last account) for this request."""
        if account is None and isinstance(pid, int) and not isinstance(pid, bool):
            account = usable_account_state(pid, latest_account_state(pid))
            if account is None:
                with self.lock:
                    item = self.processes.get(pid)
                account = item.get("account") if isinstance(item, dict) else None
        resolved = self.account_stores.resolve(account)
        self._store_context.account = resolved
        return resolved

    def strategy_account(self) -> StrategyAccount:
        account = getattr(getattr(self, "_store_context", None), "account", None)
        if account is not None:
            return account
        stores = getattr(self, "account_stores", None)
        # Without an account store (tests), the shared file of earlier versions.
        return stores.resolve(None) if stores is not None else StrategyAccount(None, None, STRATEGY_STORE)

    def store_path(self) -> Path:
        return self.strategy_account().path

    def strategy_store(self) -> dict[str, Any]:
        return load_strategy_store(self.store_path())

    def strategy_team_store(self) -> StrategyTeamStore:
        """The teams saved with this account's strategy groups (ids repeat across accounts)."""
        key = self.strategy_account().key
        return StrategyTeamStore(STRATEGY_TEAM_ROOT / key) if key else StrategyTeamStore()

    def ui_preferences(self) -> dict[str, str]:
        value = read_json(UI_PREFERENCES, {})
        language = value.get("language") if isinstance(value, dict) else None
        return {"language": language if language in {"en", "zh-CN"} else "en"}

    def save_ui_preferences(self, value: Any) -> dict[str, str]:
        if not isinstance(value, dict) or value.get("language") not in {"en", "zh-CN"}:
            raise ValueError("不支持的界面语言")
        preferences = {"language": str(value["language"])}
        with self.lock:
            atomic_write_json(UI_PREFERENCES, preferences)
        return preferences

    def strategy(self, boss_mode: str = "chimera") -> dict[str, Any]:
        return strategy_for_mode(self.strategy_store(), boss_mode)

    @staticmethod
    def strategy_profile_name(value: Any) -> str:
        name = str(value or "").strip()
        if not name:
            raise ValueError("请输入策略组名称")
        if len(name) > 60:
            raise ValueError("策略组名称不能超过 60 个字符")
        return name

    def strategy_bundle(
        self, boss_mode: str, store: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        boss_mode = normalize_mode(boss_mode)
        current_store = store if store is not None else self.strategy_store()
        selected_id = active_strategy_id(current_store, boss_mode)
        return {
            "config": strategy_for_mode(current_store, boss_mode, selected_id),
            "revision": revision(strategy_for_mode(current_store, boss_mode, selected_id)),
            "activeStrategyId": selected_id,
            "strategyProfiles": strategy_profiles_for_mode(current_store, boss_mode),
            "strategyAccount": self.strategy_account().public(),
        }

    def save_strategy(
        self, value: Any, boss_mode: str = "chimera",
        strategy_id: str | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        boss_mode = normalize_mode(boss_mode)
        with self.lock:
            store = self.strategy_store()
            selected_id = str(strategy_id or active_strategy_id(store, boss_mode))
            previous = strategy_for_mode(store, boss_mode, selected_id)
            if expected_revision is not None and expected_revision != revision(previous):
                raise ValueError("策略已在其他窗口更新；草稿已保留，请创建副本或重新加载后再保存")
            config = normalized_strategy(value, previous, boss_mode)
            updated = update_mode_strategy(
                store, boss_mode, config, strategy_id=selected_id
            )
            write_strategy_store(self.store_path(), updated)
            return self.strategy_bundle(boss_mode, updated)

    def recover_strategies(self, document: Any = None, boss_mode: str = "chimera") -> None:
        with self.lock:
            if self.controller.snapshot()["running"]:
                raise ValueError("请先暂停接管再恢复策略")
            if document is None:
                recover_strategy_store(self.store_path())
                return
            boss_mode = normalize_mode(boss_mode)
            if (not isinstance(document, dict) or document.get("format") != "raid-boss-strategy"
                    or type(document.get("version")) is not int or document["version"] not in {1, 2}
                    or not isinstance(document.get("strategy"), dict)):
                raise ValueError("请选择有效的导出策略文件")
            if document.get("bossMode") != boss_mode:
                raise ValueError("导出策略所属 Boss 与当前模式不一致")
            config = normalized_strategy(document["strategy"], strategy_template(boss_mode), boss_mode)
            if isinstance(config.get("team"), dict):
                config["team"].pop("heroInstanceIds", None)
            store = update_mode_strategy(default_store(), boss_mode, config)
            restore_strategy_value(self.store_path(), store)

    def create_strategy_profile(
        self, value: Any, name: Any, boss_mode: str = "chimera"
    ) -> dict[str, Any]:
        boss_mode = normalize_mode(boss_mode)
        profile_name = self.strategy_profile_name(name)
        with self.lock:
            store = self.strategy_store()
            previous = strategy_for_mode(store, boss_mode)
            candidate = dict(value) if isinstance(value, dict) else dict(previous)
            candidate["name"] = profile_name
            config = normalized_strategy(candidate, previous, boss_mode)
            existing_ids = {
                item["id"] for item in strategy_profiles_for_mode(store, boss_mode)
            }
            while True:
                strategy_id = f"strategy-{int(time.time() * 1000)}-{secrets.token_hex(3)}"
                if strategy_id not in existing_ids:
                    break
            updated = update_mode_strategy(
                store, boss_mode, config, strategy_id=strategy_id
            )
            write_strategy_store(self.store_path(), updated)
            return self.strategy_bundle(boss_mode, updated)

    def rename_strategy_profile(
        self, strategy_id: str, name: Any, boss_mode: str = "chimera"
    ) -> dict[str, Any]:
        boss_mode = normalize_mode(boss_mode)
        profile_name = self.strategy_profile_name(name)
        with self.lock:
            store = self.strategy_store()
            config = strategy_for_mode(store, boss_mode, strategy_id)
            config["name"] = profile_name
            updated = update_mode_strategy(
                store, boss_mode, config, strategy_id=strategy_id
            )
            write_strategy_store(self.store_path(), updated)
            return self.strategy_bundle(boss_mode, updated)

    def select_strategy_profile(
        self, strategy_id: str, boss_mode: str = "chimera"
    ) -> dict[str, Any]:
        boss_mode = normalize_mode(boss_mode)
        with self.lock:
            updated = select_mode_strategy(
                self.strategy_store(), boss_mode, strategy_id
            )
            write_strategy_store(self.store_path(), updated)
            return self.strategy_bundle(boss_mode, updated)

    def delete_strategy_profile(
        self, strategy_id: str, boss_mode: str = "chimera"
    ) -> dict[str, Any]:
        boss_mode = normalize_mode(boss_mode)
        with self.lock:
            updated = delete_mode_strategy(
                self.strategy_store(), boss_mode, strategy_id
            )
            write_strategy_store(self.store_path(), updated)
            self.strategy_team_store().delete(boss_mode, strategy_id)
            return self.strategy_bundle(boss_mode, updated)

    def export_strategy_profile(
        self, strategy_id: str | None, boss_mode: str = "chimera"
    ) -> dict[str, Any]:
        """Return a portable profile without account-specific champion IDs."""
        boss_mode = normalize_mode(boss_mode)
        with self.lock:
            store = self.strategy_store()
            selected_id = str(
                strategy_id or active_strategy_id(store, boss_mode)
            ).strip()
            strategy = strategy_for_mode(store, boss_mode, selected_id)
        team = strategy.get("team")
        snapshot = None
        if isinstance(team, dict):
            portable_team = dict(team)
            portable_team.pop("heroInstanceIds", None)
            strategy["team"] = portable_team
            # The team as it was when the strategy group was saved, simulatable by others.
            saved = self.strategy_team_store().load(boss_mode, selected_id)
            if saved and same_team(saved.get("heroTypeIds"), portable_team.get("heroTypeIds")):
                snapshot = exported_team(saved)
        # An imported strategy passes its author's team on unchanged; otherwise
        # the newest look of this team on a preparation screen (display only).
        snapshot = snapshot or sanitize_reference_team(strategy.get("referenceTeam"))
        if snapshot is None and isinstance(team, dict):
            snapshot = self.team_snapshots.find(strategy["team"].get("heroTypeIds"))
        return {
            "format": "raid-boss-strategy",
            "version": 2 if strategy.get("strategyFlow") is not None else 1,
            "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "bossMode": boss_mode,
            "strategy": strategy,
            **({"teamSnapshot": snapshot} if snapshot else {}),
        }

    def import_strategy_profile(
        self, document: Any, boss_mode: str = "chimera"
    ) -> dict[str, Any]:
        """Validate a portable document and create an independent profile."""
        boss_mode = normalize_mode(boss_mode)
        if not isinstance(document, dict):
            raise ValueError("导入文件的根节点必须是对象")
        if document.get("format") == "raid-boss-strategy":
            version = document.get("version")
            if not isinstance(version, int) or isinstance(version, bool) or version not in {1, 2}:
                raise ValueError("不支持此策略导出版本")
            value = document.get("strategy")
            declared_mode = document.get("bossMode")
        elif "format" in document:
            raise ValueError("不支持此策略文件格式")
        else:
            # Also accept a raw strategy JSON from older/source-only builds.
            value = document
            declared_mode = document.get("bossMode")
        if not isinstance(value, dict):
            raise ValueError("导入文件中没有有效策略")
        if declared_mode is not None and normalize_mode(declared_mode) != boss_mode:
            raise ValueError("策略所属 Boss 与当前界面不一致，请先切换模式")
        portable = dict(value)
        team = portable.get("team")
        if isinstance(team, dict):
            portable_team = dict(team)
            portable_team.pop("heroInstanceIds", None)
            portable["team"] = portable_team
        # The author's heroes and gear stay with the strategy for reference.
        reference = sanitize_reference_team(document.get("teamSnapshot")) or \
            sanitize_reference_team(portable.get("referenceTeam"))
        portable.pop("referenceTeam", None)
        if reference:
            portable["referenceTeam"] = reference
        config = normalized_strategy(
            portable, strategy_template(boss_mode), boss_mode
        )
        name = str(config.get("name") or "导入策略").strip() or "导入策略"
        config["name"] = self.strategy_profile_name(name)
        with self.lock:
            store = self.strategy_store()
            existing_ids = {
                item["id"] for item in strategy_profiles_for_mode(store, boss_mode)
            }
            while True:
                strategy_id = (
                    f"strategy-{int(time.time() * 1000)}-{secrets.token_hex(3)}"
                )
                if strategy_id not in existing_ids:
                    break
            updated = update_mode_strategy(
                store, boss_mode, config, strategy_id=strategy_id
            )
            write_strategy_store(self.store_path(), updated)
            return self.strategy_bundle(boss_mode, updated)

    def refresh_processes(self, force: bool = False) -> list[dict[str, Any]]:
        with self.lock:
            if not force and self.processes and time.monotonic() - self.last_process_refresh < 4:
                return list(self.processes.values())
        found = raid_processes()
        attach_windows(found)
        valid: dict[int, dict[str, Any]] = {}
        for raw in found.values():
            path = raw.get("path")
            if not isinstance(path, str) or not is_supported_raid_executable(path):
                continue
            item = dict(raw)
            try:
                item["account"] = identify_account(int(item["pid"]))
                account = item.get("account")
                if isinstance(account, dict):
                    desktop_log(
                        f"[account] PID {item['pid']} -> "
                        f"{account.get('accountName')} ({account.get('userId')})"
                    )
                else:
                    desktop_log(
                        f"[account] PID {item['pid']} -> 账户模型尚未就绪"
                    )
            except Exception as error:
                # One unreadable client must not hide the others.
                item["error"] = str(error)
                try:
                    item["account"] = latest_account_state(int(item["pid"]))
                except Exception:
                    item["account"] = None
                desktop_log(
                    f"[account] PID {item['pid']} 读取失败：{error}"
                )
            item["label"] = process_display(item)
            valid[int(item["pid"])] = item
        self.reload_hero_catalog_if_changed()
        with self.lock:
            self.processes = valid
            self.last_process_refresh = time.monotonic()
        return list(valid.values())

    def frontend_process(self, item: dict[str, Any]) -> dict[str, Any]:
        account = item.get("account") if isinstance(item.get("account"), dict) else {}
        return {
            "pid": int(item["pid"]),
            "label": str(item.get("label") or process_display(item)),
            "accountName": account.get("accountName"),
            "userId": account.get("userId"),
            "error": item.get("error"),
        }

    @staticmethod
    def preferred_process_pid(
        available: dict[int, dict[str, Any]], boss_mode: str
    ) -> int | None:
        """Prefer the Raid process that is currently showing the requested boss."""
        candidates: list[tuple[int, int, int]] = []
        screen_priority = {"team_selection": 3, "battle": 2, "result": 1}
        for pid in available:
            try:
                with AgentIpc(pid) as ipc:
                    lifecycle = ipc.lifecycle() or {}
            except (FileNotFoundError, ValueError, OSError):
                continue
            screen = str(lifecycle.get("screen") or "unknown")
            section_name = "selection" if screen == "team_selection" else "battle"
            section = lifecycle.get(section_name)
            if not isinstance(section, dict) or section.get("bossMode") != boss_mode:
                continue
            observed = lifecycle.get("observedAtTick")
            candidates.append(
                (
                    screen_priority.get(screen, 0),
                    int(observed) if isinstance(observed, int) else 0,
                    int(pid),
                )
            )
        if candidates:
            return max(candidates)[2]
        return next(iter(sorted(available)), None)

    def heroes(self) -> list[dict[str, Any]]:
        with self.lock:
            catalog = {
                hero_id: dict(hero)
                for hero_id, hero in self.hero_catalog.items()
                if isinstance(hero, dict)
            }
        result = []
        for hero_id, hero in sorted(catalog.items(), key=lambda pair: str(pair[1].get("name", ""))):
            if not isinstance(hero, dict):
                continue
            skills = []
            for raw_skill in merge_skill_catalog(hero.get("skills", [])):
                if not isinstance(raw_skill, dict):
                    continue
                skill = dict(raw_skill)
                skill["name"] = skill_display_name(skill)
                summary = skill_effect_summary(skill.get("typeId"))
                if summary:
                    skill["effectSummary"] = summary
                skill["isTransform"] = is_transform_skill(hero, skill)
                skills.append(skill)
            result.append({"typeId": int(hero_id), **hero, "skills": skills})
        return result

    def hydra_heads(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                dict(head)
                for _, head in sorted(self.hydra_head_catalog.items())
                if isinstance(head, dict)
                and head.get("typeId") not in HYDRA_RESERVED_HEAD_TYPE_IDS
            ]

    def update_hydra_heads(self, values: Any) -> bool:
        if not isinstance(values, list):
            return False
        changed = False
        with self.lock:
            for value in values:
                if (
                    not isinstance(value, dict)
                    or not isinstance(value.get("typeId"), int)
                    or isinstance(value.get("typeId"), bool)
                ):
                    continue
                normalized = normalize_hydra_head(value)
                if hydra_head_is_exposed_neck(normalized):
                    continue
                type_id = canonical_hydra_head_type_id(normalized)
                if type_id is None or type_id in HYDRA_RESERVED_HEAD_TYPE_IDS:
                    continue
                previous = {
                    key: item
                    for key, item in self.hydra_head_catalog.get(type_id, {}).items()
                    if key not in {"healthPct", "health", "currentHealth", "maxHealth"}
                }
                raw_type_id = int(value["typeId"])
                runtime_type_ids = sorted(
                    {
                        type_id,
                        raw_type_id,
                        *(
                            item
                            for item in previous.get("runtimeTypeIds", [])
                            if isinstance(item, int) and not isinstance(item, bool)
                        ),
                    }
                )
                incoming_name = str(normalized.get("name") or "")
                previous_name = str(previous.get("name") or "")
                name = (
                    incoming_name
                    if incoming_name and (not previous_name or previous_name.startswith("蛇头 "))
                    else previous_name or incoming_name or f"蛇头 {type_id}"
                )
                updated = {
                    **previous,
                    **{
                        key: item
                        for key, item in normalized.items()
                        if key not in {
                            "typeId", "canonicalTypeId", "name",
                            "healthPct", "health", "currentHealth", "maxHealth",
                        }
                        if item not in (None, "")
                    },
                    "typeId": type_id,
                    "runtimeTypeIds": runtime_type_ids,
                    "name": name,
                }
                updated = static_entity(updated)
                if updated != static_entity(previous):
                    self._catalog_changed()
                    self.hydra_head_catalog[type_id] = updated
                    changed = True
        return changed

    def update_hero_catalog(self, state: dict[str, Any]) -> bool:
        raw_heroes = state.get("heroes")
        if not isinstance(raw_heroes, list):
            return False
        active_hero_type_id = state.get("activeHeroTypeId")
        live_skills = {
            int(skill["typeId"]): skill
            for skill in state.get("skills", [])
            if isinstance(skill, dict) and isinstance(skill.get("typeId"), int)
        }
        changed = False
        with self.lock:
            for raw_hero in raw_heroes:
                if not isinstance(raw_hero, dict) or not isinstance(raw_hero.get("typeId"), int):
                    continue
                runtime_hero_id = int(raw_hero["typeId"])
                hero_id = hero_catalog_identity(runtime_hero_id, raw_hero)
                previous = self.hero_catalog.get(hero_id, {})
                previous_by_type = {
                    int(skill["typeId"]): skill
                    for skill in previous.get("skills", [])
                    if isinstance(skill, dict) and isinstance(skill.get("typeId"), int)
                }
                skills: list[dict[str, Any]] = []
                seen_skill_type_ids: set[int] = set()
                slot = 0
                for raw_skill in raw_hero.get("skills", []):
                    if not isinstance(raw_skill, dict):
                        continue
                    if raw_skill.get("activeSkill") is False or raw_skill.get("hiddenOnHud") is True:
                        continue
                    slot += 1
                    type_id = raw_skill.get("typeId")
                    # A new TypeId must not inherit another skill's localized
                    # name, icon or flags merely because its position matches.
                    cached = previous_by_type.get(type_id, {})
                    merged = dict(cached) if isinstance(cached, dict) else {}
                    merged.update({key: value for key, value in raw_skill.items() if value not in (None, "")})
                    merged["slot"] = int(raw_skill.get("slot") or slot)
                    incoming_name = str(raw_skill.get("name") or "")
                    cached_name = str(cached.get("name") or "") if isinstance(cached, dict) else ""
                    if is_unresolved_skill_name(incoming_name) and not is_unresolved_skill_name(cached_name):
                        merged["name"] = cached_name
                    else:
                        merged["name"] = skill_display_name(merged)
                    if runtime_hero_id == active_hero_type_id and isinstance(type_id, int):
                        live = live_skills.get(type_id)
                        if live:
                            merged.update({key: value for key, value in live.items() if value not in (None, "")})
                            live_name = str(live.get("name") or "")
                            if is_unresolved_skill_name(live_name) and not is_unresolved_skill_name(cached_name):
                                merged["name"] = cached_name
                            else:
                                merged["name"] = skill_display_name(merged)
                    skills.append(merged)
                    if isinstance(type_id, int):
                        seen_skill_type_ids.add(type_id)
                # Battle snapshots can expose only the current form. Keep the
                # complete static catalog loaded during initialization so names
                # and the other form's skills do not flicker out of the editor.
                excluded_ids = {raw.get("typeId") for raw in raw_hero.get("skills", [])
                    if isinstance(raw, dict) and (raw.get("activeSkill") is False or raw.get("hiddenOnHud") is True)}
                skills = merge_skill_catalog(skills, [value for value in previous.get("skills", [])
                    if isinstance(value, dict) and value.get("typeId") not in excluded_ids])
                updated = {
                    **previous,
                    **{
                        key: value
                        for key, value in raw_hero.items()
                        if key != "skills" and value not in (None, "")
                    },
                    "name": str(raw_hero.get("name") or previous.get("name") or f"英雄 {hero_id}"),
                    "avatar": raw_hero.get("avatar") or previous.get("avatar") or "",
                    "typeId": hero_id,
                    "runtimeTypeIds": sorted(
                        {
                            hero_id,
                            runtime_hero_id,
                            *(
                                value
                                for value in previous.get("runtimeTypeIds", [])
                                if isinstance(value, int)
                            ),
                        }
                    ),
                    "skills": skills,
                }
                updated = static_entity(updated)
                if updated != previous:
                    self._catalog_changed()
                    self.hero_catalog[hero_id] = updated
                    changed = True
            if changed:
                self.hero_catalog = canonicalize_hero_catalog(self.hero_catalog)
                save_hero_catalog(self.hero_catalog)
        return changed

    def update_rotation_catalog(
        self,
        rotation: dict[str, Any],
        lifecycle_section: dict[str, Any],
    ) -> int | None:
        status_effects = rotation.get("statusEffects")
        if isinstance(status_effects, list):
            updated_effects = runtime_effect_options(status_effects)
            with self.lock:
                self.status_effect_catalog = [
                    dict(effect)
                    for effect in status_effects
                    if isinstance(effect, dict)
                ]
                if updated_effects != self.effect_options:
                    self.effect_options = updated_effects
                    self._catalog_changed()
        self.update_hydra_heads(rotation.get("hydraHeads"))
        self.boss_skills.learn(listed_skills(rotation.get("hydraHeads")), static=True)
        catalog = rotation.get("catalog")
        identity = rotation.get("identity")
        stage_id = lifecycle_section.get("stageId")
        if not isinstance(catalog, dict) or not isinstance(identity, dict):
            return None
        selected: dict[str, Any] | None = None
        for difficulty in catalog.get("difficulties", []):
            if isinstance(difficulty, dict) and stage_id in difficulty.get("stageIds", []):
                selected = difficulty
                break
        selected_difficulty_id = (
            int(selected["difficultyId"])
            if isinstance(selected, dict) and isinstance(selected.get("difficultyId"), int)
            else None
        )
        fingerprint = identity.get("rewardRotationFingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            return selected_difficulty_id
        live_difficulties = [
            value
            for value in catalog.get("difficulties", [])
            if isinstance(value, dict) and isinstance(value.get("difficultyId"), int)
        ]
        cache_keys = {
            (fingerprint, int(value["difficultyId"]))
            for value in live_difficulties
        }
        with self.lock:
            if cache_keys and cache_keys.issubset(self.cached_rotation_keys):
                return selected_difficulty_id
        with self.lock:
            self.ui_catalog = cache_live_rotation_catalog(
                self.ui_catalog, catalog, identity,
            )
            self.cached_rotation_keys.update(cache_keys)
            self._catalog_changed()
        return selected_difficulty_id

    @staticmethod
    def _trial_statuses(state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[int]]:
        trials: list[dict[str, Any]] = []
        completed: list[int] = []
        for boss in state.get("bosses", []):
            if not isinstance(boss, dict):
                continue
            for challenge in boss.get("challenges", []):
                if not isinstance(challenge, dict) or not isinstance(challenge.get("id"), int):
                    continue
                trial = {
                    key: challenge.get(key)
                    for key in ("id", "completed", "possible", "impossible", "eligibleNow", "activeInChain", "progress")
                    if key in challenge
                }
                trials.append(trial)
                if challenge.get("completed") is True:
                    completed.append(int(challenge["id"]))
        return trials, completed

    def live_state(self, pid: int, boss_mode: str = "chimera") -> dict[str, Any]:
        boss_mode = normalize_mode(boss_mode)
        spec = mode_spec(boss_mode)
        try:
            with AgentIpc(pid) as ipc:
                header = ipc.header()
                state = ipc.decision() or {}
                lifecycle = ipc.lifecycle() or {}
                ledger = ipc.battle_ledger() or {}
                rotation = ipc.rotation_catalog() or {}
                usage = ipc.slot_usage() if hasattr(ipc, "slot_usage") else {}
        except (FileNotFoundError, ValueError, OSError) as error:
            return {
                "bossMode": boss_mode,
                "statusLabel": f"尚无已验证的{spec['label']}状态",
                "agentReady": False,
                "modeReady": False,
                "error": str(error),
            }

        screen = lifecycle.get("screen")
        team = lifecycle_team_ids(lifecycle)
        expected_team_size = int(spec["teamSize"])
        lifecycle_section = lifecycle.get(
            "selection" if screen == "team_selection" else "battle"
        )
        if not isinstance(lifecycle_section, dict):
            lifecycle_section = {}
        raw_team_types = lifecycle_section.get("heroTypeIds")
        preparation_team_slots = (
            [
                value
                if isinstance(value, int)
                and not isinstance(value, bool)
                and value > 0
                else 0
                for value in raw_team_types
            ]
            if screen == "team_selection"
            and isinstance(raw_team_types, list)
            and len(raw_team_types) == expected_team_size
            else []
        )
        raw_team_instances = lifecycle_section.get("heroIds")
        team_instances = (
            [
                value
                for value in raw_team_instances
                if isinstance(value, int) and not isinstance(value, bool) and value > 0
            ]
            if isinstance(raw_team_instances, list)
            else []
        )
        if len(team_instances) != expected_team_size or len(set(team_instances)) != len(team_instances):
            team_instances = []
        if state.get("type") == "hero_catalog_state":
            self.update_hero_catalog(state)
            state = {}
        else:
            self.update_hero_catalog(state)
        lifecycle_catalog = lifecycle_section.get("heroCatalog")
        if isinstance(lifecycle_catalog, list):
            self.update_hero_catalog({"heroes": lifecycle_catalog})
        live_chimera_difficulty_id = self.update_rotation_catalog(
            rotation, lifecycle_section
        )
        battle = state.get("battle") if isinstance(state.get("battle"), dict) else {}
        state_mode = state.get("bossMode")
        lifecycle_mode = lifecycle_section.get("bossMode")
        ledger_mode = ledger.get("bossMode")
        battle_is_hydra = battle.get("hydraBattle") is True
        detected_mode = (
            str(state_mode)
            if state_mode in MODE_SPECS
            else (
                str(lifecycle_mode)
                if lifecycle_mode in MODE_SPECS
                else (
                    str(ledger_mode)
                    if ledger_mode in MODE_SPECS
                    else ("hydra" if battle_is_hydra else "chimera")
                )
            )
        )
        mode_matches = detected_mode == boss_mode and bool(
            state or screen in {"team_selection", "result"}
        )
        team_cache_key = (pid, boss_mode)
        if mode_matches and preparation_team_slots:
            team = preparation_team_slots
            selected_team = [value for value in team if value > 0]
            if (
                len(selected_team) == expected_team_size
                and len(set(selected_team)) == expected_team_size
            ):
                with self.lock:
                    self.live_team_cache[team_cache_key] = list(team)
        elif mode_matches and len(team) == expected_team_size:
            with self.lock:
                self.live_team_cache[team_cache_key] = list(team)
        elif mode_matches and screen == "team_selection":
            with self.lock:
                team = list(self.live_team_cache.get(team_cache_key, []))
        chimera = state.get("chimera") if isinstance(state.get("chimera"), dict) else {}
        hydra = state.get("hydra") if isinstance(state.get("hydra"), dict) else {}
        bosses = state.get("bosses") if isinstance(state.get("bosses"), list) else []
        if boss_mode == "hydra" and mode_matches:
            devouring_ids = hydra_devouring_head_ids(state)
            current_heads: dict[int, dict[str, Any]] = {}
            for value in bosses:
                if not isinstance(value, dict):
                    continue
                head = normalize_hydra_head(value)
                if head.get("dead") is True:
                    continue
                actor_id = head.get("id")
                if actor_id in devouring_ids:
                    head["isDevouring"] = True
                if not isinstance(actor_id, int) or isinstance(actor_id, bool):
                    continue
                # Hydra creates a new actor whenever a head returns.  The UI
                # keys current entries by actor rather than type, so two live
                # instances can never overwrite one another.  HP is omitted so
                # replaced actors cannot accumulate meaningless history.
                for key in ("healthPct", "health", "currentHealth", "maxHealth"):
                    head.pop(key, None)
                current_heads[actor_id] = head
            bosses = list(current_heads.values())
            self.update_hydra_heads(bosses)
        if mode_matches:
            self.boss_skills.learn(listed_skills(bosses))
        boss = next((value for value in bosses if isinstance(value, dict)), {})
        trial_states, completed = self._trial_statuses(state)
        form = chimera.get("currentForm")
        hero = state.get("activeHeroName")
        if screen == "team_selection" and mode_matches:
            status = f"{spec['label']}队伍准备界面"
        elif screen == "result":
            status = "战绩结算画面 · 等待你的决定"
        elif state and mode_matches:
            status = " · ".join(
                value
                for value in (
                    (
                        {"Ultimate": "终极形态", "Ram": "公羊形态", "Lion": "狮子形态", "Snake": "毒蛇形态"}.get(str(form), str(form or "未知形态"))
                        if boss_mode == "chimera"
                        else f"{len(bosses)} 个在场目标"
                    ),
                    str(hero or "未知英雄"),
                    "等待行动" if battle.get("waitingForManualCommand") else "战斗进行中",
                )
                if value
            )
        else:
            status = f"已连接 · 等待{spec['label']}状态"
        damage = (
            ledger.get("damage", 0)
            if screen == "result" and ledger.get("bossMode") == boss_mode
            else battle.get("currentDamage", 0)
        )
        return {
            "bossMode": boss_mode,
            "screen": screen,
            "statusLabel": status,
            "form": form,
            "activeHeroName": hero,
            "activeHeroTypeId": state.get("activeHeroTypeId"),
            "bossHpPct": boss.get("healthPct") if boss_mode == "chimera" else None,
            "waitingForCommand": battle.get("waitingForManualCommand"),
            "chimeraTurn": chimera.get("turnCount"),
            "chimeraDifficultyId": (
                live_chimera_difficulty_id if boss_mode == "chimera" else None
            ),
            "hydraTurn": hydra.get("turnCount", battle.get("turn")),
            "headCount": len(bosses) if boss_mode == "hydra" and mode_matches else 0,
            "heads": bosses if boss_mode == "hydra" and mode_matches else [],
            "playerTurn": battle.get("playerTurnCount"),
            "damage": damage,
            "teamHeroIds": team,
            "teamHeroInstanceIds": team_instances,
            "completedTrialIds": completed,
            "trials": trial_states,
            "agentReady": bool(header.get("ready")),
            "modeReady": mode_matches,
            "ipcUsage": usage,
        }

    def _catalog_changed(self) -> None:
        self.catalog_epoch = getattr(self, "catalog_epoch", 0) + 1

    def catalog_payload(self, known_revision: str | None = None) -> dict[str, Any]:
        with self.lock:
            catalog_revision = f"{getattr(self, 'catalog_session', 'initial')}:{getattr(self, 'catalog_epoch', 0)}"
            if known_revision == catalog_revision:
                return {"catalogRevision": catalog_revision}
            return {
                "catalogRevision": catalog_revision,
                "heroes": self.heroes(),
                "hydraHeads": self.hydra_heads(),
                "effects": list(self.effect_options),
                "difficulties": self.ui_catalog.get("difficulties", []),
            }

    def state_payload(self, pid: int | None, boss_mode: str,
                      catalog_revision: str | None = None,
                      log_cursor: str | None = None) -> dict[str, Any]:
        state = self.live_state(pid, boss_mode) if pid is not None else {
            "bossMode": boss_mode, "statusLabel": "没有找到 Raid 账户", "modeReady": False,
        }
        health = storage_health(self.store_path())
        store = self.strategy_store() if health["ok"] else default_store()
        prepared = self.prepared_team_preview(pid)
        sources = self.team_sources(store, boss_mode, pid)
        return {
            "pid": pid, "bossMode": boss_mode,
            "state": state,
            "controller": self.controller.snapshot(boss_mode, after=log_cursor),
            "strategyProfiles": strategy_profiles_for_mode(store, boss_mode),
            "strategyAccount": self.strategy_account().public(),
            "storageHealth": health,
            **({"hydraForecasts": recent_hydra_forecasts()} if boss_mode == "hydra" else {}),
            **({"chimeraSimulation": {**self.simulation_overview(), "teamSources": sources}}
               if boss_mode == "chimera" else {}),
            **({"hydraSimulation": {**self.hydra_simulation_overview(), "teamSources": sources}}
               if boss_mode == "hydra" else {}),
            "teamPreview": self.team_preview_summary(pid, prepared),
            **self.catalog_payload(catalog_revision),
        }

    def prepared_team_preview(self, pid: int | None) -> dict[str, Any] | None:
        """The preparation-screen team with its battle-setup parts (internal; not for the interface)."""
        if not isinstance(pid, int) or isinstance(pid, bool):
            return None
        try:
            with AgentIpc(pid) as ipc:
                raw = ipc.team_preview()
        except (FileNotFoundError, ValueError, OSError):
            raw = None
        cached = self.team_previews.get(pid)
        tick = raw.get("observedAtTick") if isinstance(raw, dict) else None
        if raw is not None and (cached is None or cached.get("tick") != tick):
            preview = decode_preview(raw)
            if preview is not None:
                preview["capturedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self.check_team_setups(preview)
            cached = {"tick": tick, "preview": preview}
            self.team_previews[pid] = cached
        preview = cached.get("preview") if cached else None
        if preview is not None:
            public = public_preview(preview)
            if public is not None:
                self.team_snapshots.remember(public)
        return preview

    def team_preview(self, pid: int | None) -> dict[str, Any] | None:
        """The preparation-screen team of this account (hero-screen stats, sets, masteries)."""
        return display_preview(self.prepared_team_preview(pid))

    @staticmethod
    def check_team_setups(preview: dict[str, Any]) -> None:
        """Compare the assembled setups with saved battles of the same heroes (background)."""
        boss_mode = preview.get("bossMode")
        team = preview_team(preview, boss_mode) if boss_mode in TEAM_SIZE else None
        if team is None:
            return

        def work() -> None:
            try:
                remember_check(check_against_captures(team["heroes"], team["observatory"], boss_mode))
            except Exception as error:  # A failed check only leaves the default rule in place.
                desktop_lifecycle_log(f"team_setup_check_failed {type(error).__name__}: {error}")

        threading.Thread(target=work, name="team-setup-check", daemon=True).start()

    def team_sources(self, store: dict[str, Any], boss_mode: str, pid: int | None = None) -> dict[str, Any]:
        strategy_id = active_strategy_id(store, boss_mode)
        config = strategy_for_mode(store, boss_mode, strategy_id)
        team = config.get("team") if isinstance(config.get("team"), dict) else {}
        # The current team and every account bonus are read from the running game.
        return {"strategyId": strategy_id, "accountReadable": isinstance(pid, int), **team_sources(
            boss_mode, team.get("heroTypeIds"), bound=bool(team_hero_ids(team)),
            saved=self.strategy_team_store().load(boss_mode, strategy_id),
            reference=sanitize_reference_team(config.get("referenceTeam")), check=latest_check(boss_mode))}

    @staticmethod
    def agent_request(pid: Any, ids: list[int], kind: int, slot: str, what: str,
                      timeout: float = 5.0) -> dict[str, Any]:
        """Ask the game (read only) for account data and wait for the answer with its nonce."""
        if not isinstance(pid, int) or isinstance(pid, bool):
            raise TeamSetupError(f"需要打开游戏读取{what}")
        nonce = secrets.randbits(62) + 1
        try:
            queued = request_account_bonuses(pid, [int(value) for value in ids], nonce, kind=kind)
        except RuntimeError as error:
            if "ABI" in str(error) or "incompatible" in str(error):
                raise TeamSetupError("游戏内模块不是最新版本：请重启一次游戏") from error
            raise TeamSetupError(f"没有读到{what}：{error}") from error
        except (OSError, ValueError) as error:
            raise TeamSetupError(f"没有读到{what}：{error}") from error
        if not queued.get("queued"):
            raise TeamSetupError(f"游戏暂时无法读取{what}（{queued.get('agentResult')}），请稍后重试")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with AgentIpc(pid) as ipc:
                    raw = ipc.read_json(slot)
            except (FileNotFoundError, ValueError, OSError):
                raw = None
            if isinstance(raw, dict) and raw.get("nonce") == nonce:
                return raw
            time.sleep(0.05)
        raise TeamSetupError(f"游戏没有及时返回{what}：请确认游戏在运行后重试")

    @classmethod
    def account_bonuses(cls, pid: Any, type_ids: list[int]) -> dict[str, Any]:
        """The current account's academy, building and area bonuses for these hero types (read only)."""
        raw = cls.agent_request(pid, type_ids, REQUEST_BONUSES_BY_TYPE, "account_bonuses",
                                "当前账号的学院、建筑和区域加成")
        return decode_account_bonuses(raw, type_ids)

    def boss_skill_labels(self, timeline: Any, actors: Any = None) -> dict[str, dict[str, Any]]:
        """Names, descriptions and cooldowns of the skills the bosses used in a simulated battle.

        Skills not yet read from the static data are asked of a running game
        (read only); without one the report shows what is already known.
        """
        used = enemy_skill_ids(timeline, actors)
        missing = self.boss_skills.missing(used)
        if missing and time.monotonic() >= self.boss_skill_retry_at:
            pid = next((
                int(process["pid"]) for process in self.refresh_processes()
                if (read_agent_status(int(process["pid"])) or {}).get("compatible") is True
            ), None)
            try:
                if pid is None:
                    raise TeamSetupError("没有可读取的游戏")
                for start in range(0, len(missing), 8):
                    chunk = missing[start:start + 8]
                    raw = self.agent_request(pid, chunk, REQUEST_SKILL_TYPES, "team_data", "Boss 技能说明", 2.0)
                    if raw.get("status") != "captured":
                        raise TeamSetupError(str(raw.get("reason") or "unavailable"))
                    # Ids the static data does not have are recorded too, so they are not asked again.
                    self.boss_skills.learn([*(raw.get("skills") or []), *({"typeId": value} for value in chunk)],
                                           static=True)
            except TeamSetupError:
                self.boss_skill_retry_at = time.monotonic() + 60
        return self.boss_skills.lookup(used)

    @classmethod
    def roster(cls, pid: Any) -> list[dict[str, Any]]:
        """The current account's champions, for choosing a strategy group's team (read only)."""
        return decode_roster(cls.agent_request(pid, [1], REQUEST_ROSTER, "team_data", "账号的英雄列表", 10.0))

    @classmethod
    def team_data(cls, pid: Any, hero_ids: list[int], boss_mode: str, timeout: float = 5.0) -> dict[str, Any]:
        """These heroes of the current account as they are now: hero screen data and battle inputs."""
        kind = REQUEST_TEAM_DATA | (REQUEST_HYDRA if boss_mode == "hydra" else 0)
        raw = cls.agent_request(pid, hero_ids, kind, "team_data", "策略组英雄现在的装备", timeout)
        preview = decode_preview(raw)
        if preview is None or preview.get("status") != "captured":
            reason = (preview or {}).get("reason") or "unknown"
            if reason == "heroes_not_found":
                raise TeamSetupError("当前账号里找不到策略组的英雄（可能切换了账号，或英雄已不在）")
            raise TeamSetupError(f"没有读到策略组英雄现在的装备（{reason}）")
        found = [hero.get("heroId") for hero in preview.get("heroes") or []]
        if sorted(found) != sorted(hero_ids):
            raise TeamSetupError("当前账号里找不到策略组的部分英雄（可能切换了账号，或英雄已不在）")
        preview["capturedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return preview

    def remember_strategy_team(self, pid: Any, boss_mode: str, strategy_id: str) -> bool:
        """Snapshot the strategy group's team when it is saved: read from the running game, any screen.

        Without the game (or with another account's heroes) the previous snapshot stays.
        """
        config = strategy_for_mode(self.strategy_store(), boss_mode, strategy_id) if strategy_id else {}
        team = config.get("team") if isinstance(config.get("team"), dict) else {}
        hero_ids = team_hero_ids(team)
        if not strategy_id or not hero_ids or not isinstance(pid, int) or isinstance(pid, bool):
            return False
        try:
            preview = self.team_data(pid, hero_ids, boss_mode, timeout=3.0)
        except TeamSetupError as error:
            desktop_lifecycle_log(f"strategy_team_snapshot_skipped {error}")
            return False
        prepared = preview_team(preview, boss_mode)
        if prepared is None or not same_team(prepared["heroTypeIds"], team.get("heroTypeIds")):
            return False
        try:
            self.strategy_team_store().save(boss_mode, strategy_id, prepared,
                                            display=portable_snapshot(display_preview(preview)))
        except OSError:
            return False
        self.check_team_setups(preview)
        return True

    def simulation_team(self, body: dict[str, Any], config: dict[str, Any], boss_mode: str) -> dict[str, Any] | None:
        source = str(body.get("teamSource") or "battle")
        if source == "battle":
            return None
        pid = body.get("pid") if isinstance(body.get("pid"), int) and not isinstance(body.get("pid"), bool) else None
        strategy_id = str(body.get("strategyId") or "")
        # The team and the author's team come with the saved strategy group, the rules with the draft.
        stored = strategy_for_mode(self.strategy_store(), boss_mode, strategy_id) if strategy_id else {}
        config = stored if isinstance(stored.get("team"), dict) else config
        team = config.get("team") if isinstance(config.get("team"), dict) else {}
        hero_ids = team_hero_ids(team)

        def current() -> dict[str, Any] | None:
            if not hero_ids:
                raise TeamSetupError("这个策略组还没有绑定你账号里的英雄：在“策略组队伍”卡片上选择英雄")
            preview = self.team_data(pid, hero_ids, boss_mode)
            self.check_team_setups(preview)
            team = preview_team(preview, boss_mode)
            if team is not None:
                team["display"] = portable_snapshot(display_preview(preview))
            return team

        return simulation_team(
            source, boss_mode, team.get("heroTypeIds"),
            saved=self.strategy_team_store().load(boss_mode, strategy_id) if strategy_id else None,
            reference=sanitize_reference_team(config.get("referenceTeam")),
            check=latest_check(boss_mode),
            current=current if pid is not None else None,
            bonuses=(lambda type_ids: self.account_bonuses(pid, type_ids)) if pid is not None else None)

    def team_preview_summary(self, pid: int | None, preview: dict[str, Any] | None = None) -> dict[str, Any] | None:
        preview = preview if preview is not None else self.prepared_team_preview(pid)
        if preview is None:
            return None
        return {"revision": str(preview.get("teamKey") or preview.get("observedAtTick")),
                "status": preview.get("status"), "bossMode": preview.get("bossMode"),
                "heroes": len(preview.get("heroes") or [])}

    def simulation_overview(self) -> dict[str, Any]:
        return {"captures": list_chimera_captures(8), "job": self.simulations.status(),
                "recent": recent_chimera_simulations(8),
                "battleForecasts": recent_chimera_simulations(6, root=BATTLE_FORECAST_ROOT)}

    def hydra_simulation_overview(self) -> dict[str, Any]:
        return {"captures": self.hydra_simulations.captures(8), "job": self.hydra_simulations.status(),
                "recent": recent_hydra_simulations(8)}

    def start_hydra_simulation(self, body: dict[str, Any]) -> dict[str, Any]:
        config = normalized_strategy(body.get("config"), {}, "hydra")
        runs = body.get("runs")
        if not isinstance(runs, int) or isinstance(runs, bool):
            raise ValueError("模拟场数无效")
        pid = body.get("pid")
        return self.hydra_simulations.start(
            config,
            {"id": str(body.get("strategyId") or ""), "name": str(body.get("strategyName") or config.get("name") or "")},
            str(body.get("captureId") or ""), runs,
            pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
            team=self.simulation_team(body, config, "hydra"))

    def start_simulation(self, body: dict[str, Any]) -> dict[str, Any]:
        config = normalized_strategy(body.get("config"), {}, "chimera")
        runs = body.get("runs")
        if not isinstance(runs, int) or isinstance(runs, bool):
            raise ValueError("模拟场数无效")
        pid = body.get("pid")
        return self.simulations.start(
            config,
            {"id": str(body.get("strategyId") or ""), "name": str(body.get("strategyName") or config.get("name") or "")},
            str(body.get("captureId") or ""), runs,
            pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
            team=self.simulation_team(body, config, "chimera"))

    def bootstrap(
        self, pid: int | None = None, force: bool = False,
        boss_mode: str = "chimera"
    ) -> dict[str, Any]:
        boss_mode = normalize_mode(boss_mode)
        processes = self.refresh_processes(force=force)
        available = {int(item["pid"]): item for item in processes}
        if pid not in available:
            running = self.controller.snapshot().get("pid")
            pid = (
                running
                if isinstance(running, int) and running in available
                else self.preferred_process_pid(available, boss_mode)
            )
        self.use_strategy_account(pid)
        health = storage_health(self.store_path())
        strategy_bundle = self.strategy_bundle(boss_mode, None if health["ok"] else default_store())
        config = strategy_bundle["config"]
        live = (
            self.live_state(pid, boss_mode)
            if isinstance(pid, int)
            else {"bossMode": boss_mode, "statusLabel": "没有找到 Raid 账户", "modeReady": False}
        )
        team = live.get("teamHeroIds") if isinstance(live, dict) else []
        if isinstance(team, list) and team:
            self.start_visual_preload(team)
        difficulties = self.ui_catalog.get("difficulties", [])
        return {
            "processes": [self.frontend_process(item) for item in sorted(processes, key=lambda value: int(value["pid"]))],
            "selectedPid": pid,
            "bossMode": boss_mode,
            "modes": list(MODE_SPECS.values()),
            "config": config,
            "activeStrategyId": strategy_bundle["activeStrategyId"],
            "strategyProfiles": strategy_bundle["strategyProfiles"],
            "strategyAccount": strategy_bundle["strategyAccount"],
            **self.catalog_payload(),
            "revision": strategy_bundle["revision"],
            "storageHealth": health,
            "language": self.ui_preferences()["language"],
            "difficulties": difficulties if isinstance(difficulties, list) else [],
            "state": live,
            "controller": self.controller.snapshot(boss_mode),
            "cacheStatus": (
                f"{len(difficulties) if isinstance(difficulties, list) else 0} 个难度 · "
                f"{self.visual_cache_status.get('avatars', 0)} 个头像 · "
                f"{self.visual_cache_status.get('skills', 0)} 个当前队伍技能图标 · "
                f"{self.visual_cache_status.get('rewards', 0)} 个奖励图标已缓存"
            ),
        }

    def start(self, pid: int, boss_mode: str = "chimera") -> dict[str, Any]:
        boss_mode = normalize_mode(boss_mode)
        processes = {int(item["pid"]): item for item in self.refresh_processes()}
        item = processes.get(pid)
        if item is None:
            raise RuntimeError("所选 Raid 进程已经不存在")
        account = item.get("account")
        if not isinstance(account, dict):
            raise RuntimeError("必须先读取到游戏内用户名和玩家 ID，不能只按 PID 启动")
        name = account.get("accountName")
        user_id = account.get("userId")
        if not isinstance(name, str) or not name or not isinstance(user_id, int) or isinstance(user_id, bool):
            raise RuntimeError("游戏内账户信息不完整，已拒绝启动")
        live = self.live_state(pid, boss_mode)
        if boss_mode == "hydra" and live.get("modeReady") is not True:
            raise RuntimeError(
                "尚未读取到当前账户的六头蛇准备界面或战斗状态；为避免误操作，暂不启动接管"
            )
        self.use_strategy_account(pid, account)
        with self.lock:
            bundle = self.strategy_bundle(boss_mode)
            config = normalized_strategy(bundle["config"], bundle["config"], boss_mode)
            self.controller.start(pid, name, user_id, boss_mode, config=config, strategy_id=bundle["activeStrategyId"])
        return self.controller.snapshot(boss_mode)

    def asset(self, kind: str, parts: list[str]) -> Path | None:
        if kind in TEAM_ICON_BUNDLES and len(parts) == 1:
            return game_named_sprite(TEAM_ICON_BUNDLES[kind], parts[0])
        if kind == "hero" and len(parts) == 1 and parts[0].isdigit():
            requested_hero_id = int(parts[0])
            hero = self.hero_catalog.get(requested_hero_id)
            if hero is None:
                hero = next(
                    (
                        candidate
                        for candidate in self.hero_catalog.values()
                        if isinstance(candidate, dict)
                        and requested_hero_id in candidate.get("runtimeTypeIds", [])
                    ),
                    None,
                )
            if isinstance(hero, dict):
                hero_id = hero_catalog_identity(requested_hero_id, hero)
                native = game_hero_asset(hero_id, hero)
                fallback = DIST_DIR / "hero-fallbacks" / f"{hero_id}.png"
                return native or (fallback if fallback.is_file() else None) or cache_visual_asset(
                    hero.get("avatar") or hero.get("avatarUrl"),
                    "hero",
                    hero_id,
                    allow_network=False,
                )
        if kind == "head" and len(parts) == 1 and parts[0].isdigit():
            type_id = int(parts[0])
            with self.lock:
                head = dict(self.hydra_head_catalog.get(type_id, {}))
            native = game_hero_asset(type_id, head)
            return native or cache_visual_asset(
                head.get("avatar") or head.get("avatarUrl"),
                "head",
                type_id,
                allow_network=False,
            )
        if kind == "skill" and len(parts) == 2 and all(value.isdigit() for value in parts):
            hero_id, identity = map(int, parts)
            hero = self.hero_catalog.get(hero_id)
            if hero is None:
                # A live hero type id (rank/ascension variant) from the team preview.
                hero = next(
                    (
                        candidate
                        for candidate in self.hero_catalog.values()
                        if isinstance(candidate, dict)
                        and hero_id in candidate.get("runtimeTypeIds", [])
                    ),
                    {},
                )
                # Hero type ids are the base id plus the rank digit.
                hero_id = hero_catalog_identity(hero_id, hero) if hero else hero_id - hero_id % 10
            if isinstance(hero, dict):
                skill = next(
                    (
                        item
                        for item in hero.get("skills", [])
                        if isinstance(item, dict) and item.get("typeId") == identity
                    ),
                    None,
                )
                if skill is None:
                    skill = next(
                        (
                            item
                            for item in hero.get("skills", [])
                            if isinstance(item, dict) and item.get("slot") == identity
                        ),
                        None,
                    )
                if skill is None and identity >= 100:
                    # A skill the catalog leaves out (passives): its type id ends in the slot.
                    skill = {"typeId": identity, "slot": identity % 100}
                if isinstance(skill, dict):
                    asset_identity = skill.get("typeId") or f"{hero_id}-{identity}"
                    native = game_skill_asset(hero_id, hero, skill)
                    return native or cache_visual_asset(
                        skill.get("icon") or skill.get("iconUrl"),
                        "skill",
                        asset_identity,
                        allow_network=False,
                    )
        if kind == "effect" and len(parts) == 1:
            name = parts[0]
            if name.replace("_", "").isalnum():
                return ensure_icon_cache().get(name)
        if kind == "reward" and len(parts) == 1:
            identity = parts[0]
            if identity.replace("-", "").isalnum():
                return game_reward_asset(identity)
        return None


class ChimeraHandler(BaseHTTPRequestHandler):
    server: "ChimeraHttpServer"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _json(self, value: Any, status: int = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, error: Exception, status: int = HTTPStatus.BAD_REQUEST) -> None:
        try:
            self._json({"error": str(error)}, status)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _write_authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Chimera-Token", ""), self.server.token)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 2_000_000:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("请求格式无效")
        return value

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path.startswith("/api/"):
            raw_pid = query.get("pid", [None])[0]
            try:
                self.server.service.use_strategy_account(
                    int(raw_pid) if raw_pid and str(raw_pid).isdigit() else None)
            except Exception as error:  # Never block a request on the account lookup.
                desktop_lifecycle_log(f"strategy_account_failed {type(error).__name__}: {error}")
        try:
            if parsed.path == "/api/session":
                supplied = query.get("token", [""])[0]
                if not secrets.compare_digest(str(supplied), self.server.token):
                    self._error(PermissionError("本次界面会话已失效"), HTTPStatus.FORBIDDEN)
                    return
                self._session_stream()
                return
            if parsed.path == "/api/bootstrap":
                if not self._write_authorized():
                    self._error(PermissionError("本次界面会话已失效"), HTTPStatus.FORBIDDEN)
                    return
                raw_pid = query.get("pid", [None])[0]
                pid = int(raw_pid) if raw_pid and str(raw_pid).isdigit() else None
                boss_mode = normalize_mode(query.get("mode", ["chimera"])[0])
                self._json(
                    self.server.service.bootstrap(
                        pid, force=True, boss_mode=boss_mode
                    )
                )
                return
            if parsed.path == "/api/state":
                if not self._write_authorized():
                    self._error(PermissionError("本次界面会话已失效"), HTTPStatus.FORBIDDEN)
                    return
                raw_pid = query.get("pid", [None])[0]
                if not raw_pid or not str(raw_pid).isdigit():
                    raw_pid = None
                pid = int(raw_pid) if raw_pid else None
                boss_mode = normalize_mode(query.get("mode", ["chimera"])[0])
                self._json(self.server.service.state_payload(
                    pid, boss_mode,
                    query.get("catalogRevision", [None])[0],
                    query.get("logCursor", [None])[0],
                ))
                return
            if parsed.path == "/api/roster":
                if not self._write_authorized():
                    self._error(PermissionError("本次界面会话已失效"), HTTPStatus.FORBIDDEN)
                    return
                raw_pid = query.get("pid", [None])[0]
                pid = int(raw_pid) if raw_pid and str(raw_pid).isdigit() else None
                self._json({"heroes": self.server.service.roster(pid)})
                return
            if parsed.path == "/api/team-preview":
                if not self._write_authorized():
                    self._error(PermissionError("本次界面会话已失效"), HTTPStatus.FORBIDDEN)
                    return
                raw_pid = query.get("pid", [None])[0]
                pid = int(raw_pid) if raw_pid and str(raw_pid).isdigit() else None
                self._json({"preview": self.server.service.team_preview(pid)})
                return
            if parsed.path in ("/api/chimera-simulation", "/api/hydra-simulation"):
                if not self._write_authorized():
                    self._error(PermissionError("本次界面会话已失效"), HTTPStatus.FORBIDDEN)
                    return
                service = (self.server.service.hydra_simulations if parsed.path == "/api/hydra-simulation"
                           else self.server.service.simulations)
                simulation_id = query.get("id", [""])[0]
                raw_run = query.get("run", [None])[0]
                if raw_run is not None and str(raw_run).isdigit():
                    run = service.load_run(simulation_id, int(raw_run))
                    self._json({**run, "bossSkills": self.server.service.boss_skill_labels(run.get("timeline"), run.get("actors"))})
                else:
                    self._json(service.load(simulation_id))
                return
            if parsed.path.startswith("/api/asset/"):
                parts = [part for part in parsed.path.split("/") if part][2:]
                if len(parts) < 2:
                    raise FileNotFoundError("资源不存在")
                asset = self.server.service.asset(parts[0], parts[1:])
                if asset is None or not asset.is_file():
                    raise FileNotFoundError("资源尚未缓存")
                payload = asset.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mimetypes.guess_type(asset.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(payload)
                return
            self._static(parsed.path)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        except FileNotFoundError as error:
            self._error(error, HTTPStatus.NOT_FOUND)
        except Exception as error:
            self._error(error)

    def _session_stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.server.service.client_opened()
        try:
            while not self.server.stopping.is_set():
                self.wfile.write(b": chimera-ui\n\n")
                self.wfile.flush()
                time.sleep(0.75)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.service.client_closed()

    def do_POST(self) -> None:  # noqa: N802
        if not self._write_authorized():
            self._error(PermissionError("本次界面会话已失效"), HTTPStatus.FORBIDDEN)
            return
        try:
            body = self._body()
            if self.path != "/api/ui-error":
                try:
                    self.server.service.use_strategy_account(body.get("pid"))
                except Exception as error:  # Never block a request on the account lookup.
                    desktop_lifecycle_log(f"strategy_account_failed {type(error).__name__}: {error}")
            if self.path == "/api/ui-error":
                # Only bounded diagnostic strings, never frontend state or the session token.
                fields = {key: re.sub(r"(?i)(token[=:]\s*)[^\s&]+", r"\1[redacted]", str(body.get(key, ""))[:limit])
                          for key, limit in (("kind", 64), ("message", 2000), ("stack", 6000), ("componentStack", 3000))}
                desktop_lifecycle_log("frontend_error " + json.dumps(fields, ensure_ascii=False))
                self._json({"recorded": True})
                return
            if self.path == "/api/config":
                boss_mode = normalize_mode(body.get("bossMode", "chimera"))
                config = body.get("config")
                strategy_id = str(body.get("strategyId") or "") or None
                result = self.server.service.save_strategy(
                    config, boss_mode,
                    strategy_id=strategy_id,
                    expected_revision=body.get("expectedRevision"),
                )
                team_saved = snapshot_requested(body) and self.server.service.remember_strategy_team(
                    body.get("pid"), boss_mode, strategy_id or result["activeStrategyId"])
                self._json({**result, "message": "策略组和队伍数据已保存" if team_saved else "策略组已保存"})
                return
            if self.path == "/api/config/recover":
                self.server.service.recover_strategies(body.get("document"), body.get("bossMode", "chimera"))
                self._json({"message": "已恢复策略；原文件已保留"})
                return
            if self.path == "/api/preferences":
                self._json(self.server.service.save_ui_preferences(body))
                return
            if self.path == "/api/strategy/profile":
                boss_mode = normalize_mode(body.get("bossMode", "chimera"))
                action = str(body.get("action") or "")
                strategy_id = str(body.get("strategyId") or "").strip()
                if action == "create":
                    config = body.get("config")
                    result = self.server.service.create_strategy_profile(
                        config, body.get("name"), boss_mode
                    )
                    message = "新策略组已创建"
                    if snapshot_requested(body) and self.server.service.remember_strategy_team(
                            body.get("pid"), boss_mode, result["activeStrategyId"]):
                        message = "新策略组已创建，队伍数据已保存"
                elif action == "rename":
                    if not strategy_id:
                        raise ValueError("缺少策略组 ID")
                    result = self.server.service.rename_strategy_profile(
                        strategy_id, body.get("name"), boss_mode
                    )
                    message = "策略组已重命名"
                elif action == "select":
                    if not strategy_id:
                        raise ValueError("缺少策略组 ID")
                    result = self.server.service.select_strategy_profile(
                        strategy_id, boss_mode
                    )
                    message = "已切换策略组"
                elif action == "delete":
                    if not strategy_id:
                        raise ValueError("缺少策略组 ID")
                    result = self.server.service.delete_strategy_profile(
                        strategy_id, boss_mode
                    )
                    message = "策略组已删除"
                elif action == "export":
                    document = self.server.service.export_strategy_profile(
                        strategy_id or None, boss_mode
                    )
                    self._json({"document": document})
                    return
                elif action == "import":
                    result = self.server.service.import_strategy_profile(
                        body.get("document"), boss_mode
                    )
                    message = "策略已导入为新的策略组"
                else:
                    raise ValueError("未知的策略组操作")
                self._json({**result, "message": message})
                return
            if self.path == "/api/start":
                pid = body.get("pid")
                if not isinstance(pid, int) or isinstance(pid, bool):
                    raise ValueError("请选择游戏内账户")
                boss_mode = normalize_mode(body.get("bossMode", "chimera"))
                self._json(
                    {"controller": self.server.service.start(pid, boss_mode)}
                )
                return
            if self.path == "/api/stop":
                pid = body.get("pid")
                boss_mode = normalize_mode(body.get("bossMode", "chimera"))
                self.server.service.controller.stop(pid if isinstance(pid, int) else None)
                self._json({"controller": self.server.service.controller.snapshot(boss_mode)})
                return
            if self.path == "/api/chimera-simulation/start":
                self._json({"job": self.server.service.start_simulation(body)})
                return
            if self.path == "/api/chimera-simulation/stop":
                self.server.service.simulations.stop()
                self._json({"job": self.server.service.simulations.status()})
                return
            if self.path == "/api/hydra-simulation/start":
                self._json({"job": self.server.service.start_hydra_simulation(body)})
                return
            if self.path == "/api/hydra-simulation/stop":
                self.server.service.hydra_simulations.stop()
                self._json({"job": self.server.service.hydra_simulations.status()})
                return
            if self.path == "/api/logs/clear":
                boss_mode = normalize_mode(body.get("bossMode", "chimera"))
                self.server.service.controller.clear_logs(boss_mode)
                self._json({"controller": self.server.service.controller.snapshot(boss_mode)})
                return
            self._error(FileNotFoundError("接口不存在"), HTTPStatus.NOT_FOUND)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        except Exception as error:
            self._error(error)

    def _static(self, raw_path: str) -> None:
        relative = unquote(raw_path).lstrip("/") or "index.html"
        candidate = (DIST_DIR / relative).resolve()
        if DIST_DIR.resolve() not in candidate.parents and candidate != DIST_DIR.resolve():
            raise FileNotFoundError("页面不存在")
        if not candidate.is_file():
            candidate = DIST_DIR / "index.html"
        payload = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache" if candidate.name == "index.html" else "public, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)


class ChimeraHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service: ChimeraService, token: str):
        self.service = service
        self.token = token
        self.stopping = threading.Event()
        super().__init__(address, ChimeraHandler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        error = sys.exc_info()[1]
        if isinstance(
            error,
            (BrokenPipeError, ConnectionAbortedError, ConnectionResetError),
        ):
            return
        super().handle_error(request, client_address)


def restore_internal_worker_streams() -> None:
    """Reconnect PyInstaller windowed workers to their inherited pipes."""
    if os.name != "nt":
        return
    try:
        import msvcrt

        get_std_handle = ctypes.windll.kernel32.GetStdHandle
        get_std_handle.argtypes = [ctypes.c_ulong]
        get_std_handle.restype = ctypes.c_void_p
        for attribute, identifier in (("stdout", -11), ("stderr", -12)):
            existing = getattr(sys, attribute)
            if existing is not None:
                reconfigure = getattr(existing, "reconfigure", None)
                if callable(reconfigure):
                    reconfigure(
                        encoding="utf-8",
                        errors="backslashreplace",
                        line_buffering=True,
                        write_through=True,
                    )
                continue
            handle = get_std_handle(identifier & 0xFFFFFFFF)
            if not handle or handle == ctypes.c_void_p(-1).value:
                continue
            descriptor = msvcrt.open_osfhandle(int(handle), os.O_WRONLY)
            stream = open(
                descriptor,
                "w",
                encoding="utf-8",
                errors="replace",
                buffering=1,
                closefd=False,
            )
            setattr(sys, attribute, stream)
    except (OSError, TypeError, ValueError):
        pass


def run_internal_worker(worker: str, arguments: list[str]) -> int:
    """Run controller helpers inside the bundled interpreter."""
    restore_internal_worker_streams()
    original_argv = sys.argv
    sys.argv = [sys.argv[0], *arguments]
    try:
        if worker == "injector":
            from inject_probe import main as worker_main
        elif worker == "controller":
            from chimera_controller import main as worker_main
        else:
            raise ValueError(f"未知内部工作进程：{worker}")
        return int(worker_main())
    finally:
        # Preserve the worker marker for the outer error handler; otherwise a
        # helper failure would be mistaken for desktop startup and show a popup.
        sys.argv = original_argv


def self_test() -> int:
    if not (DIST_DIR / "index.html").is_file():
        raise RuntimeError("React 界面尚未构建")
    service = ChimeraService()
    config = normalized_strategy(service.strategy(), service.strategy())
    assert config["mode"] == "execute"
    assert config["objectives"]["onAllMetAtResult"] == "hold_for_user"
    old_early_retry_config = normalized_strategy(
        {
            **config,
            "objectives": {
                **config["objectives"],
                "mandatoryTrialIds": [8000607],
                "earlyRetryConditions": [
                    {"bossTurnAtLeast": 5, "mode": "any", "trialIds": [8000607]}
                ],
            },
        },
        config,
    )
    assert "earlyRetryConditions" not in old_early_retry_config["objectives"]
    assert old_early_retry_config["objectives"]["mandatoryTrialIds"] == [8000607]
    hydra_config = default_strategy("hydra")
    hydra_retry_config = normalized_strategy(
        {
            **hydra_config,
            "objectives": {
                **hydra_config["objectives"],
                "devourOrderRetryConditions": [
                    {
                        "markIndex": 2,
                        "relation": "isNoneOf",
                        "heroTypeIds": [6200, 9510, 6200],
                    }
                ],
            },
        },
        hydra_config,
        "hydra",
    )
    assert hydra_retry_config["objectives"]["devourOrderRetryConditions"] == [
        {
            "markIndex": 2,
            "relation": "isNoneOf",
            "heroTypeIds": [6200, 9510],
        }
    ]
    assert len(service.ui_catalog.get("difficulties", [])) == 6
    reward_asset = service.asset("reward", ["resource-4100"])
    assert reward_asset is not None and reward_asset.is_file()
    head_ids = {head.get("typeId") for head in service.hydra_heads()}
    assert set(HYDRA_HEAD_TYPE_IDS).issubset(head_ids)
    assert head_ids.isdisjoint(HYDRA_RESERVED_HEAD_TYPE_IDS)
    assert service.update_hydra_heads(
        [
            {
                "typeId": 26300,
                "name": "动态蛇头测试",
                "avatar": "HeroAvatars/26300",
                "skills": [{"typeId": 263001}],
            }
        ]
    )
    assert 26300 in {head.get("typeId") for head in service.hydra_heads()}
    # The bosses' skills in a simulated battle are asked of a compatible running game once, then kept.
    import tempfile
    global read_agent_status
    with tempfile.TemporaryDirectory() as folder:
        real_catalog, real_status = service.boss_skills, read_agent_status
        service.boss_skills = BossSkillCatalog(Path(folder) / "boss-skills.json")
        asked: list[tuple[Any, ...]] = []

        def fake_request(pid: Any, ids: list[int], kind: int, slot: str, what: str,
                         timeout: float = 5.0) -> dict[str, Any]:
            asked.append((pid, list(ids), kind, slot))
            return {"status": "captured", "skills": [
                {"typeId": 1266901, "name": "撞击", "description": "攻击1名敌人。", "defaultCooldown": 0}]}

        service.agent_request = fake_request  # type: ignore[method-assign]
        service.refresh_processes = lambda force=False: [{"pid": 4242}]  # type: ignore[method-assign]
        read_agent_status = lambda pid: {"compatible": True}  # noqa: E731
        try:
            timeline = [{"source": "enemy", "skillTypeId": 1266901}, {"source": "enemy", "skillTypeId": 1266903},
                        {"source": "policy", "skillTypeId": 99501}]
            labels = service.boss_skill_labels(timeline)
            assert labels == {"1266901": {"name": "撞击", "description": "攻击1名敌人。", "defaultCooldown": 0}}
            assert asked == [(4242, [1266901, 1266903], REQUEST_SKILL_TYPES, "team_data")]
            assert service.boss_skill_labels(timeline) == labels and len(asked) == 1
        finally:
            del service.agent_request, service.refresh_processes
            service.boss_skills, read_agent_status = real_catalog, real_status
    # Only the six complete heads are exposed; installed art placeholders are
    # deliberately excluded from the strategy editor.
    for head_type_id in HYDRA_HEAD_TYPE_IDS:
        head_asset = service.asset("head", [str(head_type_id)])
        assert head_asset is not None and head_asset.is_file()
    # A pristine user-data directory intentionally has no account hero catalog
    # until the first agent scan. Seed one representative identity so this
    # assertion tests the bundled fallback rather than pre-existing user data.
    service.hero_catalog[9170] = {
        "typeId": 9170,
        "avatar": "HeroAvatars/9170",
        "runtimeTypeIds": [9176],
    }
    thor_asset = service.asset("hero", ["9170"])
    assert thor_asset is not None and thor_asset.is_file()
    thor_runtime_asset = service.asset("hero", ["9176"])
    assert thor_runtime_asset == thor_asset
    service.controller.append("chimera-log-selftest", "chimera")
    service.controller.append("hydra-log-selftest", "hydra")
    assert service.controller.snapshot("chimera")["logs"] == ["chimera-log-selftest"]
    assert service.controller.snapshot("hydra")["logs"] == ["hydra-log-selftest"]
    service.controller.clear_logs("chimera")
    assert service.controller.snapshot("chimera")["logs"] == []
    assert service.controller.snapshot("hydra")["logs"] == ["hydra-log-selftest"]
    service.controller.clear_logs("hydra")
    import webview

    assert callable(webview.create_window)
    service.client_opened()
    assert service.client_count() == 1
    service.client_closed()
    assert service.client_count() == 0
    service.shutdown()
    assert service.controller.snapshot()["status"] == "已关闭"
    desktop_log("chimera-web-selftest-ok")
    return 0


def desktop_lifecycle_log(event: str, error: Exception | None = None) -> None:
    """Persist desktop failures even when the frozen app has no console."""
    try:
        logger = logging.getLogger("raid.desktop.lifecycle")
        if not logger.handlers:
            path = PROJECT_ROOT / "logs" / "desktop-lifecycle.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        logger.info(event, exc_info=(type(error), error, error.__traceback__) if error else None)
    except (OSError, ValueError):
        pass


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--internal-worker":
        return run_internal_worker(sys.argv[2], sys.argv[3:])
    parser = argparse.ArgumentParser(description="奇美拉 React 策略中心")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-window", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not (DIST_DIR / "index.html").is_file():
        raise RuntimeError("React 界面未构建，请重新运行安装步骤")

    mutex: NamedMutex | None = None
    # The hidden no-window mode exists only for local UI verification.  It may
    # coexist with an already-open user window and never launches a controller.
    if not args.no_window:
        mutex = NamedMutex(r"Local\RaidChimeraTool.Singleton")
        try:
            mutex.acquire()
        except RuntimeError:
            ctypes.windll.user32.MessageBoxW(
                None,
                f"{APP_NAME} 已经在运行，请使用现有窗口。",
                APP_NAME,
                0x40,
            )
            return 4
        if getattr(sys, "frozen", False):
            # Only the single running instance copies, before anything reads the data.
            from data_root import DATA_DIR_NAME, migrate_legacy_data
            if PROJECT_ROOT.name == DATA_DIR_NAME:
                try:
                    migrated = migrate_legacy_data(PROJECT_ROOT)
                except OSError as error:
                    raise RuntimeError(
                        f"无法把旧版本的策略和记录复制到 {PROJECT_ROOT}：{error}\n\n"
                        "旧数据没有改动；释放磁盘空间或检查权限后重新打开即可继续。"
                    ) from error
                if migrated:
                    desktop_lifecycle_log(f"data_migrated from={migrated['from']} files={migrated['files']}")
    service = None
    server = None
    server_thread = None
    startup = WindowStartup()
    try:
        desktop_lifecycle_log("startup_begin")
        token = secrets.token_urlsafe(24)
        service = ChimeraService()
        server = ChimeraHttpServer(("127.0.0.1", 0), service, token)
        port = int(server.server_address[1])
        url = f"http://127.0.0.1:{port}/?token={token}"
        server_thread = threading.Thread(target=server.serve_forever, daemon=True, name="chimera-ui-http")
        server_thread.start()
        if args.no_window:
            desktop_log(url)
            while True:
                time.sleep(0.5)
        import webview

        class WebviewDiagnostics(logging.Handler):
            def emit(self, record):
                desktop_lifecycle_log("webview: " + record.getMessage(), record.exc_info[1] if record.exc_info else None)

        logging.getLogger("pywebview").addHandler(WebviewDiagnostics(level=logging.WARNING))
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        webview.settings["SHOW_DEFAULT_MENUS"] = False
        webview.settings["ALLOW_DOWNLOADS"] = True
        window = webview.create_window(
            f"{APP_NAME} {APP_VERSION}".strip(),
            url,
            width=1380,
            height=860,
            min_size=(1024, 640),
            hidden=True,
            background_color="#07101b",
            text_select=True,
        )

        def closing_window() -> None:
            startup.close()
            service.stopping.set()
            server.stopping.set()
            service.controller.request_shutdown()
            desktop_lifecycle_log("window_close_requested")

        window.events.closing += closing_window
        window.events.closed += startup.close

        def startup_failed(message: str) -> None:
            if startup.closed.is_set():
                return
            desktop_lifecycle_log("window_startup_failed: " + message)
            ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
            try:
                window.destroy()
            except Exception as error:
                desktop_lifecycle_log("failed_window_cleanup", error)

        def reveal_ready_window() -> None:
            startup.reveal(window, url, startup_failed)

        webview.start(
            reveal_ready_window,
            gui="edgechromium",
            debug=False,
            private_mode=True,
            icon=str(APP_ICON) if APP_ICON.is_file() else None,
        )
        return 0
    except Exception as error:
        if startup.closed.is_set():
            desktop_lifecycle_log("initialization_cancelled_by_close", error)
            return 0
        raise
    finally:
        startup.close()
        if server is not None:
            server.stopping.set()
        if service is not None:
            try:
                service.shutdown()
            except Exception as error:
                desktop_lifecycle_log("controller_shutdown_failed", error)
        if server is not None:
            try:
                if server_thread is not None and server_thread.is_alive():
                    server.shutdown()
                server.server_close()
            except Exception as error:
                desktop_lifecycle_log("server_shutdown_failed", error)
        if mutex is not None:
            mutex.close()
        desktop_lifecycle_log("shutdown_complete")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        desktop_lifecycle_log("startup_failed", error)
        if "--internal-worker" in sys.argv and sys.stderr is not None:
            try:
                print(str(error), file=sys.stderr, flush=True)
            except (OSError, ValueError):
                pass
        if not any(flag in sys.argv for flag in ("--self-test", "--no-window", "--internal-worker")):
            ctypes.windll.user32.MessageBoxW(
                None,
                f"{APP_NAME} 启动失败：\n\n{error}",
                APP_NAME,
                0x10,
            )
        raise SystemExit(1)
