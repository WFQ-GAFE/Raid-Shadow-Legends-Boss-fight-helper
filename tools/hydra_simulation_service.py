"""Hydra strategy simulations for the desktop app: captures, runs, saved reports.

The same service as the Chimera one (chimera_simulation_service), fed from
the Hydra openings the controller saves at every takeover from the start
(cache/hydra-capture) and from the openings the devour-order forecast saved
(cache/hydra-forecast). Results go to cache/hydra-simulations/<id>/.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Callable

from capture_identity import unique_first
from chimera_simulation_service import KEEP_SIMULATIONS, SimulationService, _read_json, recent_folders, summary_row
from hydra_forecast_live import REPORT_PREFIX, WORK_ROOT as FORECAST_ROOT
from hydra_simulation import aggregate, difficulty_for_stage, run_simulation, run_timeline, summarize_run
from ui_text import ui_text


PROJECT_ROOT = Path(
    os.environ.get("CHIMERA_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
CAPTURE_ROOTS = {"capture": PROJECT_ROOT / "cache" / "hydra-capture", "forecast": FORECAST_ROOT}
SIMULATION_ROOT = PROJECT_ROOT / "cache" / "hydra-simulations"
PROVENANCE_TYPE = "verified_hydra_replay_source"


def list_captures(limit: int = 20, roots: dict[str, Path] | None = None) -> list[dict[str, Any]]:
    """Saved Hydra openings a simulation can use, newest first (one per battle)."""
    folders: list[tuple[float, str, Path]] = []
    for key, root in (roots or CAPTURE_ROOTS).items():
        try:
            folders.extend((item.stat().st_mtime, key, item) for item in root.iterdir() if item.is_dir())
        except OSError:
            continue
    # One folder per battle: the takeover capture before a forecast folder of
    # the same battle, otherwise the newest.
    chosen: dict[str, tuple[float, str, Path, dict[str, Any]]] = {}
    for modified, key, folder in folders:
        provenance = _read_json(folder / "capture-provenance.json")
        if not isinstance(provenance, dict) or provenance.get("type") != PROVENANCE_TYPE \
                or not (folder / "battle-setup.json").is_file():
            continue
        setup = str(provenance.get("battleSetupId"))
        current = chosen.get(setup)
        if current is None or (key == "capture", modified) > (current[1] == "capture", current[0]):
            chosen[setup] = (modified, key, folder, provenance)
    result: list[dict[str, Any]] = []
    ordered = sorted(chosen.values(), key=lambda item: -item[0])
    # One entry per set-up: a battle repeating a newer one's team and boss adds nothing.
    distinct = set(unique_first([folder for _, _, folder, _ in ordered]))
    for modified, key, folder, provenance in ordered:
        if folder not in distinct:
            continue
        strategy = _read_json(folder / "strategy.json")
        result.append({
            "id": f"{key}:{folder.name}",
            "capturedAt": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)),
            "stageId": provenance.get("stageId"),
            "difficulty": difficulty_for_stage(provenance.get("stageId")),
            "seed": provenance.get("seed"),
            "teamHeroTypeIds": provenance.get("teamHeroTypeIds") or [],
            "teamHeroIds": provenance.get("teamHeroIds") or [],
            "strategyName": strategy.get("name") if isinstance(strategy, dict) else None,
        })
        if len(result) >= limit:
            break
    return result


def _history_row(name: str, summary: dict[str, Any]) -> dict[str, Any]:
    value = summary.get("aggregate") or {}
    return {"id": name, "createdAt": summary.get("createdAt"),
            "strategyName": summary.get("strategy", {}).get("name"),
            "captureId": summary.get("capture", {}).get("id"),
            "status": summary.get("status"), "runs": value.get("runs"),
            "finishedRuns": value.get("finishedRuns"),
            "damageMedian": (value.get("damage") or {}).get("median"),
            "regroupRuns": value.get("regroupRuns"),
            "stuckRuns": len(value.get("stuckRuns") or [])}


def recent_simulations(limit: int = 10, root: Path | None = None) -> list[dict[str, Any]]:
    rows = (summary_row(folder, _history_row) for folder in recent_folders(root or SIMULATION_ROOT, limit))
    return [row for row in rows if row is not None]


class HydraSimulationService(SimulationService):
    boss_mode = "hydra"

    def __init__(self, capture_roots: dict[str, Path] | None = None, simulation_root: Path = SIMULATION_ROOT,
                 runner: Callable[..., dict[str, Any]] = run_simulation,
                 bundle_provider: Callable[[int | None], Path] | None = None):
        self.capture_roots = capture_roots or CAPTURE_ROOTS
        super().__init__(capture_root=self.capture_roots["capture"], simulation_root=simulation_root,
                         runner=runner, bundle_provider=bundle_provider,
                         battle_forecast_root=self.capture_roots.get("forecast", simulation_root))

    def _folder(self, simulation_id: str) -> Path:
        # An opening forecast's report lives in the forecast's own folder (hydra_forecast_live).
        if not str(simulation_id).startswith(REPORT_PREFIX):
            return super()._folder(simulation_id)
        root = self.battle_forecast_root.resolve()
        folder = (root / str(simulation_id)[len(REPORT_PREFIX):]).resolve()
        if folder.parent != root:
            raise FileNotFoundError(ui_text("simService.recordNotFound"))
        return folder

    def captures(self, limit: int = 20) -> list[dict[str, Any]]:
        return list_captures(limit, self.capture_roots)

    def _capture_folder(self, capture_id: str) -> Path:
        key, _, name = capture_id.partition(":")
        root = self.capture_roots.get(key)
        if root is None or not name:
            raise ValueError(ui_text("simService.openingNotFound"))
        folder = (root / name).resolve()
        provenance = _read_json(folder / "capture-provenance.json")
        if folder.parent != root.resolve() or not isinstance(provenance, dict) \
                or provenance.get("type") != PROVENANCE_TYPE:
            raise ValueError(ui_text("simService.openingNotFound"))
        return folder

    def _capture_facts(self, provenance: dict[str, Any]) -> dict[str, Any]:
        facts = {key: provenance.get(key) for key in ("stageId", "seed", "teamHeroTypeIds")}
        facts["difficulty"] = difficulty_for_stage(provenance.get("stageId"))
        return facts

    def _runner_arguments(self, capture: Path, provenance: dict[str, Any]) -> dict[str, Any]:
        return {"team_selection": {"heroTypeIds": provenance.get("teamHeroTypeIds") or [],
                                   "heroIds": provenance.get("teamHeroIds") or []}}

    @staticmethod
    def _progress(values: dict[str, Any]) -> tuple[str, int]:
        return "currentTurn", int(values.get("turn") or 0)

    @staticmethod
    def _summarize(result: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
        return summarize_run(result, strategy)

    @staticmethod
    def _aggregate(summaries: list[dict[str, Any]], strategy: dict[str, Any]) -> dict[str, Any]:
        return aggregate(summaries, strategy)

    @staticmethod
    def _run_view(result: dict[str, Any]) -> dict[str, Any]:
        engine = result.get("engine") or {}
        return {"actors": engine.get("actors", []), "marks": result.get("marks") or [],
                "timeline": run_timeline(result)}


__all__ = ["HydraSimulationService", "list_captures", "recent_simulations", "KEEP_SIMULATIONS"]
