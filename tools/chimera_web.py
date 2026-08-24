#!/usr/bin/env python3
"""Local React shell for the Chimera controller.

The HTTP server binds to loopback only. State-changing requests also require a
random token that is generated for each launch and never written to disk.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


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
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            root = Path(local_app_data) / "WFQ-GAFE" / "RaidBossStrategyStudio"
        else:
            root = (
                Path.home()
                / "AppData"
                / "Local"
                / "WFQ-GAFE"
                / "RaidBossStrategyStudio"
            )
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

from agent_ipc import AgentIpc  # noqa: E402
from boss_modes import (  # noqa: E402
    HYDRA_HEAD_TYPE_IDS,
    HYDRA_RESERVED_HEAD_TYPE_IDS,
    MODE_SPECS,
    STRATEGY_STORE,
    active_strategy_id,
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
from chimera_runtime import (  # noqa: E402
    AGENT,
    USER_STRATEGY,
    identify_account,
    lifecycle_team_ids,
    require_expected_account,
    strategy_template,
    worker_command,
)
from chimera_icons import (  # noqa: E402
    cache_visual_asset,
    discover_game_hydra_heads,
    ensure_icon_cache,
    game_hero_asset,
    game_reward_asset,
    game_skill_asset,
    preload_game_visuals,
    runtime_effect_options,
    skill_effect_summary,
)
from controller_pause import signal_controller_pause  # noqa: E402
from inject_probe import seed_battle_context, seed_selection_context  # noqa: E402
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
HERO_CATALOG = PROJECT_ROOT / "cache" / "chimera-hero-catalog.json"
UI_PREFERENCES = PROJECT_ROOT / "config" / "raid-boss-ui-preferences.user.json"
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

        skills: dict[tuple[int, int], dict[str, Any]] = {}
        for source in (previous.get("skills", []), raw.get("skills", [])):
            for skill in source if isinstance(source, list) else []:
                if not isinstance(skill, dict):
                    continue
                form = int(skill.get("formIndex", 0) or 0)
                identity = skill.get("typeId")
                if not isinstance(identity, int):
                    identity = int(skill.get("slot", 0) or 0)
                skills[(form, identity)] = {**skills.get((form, identity), {}), **skill}
        merged["skills"] = list(skills.values())
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


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def utf8_subprocess_environment() -> dict[str, str]:
    """Keep Python worker output decodable regardless of Windows code page."""
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


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


def controller_exit_label(code: int, stop_requested: bool) -> str:
    if code == 6:
        return "安全中断"
    if code == 7:
        return "已免费重整"
    if code == 8:
        return "游戏内暂停"
    if code == 0 and stop_requested:
        return "已暂停"
    if code == 0:
        return "已完成"
    return f"异常停止（代码 {code}）"


class ControllerManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.pid: int | None = None
        self.boss_mode = "chimera"
        self.status = "已停止"
        self.error: str | None = None
        self.logs_by_mode: dict[str, deque[str]] = {
            mode: deque(maxlen=800) for mode in MODE_SPECS
        }
        self.stop_requested = False
        self.preparing = False
        self.closing = False

    def append(self, line: str, boss_mode: str | None = None) -> None:
        line = str(line).rstrip()
        if not line:
            return
        with self.lock:
            mode = normalize_mode(boss_mode or self.boss_mode)
            self.logs_by_mode[mode].append(line)

    def clear_logs(self, boss_mode: str) -> None:
        with self.lock:
            self.logs_by_mode[normalize_mode(boss_mode)].clear()

    def snapshot(self, log_mode: str | None = None) -> dict[str, Any]:
        with self.lock:
            selected_log_mode = normalize_mode(log_mode or self.boss_mode)
            running = self.preparing or (
                self.process is not None and self.process.poll() is None
            )
            return {
                "running": running,
                "status": self.status,
                "pid": self.pid,
                "bossMode": self.boss_mode,
                "logMode": selected_log_mode,
                "logs": list(self.logs_by_mode[selected_log_mode]),
                "error": self.error,
            }

    def start(
        self, pid: int, account_name: str, user_id: int, boss_mode: str
    ) -> None:
        boss_mode = normalize_mode(boss_mode)
        with self.lock:
            if self.closing:
                raise RuntimeError("主工具正在关闭")
            if self.preparing or (self.process is not None and self.process.poll() is None):
                raise RuntimeError("控制器已经在运行")
            self.pid = pid
            self.boss_mode = boss_mode
            self.status = "正在准备代理…"
            self.error = None
            self.stop_requested = False
            self.preparing = True
            self.logs_by_mode[boss_mode].append(
                f"正在为游戏内账户 {account_name} 准备{mode_spec(boss_mode)['label']}接管。"
            )
        threading.Thread(
            target=self._prepare_and_run,
            args=(pid, account_name, user_id, boss_mode),
            daemon=True,
            name="chimera-controller-launch",
        ).start()

    def _run_injector(self, arguments: list[str], timeout: int) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            [*worker_command("injector"), "--pid", str(arguments[0]), "--agent", str(AGENT), *arguments[1:]],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creation_flags,
            env=utf8_subprocess_environment(),
        )
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
        return result, payload if isinstance(payload, dict) else {}

    def _prepare_and_run(
        self, pid: int, account_name: str, user_id: int, boss_mode: str
    ) -> None:
        try:
            require_expected_account(pid, account_name, user_id)
            check, check_payload = self._run_injector([str(pid), "--check-only"], 20)
            if check.returncode:
                raise RuntimeError(check_payload.get("reason") or check.stderr.strip() or "代理检查失败")

            if check_payload.get("agentLoaded") and (
                not check_payload.get("agentCompatible") or not check_payload.get("agentReady")
            ):
                previous_lifecycle: dict[str, Any] = {}
                try:
                    with AgentIpc(pid) as ipc:
                        previous_lifecycle = ipc.lifecycle() or {}
                except (FileNotFoundError, ValueError):
                    pass
                if previous_lifecycle.get("screen") == "result":
                    raise RuntimeError("当前停留在战绩结算画面，不会在此时更新代理")
                self.append("正在只更新所选游戏账户的代理版本…", boss_mode)
                reload_result, reload_payload = self._run_injector([str(pid), "--reload"], 35)
                if reload_result.returncode:
                    raise RuntimeError(
                        reload_payload.get("reason")
                        or reload_result.stderr.strip()
                        or "所选账户代理更新失败"
                    )
                screen = previous_lifecycle.get("screen")
                if screen == "battle":
                    context = (previous_lifecycle.get("battle") or {}).get("context")
                    if isinstance(context, int) and context > 0:
                        if not seed_battle_context(pid, AGENT, context).get("accepted"):
                            raise RuntimeError("更新后恢复当前奇美拉战斗失败")
                elif screen == "team_selection":
                    context = (previous_lifecycle.get("selection") or {}).get("context")
                    if isinstance(context, int) and context > 0:
                        if not seed_selection_context(pid, AGENT, context).get("accepted"):
                            raise RuntimeError("更新后恢复奇美拉队伍界面失败")
                require_expected_account(pid, account_name, user_id)
                check_payload = {"agentLoaded": True, "agentCompatible": True, "agentReady": True}

            if not check_payload.get("agentLoaded"):
                self.append("代理尚未载入，正在载入所选账户…", boss_mode)
                load_result, load_payload = self._run_injector([str(pid)], 35)
                if load_result.returncode:
                    raise RuntimeError(load_payload.get("reason") or load_result.stderr.strip() or "代理载入失败")
                require_expected_account(pid, account_name, user_id)

            require_expected_account(pid, account_name, user_id)
            with self.lock:
                if self.stop_requested:
                    self.status = "已暂停"
                    self.preparing = False
                    return

            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            controller_arguments = [
                    *worker_command("controller"),
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
                    "--boss-mode",
                    boss_mode,
                    "--agent",
                    str(AGENT),
                    "--bootstrap-current",
                    "--execute",
                ]
            controller_arguments.append("--auto-start")
            process = subprocess.Popen(
                controller_arguments,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
                env=utf8_subprocess_environment(),
            )
            with self.lock:
                self.process = process
                self.preparing = False
                self.status = f"正在运行 · {account_name}"
            self.append("控制器已启动。", boss_mode)
            assert process.stdout is not None
            for line in process.stdout:
                self.append(line, boss_mode)
            code = process.wait()
            with self.lock:
                self.status = controller_exit_label(code, self.stop_requested)
                self.logs_by_mode[boss_mode].append(f"控制器已退出，代码 {code}。")
                self.process = None
                self.preparing = False
                self.stop_requested = False
        except Exception as error:
            with self.lock:
                self.error = str(error)
                self.status = "启动失败"
                self.logs_by_mode[boss_mode].append(f"启动失败：{error}")
                self.process = None
                self.preparing = False

    def stop(self, pid: int | None) -> None:
        with self.lock:
            self.stop_requested = True
            self.status = "正在暂停接管…"
            process = self.process
            actual_pid = self.pid
        if process is not None and process.poll() is None:
            target = pid if isinstance(pid, int) else actual_pid
            if not isinstance(target, int) or not signal_controller_pause(target):
                with self.lock:
                    self.stop_requested = False
                    self.status = "暂停信号发送失败"
                raise RuntimeError("暂停信号发送失败；控制器仍保持运行")
            self.append("已请求暂停；正在等待控制器清理接管会话。", self.boss_mode)
            return
        with self.lock:
            if not self.preparing:
                self.status = "已停止"
                self.stop_requested = False

    def shutdown(self, timeout: float = 8.0) -> None:
        with self.lock:
            self.closing = True
            self.stop_requested = True
            self.status = "正在关闭…"
            process = self.process
            target = self.pid

        if process is not None and process.poll() is None:
            if isinstance(target, int):
                signal_controller_pause(target)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)

        with self.lock:
            self.process = None
            self.preparing = False
            self.status = "已关闭"


class ChimeraService:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.visual_preload_lock = threading.RLock()
        self.visual_preload_global_started = False
        self.visual_preload_requested_heroes: set[int] = set()
        self.client_lock = threading.RLock()
        self.first_client = threading.Event()
        self.active_clients = 0
        self.controller = ControllerManager()
        self.processes: dict[int, dict[str, Any]] = {}
        self.last_process_refresh = 0.0
        loaded_hero_catalog = load_hero_catalog({})
        self.hero_catalog = canonicalize_hero_catalog(loaded_hero_catalog)
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
                status = preload_game_visuals(catalog, sorted(pending))
                with self.lock:
                    self.visual_cache_status = status
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
            self.hero_catalog = catalog
            self.hero_catalog_mtime_ns = modified

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
        self.controller.shutdown()

    def strategy_store(self) -> dict[str, Any]:
        return load_strategy_store(STRATEGY_STORE)

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
            "activeStrategyId": selected_id,
            "strategyProfiles": strategy_profiles_for_mode(current_store, boss_mode),
        }

    def save_strategy(
        self, value: Any, boss_mode: str = "chimera",
        strategy_id: str | None = None,
    ) -> dict[str, Any]:
        boss_mode = normalize_mode(boss_mode)
        with self.lock:
            store = self.strategy_store()
            selected_id = str(strategy_id or active_strategy_id(store, boss_mode))
            previous = strategy_for_mode(store, boss_mode, selected_id)
            config = normalized_strategy(value, previous, boss_mode)
            updated = update_mode_strategy(
                store, boss_mode, config, strategy_id=selected_id
            )
            atomic_write_json(STRATEGY_STORE, updated)
            return self.strategy_bundle(boss_mode, updated)

    def config_with_prepared_team(
        self, value: Any, pid: Any, boss_mode: str
    ) -> Any:
        """Attach the currently visible preparation heroes to a strategy."""
        if (
            not isinstance(value, dict)
            or not isinstance(pid, int)
            or isinstance(pid, bool)
        ):
            return value
        live = self.live_state(pid, boss_mode)
        expected_size = int(mode_spec(boss_mode)["teamSize"])
        hero_type_ids = live.get("teamHeroIds")
        if (
            live.get("screen") != "team_selection"
            or not isinstance(hero_type_ids, list)
            or len(hero_type_ids) != expected_size
            or any(
                not isinstance(hero_id, int)
                or isinstance(hero_id, bool)
                or hero_id < 0
                for hero_id in hero_type_ids
            )
        ):
            return value
        selected_type_ids = [hero_id for hero_id in hero_type_ids if hero_id > 0]
        if len(set(selected_type_ids)) != len(selected_type_ids):
            return value
        hero_instance_ids = live.get("teamHeroInstanceIds")
        team = {"heroTypeIds": selected_type_ids}
        if (
            isinstance(hero_instance_ids, list)
            and len(hero_instance_ids) == len(selected_type_ids)
            and all(
                isinstance(hero_id, int)
                and not isinstance(hero_id, bool)
                and hero_id > 0
                for hero_id in hero_instance_ids
            )
            and len(set(hero_instance_ids)) == len(hero_instance_ids)
        ):
            team["heroInstanceIds"] = list(hero_instance_ids)
        return {**value, "team": team}

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
            atomic_write_json(STRATEGY_STORE, updated)
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
            atomic_write_json(STRATEGY_STORE, updated)
            return self.strategy_bundle(boss_mode, updated)

    def select_strategy_profile(
        self, strategy_id: str, boss_mode: str = "chimera"
    ) -> dict[str, Any]:
        boss_mode = normalize_mode(boss_mode)
        with self.lock:
            updated = select_mode_strategy(
                self.strategy_store(), boss_mode, strategy_id
            )
            atomic_write_json(STRATEGY_STORE, updated)
            return self.strategy_bundle(boss_mode, updated)

    def delete_strategy_profile(
        self, strategy_id: str, boss_mode: str = "chimera"
    ) -> dict[str, Any]:
        boss_mode = normalize_mode(boss_mode)
        with self.lock:
            updated = delete_mode_strategy(
                self.strategy_store(), boss_mode, strategy_id
            )
            atomic_write_json(STRATEGY_STORE, updated)
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
        if isinstance(team, dict):
            portable_team = dict(team)
            portable_team.pop("heroInstanceIds", None)
            strategy["team"] = portable_team
        return {
            "format": "raid-boss-strategy",
            "version": 1,
            "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "bossMode": boss_mode,
            "strategy": strategy,
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
            if not isinstance(version, int) or isinstance(version, bool) or version != 1:
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
            atomic_write_json(STRATEGY_STORE, updated)
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
                item["error"] = str(error)
                item["account"] = latest_account_state(int(item["pid"]))
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
            for raw_skill in hero.get("skills", []):
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
                if updated != previous:
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
                previous_by_slot = {
                    int(skill["slot"]): skill
                    for skill in previous.get("skills", [])
                    if isinstance(skill, dict) and isinstance(skill.get("slot"), int)
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
                    cached = previous_by_type.get(type_id, previous_by_slot.get(slot, {}))
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
                skills.extend(
                    dict(skill)
                    for type_id, skill in previous_by_type.items()
                    if type_id not in seen_skill_type_ids
                )
                if not skills:
                    skills = [
                        dict(value)
                        for value in previous.get("skills", [])
                        if isinstance(value, dict)
                    ]
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
                if updated != previous:
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
        self.update_hydra_heads(rotation.get("hydraHeads"))
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
            current_heads: dict[int, dict[str, Any]] = {}
            for value in bosses:
                if not isinstance(value, dict):
                    continue
                head = normalize_hydra_head(value)
                if head.get("dead") is True:
                    continue
                identity = canonical_hydra_head_type_id(head)
                actor_id = head.get("id")
                if not isinstance(identity, int):
                    identity = actor_id if isinstance(actor_id, int) else None
                if identity is None:
                    continue
                # Hydra creates a new actor whenever a head returns.  The UI
                # keeps only the current identity and deliberately carries no
                # HP fields, so old actor records cannot accumulate forever.
                for key in ("healthPct", "health", "currentHealth", "maxHealth"):
                    head.pop(key, None)
                current_heads[identity] = head
            bosses = list(current_heads.values())
            self.update_hydra_heads(bosses)
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
        }

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
        strategy_bundle = self.strategy_bundle(boss_mode)
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
            "heroes": self.heroes(),
            "hydraHeads": self.hydra_heads(),
            "effects": list(self.effect_options),
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
        self.controller.start(pid, name, user_id, boss_mode)
        return self.controller.snapshot(boss_mode)

    def asset(self, kind: str, parts: list[str]) -> Path | None:
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
            hero = self.hero_catalog.get(hero_id, {})
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
        self._json({"error": str(error)}, status)

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
                    raise ValueError("缺少账户")
                pid = int(raw_pid)
                boss_mode = normalize_mode(query.get("mode", ["chimera"])[0])
                store = self.server.service.strategy_store()
                self._json({
                    "state": self.server.service.live_state(pid, boss_mode),
                    "controller": self.server.service.controller.snapshot(boss_mode),
                    # Keep the profile picker current when a strategy is
                    # created or imported outside this already-open window.
                    # The live editor config is deliberately not replaced,
                    # so unsaved rule edits remain intact.
                    "strategyProfiles": strategy_profiles_for_mode(
                        store, boss_mode
                    ),
                    "heroes": self.server.service.heroes(),
                    "hydraHeads": self.server.service.hydra_heads(),
                    "effects": list(self.server.service.effect_options),
                    "difficulties": self.server.service.ui_catalog.get("difficulties", []),
                })
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
            if self.path == "/api/config":
                boss_mode = normalize_mode(body.get("bossMode", "chimera"))
                config = body.get("config")
                if body.get("capturePreparedTeam") is True:
                    config = self.server.service.config_with_prepared_team(
                        config, body.get("pid"), boss_mode
                    )
                result = self.server.service.save_strategy(
                    config, boss_mode,
                    strategy_id=str(body.get("strategyId") or "") or None,
                )
                self._json({**result, "message": "策略组已保存"})
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
                    if body.get("capturePreparedTeam") is True:
                        config = self.server.service.config_with_prepared_team(
                            config, body.get("pid"), boss_mode
                        )
                    result = self.server.service.create_strategy_profile(
                        config, body.get("name"), boss_mode
                    )
                    message = "新策略组已创建"
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
            if self.path == "/api/logs/clear":
                boss_mode = normalize_mode(body.get("bossMode", "chimera"))
                self.server.service.controller.clear_logs(boss_mode)
                self._json({"controller": self.server.service.controller.snapshot(boss_mode)})
                return
            self._error(FileNotFoundError("接口不存在"), HTTPStatus.NOT_FOUND)
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
            if getattr(sys, attribute) is not None:
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
    except (OSError, ValueError):
        pass


def run_internal_worker(worker: str, arguments: list[str]) -> int:
    """Run controller helpers inside the bundled interpreter."""
    restore_internal_worker_streams()
    sys.argv = [sys.argv[0], *arguments]
    if worker == "injector":
        from inject_probe import main as worker_main
    elif worker == "controller":
        from chimera_controller import main as worker_main
    else:
        raise ValueError(f"未知内部工作进程：{worker}")
    return int(worker_main())


def self_test() -> int:
    if not (DIST_DIR / "index.html").is_file():
        raise RuntimeError("React 界面尚未构建")
    service = ChimeraService()
    config = normalized_strategy(service.strategy(), service.strategy())
    assert config["mode"] == "execute"
    assert config["objectives"]["onAllMetAtResult"] == "hold_for_user"
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
                "奇美拉工具已经在运行，请使用现有窗口。",
                "联盟 Boss 策略中心",
                0x40,
            )
            return 4
    token = secrets.token_urlsafe(24)
    service = ChimeraService()
    server = ChimeraHttpServer(("127.0.0.1", 0), service, token)
    port = int(server.server_address[1])
    url = f"http://127.0.0.1:{port}/?token={token}"
    server_thread = threading.Thread(target=server.serve_forever, daemon=True, name="chimera-ui-http")
    server_thread.start()
    try:
        if args.no_window:
            desktop_log(url)
            while True:
                time.sleep(0.5)
        import webview

        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        webview.settings["SHOW_DEFAULT_MENUS"] = False
        webview.settings["ALLOW_DOWNLOADS"] = True
        window = webview.create_window(
            "Alliance Boss Strategy Studio",
            url,
            width=1380,
            height=860,
            min_size=(1024, 640),
            hidden=True,
            background_color="#07101b",
            text_select=True,
        )

        def reveal_ready_window() -> None:
            # Moving a WinForms host while WebView2 is still creating its
            # controller can leave a permanently blank surface.  Build and
            # navigate the hidden window first, then reveal it as soon as the
            # initial document is ready.  If the renderer itself cannot load a
            # local document, fail visibly instead of exposing a blank form.
            if not window.events.loaded.wait(15.0):
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "WebView2 未能完成初始化，请关闭工具后重新打开。",
                    "联盟 Boss 策略中心",
                    0x10,
                )
                try:
                    window.destroy()
                except Exception:
                    pass
                return
            window.show()

            # React mounts before making the slower bootstrap API request.  If
            # the module failed to mount at all, retry the local navigation once
            # rather than leaving the user on a bare background indefinitely.
            time.sleep(2.0)
            try:
                mounted = bool(
                    window.evaluate_js(
                        "document.documentElement.dataset.appMounted === 'true'"
                    )
                )
            except Exception:
                mounted = False
            if mounted:
                return
            try:
                window.load_url(url)
            except Exception:
                pass

        webview.start(
            reveal_ready_window,
            gui="edgechromium",
            debug=False,
            private_mode=True,
        )
        return 0
    finally:
        server.stopping.set()
        try:
            service.shutdown()
        finally:
            server.shutdown()
            server.server_close()
        if mutex is not None:
            mutex.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        if "--self-test" not in sys.argv and "--no-window" not in sys.argv:
            ctypes.windll.user32.MessageBoxW(
                None,
                f"联盟 Boss 策略中心启动失败：\n\n{error}",
                "联盟 Boss 策略中心",
                0x10,
            )
        raise
