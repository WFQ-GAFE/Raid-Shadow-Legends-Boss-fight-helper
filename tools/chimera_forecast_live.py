"""Battle-start simulation for the live Chimera takeover.

When a takeover starts at a Chimera opening, the capture monitor saves this
battle's own setup (chimera_capture_live). This monitor then plays the whole
battle once in the isolated original engine with the captured seed and the
running strategy (chimera_simulation.run_simulation), in the background while
the live battle continues. Before the prediction is used, every live player
turn so far must match it (turn, actor, battle RNG words and the submitted
command). A predicted failure — mandatory trials not completed, damage below
the minimum, or rules that leave a hero without an action before the goals
are met — then triggers the configured free regroup at once instead of
playing the battle out. Any failure is reported and leaves the normal
per-turn checks in charge.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Callable

import chimera_controller as controller
from chimera_simulation import run_simulation, summarize_run
from chimera_simulation_service import (BATTLE_FORECAST_PREFIX, BATTLE_FORECAST_ROOT, aggregate,
                                        difficulty_for_stage)
from convert_hydra_replay_source import ConversionError, convert
from hydra_forecast_live import (ForecastSetupError, ensure_runtime_bundle, game_build_directory,
                                 newest_static_data, probe_source)
from strategy_storage import atomic_write_json


KEEP_RECORDS = 20
CAPTURE_WAIT_SECONDS = 30.0
SIMULATION_TIMEOUT_SECONDS = 120.0


def forecast_enabled(config: dict[str, Any]) -> bool:
    objectives = config.get("objectives")
    if not isinstance(objectives, dict) or objectives.get("battleForecast", True) is not True:
        return False
    mandatory = controller.configured_trial_ids(
        objectives.get("mandatoryTrials", objectives.get("mandatoryTrialIds", [])))
    minimum = objectives.get("minimumDamage", 0)
    return bool(mandatory) or (isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and minimum > 0)


STUCK_REASONS = {
    "no_matching_rule": "没有匹配且可执行的规则",
    "rule_command_not_legal": "规则选出的技能或目标不合法",
    "no_progress": "同一回合反复决策而战斗没有推进",
    "engine_rejected_command": "游戏引擎拒绝了规则给出的指令",
}


def damage_text(value: float) -> str:
    return f"{value / 1e4:,.0f} 万"


def _names_by_type(state: dict[str, Any]) -> dict[int, str]:
    return {hero.get("typeId"): str(hero.get("name") or f"英雄 {hero.get('typeId')}")
            for hero in state.get("heroes", []) if isinstance(hero, dict) and isinstance(hero.get("typeId"), int)}


def evaluate_forecast(config: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Whether the predicted battle meets the strategy's goals."""
    summary = summarize_run(result, config)
    objectives = config.get("objectives") if isinstance(config.get("objectives"), dict) else {}
    minimum = objectives.get("minimumDamage", 0)
    minimum = float(minimum) if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) else 0.0
    damage = float(summary.get("damage") or 0.0)
    missing = [item["trialId"] for item in summary.get("mandatory", []) if not item.get("completed")]
    goals_met = not missing and damage >= minimum
    status = result.get("status")
    if status == "stuck":
        verdict = "continue" if goals_met else "retry"
        cause = "stuck"
    elif status == "complete":
        verdict = "continue" if goals_met else "retry"
        cause = None if goals_met else ("mandatory" if missing else "damage")
    else:
        verdict, cause = "unavailable", None
    return {"verdict": verdict, "cause": cause, "goalsMet": goals_met, "missingTrialIds": missing,
            "damage": damage, "minimumDamage": minimum, "bossTurns": summary.get("bossTurns"),
            "stuck": summary.get("stuck"), "summary": summary}


def live_window(state: dict[str, Any]) -> dict[str, Any] | None:
    battle = state.get("battle")
    if not isinstance(battle, dict) or not isinstance(battle.get("turn"), int):
        return None
    rng = state.get("battleRandom")
    words = (list(rng["words"]) if isinstance(rng, dict) and rng.get("available") is True
             and isinstance(rng.get("words"), list) and rng.get("turn") == battle.get("turn") else None)
    return {"turn": battle["turn"], "playerTurnCount": battle.get("playerTurnCount"),
            "activeHeroId": state.get("activeHeroId"), "words": words}


def window_difference(result: dict[str, Any], window: dict[str, Any], command: dict[str, Any] | None) -> str | None:
    turn = window["turn"]
    decision = next((item for item in result.get("decisions", []) if item.get("turn") == turn), None)
    if decision is None:
        return f"第 {turn} 回合不在模拟的玩家回合中"
    if decision.get("playerTurnCount") != window["playerTurnCount"]:
        return f"第 {turn} 回合玩家行动次数不同"
    if decision.get("activeHeroId") != window["activeHeroId"]:
        return f"第 {turn} 回合行动英雄不同"
    if window["words"] is not None:
        action = next((item for item in (result.get("engine") or {}).get("actions", [])
                       if item.get("turn") == turn and item.get("source") != "enemy"), None)
        if action is not None and list(action.get("rng") or []) != window["words"]:
            return f"第 {turn} 回合随机状态不同"
    if command is not None and decision.get("status") == "command" and (
            decision.get("skillTypeId") != command["skillTypeId"] or decision.get("targetId") != command["targetId"]):
        return f"第 {turn} 回合提交的技能或目标不同"
    return None


@dataclass(frozen=True)
class ChimeraForecastRetry:
    cause: str
    behavior: str
    message: str
    record_id: str | None


@dataclass
class _Job:
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    record: Path | None = None


@dataclass
class _Battle:
    generation: int
    status: str = "waiting_capture"
    started_at: float = field(default_factory=time.monotonic)
    strategy: dict[str, Any] | None = None
    memory: Any = None
    job: _Job | None = None
    windows: dict[int, dict[str, Any]] = field(default_factory=dict)
    commands: dict[int, dict[str, Any]] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None = None
    divergence_reported: bool = False
    reason: str | None = None
    conclusion: str | None = None
    finished_at: str | None = None


def _prune(root: Path) -> None:
    try:
        entries = sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime)
    except OSError:
        return
    for stale in entries[:-KEEP_RECORDS]:
        shutil.rmtree(stale, ignore_errors=True)


class ChimeraForecastMonitor:
    """One battle-start simulation per Chimera battle; consulted on every live decision."""

    def __init__(self, emit: Callable[[str], None] | None = None,
                 runner: Callable[..., dict[str, Any]] = run_simulation,
                 converter: Callable[[Path, Path], dict[str, Any]] = convert,
                 bundle_provider: Callable[[int], Path] | None = None,
                 work_root: Path = BATTLE_FORECAST_ROOT,
                 clock: Callable[[], float] = time.monotonic):
        self.emit = emit or (lambda text: print(text, flush=True))
        self.runner = runner
        self.converter = converter
        self.bundle_provider = bundle_provider or (lambda pid: ensure_runtime_bundle(
            probe_source(), game_build_directory(pid), newest_static_data()))
        self.work_root = work_root
        self.clock = clock
        self.battle: _Battle | None = None

    def cancel(self) -> None:
        if self.battle and self.battle.job:
            self.battle.job.cancel.set()

    def observe(self, config: dict[str, Any], state: dict[str, Any], *, capture: Any,
                capability_memory: Any) -> ChimeraForecastRetry | None:
        generation = state.get("battleGeneration")
        if type(generation) is not int or generation <= 0 or not forecast_enabled(config):
            return None
        if self.battle is None or self.battle.generation != generation:
            self.cancel()
            self.battle = _Battle(generation=generation)
            battle = state.get("battle") if isinstance(state.get("battle"), dict) else {}
            if battle.get("playerTurnCount") not in (0, 1):
                self._conclude("not_opening", "奇美拉开局模拟：本场不是从开局接管，跳过整场模拟。", "not_opening")
                return None
            # What the controller decides with from the opening on.
            self.battle.strategy = copy.deepcopy(config)
            self.battle.memory = copy.deepcopy(capability_memory)
        current = self.battle
        window = live_window(state)
        if window is not None and current.status in ("waiting_capture", "running", "applied"):
            current.windows.setdefault(window["turn"], window)
        if current.status == "waiting_capture":
            self._start(state, capture)
            return None
        if current.status == "running":
            job = current.job
            if job is None or job.thread is None or job.thread.is_alive():
                return None
            return self._apply(state)
        if current.status == "applied" and current.result and window is not None \
                and not current.divergence_reported:
            difference = window_difference(current.result, window, current.commands.get(window["turn"]))
            if difference:
                current.divergence_reported = True
                self.emit(f"奇美拉开局模拟：实战已偏离模拟（{difference}）；此后按实际战况判断。")
        return None

    def observe_command(self, state: dict[str, Any], *, skill_type_id: Any, target_id: Any) -> None:
        current = self.battle
        battle = state.get("battle") if isinstance(state.get("battle"), dict) else {}
        if current is None or current.generation != state.get("battleGeneration") \
                or not isinstance(battle.get("turn"), int):
            return
        command = {"skillTypeId": skill_type_id, "targetId": target_id}
        current.commands.setdefault(battle["turn"], command)
        if current.status == "applied" and current.result and not current.divergence_reported:
            window = current.windows.get(battle["turn"])
            difference = window_difference(current.result, window, command) if window else None
            if difference:
                current.divergence_reported = True
                self.emit(f"奇美拉开局模拟：实战已偏离模拟（{difference}）；此后按实际战况判断。")

    def _conclude(self, status: str, conclusion: str, reason: str | None = None) -> None:
        assert self.battle is not None
        current = self.battle
        current.status = status
        current.reason = reason
        current.conclusion = conclusion
        current.finished_at = time.strftime("%H:%M:%S")
        self.emit(conclusion)
        record = current.job.record if current.job else None
        if record is not None and record.is_dir():
            try:
                summary = json.loads((record / "summary.json").read_text(encoding="utf-8"))
                summary["verdict"] = {"status": status, "reason": reason, "conclusion": conclusion,
                                      "checkedTurns": len(current.windows), "finishedAt": current.finished_at,
                                      **{key: value for key, value in (current.evaluation or {}).items()
                                         if key != "summary"}}
                atomic_write_json(record / "summary.json", summary)
            except (OSError, json.JSONDecodeError):
                pass

    def _give_up(self, reason: str) -> None:
        self._conclude("unavailable", f"奇美拉开局模拟无法进行：{reason}；本场按实际战况判断。", reason)

    def _start(self, state: dict[str, Any], capture: Any) -> None:
        assert self.battle is not None
        current = self.battle
        folder = getattr(capture, "folder", None)
        capture_status = getattr(capture, "status", None)
        if getattr(capture, "generation", None) != current.generation or capture_status == "waiting":
            if self.clock() - current.started_at > CAPTURE_WAIT_SECONDS:
                self._give_up("开局数据未保存")
            return
        if capture_status != "saved" or not isinstance(folder, Path):
            self._give_up(f"开局数据未保存（{getattr(capture, 'reason', None) or capture_status}）")
            return
        opening = getattr(capture, "opening", None) or {}
        static_state = dict(getattr(capture, "static_payload", None) or {})
        if isinstance(opening.get("rotationIdentity"), dict):
            static_state["rotationIdentity"] = opening["rotationIdentity"]
        start_selection = opening.get("chimeraStartSelection")
        strategy = current.strategy or {}
        memory = current.memory
        pid = state.get("pid")
        job = _Job()

        def work() -> None:
            try:
                provenance = json.loads((folder / "capture-provenance.json").read_text(encoding="utf-8"))
                bundle = self.bundle_provider(int(pid))
                probe = bundle / "raid_offline_probe.exe"
                if not (folder / "packed" / "conversion-report.json").is_file():
                    shutil.rmtree(folder / "packed", ignore_errors=True)
                    self.converter(folder, probe)
                team = {"heroTypeIds": provenance.get("teamHeroTypeIds") or [],
                        "heroIds": provenance.get("teamHeroIds") or []}
                result = self.runner(probe, folder / "packed", strategy, capability_memory=memory,
                                     static_state=static_state, team_selection=team,
                                     start_selection=start_selection if isinstance(start_selection, dict) else None,
                                     timeout_seconds=SIMULATION_TIMEOUT_SECONDS, cancel=job.cancel)
                job.result = result
                job.record = self._save(strategy, provenance, folder.name, result)
            except (ForecastSetupError, ConversionError, OSError, ValueError) as error:
                job.error = str(error)
            except Exception as error:  # Never let the forecast thread take down control.
                job.error = f"{type(error).__name__}: {error}"

        job.thread = threading.Thread(target=work, daemon=True, name="chimera-forecast")
        current.job = job
        current.status = "running"
        self.emit("奇美拉开局模拟：已取得本局开局数据，正在后台模拟整场战斗；战斗照常进行。")
        job.thread.start()

    def _save(self, strategy: dict[str, Any], provenance: dict[str, Any], capture_id: str,
              result: dict[str, Any]) -> Path | None:
        """Saved like a strategy simulation, so the report view can open it."""
        record_id = BATTLE_FORECAST_PREFIX + time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        folder = self.work_root / record_id
        try:
            folder.mkdir(parents=True, exist_ok=False)
            run = summarize_run(result, strategy)
            run["index"] = 1
            atomic_write_json(folder / "strategy.json", strategy)
            atomic_write_json(folder / "run-01.json", result)
            atomic_write_json(folder / "summary.json", {
                "schema": 1, "id": record_id, "kind": "battle", "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "complete",
                "strategy": {"id": strategy.get("id"), "name": strategy.get("name"),
                             "rules": len(strategy.get("rules") or [])},
                "capture": {"id": capture_id, "stageId": provenance.get("stageId"), "seed": provenance.get("seed"),
                            "teamHeroTypeIds": provenance.get("teamHeroTypeIds"),
                            "bossHeroTypeId": provenance.get("bossHeroTypeId"),
                            "difficulty": difficulty_for_stage(provenance.get("stageId"))},
                "runs": [run], "aggregate": aggregate([run], strategy)})
        except OSError:
            return None
        finally:
            _prune(self.work_root)
        return folder

    def _apply(self, state: dict[str, Any]) -> ChimeraForecastRetry | None:
        assert self.battle is not None and self.battle.job is not None
        current = self.battle
        job = current.job
        if job.error or not job.result:
            self._give_up(job.error or "没有结果")
            return None
        result = job.result
        strategy = current.strategy or {}
        evaluation = evaluate_forecast(strategy, result)
        current.evaluation = evaluation
        if evaluation["verdict"] == "unavailable":
            self._give_up(f"模拟没有完成（{result.get('reason')}）")
            return None
        if not current.windows:
            self._give_up("没有可核对的实战回合")
            return None
        for turn in sorted(current.windows):
            difference = window_difference(result, current.windows[turn], current.commands.get(turn))
            if difference:
                self._conclude("unavailable", f"奇美拉开局模拟与实战不一致（{difference}）；本场不据此重整。",
                               "live_turn_differs")
                return None
        current.result = result
        record_id = job.record.name if job.record else None
        checked = len(current.windows)
        missing = evaluation["missingTrialIds"]
        damage_note = (f"预计伤害 {damage_text(evaluation['damage'])}"
                       + (f"（最低要求 {damage_text(evaluation['minimumDamage'])}）"
                          if evaluation["minimumDamage"] > 0 else ""))
        trial_note = ("必要试炼全部完成" if not missing
                      else "必要试炼未完成：" + ", ".join(map(str, missing)))
        stuck = evaluation.get("stuck") if isinstance(evaluation.get("stuck"), dict) else None
        stuck_note = None
        if stuck:
            type_id = stuck.get("activeHeroTypeId")
            hero = _names_by_type(state).get(type_id, f"英雄 {type_id}")
            stuck_note = (f"模拟在 Boss 第 {stuck.get('bossTurns')} 回合卡住：“{hero}”"
                          f"{STUCK_REASONS.get(stuck.get('reason'), stuck.get('reason'))}")
        header = (f"奇美拉开局模拟完成（{result.get('elapsedSeconds')} 秒，已与 {checked} 个实战回合逐项核对一致）：")
        parts = [part for part in (stuck_note, trial_note, damage_note) if part]
        if evaluation["verdict"] == "continue":
            self._conclude("applied", header + "；".join(parts) + "，继续战斗。", "continue")
            return None
        objectives = strategy.get("objectives") if isinstance(strategy.get("objectives"), dict) else {}
        behavior = objectives.get("onMandatoryTrialImpossible", "free_regroup_and_retry_manual")
        message = header + "；".join(parts) + "，预计无法完成目标，执行免费重整。"
        self._conclude("applied", message, "retry")
        return ChimeraForecastRetry(cause=evaluation["cause"] or "goals", behavior=str(behavior),
                                    message=message, record_id=record_id)

    def telemetry(self) -> dict[str, Any] | None:
        current = self.battle
        if current is None:
            return None
        payload: dict[str, Any] = {"status": current.status, "reason": current.reason,
                                   "conclusion": current.conclusion, "finishedAt": current.finished_at,
                                   "checkedTurns": len(current.windows), "battleGeneration": current.generation}
        if current.job and current.job.record:
            payload["recordId"] = current.job.record.name
        evaluation = current.evaluation
        if evaluation:
            payload.update({key: evaluation.get(key) for key in
                            ("verdict", "cause", "goalsMet", "missingTrialIds", "damage", "minimumDamage", "bossTurns")})
            stuck = evaluation.get("stuck")
            if isinstance(stuck, dict):
                payload["stuck"] = {key: stuck.get(key) for key in
                                    ("reason", "turn", "bossTurns", "form", "activeHeroTypeId")}
        return payload
