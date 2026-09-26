"""Battle-start devour-order forecast for the live Hydra controller.

At the opening player window the agent has already published this battle's
original BattleSetup/BattleSettings JSON. A background thread converts them
with the isolated original runtime, simulates the battle with the running
strategy (``hydra_forecast.run_forecast``) and hands back the predicted mark
order. The live battle keeps running meanwhile; before the forecast is used,
every player window that already happened must match it exactly (turn,
actor, all four RNG words, opening mark). Only then may a predicted
violation of ``devourOrderRetryConditions`` trigger the normal free regroup.
Any failure is reported and leaves the reactive per-mark check in charge.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from typing import Any, Callable

from convert_hydra_replay_source import ConversionError, convert
from hydra_forecast import evaluate_conditions, run_forecast
from hydra_replay_source import ReplaySourceError, validate_replay_source
from raid_processes import is_supported_raid_executable, process_path
from strategy_storage import atomic_write_bytes, atomic_write_json


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
RESOURCE_ROOT = Path(
    os.environ.get("CHIMERA_RESOURCE_ROOT", getattr(sys, "_MEIPASS", PROJECT_ROOT))
).resolve()
PROBE_CANDIDATES = (
    RESOURCE_ROOT / "offline" / "raid_offline_probe.exe",
    PROJECT_ROOT / "build" / "offline-runtime" / "Release" / "raid_offline_probe.exe",
)
STATIC_DATA_ROOT = (Path(os.environ.get("USERPROFILE", str(Path.home())))
                    / "AppData" / "LocalLow" / "Plarium" / "Raid_ Shadow Legends" / "static-data")
WORK_ROOT = PROJECT_ROOT / "cache" / "hydra-forecast"
RUNTIME_ROOT = PROJECT_ROOT / "runtime" / "hydra-forecast"
KEEP_WORK_DIRECTORIES = 12
INPUT_WAIT_SECONDS = 20.0


class ForecastSetupError(RuntimeError):
    """A stable, user-facing reason why no forecast can run."""


def minimum_damage(config: dict[str, Any]) -> float:
    objectives = config.get("objectives")
    value = objectives.get("minimumDamage", 0) if isinstance(objectives, dict) else 0
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def forecast_enabled(config: dict[str, Any]) -> bool:
    objectives = config.get("objectives")
    return (isinstance(objectives, dict)
            and objectives.get("devourOrderForecast") is True
            and (bool(objectives.get("devourOrderRetryConditions")) or minimum_damage(config) > 0))


def damage_text(value: float) -> str:
    return f"{value / 1e8:.1f} 亿"


def probe_source() -> Path:
    for candidate in PROBE_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise ForecastSetupError("未找到离线战斗引擎（raid_offline_probe.exe）")


def game_build_directory(pid: int) -> Path:
    path = process_path(pid)
    if not path or not is_supported_raid_executable(path):
        raise ForecastSetupError("无法确认游戏安装目录")
    return Path(path).resolve().parent


def newest_static_data() -> Path:
    candidates = [item for item in STATIC_DATA_ROOT.glob("*/*") if item.is_file()]
    if not candidates:
        raise ForecastSetupError("未找到游戏本地静态数据缓存")
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


_HASH_CACHE: dict[tuple[str, int, int], str] = {}


def _file_sha256(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _HASH_CACHE:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        _HASH_CACHE[key] = digest.hexdigest()
    return _HASH_CACHE[key]


def ensure_runtime_bundle(probe: Path, build: Path, static_data: Path,
                          root: Path = RUNTIME_ROOT) -> Path:
    """Content-addressed private copy of the engine files beside the probe.

    The probe grants its AppContainer read access to its own directory only,
    so the game files are copied there; the game installation is untouched.
    """
    files = {
        "raid_offline_probe.exe": probe,
        "GameAssembly.dll": build / "GameAssembly.dll",
        "baselib.dll": build / "baselib.dll",
        "il2cpp_data/Metadata/global-metadata.dat":
            build / "Raid_Data" / "il2cpp_data" / "Metadata" / "global-metadata.dat",
        "static-data.msgpack": static_data,
    }
    for source in files.values():
        if not source.is_file():
            raise ForecastSetupError(f"缺少离线引擎文件：{source.name}")
    hashes = {name: _file_sha256(source) for name, source in files.items()}
    key = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("ascii")).hexdigest()[:24]
    target = root / key

    def complete() -> bool:
        try:
            recorded = json.loads((target / "bundle.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return (isinstance(recorded, dict) and recorded.get("files") == hashes
                and all((target / name).is_file() for name in files))

    if complete():
        return target
    staging = root / f".{key}.{os.getpid()}.{int(time.time() * 1000)}"
    (staging / "il2cpp_data" / "Metadata").mkdir(parents=True, exist_ok=True)
    (staging / "il2cpp_data" / "etc").mkdir(parents=True, exist_ok=True)
    try:
        for name, source in files.items():
            shutil.copyfile(source, staging / name)
            if _file_sha256(staging / name) != hashes[name]:
                raise ForecastSetupError(f"离线引擎文件复制校验失败：{name}")
        atomic_write_json(staging / "bundle.json", {
            "schema": 1, "files": hashes,
            "sources": {name: str(source) for name, source in files.items()},
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })
        # Another controller may have finished the same content meanwhile;
        # never remove a complete bundle that a running probe could be using.
        if not complete():
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    for stale in root.iterdir():
        if stale.is_dir() and stale.name != key and not stale.name.startswith("."):
            shutil.rmtree(stale, ignore_errors=True)
    return target


def _prune_work(root: Path) -> None:
    try:
        entries = sorted((item for item in root.iterdir() if item.is_dir()),
                         key=lambda item: item.stat().st_mtime)
    except OSError:
        return
    for stale in entries[:-KEEP_WORK_DIRECTORIES]:
        shutil.rmtree(stale, ignore_errors=True)


def recent_forecasts(limit: int = 5, root: Path | None = None) -> list[dict[str, Any]]:
    """Newest-first summaries of saved battle forecasts for the interface.

    Reads only each battle's small verdict.json; a directory without one is a
    forecast still running (or interrupted before its conclusion).
    """
    root = root or WORK_ROOT
    try:
        folders = [item for item in root.iterdir() if item.is_dir()]
    except OSError:
        return []

    def started(folder: Path) -> int:
        suffix = folder.name.rsplit("-", 1)[-1]
        return int(suffix) if suffix.isdigit() else 0

    summaries: list[dict[str, Any]] = []
    for folder in sorted(folders, key=started, reverse=True)[:limit]:
        summary: dict[str, Any] = {
            "id": folder.name, "battleSetupId": folder.name.rsplit("-", 1)[0],
            "startedAt": time.strftime("%H:%M:%S", time.localtime(started(folder) / 1000)),
            "status": "running",
        }
        try:
            verdict = json.loads((folder / "verdict.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            verdict = None
        if isinstance(verdict, dict):
            for key in ("status", "verdict", "reason", "conclusion", "finishedAt", "checkedWindows",
                        "marks", "horizon", "turn", "elapsedSeconds", "predictedDamage", "minimumDamage"):
                if key in verdict:
                    summary[key] = verdict[key]
        elif (folder / "forecast.json").is_file() or time.time() - started(folder) / 1000 > 600:
            # Finished without a recorded conclusion (older build or the
            # controller stopped first); never show it as still running.
            summary["status"] = "unrecorded"
        summaries.append(summary)
    return summaries


def live_window(state: dict[str, Any]) -> dict[str, Any] | None:
    """The fields of one live player window compared with the forecast."""
    battle = state.get("battle")
    rng = state.get("battleRandom")
    if (not isinstance(battle, dict) or battle.get("waitingForManualCommand") is not True
            or not isinstance(rng, dict) or rng.get("available") is not True
            or not isinstance(rng.get("words"), list) or rng.get("turn") != battle.get("turn")):
        return None
    return {"turn": battle.get("turn"), "playerTurnCount": battle.get("playerTurnCount"),
            "activeHeroId": state.get("activeHeroId"),
            "activeHeroTypeId": state.get("activeHeroTypeId"),
            "words": list(rng["words"])}


def window_difference(forecast: dict[str, Any], window: dict[str, Any]) -> str | None:
    entry = next((item for item in forecast.get("decisions", [])
                  if item.get("turn") == window["turn"]), None)
    if entry is None:
        return f"第 {window['turn']} 回合不在推演的玩家回合中"
    for key, label in (("playerTurnCount", "玩家行动次数"), ("activeHeroId", "行动英雄"),
                       ("activeHeroTypeId", "行动英雄类型")):
        if entry.get(key) != window[key]:
            return f"第 {window['turn']} 回合{label}不同"
    if entry.get("rngBefore") != window["words"]:
        return f"第 {window['turn']} 回合随机状态不同"
    return None


@dataclass
class ForecastJob:
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    work: Path | None = None


@dataclass
class BattleForecast:
    key: tuple[Any, Any]
    status: str = "waiting_input"
    started_at: float = field(default_factory=time.monotonic)
    job: ForecastJob | None = None
    windows: dict[int, dict[str, Any]] = field(default_factory=dict)
    opening_marked_actor: int | None = None
    result: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None = None
    divergence_reported: bool = False
    reason: str | None = None
    conclusion: str | None = None
    finished_at: str | None = None


@dataclass(frozen=True)
class ForecastRetry:
    condition_index: int
    mark_index: int
    relation: str
    expected_hero_type_ids: tuple[int, ...]
    actual_hero_type_id: int
    predicted_sequence: tuple[str, ...]
    apply_turn: int
    mark_limit: int | None = None
    cause: str = "marked"
    predicted_damage: int | None = None
    minimum_damage: float | None = None


REASON_LABELS = {
    "isolated_engine_failed": "离线引擎异常退出",
    "forecast_timeout": "推演超时",
    "isolated_engine_not_policy_driven": "离线引擎版本不匹配",
    "mark_event_streams_disagree": "标记事件记录不一致",
    "policy_stopped": "策略在某个离线回合没有给出可执行动作",
    "policy_command_rejected": "策略动作被原版引擎拒绝",
    "decision_state_unavailable": "离线回合状态读取失败",
    "enemy_ai_command_unavailable": "蛇头行动生成失败",
    "probe_report_missing": "离线引擎没有返回结果",
}


def reason_label(reason: str | None) -> str:
    text = str(reason or "未知原因")
    label = REASON_LABELS.get(text.split(":", 1)[0])
    return f"{label}（{text}）" if label else text


def _names_by_type(state: dict[str, Any]) -> dict[int, str]:
    return {hero.get("typeId"): str(hero.get("name") or f"英雄 {hero.get('typeId')}")
            for hero in state.get("heroes", []) if isinstance(hero, dict)
            and isinstance(hero.get("typeId"), int)}


class HydraForecastMonitor:
    """One forecast per Hydra battle; consulted on every live decision."""

    def __init__(self, emit: Callable[[str], None] | None = None,
                 runner: Callable[..., dict[str, Any]] = run_forecast,
                 converter: Callable[[Path, Path], dict[str, Any]] = convert,
                 bundle_provider: Callable[[int], Path] | None = None,
                 work_root: Path = WORK_ROOT,
                 validator: Callable[..., tuple[dict[str, Any], bytes, bytes]] = validate_replay_source):
        self.emit = emit or (lambda text: print(text, flush=True))
        self.runner = runner
        self.converter = converter
        self.validator = validator
        self.bundle_provider = bundle_provider or (lambda pid: ensure_runtime_bundle(
            probe_source(), game_build_directory(pid), newest_static_data()))
        self.work_root = work_root
        self.battle: BattleForecast | None = None

    def cancel(self) -> None:
        if self.battle and self.battle.job:
            self.battle.job.cancel.set()

    def observe(self, config: dict[str, Any], state: dict[str, Any], *, ipc: Any,
                capability_memory: Any, marked_target: Callable[[dict[str, Any]], Any],
                tracker_armed: bool) -> ForecastRetry | None:
        if not forecast_enabled(config):
            return None
        rng = state.get("battleRandom")
        battle = state.get("battle", {})
        if not isinstance(rng, dict) or not isinstance(battle, dict):
            return None
        key = (state.get("battleGeneration"), rng.get("battleSetupId"))
        if self.battle is None or self.battle.key != key:
            self.cancel()
            self.battle = BattleForecast(key=key)
            opening = battle.get("playerTurnCount") in (0, 1) and tracker_armed
            if not opening:
                self._conclude("not_opening", "六头蛇开局推演：本场不是从开局接管，跳过离线推演；"
                               "仍按实际吞噬标记判定。", "not_opening")
                return None
            marked = marked_target(state)
            self.battle.opening_marked_actor = marked.get("id") if isinstance(marked, dict) else None
        current = self.battle
        window = live_window(state)
        if window is not None and current.status in ("waiting_input", "running", "applied"):
            current.windows.setdefault(window["turn"], window)
        if current.status == "waiting_input":
            self._start(config, state, ipc, capability_memory)
            return None
        if current.status == "running":
            job = current.job
            if job is None or job.thread is None or job.thread.is_alive():
                return None
            return self._apply(config, state)
        if current.status == "applied" and current.result and window is not None \
                and not current.divergence_reported:
            difference = window_difference(current.result, window)
            if difference:
                current.divergence_reported = True
                self.emit(f"六头蛇开局推演：实战已偏离推演（{difference}）；"
                          "此后仅按实际吞噬标记判定。")
        return None

    def _conclude(self, status: str, conclusion: str, reason: str | None = None) -> None:
        """Record, print and persist the battle's final forecast outcome."""
        assert self.battle is not None
        current = self.battle
        current.status = status
        current.reason = reason
        current.conclusion = conclusion
        current.finished_at = time.strftime("%H:%M:%S")
        self.emit(conclusion)
        work = current.job.work if current.job else None
        if work is not None and work.is_dir():
            try:
                atomic_write_json(work / "verdict.json", {
                    "schema": 1, "status": status, "reason": reason,
                    "conclusion": conclusion, "checkedWindows": len(current.windows),
                    "evaluation": current.evaluation,
                    "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    **(self.telemetry() or {}),
                })
            except OSError:
                pass

    def _give_up(self, reason: str) -> None:
        self._conclude("unavailable", f"六头蛇开局推演无法进行：{reason}；本场仍按实际吞噬标记判定。", reason)

    def _start(self, config: dict[str, Any], state: dict[str, Any], ipc: Any,
               capability_memory: Any) -> None:
        assert self.battle is not None
        current = self.battle
        source = ipc.replay_input()
        if not isinstance(source, dict) or source.get("battleGeneration") != state.get("battleGeneration"):
            if time.monotonic() - current.started_at > INPUT_WAIT_SECONDS:
                self._give_up("代理未发布本局开局数据")
            return
        if source.get("status") != "captured":
            self._give_up(f"代理未取得本局开局数据（{source.get('reason') or source.get('status')}）")
            return
        battle = state.get("battle", {})
        if battle.get("playerTurnCount") not in (0, 1):
            self._give_up("开局窗口已过")
            return
        account = ipc.account()
        account_name = account.get("accountName") if isinstance(account, dict) else None
        try:
            provenance, setups, settings = self.validator(
                source, state, account, account_name if isinstance(account_name, str) else "")
        except ReplaySourceError as error:
            if str(error).startswith("opening_") and time.monotonic() - current.started_at <= INPUT_WAIT_SECONDS:
                return  # The opening snapshot may still lack hero models.
            self._give_up(f"开局数据校验失败（{error}）")
            return
        lifecycle = ipc.lifecycle() or {}
        section = lifecycle.get("battle") if isinstance(lifecycle, dict) else None
        team = ({"heroTypeIds": list(section["heroTypeIds"]),
                 "heroIds": list(section.get("heroIds") or provenance["teamHeroIds"])}
                if isinstance(section, dict) and isinstance(section.get("heroTypeIds"), list)
                and len(section["heroTypeIds"]) == 6
                else {"heroTypeIds": provenance["teamHeroTypeIds"],
                      "heroIds": provenance["teamHeroIds"]})
        strategy = copy.deepcopy(config)
        memory = copy.deepcopy(capability_memory)
        pid = state.get("pid")
        job = ForecastJob()

        def work() -> None:
            try:
                bundle = self.bundle_provider(int(pid))
                folder = self.work_root / f"{provenance['battleSetupId']}-{int(time.time() * 1000)}"
                folder.mkdir(parents=True, exist_ok=False)
                job.work = folder
                atomic_write_bytes(folder / "battle-setup.json", setups)
                atomic_write_bytes(folder / "battle-settings.json", settings)
                atomic_write_json(folder / "capture-provenance.json", provenance)
                atomic_write_json(folder / "strategy.json", strategy)
                self.converter(folder, bundle / "raid_offline_probe.exe")
                result = self.runner(bundle / "raid_offline_probe.exe", folder / "packed", strategy,
                                     team_selection=team, capability_memory=memory,
                                     cancel=job.cancel)
                result["bundle"] = bundle.name
                compact = {key: value for key, value in result.items() if key != "engineTurns"}
                atomic_write_json(folder / "forecast.json", compact)
                job.result = result
            except (ForecastSetupError, ConversionError) as error:
                job.error = str(error)
            except Exception as error:  # Never let the forecast thread take down control.
                job.error = f"{type(error).__name__}: {error}"
            finally:
                _prune_work(self.work_root)

        job.thread = threading.Thread(target=work, daemon=True, name="hydra-forecast")
        current.job = job
        current.status = "running"
        self.emit("六头蛇开局推演：已取得本局开局数据，正在后台离线模拟（最多 1000 回合）；"
                  "战斗照常进行。")
        job.thread.start()

    def _apply(self, config: dict[str, Any], state: dict[str, Any]) -> ForecastRetry | None:
        assert self.battle is not None and self.battle.job is not None
        current = self.battle
        job = current.job
        if job.error or not job.result:
            reason = job.error or "没有结果"
            self._conclude("unavailable", f"六头蛇开局推演无法判断：{reason}；本场不据此重整，"
                           "仍按实际吞噬标记判定。", reason)
            return None
        result = job.result
        if result.get("status") != "complete":
            reason = str(result.get("reason"))
            self._conclude("unavailable", f"六头蛇开局推演无法判断：{reason_label(reason)}；"
                           "本场不据此重整，仍按实际吞噬标记判定。", reason)
            return None
        marks = result.get("marks", [])
        if (marks and marks[0].get("applyTurn") == 0
                and current.opening_marked_actor != marks[0].get("actorId")):
            self._conclude("unavailable", "六头蛇开局推演与实战开局标记不一致；本场不据此重整。",
                           "opening_mark_differs")
            return None
        if not current.windows:
            self._conclude("unavailable", "六头蛇开局推演：没有可核对的实战回合；本场不据此重整。",
                           "no_live_windows")
            return None
        for turn in sorted(current.windows):
            difference = window_difference(result, current.windows[turn])
            if difference:
                self._conclude("unavailable", f"六头蛇开局推演与实战不一致（{difference}）；本场不据此重整。",
                               "live_window_differs")
                return None
        current.result = result
        objectives = config.get("objectives", {})
        conditions = objectives.get("devourOrderRetryConditions", []) if isinstance(objectives, dict) else []
        evaluation = evaluate_conditions(conditions, result, minimum_damage(config))
        current.evaluation = evaluation
        names = _names_by_type(state)
        sequence = tuple(names.get(mark["heroTypeId"], f"英雄 {mark['heroTypeId']}") for mark in marks)
        horizon = ("战斗结束" if result.get("horizon") == "battle_finished"
                   else f"第 {result.get('turn')} 回合上限")
        checked = len(current.windows)
        order = " → ".join(f"{index}.{name}" for index, name in enumerate(sequence, 1)) or "无"
        self.emit(f"六头蛇开局推演完成（{result.get('elapsedSeconds')} 秒，推演至{horizon}，"
                  f"已与 {checked} 个实战回合逐项核对一致）：预计吞噬标记顺序 {order}。")
        damage = evaluation.get("damage")
        damage_note = (f"预计整场伤害 {damage_text(damage['predicted'])}（最低要求 {damage_text(damage['minimum'])}）"
                       if damage else None)
        if evaluation["verdict"] == "retry":
            mark_violations = [item for item in evaluation["violations"] if item.get("cause") != "damage"]
            damage_violation = next((item for item in evaluation["violations"] if item.get("cause") == "damage"), None)
            reasons = []
            if mark_violations:
                violation = mark_violations[0]
                name = names.get(violation["actualHeroTypeId"], f"英雄 {violation['actualHeroTypeId']}")
                reasons.append(f"预计第 {violation['markIndex']} 个标记（约第 {violation['applyTurn']} 回合）"
                               f"为“{name}”，违反条件 {violation['conditionIndex'] + 1}")
            if damage_violation:
                reasons.append(f"{damage_note}，未达到")
            self._conclude("applied", f"六头蛇开局推演结论：{'；'.join(reasons)}，执行免费重整。", "retry")
            if not mark_violations:
                return ForecastRetry(
                    condition_index=-1, mark_index=0, relation="damage", expected_hero_type_ids=(),
                    actual_hero_type_id=0, predicted_sequence=sequence,
                    apply_turn=int(result.get("turn") or 0), cause="damage",
                    predicted_damage=damage_violation["predictedDamage"],
                    minimum_damage=damage_violation["minimumDamage"])
            violation = mark_violations[0]
            return ForecastRetry(
                condition_index=violation["conditionIndex"],
                mark_index=violation["markIndex"],
                relation=violation["relation"],
                expected_hero_type_ids=tuple(violation["expectedHeroTypeIds"]),
                actual_hero_type_id=violation["actualHeroTypeId"],
                predicted_sequence=sequence,
                apply_turn=violation["applyTurn"],
                mark_limit=violation.get("markLimit"),
                predicted_damage=damage["predicted"] if damage else None,
                minimum_damage=damage["minimum"] if damage else None,
            )
        unresolved = evaluation.get("unresolved") or []
        suffix = (f"；{len(unresolved)} 个条件的标记次序超出推演范围"
                  if unresolved else "")
        parts = []
        if conditions:
            parts.append(f"吞噬顺序条件在推演范围内均满足{suffix}")
        if damage_note:
            parts.append(f"{damage_note}，已达到")
        self._conclude("applied", f"六头蛇开局推演结论：{'；'.join(parts)}，继续战斗。", "continue")
        return None

    def telemetry(self) -> dict[str, Any] | None:
        current = self.battle
        if current is None:
            return None
        payload: dict[str, Any] = {"status": current.status, "checkedWindows": len(current.windows),
                                   "reason": current.reason, "conclusion": current.conclusion,
                                   "finishedAt": current.finished_at,
                                   "battleSetupId": current.key[1]}
        if current.result:
            payload["marks"] = [{"markIndex": mark["markIndex"], "heroTypeId": mark["heroTypeId"],
                                 "applyTurn": mark["applyTurn"]}
                                for mark in current.result.get("marks", [])]
            payload["horizon"] = current.result.get("horizon")
            payload["predictedDamage"] = current.result.get("hydraDamage")
            payload["turn"] = current.result.get("turn")
            payload["elapsedSeconds"] = current.result.get("elapsedSeconds")
        if current.evaluation:
            payload["verdict"] = current.evaluation.get("verdict")
            damage = current.evaluation.get("damage")
            if damage:
                payload["minimumDamage"] = damage["minimum"]
        return payload
