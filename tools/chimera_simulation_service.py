"""Strategy simulations for the desktop app: captures, runs, saved reports.

A simulation takes one saved Chimera capture (cache/chimera-capture/...), the
strategy the player is editing, and runs the battle in the isolated original
engine several times: first with the captured seed (that exact battle), then
with reproducible other seeds. Results are saved under
cache/chimera-simulations/<id>/ and summarized for the interface.
"""
from __future__ import annotations

import copy
import gzip
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import chimera_controller as controller
from chimera_simulation import action_window, form_window, run_simulation, summarize_run
from convert_hydra_replay_source import ConversionError, convert
from hydra_forecast_live import (ForecastSetupError, RUNTIME_ROOT, ensure_runtime_bundle, game_build_directory,
                                 newest_static_data, probe_source)
from strategy_storage import atomic_write_json


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
CAPTURE_ROOT = PROJECT_ROOT / "cache" / "chimera-capture"
SIMULATION_ROOT = PROJECT_ROOT / "cache" / "chimera-simulations"
# Whole-battle simulations the takeover runs at each Chimera opening
# (chimera_forecast_live), saved in the same format.
BATTLE_FORECAST_ROOT = PROJECT_ROOT / "cache" / "chimera-battle-forecasts"
BATTLE_FORECAST_PREFIX = "battle-"
KEEP_SIMULATIONS = 20
MAX_RUNS = 20
PARALLEL_RUNS = 3
FORM_NAMES = {0: "Ultimate", 1: "Ram", 2: "Lion", 3: "Viper"}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _first_state(folder: Path) -> dict[str, Any] | None:
    try:
        with gzip.open(folder / "decision-states.jsonl.gz", "rt", encoding="utf-8") as stream:
            line = stream.readline()
        value = json.loads(line) if line else None
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, EOFError):
        return None


def difficulty_for_stage(stage_id: object) -> int | None:
    # Chimera stages are 130<rotation>900<difficulty>, e.g. 13029006.
    return stage_id % 10 if isinstance(stage_id, int) and 13000000 < stage_id < 14000000 else None


def list_captures(limit: int = 20, root: Path | None = None) -> list[dict[str, Any]]:
    """Saved Chimera captures that a simulation can use, newest first."""
    base = root or CAPTURE_ROOT
    try:
        folders = sorted((item for item in base.iterdir() if item.is_dir()),
                         key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return []
    result = []
    for folder in folders:
        provenance = _read_json(folder / "capture-provenance.json")
        if not isinstance(provenance, dict) or provenance.get("type") != "verified_chimera_replay_source":
            continue
        strategy = _read_json(folder / "strategy.json")
        result.append({
            "id": folder.name,
            "capturedAt": time.strftime("%Y-%m-%d %H:%M", time.localtime(folder.stat().st_mtime)),
            "stageId": provenance.get("stageId"),
            "difficulty": difficulty_for_stage(provenance.get("stageId")),
            "seed": provenance.get("seed"),
            "teamHeroTypeIds": provenance.get("teamHeroTypeIds") or [],
            "teamHeroIds": provenance.get("teamHeroIds") or [],
            "bossHeroTypeId": provenance.get("bossHeroTypeId"),
            "strategyName": strategy.get("name") if isinstance(strategy, dict) else None,
        })
        if len(result) >= limit:
            break
    return result


def derived_seeds(captured_seed: int, count: int) -> list[int]:
    """The captured seed first, then reproducible other 32-bit seeds."""
    generator = random.Random(captured_seed)
    seeds = [captured_seed]
    while len(seeds) < count:
        value = generator.getrandbits(32) - 0x80000000
        if value not in seeds:
            seeds.append(value)
    return seeds


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def aggregate(summaries: list[dict[str, Any]], strategy: dict[str, Any]) -> dict[str, Any]:
    """Across runs: trials, damage, deaths, rules, stuck runs, regroup points.

    A stuck run counts for trials, deaths and rules up to where it stopped
    (the live takeover would have stalled there too); damage is compared only
    over runs that reached the end of the battle.
    """
    finished = [item for item in summaries if item.get("status") in ("complete", "partial", "stuck")]
    battle_ends = [item for item in finished if item.get("status") != "stuck"]
    objectives = strategy.get("objectives") if isinstance(strategy.get("objectives"), dict) else {}
    mandatory = [int(item) for item in controller.configured_trial_ids(
        objectives.get("mandatoryTrials", objectives.get("mandatoryTrialIds", [])))]
    trial_ids = set(mandatory)
    for item in finished:
        trial_ids.update(int(key) for key in item.get("completedTrials", {}))
        for window in item.get("progressByWindow", {}).values():
            trial_ids.update(int(key) for key, value in window.items() if value > 0)
    trials = []
    for trial_id in sorted(trial_ids):
        completed_turns = [item["completedTrials"][str(trial_id)] if str(trial_id) in item["completedTrials"]
                           else item["completedTrials"].get(trial_id) for item in finished]
        completed_turns = [turn for turn in completed_turns if isinstance(turn, int)]
        best = []
        windows: dict[str, list[float]] = {}
        seen_windows = {window for item in finished for window, values in item.get("progressByWindow", {}).items()
                        if str(trial_id) in values}
        for item in finished:
            ratios = [values.get(str(trial_id), 0.0) for values in item.get("progressByWindow", {}).values()]
            done = str(trial_id) in item.get("completedTrials", {}) or trial_id in item.get("completedTrials", {})
            best.append(1.0 if done else max(ratios, default=0.0))
            # Every run counts in each window (0 where it made no progress),
            # so a window's median is comparable with the completion rate.
            completed_turn = item.get("completedTrials", {}).get(str(trial_id), item.get("completedTrials", {}).get(trial_id))
            for window in seen_windows:
                value = item.get("progressByWindow", {}).get(window, {}).get(str(trial_id), 0.0)
                if isinstance(completed_turn, int) and form_window(completed_turn) <= int(window):
                    value = 1.0
                windows.setdefault(window, []).append(value)
        trials.append({
            "trialId": trial_id, "mandatory": trial_id in mandatory,
            "completedRuns": len(completed_turns), "runs": len(finished),
            "completedBossTurnMedian": _median([float(turn) for turn in completed_turns]),
            "bestRatioMedian": _median(best), "bestRatioMax": max(best, default=0.0),
            "windows": {window: {"median": _median(values), "max": max(values)}
                        for window, values in sorted(windows.items(), key=lambda pair: int(pair[0]))},
        })
    deaths: dict[int, list[int]] = {}
    for item in finished:
        seen: set[int] = set()
        for death in item.get("deaths", []):
            type_id = death.get("heroTypeId")
            if isinstance(type_id, int) and type_id not in seen:
                seen.add(type_id)
                deaths.setdefault(type_id, []).append(death.get("bossTurn") or 0)
    rules: dict[str, dict[str, Any]] = {}
    total_player_damage = 0.0
    for item in finished:
        for rule in item.get("rules", []):
            key = f"index:{rule['ruleIndex']}" if rule.get("ruleIndex") is not None else f"name:{rule.get('rule')}"
            entry = rules.setdefault(key, {"ruleIndex": rule.get("ruleIndex"), "rule": rule.get("rule"),
                                           "runsUsed": 0, "uses": 0, "damage": 0.0, "trialGains": {}})
            entry["runsUsed"] += 1
            entry["uses"] += rule.get("uses", 0)
            entry["damage"] += rule.get("damage", 0.0)
            total_player_damage += rule.get("damage", 0.0)
            for trial, gain in rule.get("trialGains", {}).items():
                entry["trialGains"][trial] = entry["trialGains"].get(trial, 0.0) + gain
    count = max(len(finished), 1)
    rule_list = []
    configured = strategy.get("rules") if isinstance(strategy.get("rules"), list) else []
    for index, rule in enumerate(configured, 1):
        entry = rules.pop(f"index:{index}", None) or {"ruleIndex": index, "runsUsed": 0, "uses": 0,
                                                       "damage": 0.0, "trialGains": {}}
        entry["rule"] = str(rule.get("name") or f"规则 {index}") if isinstance(rule, dict) else f"规则 {index}"
        rule_list.append(entry)
    rule_list.extend(rules.values())
    for entry in rule_list:
        entry["usesPerRun"] = round(entry["uses"] / count, 2)
        entry["damageShare"] = round(entry["damage"] / total_player_damage, 4) if total_player_damage else 0.0
        entry["trialGains"] = {trial: round(gain / count, 4) for trial, gain in entry["trialGains"].items()}
        entry.pop("damage", None)
    stuck_runs = [{"index": item.get("index"), "seed": item.get("seed"), "exact": item.get("exact"),
                   **{key: item["stuck"].get(key) for key in ("reason", "turn", "bossTurns", "form",
                                                              "activeHeroTypeId", "rule")}}
                  for item in finished if item.get("status") == "stuck" and isinstance(item.get("stuck"), dict)]
    regroup: dict[str, dict[str, Any]] = {}
    for item in finished:
        for event in item.get("objectiveEvents", []):
            entry = regroup.setdefault(event["event"], {"event": event["event"], "runs": 0, "bossTurns": [],
                                                         "trialIds": set()})
            entry["runs"] += 1
            if isinstance(event.get("bossTurn"), int):
                entry["bossTurns"].append(event["bossTurn"])
            entry["trialIds"].update(event.get("trialIds", []))
    damage = [float(item.get("damage") or 0) for item in battle_ends]
    boss_damage = [float(item.get("bossDamageTaken") or 0) for item in battle_ends]
    return {
        "runs": len(summaries), "finishedRuns": len(finished), "battleEndRuns": len(battle_ends),
        "allMandatoryRuns": sum(1 for item in finished
                                if all(entry.get("completed") for entry in item.get("mandatory", []))),
        "mandatoryTrialIds": mandatory,
        "minimumDamage": objectives.get("minimumDamage") if isinstance(objectives.get("minimumDamage"), (int, float)) else 0,
        "trials": trials,
        "damage": {"min": min(damage, default=0.0), "median": _median(damage), "max": max(damage, default=0.0)},
        "bossDamage": {"min": min(boss_damage, default=0.0), "median": _median(boss_damage),
                       "max": max(boss_damage, default=0.0)},
        "deaths": [{"heroTypeId": type_id, "runs": len(turns), "firstBossTurnMedian": _median([float(t) for t in turns])}
                   for type_id, turns in sorted(deaths.items(), key=lambda pair: -len(pair[1]))],
        "rules": rule_list,
        "stuckRuns": stuck_runs,
        "reservationReleasesPerRun": round(sum(item.get("reservationReleases") or 0 for item in finished) / count, 2),
        "regroupEvents": [{"event": entry["event"], "runs": entry["runs"],
                           "bossTurnMedian": _median([float(t) for t in entry["bossTurns"]]),
                           "trialIds": sorted(entry["trialIds"])} for entry in regroup.values()],
    }


def run_timeline(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Every action of one run with its rule and effects, for the action log."""
    engine = result.get("engine") or {}
    trials = {trial["id"]: trial for trial in engine.get("trials", []) if isinstance(trial, dict)}
    decisions = result.get("decisions") or []
    rows = []
    player_index = 0
    for action in engine.get("actions", []):
        decision: dict[str, Any] = {}
        if action.get("source") != "enemy":
            decision = decisions[player_index] if player_index < len(decisions) else {}
            player_index += 1
        changes = []
        for trial_id, before, after, counter_before, counter_after, started, completed in action.get("trials") or []:
            target = trials.get(trial_id, {}).get("targetRaw") or 0
            changes.append({"trialId": trial_id,
                            "before": round(before / target, 4) if target else None,
                            "after": round(after / target, 4) if target else None,
                            "counterBefore": counter_before, "counterAfter": counter_after,
                            "started": bool(started), "completed": bool(completed)})
        boss_turns = action.get("bossTurns") or 0
        rows.append({
            "turn": action.get("turn"), "bossTurns": boss_turns, "window": action_window(action),
            "form": action.get("form"), "actorId": action.get("actorId"), "actorTypeId": action.get("actorTypeId"),
            "source": action.get("source"), "skillTypeId": action.get("skillTypeId"), "targetId": action.get("targetId"),
            "damage": round(float(action.get("damage") or 0)), "trials": changes,
            "deaths": action.get("deaths") or [],
            "rule": decision.get("rule"), "ruleIndex": decision.get("ruleIndex"),
            "reservationReleased": decision.get("reservationReleased") is True,
        })
    return rows


def _prune(root: Path) -> None:
    try:
        entries = sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime)
    except OSError:
        return
    for stale in entries[:-KEEP_SIMULATIONS]:
        shutil.rmtree(stale, ignore_errors=True)


def recent_simulations(limit: int = 10, root: Path | None = None) -> list[dict[str, Any]]:
    base = root or SIMULATION_ROOT
    try:
        folders = sorted((item for item in base.iterdir() if item.is_dir()),
                         key=lambda item: item.stat().st_mtime, reverse=True)[:limit]
    except OSError:
        return []
    result = []
    for folder in folders:
        summary = _read_json(folder / "summary.json")
        if isinstance(summary, dict):
            aggregate_value = summary.get("aggregate") or {}
            result.append({"id": folder.name, "createdAt": summary.get("createdAt"),
                           "strategyName": summary.get("strategy", {}).get("name"),
                           "captureId": summary.get("capture", {}).get("id"),
                           "status": summary.get("status"), "runs": aggregate_value.get("runs"),
                           "allMandatoryRuns": aggregate_value.get("allMandatoryRuns"),
                           "finishedRuns": aggregate_value.get("finishedRuns"),
                           "stuckRuns": len(aggregate_value.get("stuckRuns") or []),
                           **({"verdict": summary["verdict"]} if isinstance(summary.get("verdict"), dict) else {})})
    return result


class SimulationService:
    """One simulation at a time, run in the background of the desktop app."""

    def __init__(self, capture_root: Path = CAPTURE_ROOT, simulation_root: Path = SIMULATION_ROOT,
                 runner: Callable[..., dict[str, Any]] = run_simulation,
                 bundle_provider: Callable[[int | None], Path] | None = None,
                 battle_forecast_root: Path = BATTLE_FORECAST_ROOT):
        self.capture_root = capture_root
        self.simulation_root = simulation_root
        self.battle_forecast_root = battle_forecast_root
        self.runner = runner
        self.bundle_provider = bundle_provider or self._bundle
        self.lock = threading.Lock()
        self.job: dict[str, Any] | None = None
        self.cancel = threading.Event()

    @staticmethod
    def _bundle(pid: int | None) -> Path:
        build = None
        if isinstance(pid, int):
            try:
                build = game_build_directory(pid)
            except Exception:
                build = None
        if build is None:
            # The game may be closed: reuse the installation the last engine
            # bundle was copied from.
            for recorded in sorted(RUNTIME_ROOT.glob("*/bundle.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                sources = (_read_json(recorded) or {}).get("sources", {})
                source = Path(str(sources.get("GameAssembly.dll", "")))
                if source.is_file():
                    build = source.parent
                    break
        if build is None:
            raise ForecastSetupError("找不到游戏安装目录；请先打开游戏")
        return ensure_runtime_bundle(probe_source(), build, newest_static_data())

    def status(self) -> dict[str, Any] | None:
        with self.lock:
            return copy.deepcopy(self.job) if self.job else None

    def start(self, strategy: dict[str, Any], strategy_meta: dict[str, Any], capture_id: str, runs: int,
              pid: int | None = None) -> dict[str, Any]:
        if not isinstance(strategy, dict):
            raise ValueError("缺少策略")
        controller.require_list_execution(strategy)
        runs = max(1, min(MAX_RUNS, int(runs)))
        folder = (self.capture_root / str(capture_id)).resolve()
        if folder.parent != self.capture_root.resolve() or not (folder / "capture-provenance.json").is_file():
            raise ValueError("找不到这次战斗的开局数据")
        with self.lock:
            if self.job and self.job.get("status") == "running":
                raise RuntimeError("已有模拟正在进行")
            simulation_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
            self.job = {"id": simulation_id, "status": "running", "runs": runs, "finishedRuns": 0,
                        "currentBossTurn": None, "phase": "preparing", "message": "准备离线引擎",
                        "startedAt": time.strftime("%H:%M:%S")}
            self.cancel.clear()
        thread = threading.Thread(target=self._work, name="chimera-simulation", daemon=True,
                                  args=(simulation_id, copy.deepcopy(strategy), dict(strategy_meta), folder, runs, pid))
        thread.start()
        return self.status() or {}

    def stop(self) -> None:
        self.cancel.set()

    def _update(self, **values: Any) -> None:
        with self.lock:
            if self.job:
                self.job.update(values)

    def _work(self, simulation_id: str, strategy: dict[str, Any], meta: dict[str, Any], capture: Path,
              runs: int, pid: int | None) -> None:
        output = self.simulation_root / simulation_id
        summary: dict[str, Any] = {"schema": 1, "id": simulation_id, "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                                   "status": "running", "strategy": {**meta, "rules": len(strategy.get("rules", []))},
                                   "capture": {"id": capture.name}}
        try:
            output.mkdir(parents=True, exist_ok=False)
            atomic_write_json(output / "strategy.json", strategy)
            provenance = _read_json(capture / "capture-provenance.json") or {}
            summary["capture"].update({key: provenance.get(key) for key in
                                       ("stageId", "seed", "teamHeroTypeIds", "bossHeroTypeId")})
            summary["capture"]["difficulty"] = difficulty_for_stage(provenance.get("stageId"))
            bundle = self.bundle_provider(pid)
            probe = bundle / "raid_offline_probe.exe"
            packed = capture / "packed"
            if not (packed / "conversion-report.json").is_file():
                shutil.rmtree(packed, ignore_errors=True)
                convert(capture, probe)
            static_state = {}
            static = _read_json(capture / "decision-static.json")
            if isinstance(static, dict):
                static_state.update(static)
            opening = _first_state(capture) or {}
            if isinstance(opening.get("rotationIdentity"), dict):
                static_state["rotationIdentity"] = opening["rotationIdentity"]
            start_selection = opening.get("chimeraStartSelection")
            team = {"heroTypeIds": provenance.get("teamHeroTypeIds") or [],
                    "heroIds": provenance.get("teamHeroIds") or []}
            # The memory the live controller would start the next battle with.
            memory = controller.SkillCapabilityMemory.load(controller.DEFAULT_CAPABILITY_CACHE,
                                                           controller.DEFAULT_CAPABILITY_SEED)
            seeds = derived_seeds(int(provenance.get("seed", 0)), runs)
            summaries: list[dict[str, Any] | None] = [None] * runs
            progress: dict[int, int] = {}

            def one(index: int) -> None:
                if self.cancel.is_set():
                    return
                seed = None if index == 0 else seeds[index]

                def on_progress(values: dict[str, Any]) -> None:
                    progress[index] = values.get("bossTurns") or 0
                    self._update(currentBossTurn=max(progress.values()))

                result = self.runner(probe, packed, strategy, seed=seed, capability_memory=memory,
                                     static_state=static_state, team_selection=team,
                                     start_selection=start_selection if isinstance(start_selection, dict) else None,
                                     cancel=self.cancel, on_progress=on_progress)
                atomic_write_json(output / f"run-{index + 1:02d}.json", result)
                run_summary = summarize_run(result, strategy)
                run_summary["index"] = index + 1
                summaries[index] = run_summary
                with self.lock:
                    if self.job:
                        self.job["finishedRuns"] += 1
                        self.job["message"] = f"已完成 {self.job['finishedRuns']}/{runs} 场"
                        self.job["phase"] = "running"

            self._update(phase="running", message=f"正在模拟 0/{runs} 场")
            with ThreadPoolExecutor(max_workers=min(PARALLEL_RUNS, runs)) as pool:
                list(pool.map(one, range(runs)))
            done = [item for item in summaries if item is not None]
            summary["runs"] = done
            summary["aggregate"] = aggregate(done, strategy)
            summary["status"] = "cancelled" if self.cancel.is_set() else "complete"
            self._update(status=summary["status"], phase=summary["status"],
                         message="模拟完成" if summary["status"] == "complete" else "已停止")
        except (ForecastSetupError, ConversionError, ValueError, OSError) as error:
            summary["status"] = "failed"
            summary["reason"] = str(error)
            self._update(status="failed", phase="failed", reason=str(error), message=f"模拟无法进行：{error}")
        except Exception as error:  # Never let a simulation take down the app.
            summary["status"] = "failed"
            summary["reason"] = f"{type(error).__name__}: {error}"
            self._update(status="failed", phase="failed", reason=type(error).__name__,
                         message=f"模拟失败：{type(error).__name__}")
        finally:
            try:
                if output.is_dir():
                    atomic_write_json(output / "summary.json", summary)
            except OSError:
                pass
            _prune(self.simulation_root)

    def _folder(self, simulation_id: str) -> Path:
        root = (self.battle_forecast_root if str(simulation_id).startswith(BATTLE_FORECAST_PREFIX)
                else self.simulation_root)
        folder = (root / str(simulation_id)).resolve()
        if folder.parent != root.resolve():
            raise FileNotFoundError("模拟记录不存在")
        return folder

    def load(self, simulation_id: str) -> dict[str, Any]:
        folder = self._folder(simulation_id)
        summary = _read_json(folder / "summary.json")
        if not isinstance(summary, dict):
            raise FileNotFoundError("模拟记录不存在")
        return summary

    def load_run(self, simulation_id: str, index: int) -> dict[str, Any]:
        folder = self._folder(simulation_id)
        result = _read_json(folder / f"run-{int(index):02d}.json")
        if not isinstance(result, dict):
            raise FileNotFoundError("这一场的记录不存在")
        engine = result.get("engine") or {}
        return {"index": int(index), "status": result.get("status"), "reason": result.get("reason"),
                "seed": engine.get("seed"), "actors": engine.get("actors", []),
                "trials": engine.get("trials", []), "stuck": result.get("stuck"),
                "timeline": run_timeline(result)}
