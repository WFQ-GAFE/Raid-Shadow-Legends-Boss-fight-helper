"""Battle-start whole-battle simulation for the live Chimera takeover."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from chimera_forecast_live import ChimeraForecastMonitor, evaluate_forecast, forecast_enabled


ONE = 1 << 32
CONFIG = {"name": "测试", "executionMode": "list", "rules": [],
          "objectives": {"mandatoryTrialIds": [8000602], "minimumDamage": 1000}}


def result(*, completed=True, damage=2000.0, status="complete", words=(1, 2, 3, 4), skill=101):
    trial = {"id": 8000602, "started": True, "completed": completed, "currentRaw": ONE if completed else 0,
             "targetRaw": ONE, "selfTurnWhichCompleted": 3 if completed else -1}
    decisions = [{"turn": turn, "playerTurnCount": turn - 1, "activeHeroId": 1, "status": "command",
                  "skillTypeId": skill, "targetId": 5, "rule": "r", "ruleIndex": None} for turn in (1, 2, 3)]
    actions = [{"turn": turn, "bossTurns": 0, "form": 0, "actorId": 1, "actorTypeId": 7001, "source": "policy",
                "skillTypeId": skill, "targetId": 5, "damage": damage / 3, "trials": [], "deaths": [],
                "autoReason": "", "rng": list(words)} for turn in (1, 2, 3)]
    value = {"status": status, "reason": None, "decisions": decisions, "objectiveEvents": [],
             "elapsedSeconds": 1.0,
             "engine": {"seed": 7, "capturedSeed": 7, "bossTurns": 20, "damage": damage, "bossDamageTaken": damage,
                        "commands": 3, "trials": [trial], "actions": actions, "actors": []}}
    if status == "stuck":
        value["stuck"] = {"reason": "no_matching_rule", "turn": 3, "bossTurns": 2, "activeHeroTypeId": 7001}
    return value


def state(turn, *, generation=5, words=(1, 2, 3, 4)):
    return {"bossMode": "chimera", "battleGeneration": generation, "pid": 1, "activeHeroId": 1,
            "battle": {"turn": turn, "playerTurnCount": turn - 1},
            "battleRandom": {"available": True, "turn": turn, "words": list(words)},
            "heroes": [{"typeId": 7001, "name": "测试英雄"}]}


class Capture:
    def __init__(self, folder: Path, generation=5, status="saved"):
        self.generation, self.status, self.folder, self.reason = generation, status, folder, None
        self.opening = {"chimeraStartSelection": {"heroes": []}}
        self.static_payload = {"trialCatalog": []}


def monitor(root: Path, forecast: dict, messages: list[str]) -> ChimeraForecastMonitor:
    folder = root / "capture"
    (folder / "packed").mkdir(parents=True, exist_ok=True)
    (folder / "packed" / "conversion-report.json").write_text("{}", encoding="utf-8")
    (folder / "capture-provenance.json").write_text(json.dumps(
        {"stageId": 13049006, "seed": 7, "teamHeroTypeIds": [7001], "teamHeroIds": [1]}), encoding="utf-8")
    return ChimeraForecastMonitor(emit=messages.append, runner=lambda *args, **kwargs: forecast,
                                  converter=lambda folder, probe: {}, bundle_provider=lambda pid: root,
                                  work_root=root / "records")


def run(forecast: dict, live_words=(1, 2, 3, 4), command_skill=101):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        messages: list[str] = []
        watcher = monitor(root, forecast, messages)
        capture = Capture(root / "capture")
        assert watcher.observe(CONFIG, state(1, words=live_words), capture=capture, capability_memory=None) is None
        watcher.observe_command(state(1), skill_type_id=command_skill, target_id=5)
        watcher.battle.job.thread.join()
        retry = watcher.observe(CONFIG, state(2, words=live_words), capture=capture, capability_memory=None)
        record = watcher.battle.job.record
        summary = json.loads((record / "summary.json").read_text("utf-8")) if record else None
        return retry, watcher, messages, summary


def test_enabled_by_default_only_with_goals() -> None:
    assert forecast_enabled(CONFIG)
    assert not forecast_enabled({**CONFIG, "objectives": {**CONFIG["objectives"], "battleForecast": False}})
    assert not forecast_enabled({**CONFIG, "objectives": {"mandatoryTrialIds": [], "minimumDamage": 0}})


def test_verdicts() -> None:
    assert evaluate_forecast(CONFIG, result())["verdict"] == "continue"
    missing = evaluate_forecast(CONFIG, result(completed=False))
    assert missing["verdict"] == "retry" and missing["cause"] == "mandatory" and missing["missingTrialIds"] == [8000602]
    low = evaluate_forecast(CONFIG, result(damage=10.0))
    assert low["verdict"] == "retry" and low["cause"] == "damage"
    stuck = evaluate_forecast(CONFIG, result(status="stuck", completed=False))
    assert stuck["verdict"] == "retry" and stuck["cause"] == "stuck"
    # Stuck after every goal was met: the goals are not at risk.
    assert evaluate_forecast(CONFIG, result(status="stuck"))["verdict"] == "continue"
    assert evaluate_forecast(CONFIG, result(status="partial"))["verdict"] == "unavailable"


def test_predicted_failure_asks_for_a_regroup_and_saves_a_report() -> None:
    retry, watcher, messages, summary = run(result(completed=False))
    assert retry is not None and retry.cause == "mandatory"
    assert retry.behavior == "free_regroup_and_retry_manual" and retry.record_id.startswith("battle-")
    assert watcher.telemetry()["verdict"] == "retry"
    assert summary["kind"] == "battle" and summary["verdict"]["reason"] == "retry"
    assert summary["runs"][0]["mandatory"][0]["completed"] is False
    assert "执行免费重整" in messages[-1]


def test_goals_met_continue() -> None:
    retry, watcher, messages, _ = run(result())
    assert retry is None and watcher.battle.reason == "continue" and "继续战斗" in messages[-1]


def test_a_live_difference_disables_the_prediction() -> None:
    retry, watcher, messages, _ = run(result(completed=False), live_words=(9, 9, 9, 9))
    assert retry is None and watcher.battle.reason == "live_turn_differs" and "随机状态不同" in messages[-1]
    retry, watcher, _, _ = run(result(completed=False), command_skill=999)
    assert retry is None and watcher.battle.reason == "live_turn_differs"


def test_mid_battle_takeover_and_missing_capture_are_skipped() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        messages: list[str] = []
        watcher = monitor(root, result(), messages)
        assert watcher.observe(CONFIG, state(40), capture=Capture(root), capability_memory=None) is None
        assert watcher.battle.status == "not_opening"
        watcher.observe(CONFIG, state(1, generation=6), capture=Capture(root, generation=6, status="unavailable"),
                        capability_memory=None)
        assert watcher.battle.status == "unavailable" and "无法进行" in messages[-1]
