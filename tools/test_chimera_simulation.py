"""Chimera strategy simulation: run summaries, aggregation and the service."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time

from chimera_simulation import ChimeraOfflinePolicySession, _rule_index_by_name, action_window, summarize_run
from chimera_simulation_service import SimulationService, aggregate, derived_seeds, list_captures


ONE = 1 << 32
STRATEGY = {
    "executionMode": "list",
    "objectives": {"mandatoryTrialIds": [8000602, 8000611], "minimumDamage": 80_000_000},
    "rules": [{"name": "马里斯 · 默认技能顺序", "when": {}, "action": {"type": "defaultSkillPriority"}},
              {"name": "莉迪亚 · 压制", "when": {}, "action": {"type": "cast"}},
              {"name": "从未用到", "when": {}, "action": {"type": "cast"}}],
}


def action(boss_turns, source, actor, skill, damage=0.0, trials=(), deaths=()):
    return {"turn": 1, "bossTurns": boss_turns, "form": 0, "actorId": actor, "actorTypeId": 7000 + actor,
            "source": source, "skillTypeId": skill, "targetId": 5, "damage": damage,
            "trials": [list(item) for item in trials], "deaths": list(deaths), "autoReason": "", "rng": [1, 2, 3, 4]}


def result(completed_602: bool, seed: int = 7) -> dict:
    trials = [{"id": 8000602, "started": True, "completed": completed_602, "currentRaw": 10 * ONE if completed_602 else 3 * ONE,
               "targetRaw": 10 * ONE, "counterLimit": 6, "currentCounter": 1, "selfTurnWhichCompleted": 8 if completed_602 else -1},
              {"id": 8000611, "started": False, "completed": False, "currentRaw": 0, "targetRaw": 5 * ONE,
               "counterLimit": -1, "currentCounter": -1, "selfTurnWhichCompleted": -1}]
    actions = [
        action(5, "enemy", 5, 900, trials=[(8000602, 0, 0, -1, 0, 1, 0)]),  # starts the Ram trial: no progress
        action(6, "policy", 1, 101, damage=1000.0, trials=[(8000602, 0, 3 * ONE, 0, 1, 1, 0)]),
        action(7, "policy", 2, 202, damage=50.0),
        action(8, "policy", 1, 101, damage=2000.0,
               trials=[(8000602, 3 * ONE, 10 * ONE, 1, 2, 1, 1)] if completed_602 else [], deaths=[2]),
    ]
    decisions = [
        {"status": "command", "rule": "马里斯 · 默认技能顺序 · Ram形态首回合技能", "ruleIndex": 1},
        {"status": "command", "rule": "莉迪亚 · 压制", "ruleIndex": 2, "reservationReleased": True},
        {"status": "command", "rule": "马里斯 · 默认技能顺序", "ruleIndex": 1},
    ]
    return {"status": "complete", "reason": None, "decisions": decisions, "objectiveEvents": [],
            "engine": {"seed": seed, "capturedSeed": 7, "seedOverridden": seed != 7, "bossTurns": 65,
                       "damage": 3050.0, "bossDamageTaken": 3050.0, "commands": 4,
                       "trials": trials, "actions": actions,
                       "actors": [{"actorId": 1, "heroTypeId": 7001, "player": True},
                                  {"actorId": 2, "heroTypeId": 7002, "player": True},
                                  {"actorId": 5, "heroTypeId": 26926, "player": False}]}}


def test_rule_names_with_details_map_to_their_rule() -> None:
    assert _rule_index_by_name(STRATEGY, "马里斯 · 默认技能顺序 · Ram形态首回合技能") == 1
    assert _rule_index_by_name(STRATEGY, "莉迪亚 · 压制") == 2
    assert _rule_index_by_name(STRATEGY, "莉迪亚 · 压制则") is None
    assert _rule_index_by_name(STRATEGY, None) is None


def test_chimera_turn_start_counts_in_its_new_window() -> None:
    assert action_window({"bossTurns": 5, "source": "policy"}) == 0
    assert action_window({"bossTurns": 5, "source": "enemy"}) == 1
    assert action_window({"bossTurns": 6, "source": "policy"}) == 1


def test_run_summary_attributes_actions_to_rules_in_order() -> None:
    summary = summarize_run(result(True), STRATEGY)
    assert summary["completedTrials"] == {8000602: 8}
    mandatory = {item["trialId"]: item for item in summary["mandatory"]}
    assert mandatory[8000602]["completed"] and mandatory[8000602]["bestRatio"] == 1.0
    assert not mandatory[8000611]["started"] and mandatory[8000611]["bestRatio"] == 0.0
    # The start event carries no progress, so the Ultimate window stays empty.
    assert summary["progressByWindow"] == {"1": {"8000602": 1.0}}
    rules = {item["ruleIndex"]: item for item in summary["rules"]}
    assert rules[1]["uses"] == 2 and rules[1]["damage"] == 3000.0
    assert rules[1]["trialGains"] == {"8000602": 1.0}
    assert rules[2]["uses"] == 1 and summary["reservationReleases"] == 1
    assert summary["status"] == "complete" and summary["stuck"] is None
    assert summary["deaths"] == [{"actorId": 2, "heroTypeId": 7002, "bossTurn": 8, "turn": 1}]


def test_aggregate_counts_every_run_in_each_window_and_lists_unused_rules() -> None:
    runs = [json.loads(json.dumps(summarize_run(result(True), STRATEGY))),
            json.loads(json.dumps(summarize_run(result(False, seed=9), STRATEGY)))]
    total = aggregate(runs, STRATEGY)
    trials = {item["trialId"]: item for item in total["trials"]}
    assert trials[8000602]["completedRuns"] == 1 and trials[8000602]["runs"] == 2
    assert trials[8000602]["windows"]["1"]["max"] == 1.0
    assert trials[8000602]["windows"]["1"]["median"] == (1.0 + 0.3) / 2
    assert trials[8000611]["mandatory"] and trials[8000611]["completedRuns"] == 0
    assert total["allMandatoryRuns"] == 0
    rules = {item["ruleIndex"]: item for item in total["rules"] if item["ruleIndex"] is not None}
    assert rules[3]["uses"] == 0 and rules[3]["rule"] == "从未用到"
    assert rules[1]["usesPerRun"] == 2 and rules[1]["runsUsed"] == 2
    assert total["deaths"] == [{"heroTypeId": 7002, "runs": 2, "firstBossTurnMedian": 8.0}]
    assert total["stuckRuns"] == [] and total["reservationReleasesPerRun"] == 1


STUCK = {"reason": "no_matching_rule", "turn": 3, "bossTurns": 7, "form": "Ram", "activeHeroId": 2,
         "activeHeroTypeId": 7002, "activeHeroFormIndex": 0, "skills": [], "rules": [], "detail": "x"}


def stuck_result() -> dict:
    stuck = result(False, seed=11)
    stuck["status"], stuck["reason"], stuck["stuck"] = "stuck", "no_matching_rule", STUCK
    stuck["engine"]["damage"] = 10.0
    stuck["decisions"].append({"status": "stuck", "reason": "no_matching_rule", "stuck": STUCK})
    return stuck


def test_a_stuck_run_counts_for_trials_but_not_for_damage() -> None:
    summary = summarize_run(stuck_result(), STRATEGY)
    assert summary["status"] == "stuck" and summary["stuck"]["bossTurns"] == 7
    runs = [json.loads(json.dumps(summarize_run(result(True), STRATEGY))), json.loads(json.dumps(summary))]
    runs[1]["index"] = 2
    total = aggregate(runs, STRATEGY)
    trials = {item["trialId"]: item for item in total["trials"]}
    assert trials[8000602]["runs"] == 2 and trials[8000602]["completedRuns"] == 1
    assert total["battleEndRuns"] == 1 and total["damage"]["min"] == 3050.0
    assert total["stuckRuns"] == [{"index": 2, "seed": 11, "exact": False, "reason": "no_matching_rule",
                                   "turn": 3, "bossTurns": 7, "form": "Ram", "activeHeroTypeId": 7002,
                                   "rule": None}]


def test_the_session_stops_instead_of_repeating_a_turn() -> None:
    session = ChimeraOfflinePolicySession({"executionMode": "list", "rules": []})
    state = {"bossMode": "chimera", "battle": {"turn": 4}, "activeHeroId": 1, "activeHeroTypeId": 7001,
             "chimera": {"currentForm": "Ram", "turnCount": 2}, "skills": [
                 {"typeId": 101, "slot": 1, "ready": True, "cooldown": 0, "validTargetIds": [5]}]}
    first = session.decide(state)
    assert first["status"] == "stuck" and first["reason"] == "no_matching_rule"
    assert first["stuck"]["skills"][0]["typeId"] == 101 and first["stuck"]["form"] == "Ram"
    for _ in range(6):
        session.decide(state)
    assert session.decide(state)["reason"] == "no_progress"


def test_seeds_start_with_the_captured_battle_and_repeat() -> None:
    seeds = derived_seeds(1987038969, 10)
    assert seeds[0] == 1987038969 and len(set(seeds)) == 10
    assert seeds == derived_seeds(1987038969, 10)
    assert all(-0x80000000 <= seed <= 0x7FFFFFFF for seed in seeds)


def test_service_runs_saves_and_loads_a_simulation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        capture = root / "captures" / "abc-1"
        (capture / "packed").mkdir(parents=True)
        (capture / "packed" / "conversion-report.json").write_text("{}", encoding="utf-8")
        (capture / "capture-provenance.json").write_text(json.dumps({
            "type": "verified_chimera_replay_source", "seed": 7, "stageId": 13049006,
            "teamHeroTypeIds": [7001, 7002], "teamHeroIds": [1, 2], "bossHeroTypeId": 26926}), encoding="utf-8")
        calls = []

        def runner(probe, packed, strategy, *, seed, **_):
            calls.append(seed)
            return result(seed is None, seed=7 if seed is None else seed)

        service = SimulationService(capture_root=root / "captures", simulation_root=root / "sims",
                                    runner=runner, bundle_provider=lambda pid: root)
        assert [item["id"] for item in list_captures(root=root / "captures")] == ["abc-1"]
        assert list_captures(root=root / "captures")[0]["difficulty"] == 6
        try:
            service.start(STRATEGY, {"id": "s1", "name": "测试"}, "missing", 3)
        except ValueError:
            pass
        else:
            raise AssertionError("unknown capture accepted")
        job = service.start(STRATEGY, {"id": "s1", "name": "测试"}, "abc-1", 3)
        for _ in range(200):
            if service.status()["status"] != "running":
                break
            time.sleep(0.02)
        assert service.status()["status"] == "complete", service.status()
        assert calls.count(None) == 1 and len(calls) == 3  # captured seed first, then two others
        summary = service.load(job["id"])
        assert summary["strategy"]["name"] == "测试" and summary["capture"]["difficulty"] == 6
        assert summary["aggregate"]["finishedRuns"] == 3 and summary["aggregate"]["battleEndRuns"] == 3
        assert json.loads((root / "sims" / job["id"] / "strategy.json").read_text("utf-8"))["rules"][2]["name"] == "从未用到"
        detail = service.load_run(job["id"], 1)
        assert [row["source"] for row in detail["timeline"]] == ["enemy", "policy", "policy", "policy"]
        assert detail["timeline"][1]["ruleIndex"] == 1 and detail["timeline"][2]["reservationReleased"] is True
        assert detail["timeline"][3]["trials"][0]["after"] == 1.0
