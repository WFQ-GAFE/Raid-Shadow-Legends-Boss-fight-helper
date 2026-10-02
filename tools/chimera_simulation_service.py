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
import secrets
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
from simulation_common import action_uses, read_run, write_run
from forecast_advice import advice, failure_advice
from capture_identity import unique_first
from team_preview import TeamSnapshotStore
from team_setups import prepare_team_input, team_report
from strategy_storage import atomic_write_json
from ui_text import ui_text


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
# 100 runs: rare outcomes (about 1 in 20 to 100) become visible; ~10 MB per Hydra simulation.
MAX_RUNS = 100
# Runs at once: at most five, fewer on a smaller machine (see parallel_runs).
MAX_PARALLEL_RUNS = 5
# Each run is one offline engine process: about one busy core and 300-450 MB
# (up to its 2 GB limit); two cores stay for the game and this tool.
RESERVED_CORES = 2
MEMORY_PER_RUN = 1024 ** 3


def _available_memory() -> int | None:
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("available", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong),
                    ("available_page", ctypes.c_ulonglong), ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong), ("available_extended", ctypes.c_ulonglong)]
    try:
        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        return int(status.available) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None
    except (AttributeError, OSError):
        return None


def parallel_runs(runs: int, cores: int | None = None, available_memory: int | None = None) -> int:
    """How many runs to simulate at once: at most five, fewer without enough cores or free memory."""
    cores = cores if cores is not None else (os.cpu_count() or 2)
    memory = available_memory if available_memory is not None else _available_memory()
    by_memory = MAX_PARALLEL_RUNS if memory is None else memory // MEMORY_PER_RUN
    return int(max(1, min(MAX_PARALLEL_RUNS, runs, cores - RESERVED_CORES, by_memory)))


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


def _verdict_with_advice(verdict: dict[str, Any]) -> dict[str, Any]:
    """A battle-forecast verdict; records from before 1.1.1 get their explanation on reading."""
    if verdict.get("advice") or verdict.get("status") not in ("unavailable", "not_opening"):
        return verdict
    return {**verdict, "advice": advice("chimera", verdict.get("reason"))}


def list_captures(limit: int = 20, root: Path | None = None) -> list[dict[str, Any]]:
    """Saved Chimera captures that a simulation can use, newest first."""
    base = root or CAPTURE_ROOT
    try:
        folders = sorted((item for item in base.iterdir() if item.is_dir()),
                         key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return []
    result = []
    for folder in unique_first(folders):  # an opening repeating a newer one's set-up adds nothing
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


def simulation_seeds(captured_seed: int, count: int,
                     draw: Callable[[int], int] = secrets.randbits) -> list[int]:
    """The captured seed first (an exact replay), then fresh random 32-bit seeds.

    Every simulation draws new ones, like the server gives every real battle a
    new seed: repeated simulations of one opening explore more outcomes.
    """
    seeds = [captured_seed]
    while len(seeds) < count:
        value = draw(32) - 0x80000000
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
            "deaths": action.get("deaths") or [], "uses": action_uses(action.get("uses")),
            "rule": decision.get("rule"), "ruleIndex": decision.get("ruleIndex"),
            "reservationReleased": decision.get("reservationReleased") is True,
            **({"state": decision["snapshot"]} if isinstance(decision.get("snapshot"), dict) else {}),
        })
    return rows


def _prune(root: Path) -> None:
    try:
        entries = sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime)
    except OSError:
        return
    for stale in entries[:-KEEP_SIMULATIONS]:
        shutil.rmtree(stale, ignore_errors=True)


_row_cache: dict[tuple[str, str], tuple[tuple[int, int], dict[str, Any] | None]] = {}
_row_cache_lock = threading.Lock()


def summary_row(folder: Path, build: Callable[[str, dict[str, Any]], dict[str, Any]]) -> dict[str, Any] | None:
    """A history row built from a simulation's summary, re-read only when the summary changes.

    The interface polls the history every second and a 100-run summary is
    several hundred KB.
    """
    path = folder / "summary.json"
    try:
        stat = path.stat()
    except OSError:
        return None
    key, stamp = (str(path), build.__qualname__), (stat.st_mtime_ns, stat.st_size)
    with _row_cache_lock:
        cached = _row_cache.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    summary = _read_json(path)
    row = build(folder.name, summary) if isinstance(summary, dict) else None
    with _row_cache_lock:
        _row_cache[key] = (stamp, row)
    return row


def recent_folders(base: Path, limit: int) -> list[Path]:
    try:
        return sorted((item for item in base.iterdir() if item.is_dir()),
                      key=lambda item: item.stat().st_mtime, reverse=True)[:limit]
    except OSError:
        return []


def _history_row(name: str, summary: dict[str, Any]) -> dict[str, Any]:
    aggregate_value = summary.get("aggregate") or {}
    return {"id": name, "createdAt": summary.get("createdAt"),
            "strategyName": summary.get("strategy", {}).get("name"),
            "captureId": summary.get("capture", {}).get("id"),
            "status": summary.get("status"), "runs": aggregate_value.get("runs"),
            "allMandatoryRuns": aggregate_value.get("allMandatoryRuns"),
            "finishedRuns": aggregate_value.get("finishedRuns"),
            "stuckRuns": len(aggregate_value.get("stuckRuns") or []),
            **({"verdict": _verdict_with_advice(summary["verdict"])}
               if isinstance(summary.get("verdict"), dict) else {})}


def recent_simulations(limit: int = 10, root: Path | None = None) -> list[dict[str, Any]]:
    rows = (summary_row(folder, _history_row) for folder in recent_folders(root or SIMULATION_ROOT, limit))
    return [row for row in rows if row is not None]


def engine_bundle(pid: int | None) -> Path:
    """The offline engine's private copy of the game files (see ensure_runtime_bundle)."""
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
        raise ForecastSetupError(ui_text("simService.gameNotFound"))
    return ensure_runtime_bundle(probe_source(), build, newest_static_data())


class SimulationService:
    """One simulation at a time, run in the background of the desktop app.

    The Chimera service; HydraSimulationService (hydra_simulation_service.py)
    replaces the capture lookup, runner arguments, summaries and run view.
    """

    boss_mode = "chimera"

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
        return engine_bundle(pid)

    def status(self) -> dict[str, Any] | None:
        with self.lock:
            return copy.deepcopy(self.job) if self.job else None

    def start(self, strategy: dict[str, Any], strategy_meta: dict[str, Any], capture_id: str, runs: int,
              pid: int | None = None, team: dict[str, Any] | None = None,
              capture_folder: Path | None = None) -> dict[str, Any]:
        """``team``: another team to put into the saved opening (team_setups), or None for its own."""
        if not isinstance(strategy, dict):
            raise ValueError(ui_text("simService.missingStrategy"))
        controller.require_list_execution(strategy)
        runs = max(1, min(MAX_RUNS, int(runs)))
        folder = capture_folder if capture_folder is not None else self._capture_folder(str(capture_id))
        with self.lock:
            if self.job and self.job.get("status") == "running":
                raise RuntimeError(ui_text("simService.alreadyRunning"))
            simulation_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
            self.job = {"id": simulation_id, "status": "running", "runs": runs, "finishedRuns": 0,
                        "currentBossTurn": None, "phase": "preparing", "message": ui_text("simService.preparingEngine"),
                        "startedAt": time.strftime("%H:%M:%S")}
            self.cancel.clear()
        thread = threading.Thread(target=self._work, name=f"{self.boss_mode}-simulation", daemon=True,
                                  args=(simulation_id, copy.deepcopy(strategy), dict(strategy_meta), folder, runs, pid,
                                        copy.deepcopy(team) if team else None))
        thread.start()
        return self.status() or {}

    def stop(self) -> None:
        self.cancel.set()

    # --- Boss-specific steps (Chimera here; overridden for Hydra) ---

    def _capture_folder(self, capture_id: str) -> Path:
        folder = (self.capture_root / capture_id).resolve()
        if folder.parent != self.capture_root.resolve() or not (folder / "capture-provenance.json").is_file():
            raise ValueError(ui_text("simService.openingNotFound"))
        return folder

    def _capture_facts(self, provenance: dict[str, Any]) -> dict[str, Any]:
        facts = {key: provenance.get(key) for key in ("stageId", "seed", "teamHeroTypeIds", "bossHeroTypeId")}
        facts["difficulty"] = difficulty_for_stage(provenance.get("stageId"))
        return facts

    def _runner_arguments(self, capture: Path, provenance: dict[str, Any]) -> dict[str, Any]:
        static_state = {}
        static = _read_json(capture / "decision-static.json")
        if isinstance(static, dict):
            static_state.update(static)
        opening = _first_state(capture) or {}
        if isinstance(opening.get("rotationIdentity"), dict):
            static_state["rotationIdentity"] = opening["rotationIdentity"]
        start_selection = opening.get("chimeraStartSelection")
        return {"static_state": static_state,
                "team_selection": {"heroTypeIds": provenance.get("teamHeroTypeIds") or [],
                                   "heroIds": provenance.get("teamHeroIds") or []},
                "start_selection": start_selection if isinstance(start_selection, dict) else None}

    @staticmethod
    def _team_arguments(arguments: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
        """Runner inputs that name the team, for a swapped-in team."""
        updated = {**arguments, "team_selection": {"heroTypeIds": provenance["teamHeroTypeIds"],
                                                   "heroIds": provenance["teamHeroIds"]}}
        selection = arguments.get("start_selection")
        if isinstance(selection, dict):
            updated["start_selection"] = {**selection, "heroIds": provenance["teamHeroIds"],
                                          "heroTypeIds": provenance["teamHeroTypeIds"]}
        return updated

    @staticmethod
    def _progress(values: dict[str, Any]) -> tuple[str, int]:
        return "currentBossTurn", int(values.get("bossTurns") or 0)

    @staticmethod
    def _summarize(result: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
        return summarize_run(result, strategy)

    @staticmethod
    def _aggregate(summaries: list[dict[str, Any]], strategy: dict[str, Any]) -> dict[str, Any]:
        return aggregate(summaries, strategy)

    @staticmethod
    def _run_view(result: dict[str, Any]) -> dict[str, Any]:
        engine = result.get("engine") or {}
        return {"actors": engine.get("actors", []), "trials": engine.get("trials", []),
                "timeline": run_timeline(result)}

    def _update(self, **values: Any) -> None:
        with self.lock:
            if self.job:
                self.job.update(values)

    def _work(self, simulation_id: str, strategy: dict[str, Any], meta: dict[str, Any], capture: Path,
              runs: int, pid: int | None, team: dict[str, Any] | None = None) -> None:
        output = self.simulation_root / simulation_id
        summary: dict[str, Any] = {"schema": 1, "id": simulation_id, "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                                   "status": "running", "strategy": {**meta, "rules": len(strategy.get("rules", []))},
                                   "capture": {"id": capture.name}}
        try:
            output.mkdir(parents=True, exist_ok=False)
            atomic_write_json(output / "strategy.json", strategy)
            provenance = _read_json(capture / "capture-provenance.json") or {}
            summary["capture"].update(self._capture_facts(provenance))
            bundle = self.bundle_provider(pid)
            probe = bundle / "raid_offline_probe.exe"
            packed = capture / "packed"
            if not (packed / "conversion-report.json").is_file():
                shutil.rmtree(packed, ignore_errors=True)
                convert(capture, probe)
            # The runs read their own copy: a newer battle with the same set-up
            # replaces this opening meanwhile (capture_identity.drop_repeats).
            shutil.copytree(packed, output / "battle-input")
            packed = output / "battle-input"
            arguments = self._runner_arguments(capture, provenance)
            summary["capture"]["teamSource"] = team.get("source") if team else "battle"
            battle_setup = capture / "battle-setup.json"
            if team is not None:
                # The same opening (boss, stage, seed) with the chosen team swapped in.
                team_input = output / "team-input"
                provenance = prepare_team_input(capture, team["heroes"], self.boss_mode, team_input,
                                                area=team.get("area"), rule=team["rule"], powers=team.get("powers"))
                convert(team_input, probe)
                packed = team_input / "packed"
                battle_setup = team_input / "battle-setup.json"
                arguments = self._team_arguments(arguments, provenance)
                summary["capture"].update(teamHeroTypeIds=provenance["teamHeroTypeIds"],
                                          teamSavedAt=team.get("savedAt"), teamCheck=team.get("check"))
            # The memory the live controller would start the next battle with.
            memory = controller.SkillCapabilityMemory.load(controller.DEFAULT_CAPABILITY_CACHE,
                                                           controller.DEFAULT_CAPABILITY_SEED)
            seeds = simulation_seeds(int(provenance.get("seed", 0)), runs)
            summaries: list[dict[str, Any] | None] = [None] * runs
            progress: dict[int, int] = {}
            opening: dict[str, Any] = {}

            def one(index: int) -> None:
                if self.cancel.is_set():
                    return
                seed = None if index == 0 else seeds[index]

                def on_progress(values: dict[str, Any]) -> None:
                    key, value = self._progress(values)
                    progress[index] = value
                    self._update(**{key: max(progress.values())})

                result = self.runner(probe, packed, strategy, seed=seed, capability_memory=memory,
                                     cancel=self.cancel, on_progress=on_progress, **arguments)
                write_run(output, index + 1, result)
                if index == 0:
                    opening["stats"] = result.get("openingStats") or {}
                run_summary = self._summarize(result, strategy)
                run_summary["index"] = index + 1
                summaries[index] = run_summary
                with self.lock:
                    if self.job:
                        self.job["finishedRuns"] += 1
                        self.job["message"] = ui_text("simService.runsDone", job=self.job['finishedRuns'], runs=runs)
                        self.job["phase"] = "running"

            self._update(phase="running", message=ui_text("simService.runsStarting", runs=runs))
            with ThreadPoolExecutor(max_workers=parallel_runs(runs)) as pool:
                list(pool.map(one, range(runs)))
            done = [item for item in summaries if item is not None]
            summary["team"] = self._team_report(battle_setup, opening.get("stats") or {}, team, provenance)
            summary["runs"] = done
            summary["aggregate"] = self._aggregate(done, strategy)
            summary["status"] = "cancelled" if self.cancel.is_set() else "complete"
            self._update(status=summary["status"], phase=summary["status"],
                         message=ui_text("simService.complete") if summary["status"] == "complete" else ui_text("simService.stopped"))
        except (ForecastSetupError, ConversionError, ValueError, OSError) as error:
            summary["status"] = "failed"
            summary["reason"] = str(error)
            note = failure_advice(self.boss_mode, error)
            self._update(status="failed", phase="failed", reason=str(error), message=ui_text("simService.cannotRun", error=error),
                         **({"advice": note} if note else {}))
        except Exception as error:  # Never let a simulation take down the app.
            summary["status"] = "failed"
            summary["reason"] = f"{type(error).__name__}: {error}"
            self._update(status="failed", phase="failed", reason=type(error).__name__,
                         message=ui_text("simService.failed", name=type(error).__name__), advice=failure_advice(self.boss_mode, error))
        finally:
            try:
                if output.is_dir():
                    atomic_write_json(output / "summary.json", summary)
            except OSError:
                pass
            _prune(self.simulation_root)

    @staticmethod
    def _team_report(battle_setup: Path, stats: dict[str, Any], team: dict[str, Any] | None,
                     provenance: dict[str, Any]) -> dict[str, Any] | None:
        """The team this simulation ran with, for the report's team view (never fails the job)."""
        try:
            display = team.get("display") if team else TeamSnapshotStore().find(provenance.get("teamHeroTypeIds"))
            return team_report(battle_setup, stats, source=team.get("source") if team else "battle",
                               display=display, saved_at=team.get("savedAt") if team else None)
        except Exception:  # The report works without it.
            return None

    def _folder(self, simulation_id: str) -> Path:
        root = (self.battle_forecast_root if str(simulation_id).startswith(BATTLE_FORECAST_PREFIX)
                else self.simulation_root)
        folder = (root / str(simulation_id)).resolve()
        if folder.parent != root.resolve():
            raise FileNotFoundError(ui_text("simService.recordNotFound"))
        return folder

    def load(self, simulation_id: str) -> dict[str, Any]:
        folder = self._folder(simulation_id)
        summary = _read_json(folder / "summary.json")
        if not isinstance(summary, dict):
            raise FileNotFoundError(ui_text("simService.recordNotFound"))
        return summary

    def load_run(self, simulation_id: str, index: int) -> dict[str, Any]:
        folder = self._folder(simulation_id)
        result = read_run(folder, index)
        if not isinstance(result, dict):
            raise FileNotFoundError(ui_text("simService.runNotFound"))
        engine = result.get("engine") or {}
        return {"index": int(index), "status": result.get("status"), "reason": result.get("reason"),
                "seed": engine.get("seed"), "stuck": result.get("stuck"), **self._run_view(result)}
