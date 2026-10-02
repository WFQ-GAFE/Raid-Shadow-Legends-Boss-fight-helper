"""Hydra strategy simulation: session, summaries, aggregate, timeline, captures (no engine, no game)."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile

from chimera_capture_live import HydraCaptureMonitor
from hydra_simulation import HydraSimulationSession, aggregate, battle_report, forecast_run, run_timeline, summarize_run
from hydra_simulation_service import HydraSimulationService, list_captures
from test_hydra_offline_policy import fixture


def result_fixture() -> tuple[dict, dict]:
    snapshot = {"actors": [{"id": 0, "t": 100, "s": "a", "hp": 100.0, "fx": [[150, 2, 1]]}]}
    result = {
        "status": "complete", "reason": None, "elapsedSeconds": 1.0,
        "decisions": [
            {"ruleIndex": 1, "rule": "r1", "hydraTurns": 1, "snapshot": snapshot},
            {"ruleIndex": 2, "rule": "r2", "hydraTurns": 2, "reservationReleased": True},
        ],
        "marks": [{"markIndex": 1, "heroTypeId": 100, "applyTurn": 0, "commandIndex": -1, "actorId": 0},
                  {"markIndex": 2, "heroTypeId": 200, "applyTurn": 2, "commandIndex": 1, "actorId": 1}],
        "engine": {
            "seed": 5, "capturedSeed": 5, "seedOverridden": False, "hydraDamage": 700, "turn": 1000,
            "stopReason": "game_turn_limit", "effectiveMaxTurnsInBattle": 1000, "commands": 3,
            "actors": [{"actorId": 0, "heroTypeId": 100, "player": True, "dead": False},
                       {"actorId": 1, "heroTypeId": 200, "player": True, "dead": True},
                       {"actorId": 8, "heroTypeId": 26040, "player": False, "dead": True}],
            "turns": [
                {"turnBefore": 1, "actorId": 0, "actorTypeId": 100, "playerAction": True, "skillTypeId": 1001,
                 "targetId": 8, "damageAfter": 500, "deaths": [8]},
                {"turnBefore": 2, "actorId": 9, "actorTypeId": 26040, "playerAction": False, "skillTypeId": 2601,
                 "targetId": 1, "damageAfter": 500, "deaths": [1]},
                {"turnBefore": 3, "actorId": 0, "actorTypeId": 100, "playerAction": True, "skillTypeId": 1002,
                 "targetId": 9, "damageAfter": 700, "deaths": []},
            ]},
    }
    strategy = {"bossMode": "hydra", "rules": [{"name": "r1"}, {"name": "r2"}, {"name": "unused"}],
                "objectives": {"minimumDamage": 1000,
                               "devourOrderRetryConditions": [{"relation": "neverMarked", "heroTypeIds": [200]}]}}
    return result, strategy


def test_session_reports_the_rule_and_where_the_rules_stall() -> None:
    state, strategy = fixture()
    decision = HydraSimulationSession(strategy).decide(copy.deepcopy(state))
    assert decision["status"] == "command" and decision["ruleIndex"] == 1
    state["skills"][0].update(ready=False, validTargetIds=[])
    stalled = HydraSimulationSession(strategy).decide(state)
    stuck = stalled["stuck"]
    assert stalled["status"] == "unknown" and stuck["reason"] == "no_matching_rule"
    assert stuck["activeHeroTypeId"] == 100 and stuck["skills"][0]["typeId"] == 1001
    assert [actor["id"] for actor in stuck["snapshot"]["actors"]] == [0, 8]


def test_summary_attributes_damage_and_counts_marks_deaths_kills_and_live_verdict() -> None:
    result, strategy = result_fixture()
    summary = summarize_run(result, strategy)
    rules = {rule["ruleIndex"]: rule for rule in summary["rules"]}
    assert rules[1]["uses"] == 1 and rules[1]["damage"] == 500
    assert rules[2]["uses"] == 1 and rules[2]["damage"] == 200
    assert summary["deaths"] == [{"actorId": 1, "heroTypeId": 200, "turn": 2}]
    assert summary["headKills"] == {"26040": 1}
    assert [mark["heroTypeId"] for mark in summary["marks"]] == [100, 200]
    assert summary["reservationReleases"] == 1 and summary["exact"] is True
    causes = sorted(str(violation.get("cause") or violation.get("relation")) for violation in summary["verdict"]["violations"])
    assert summary["verdict"]["verdict"] == "retry" and causes == ["damage", "neverMarked"]


def test_aggregate_and_timeline() -> None:
    result, strategy = result_fixture()
    first = summarize_run(result, strategy) | {"index": 1}
    second_result = copy.deepcopy(result)
    second_result["engine"]["hydraDamage"] = 1500
    second_result["marks"] = second_result["marks"][:1]
    second = summarize_run(second_result, {**strategy, "objectives": {"minimumDamage": 1000}}) | {"index": 2}
    value = aggregate([first, second], strategy)
    assert value["damage"] == {"min": 700.0, "median": 1100.0, "max": 1500.0}
    assert value["minimumDamageRuns"] == 1 and value["headKillsPerRunMedian"] == 1.0
    marks = {entry["heroTypeId"]: entry for entry in value["marks"]}
    assert marks[100]["runs"] == 2 and marks[200]["runs"] == 1
    assert value["regroupRuns"] == 1 and value["regroupCauses"] == {"damage": 1, "devour": 1}
    assert [rule["uses"] for rule in value["rules"]] == [2, 2, 0]
    rows = run_timeline(result)
    assert [row["source"] for row in rows] == ["policy", "enemy", "policy"]
    assert [row["damage"] for row in rows] == [500, 0, 200]
    assert rows[0]["state"]["actors"][0]["fx"] == [[150, 2, 1]] and "state" not in rows[2]
    assert rows[1]["marked"] == [1] and rows[1]["hydraTurns"] == 1 and rows[2]["hydraTurns"] == 2


def test_a_live_forecast_becomes_a_report_run() -> None:
    result, strategy = result_fixture()
    engine = result["engine"]
    forecast = {"status": "complete", "reason": None, "elapsedSeconds": 1.0, "marks": result["marks"],
                "engineTurns": engine["turns"], **{key: value for key, value in engine.items() if key != "turns"},
                "decisions": [{**decision, "command": {"skillTypeId": 1001, "targetId": 8}, "rngBefore": [1, 2, 3, 4]}
                              for decision in result["decisions"]]}
    run = forecast_run(forecast)
    assert run["status"] == "complete" and run["engine"]["turns"] == engine["turns"]
    assert run["decisions"][0]["skillTypeId"] == 1001 and "rngBefore" not in run["decisions"][0]
    assert summarize_run(run, strategy)["damage"] == 700
    assert [row["source"] for row in run_timeline(run)] == ["policy", "enemy", "policy"]
    # The engine stopped on a hero without an action: a stuck run with its report.
    stuck = {"reason": "no_matching_rule", "turn": 3, "activeHeroTypeId": 100}
    stopped = {**forecast, "stopReason": "policy_stopped",
               "decisions": forecast["decisions"] + [{"status": "unknown", "stuck": stuck}]}
    run = forecast_run(stopped)
    assert run["status"] == "stuck" and run["reason"] == "no_matching_rule" and run["stuck"] == stuck
    report = battle_report("battle-x", strategy, {"stageId": 8039003, "teamHeroTypeIds": [100, 200]}, "forecast:x", stopped)
    assert report is not None
    summary, saved = report
    assert summary["kind"] == "battle" and summary["capture"]["difficulty"] == 3
    assert summary["aggregate"]["stuckRuns"][0]["activeHeroTypeId"] == 100 and saved["stuck"] == stuck
    # No report without a battle the engine played.
    assert forecast_run({"status": "unknown", "reason": "forecast_timeout"}) is None


def _hydra_folder(root: Path, name: str, setup: str) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "battle-setup.json").write_text("{}", encoding="utf-8")
    (folder / "capture-provenance.json").write_text(json.dumps({
        "type": "verified_hydra_replay_source", "battleSetupId": setup, "stageId": 8039003, "seed": 7,
        "teamHeroTypeIds": [100, 200], "teamHeroIds": [1, 2]}), encoding="utf-8")


def test_captures_from_takeovers_and_forecasts_are_listed_once_and_checked() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        roots = {"capture": Path(temporary) / "capture", "forecast": Path(temporary) / "forecast"}
        _hydra_folder(roots["capture"], "abc-1", "abc")
        _hydra_folder(roots["forecast"], "abc-2", "abc")
        _hydra_folder(roots["forecast"], "def-3", "def")
        captures = list_captures(10, roots)
        assert sorted(item["id"] for item in captures) == ["capture:abc-1", "forecast:def-3"]
        assert captures[0]["difficulty"] == 3 and captures[0]["teamHeroTypeIds"] == [100, 200]
        service = HydraSimulationService(capture_roots=roots, simulation_root=Path(temporary) / "sims",
                                         runner=lambda *args, **kwargs: {})
        assert service._capture_folder("forecast:def-3").name == "def-3"
        for bad in ("forecast:../capture/abc-1", "other:def-3", "def-3", "capture:missing"):
            try:
                service._capture_folder(bad)
            except ValueError:
                continue
            raise AssertionError(bad)


class FakeIpc:
    def replay_input(self) -> dict:
        return {"type": "hydra_replay_source", "status": "captured", "battleGeneration": 7}

    def account(self) -> dict:
        return {"accountName": "tester"}


def test_hydra_openings_are_saved_without_full_decision_states() -> None:
    state, strategy = fixture()
    state.update(battleGeneration=7, pid=1)
    state["battle"]["playerTurnCount"] = 0
    provenance = {"type": "verified_hydra_replay_source", "battleSetupId": "abc"}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        monitor = HydraCaptureMonitor(emit=lambda _: None, work_root=root,
                                      validator=lambda *args: (provenance, b"{}", b"{}"))
        monitor.observe_decision(state, ipc=FakeIpc(), config=strategy)
        folder = next(root.iterdir())
        assert folder.name.startswith("abc-") and monitor.telemetry()["status"] == "saved"
        for name in ("battle-setup.json", "battle-settings.json", "capture-provenance.json", "strategy.json"):
            assert (folder / name).is_file(), name
        assert not (folder / "decision-states.jsonl.gz").exists()
        chimera_state = dict(state, bossMode="chimera", battleGeneration=8)
        monitor.observe_decision(chimera_state, ipc=FakeIpc())
        assert monitor.generation == 7  # Chimera decisions are not Hydra openings.
