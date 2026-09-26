"""Offline tests for the Hydra battle-start devour-order forecast.

No game files or engine are needed: the isolated engine, JSON conversion and
agent input validation are replaced by fakes where the logic under test is
the forecast bookkeeping itself.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from typing import Any

import chimera_controller as controller
from hydra_forecast import (
    ForecastError,
    evaluate_conditions,
    mark_stream,
    strategy_forecast_issue,
)
from hydra_forecast_live import HydraForecastMonitor, window_difference


WORDS = [[11, 12, 13, 14], [21, 22, 23, 24], [31, 32, 33, 34]]


def forecast_result(marks: list[tuple[int, int, int]], status: str = "complete") -> dict[str, Any]:
    return {
        "status": status, "reason": None if status == "complete" else "policy_stopped",
        "horizon": "turn_limit", "turn": 1000, "elapsedSeconds": 1.0,
        "battleSetupId": "ab" * 16, "seed": 7,
        "decisions": [
            {"turn": turn, "playerTurnCount": index, "activeHeroId": index,
             "activeHeroTypeId": 100 + index, "rngBefore": WORDS[index], "status": "command"}
            for index, turn in enumerate((1, 2, 3))
        ],
        "marks": [{"markIndex": index, "applyTurn": turn, "actorId": actor, "heroTypeId": hero}
                  for index, (turn, actor, hero) in enumerate(marks, 1)],
    }


def live_state(index: int, *, turn: int | None = None, words: list[int] | None = None,
               marked_actor: int | None = 0) -> dict[str, Any]:
    turn = (1, 2, 3)[index] if turn is None else turn
    heroes = []
    for actor in range(3):
        effects = ([{"id": 9, "effectTypeId": 680, "effectKindId": 9020, "applyTurn": 0}]
                   if actor == marked_actor else [])
        heroes.append({"id": actor, "typeId": 100 + actor, "name": f"英雄{actor}",
                       "dead": False, "battlePosition": actor + 1, "effects": effects})
    return {
        "type": "decision_state", "bossMode": "hydra", "pid": 1234, "battleGeneration": 3,
        "battle": {"turn": turn, "playerTurnCount": index, "waitingForManualCommand": True},
        "activeHeroId": index, "activeHeroTypeId": 100 + index,
        "battleRandom": {"available": True, "turn": turn, "words": words or WORDS[index],
                         "battleSetupId": "ab" * 16, "seed": 7},
        "heroes": heroes, "bosses": [],
    }


class FakeIpc:
    def replay_input(self) -> dict[str, Any]:
        return {"status": "captured", "battleGeneration": 3}

    def account(self) -> dict[str, Any]:
        return {"type": "account_state", "accountName": "tester", "userId": 5}

    def lifecycle(self) -> dict[str, Any]:
        return {"screen": "battle", "battle": {"heroTypeIds": [100, 101, 102, 103, 104, 105],
                                               "heroIds": [1, 2, 3, 4, 5, 6]}}


def config(conditions: list[dict[str, Any]], enabled: bool = True) -> dict[str, Any]:
    return {"bossMode": "hydra", "rules": [{"name": "r", "action": {"type": "cast"}}],
            "objectives": {"devourOrderForecast": enabled,
                           "devourOrderRetryConditions": conditions}}


def monitor_with(result: dict[str, Any], messages: list[str], work: Path) -> HydraForecastMonitor:
    provenance = {"battleSetupId": "ab" * 16, "teamHeroIds": [1, 2, 3, 4, 5, 6],
                  "teamHeroTypeIds": [100, 101, 102, 103, 104, 105]}
    return HydraForecastMonitor(
        emit=messages.append,
        runner=lambda *args, **kwargs: copy.deepcopy(result),
        converter=lambda folder, probe: {},
        bundle_provider=lambda pid: work,
        work_root=work / "jobs",
        validator=lambda *args: (provenance, b"[{}]", b"{}"),
    )


def drive(monitor: HydraForecastMonitor, cfg: dict[str, Any], states: list[dict[str, Any]]):
    outcome = None
    for position, state in enumerate(states):
        outcome = monitor.observe(cfg, state, ipc=FakeIpc(), capability_memory=None,
                                  marked_target=controller.hydra_marked_target,
                                  tracker_armed=True)
        if position == 0 and monitor.battle and monitor.battle.job:
            monitor.battle.job.thread.join(timeout=10)
        if outcome is not None:
            return outcome
    return outcome


def test_conditions_use_application_events_and_never_retry_on_unknown():
    result = forecast_result([(0, 0, 100), (50, 1, 101), (90, 0, 100)])
    verdict = evaluate_conditions([{"markIndex": 2, "relation": "isNoneOf", "heroTypeIds": [101]}], result)
    assert verdict["verdict"] == "retry"
    assert verdict["violations"][0]["actualHeroTypeId"] == 101
    verdict = evaluate_conditions([{"markIndex": 3, "relation": "isAnyOf", "heroTypeIds": [100]},
                                   {"markIndex": 9, "relation": "isNoneOf", "heroTypeIds": [100]}], result)
    assert verdict["verdict"] == "continue"
    assert verdict["unresolved"][0]["markIndex"] == 9
    incomplete = forecast_result([(0, 0, 101)], status="unknown")
    verdict = evaluate_conditions([{"markIndex": 1, "relation": "isNoneOf", "heroTypeIds": [101]}], incomplete)
    assert verdict["verdict"] == "unknown"


def test_mark_stream_deduplicates_and_requires_both_event_sources():
    report = {
        "playerActors": [{"actorId": 5, "heroTypeId": 10056, "inventoryHeroId": 9}],
        "resultHungerEvents": [
            {"commandIndex": -1, "actorId": 5, "effectId": 9, "applyTurn": 0},
            {"commandIndex": -1, "actorId": 5, "effectId": 9, "applyTurn": 0},
            {"commandIndex": 3, "actorId": 5, "effectId": 40, "applyTurn": 7},
        ],
        "hungerEvents": [{"actorId": 5, "appliedEffectId": 9, "applyTurn": 0},
                         {"actorId": 5, "appliedEffectId": 40, "applyTurn": 7}],
    }
    marks = mark_stream(report)
    assert [(mark["markIndex"], mark["applyTurn"]) for mark in marks] == [(1, 0), (2, 7)]
    assert all(mark["seenAfterCommand"] for mark in marks)
    # A mark applied in the battle's final command is gone before the scan.
    report["hungerEvents"].pop()
    marks = mark_stream(report)
    assert len(marks) == 2 and marks[1]["seenAfterCommand"] is False
    # A scanned mark the processor results never reported is refused.
    report["hungerEvents"].append({"actorId": 5, "appliedEffectId": 77, "applyTurn": 9})
    try:
        mark_stream(report)
    except ForecastError as error:
        assert str(error) == "mark_event_streams_disagree"
    else:
        raise AssertionError("a scanned mark missing from the result stream must be refused")


def test_strategies_depending_on_unreproduced_inputs_are_refused():
    strategy = {"bossMode": "hydra", "rules": [
        {"when": {"currentDamageAtLeast": 5}, "action": {"type": "cast"}}]}
    assert strategy_forecast_issue(strategy) == "condition_not_forecastable:currentDamageAtLeast"
    assert strategy_forecast_issue({"bossMode": "hydra", "rules": [{"action": {"type": "cast"}}]}) is None


def test_window_difference_reports_rng_and_actor_divergence():
    result = forecast_result([(0, 0, 100)])
    window = {"turn": 2, "playerTurnCount": 1, "activeHeroId": 1, "activeHeroTypeId": 101,
              "words": WORDS[1]}
    assert window_difference(result, window) is None
    assert "随机状态" in window_difference(result, {**window, "words": [1, 2, 3, 4]})
    assert "行动英雄" in window_difference(result, {**window, "activeHeroId": 2})
    assert "不在推演" in window_difference(result, {**window, "turn": 99})


def test_monitor_regroups_only_after_live_windows_match():
    with tempfile.TemporaryDirectory() as folder:
        messages: list[str] = []
        result = forecast_result([(0, 0, 100), (40, 1, 101)])
        monitor = monitor_with(result, messages, Path(folder))
        cfg = config([{"markIndex": 2, "relation": "isNoneOf", "heroTypeIds": [101]}])
        trigger = drive(monitor, cfg, [live_state(0), live_state(1)])
        assert trigger is not None and trigger.mark_index == 2 and trigger.actual_hero_type_id == 101
        assert any("逐项核对一致" in text for text in messages)
        telemetry = monitor.telemetry()
        assert telemetry["verdict"] == "retry" and "执行免费重整" in telemetry["conclusion"]
        work = monitor.battle.job.work
        assert (work / "strategy.json").is_file()
        verdict = json.loads((work / "verdict.json").read_text(encoding="utf-8"))
        assert verdict["reason"] == "retry" and verdict["evaluation"]["verdict"] == "retry"


def test_monitor_refuses_forecast_that_differs_from_the_live_battle():
    with tempfile.TemporaryDirectory() as folder:
        messages: list[str] = []
        result = forecast_result([(0, 0, 100), (40, 1, 101)])
        monitor = monitor_with(result, messages, Path(folder))
        cfg = config([{"markIndex": 2, "relation": "isNoneOf", "heroTypeIds": [101]}])
        trigger = drive(monitor, cfg, [live_state(0), live_state(1, words=[9, 9, 9, 9])])
        assert trigger is None
        assert monitor.battle.status == "unavailable"
        assert any("不一致" in text for text in messages)
        assert monitor.telemetry()["reason"] == "live_window_differs"


def test_monitor_refuses_forecast_with_a_different_opening_mark():
    with tempfile.TemporaryDirectory() as folder:
        messages: list[str] = []
        result = forecast_result([(0, 2, 102), (40, 1, 101)])
        monitor = monitor_with(result, messages, Path(folder))
        cfg = config([{"markIndex": 2, "relation": "isNoneOf", "heroTypeIds": [101]}])
        assert drive(monitor, cfg, [live_state(0), live_state(1)]) is None
        assert any("开局标记不一致" in text for text in messages)


def test_monitor_is_inert_when_disabled_or_attached_mid_battle():
    with tempfile.TemporaryDirectory() as folder:
        messages: list[str] = []
        result = forecast_result([(0, 0, 100), (40, 1, 101)])
        monitor = monitor_with(result, messages, Path(folder))
        disabled = config([{"markIndex": 2, "relation": "isNoneOf", "heroTypeIds": [101]}], enabled=False)
        assert drive(monitor, disabled, [live_state(0), live_state(1)]) is None
        assert monitor.battle is None and not messages
        cfg = config([{"markIndex": 2, "relation": "isNoneOf", "heroTypeIds": [101]}])
        late = live_state(2)
        outcome = monitor.observe(cfg, late, ipc=FakeIpc(), capability_memory=None,
                                  marked_target=controller.hydra_marked_target,
                                  tracker_armed=False)
        assert outcome is None and monitor.battle.status == "not_opening"


def test_strategy_validation_accepts_only_boolean_forecast_flag():
    strategy = {
        "name": "t", "mode": "execute", "bossMode": "hydra",
        "objectives": {"devourOrderForecast": True,
                       "devourOrderRetryConditions": [
                           {"markIndex": 1, "relation": "isNoneOf", "heroTypeIds": [100]}]},
        "safety": {}, "rules": [{"name": "r", "when": {"activeHeroTypeId": [100]},
                                 "action": {"type": "cast", "skillSlot": 1,
                                            "target": {"type": "boss"}}}],
    }
    controller.validate_strategy_config(copy.deepcopy(strategy), boss_mode="hydra")
    strategy["objectives"]["devourOrderForecast"] = "yes"
    try:
        controller.validate_strategy_config(strategy, boss_mode="hydra")
    except ValueError as error:
        assert "devourOrderForecast" in str(error)
    else:
        raise AssertionError("non-boolean forecast flag must be rejected")


def test_never_marked_condition_in_forecast():
    result = forecast_result([(0, 0, 100), (50, 1, 101), (90, 2, 102)])
    result["effectiveMaxTurnsInBattle"] = 1000
    never = {"relation": "neverMarked", "heroTypeIds": [102]}
    verdict = evaluate_conditions([never], result)
    assert verdict["verdict"] == "retry"
    assert verdict["violations"][0]["markIndex"] == 3
    assert verdict["violations"][0]["relation"] == "neverMarked"
    assert verdict["violations"][0]["markLimit"] is None
    verdict = evaluate_conditions([{**never, "markLimit": 2}], result)
    assert verdict["verdict"] == "continue" and not verdict["unresolved"]
    verdict = evaluate_conditions([{"relation": "neverMarked", "heroTypeIds": [105]}], result)
    assert verdict["verdict"] == "continue" and not verdict["unresolved"]
    shortened = {**result, "turn": 400}
    verdict = evaluate_conditions([{"relation": "neverMarked", "heroTypeIds": [105]}], shortened)
    assert verdict["verdict"] == "continue"
    assert verdict["unresolved"][0]["reason"] == "forecast_shorter_than_battle"


def test_never_marked_condition_is_evaluated_live_on_each_new_mark():
    config = {"objectives": {"devourOrderRetryConditions": [
        {"relation": "neverMarked", "heroTypeIds": [102], "markLimit": 3}]}}
    runtime: dict[str, Any] = {}
    names = []
    for marked, turn in ((0, 1), (1, 20), (2, 40)):
        state = live_state(0, turn=turn, marked_actor=marked)
        state["battle"]["playerTurnCount"] = turn
        state["hydra"] = {"turnCount": turn}
        state["pointers"] = {"context": 7}
        trigger, new_mark, armed = controller.evaluate_hydra_devour_retry_trigger(config, state, runtime)
        names.append(new_mark and new_mark["heroTypeId"])
        if marked < 2:
            assert trigger is None
    assert armed is True and names == [100, 101, 102]
    assert trigger is not None and trigger.relation == "neverMarked"
    assert trigger.mark_index == 3 and trigger.mark_limit == 3
    text = controller.hydra_devour_requirement_text(trigger.relation, "英雄2", trigger.mark_limit)
    assert "前 3 个标记内不能成为吞噬目标" in text
    # Outside the limit the same hero no longer triggers a regroup.
    runtime = {}
    limited = {"objectives": {"devourOrderRetryConditions": [
        {"relation": "neverMarked", "heroTypeIds": [102], "markLimit": 2}]}}
    for marked, turn in ((0, 1), (1, 20), (2, 40)):
        state = live_state(0, turn=turn, marked_actor=marked)
        state["battle"]["playerTurnCount"] = turn
        state["hydra"] = {"turnCount": turn}
        state["pointers"] = {"context": 7}
        trigger, _, _ = controller.evaluate_hydra_devour_retry_trigger(limited, state, runtime)
        assert trigger is None


def test_never_marked_condition_validation_and_web_normalization():
    import chimera_web as web

    base = web.default_strategy("hydra")
    strategy = web.normalized_strategy({**base, "objectives": {**base["objectives"],
        "devourOrderRetryConditions": [
            {"relation": "neverMarked", "heroTypeIds": [9906, 9906, 10056], "markIndex": 4},
            {"relation": "neverMarked", "heroTypeIds": [9516], "markLimit": 3},
            {"markIndex": 2, "relation": "isAnyOf", "heroTypeIds": [8896]},
        ]}}, base, "hydra")
    assert strategy["objectives"]["devourOrderRetryConditions"] == [
        {"relation": "neverMarked", "heroTypeIds": [9906, 10056]},
        {"relation": "neverMarked", "heroTypeIds": [9516], "markLimit": 3},
        {"markIndex": 2, "relation": "isAnyOf", "heroTypeIds": [8896]},
    ]
    invalid = copy.deepcopy(strategy)
    invalid["objectives"]["devourOrderRetryConditions"][1]["markLimit"] = 0
    try:
        controller.validate_strategy_config(invalid, boss_mode="hydra")
    except ValueError as error:
        assert "markLimit" in str(error)
    else:
        raise AssertionError("markLimit outside 1..100 must be rejected")


def test_predicted_damage_below_minimum_regroups():
    result = forecast_result([(0, 0, 100), (50, 1, 101)])
    result["hydraDamage"] = 2_980_510_080
    verdict = evaluate_conditions([], result, 10_000_000_000)
    assert verdict["verdict"] == "retry"
    assert verdict["violations"][0]["cause"] == "damage"
    assert verdict["damage"] == {"predicted": 2_980_510_080, "minimum": 10_000_000_000, "met": False}
    verdict = evaluate_conditions([], result, 2_000_000_000)
    assert verdict["verdict"] == "continue" and verdict["damage"]["met"] is True
    assert evaluate_conditions([], result, 0)["damage"] is None
    missing = forecast_result([(0, 0, 100)])
    verdict = evaluate_conditions([], missing, 10_000_000_000)
    assert verdict["verdict"] == "continue"
    assert verdict["unresolved"][0]["reason"] == "predicted_damage_unavailable"


def test_monitor_regroups_on_damage_without_devour_conditions():
    with tempfile.TemporaryDirectory() as folder:
        messages: list[str] = []
        result = forecast_result([(0, 0, 100), (40, 1, 101)])
        result["hydraDamage"] = 5_000_000_000
        monitor = monitor_with(result, messages, Path(folder))
        cfg = config([])
        cfg["objectives"]["minimumDamage"] = 10_000_000_000
        trigger = drive(monitor, cfg, [live_state(0), live_state(1)])
        assert trigger is not None and trigger.cause == "damage"
        assert trigger.predicted_damage == 5_000_000_000
        assert any("预计整场伤害 50.0 亿" in text and "未达到" in text for text in messages)
        telemetry = monitor.telemetry()
        assert telemetry["predictedDamage"] == 5_000_000_000
        assert telemetry["minimumDamage"] == 10_000_000_000
        # Enough damage: continue and say so.
        messages.clear()
        result["hydraDamage"] = 12_000_000_000
        monitor = monitor_with(result, messages, Path(folder))
        assert drive(monitor, cfg, [live_state(0), live_state(1)]) is None
        assert any("已达到" in text and "继续战斗" in text for text in messages)


def test_forecast_launches_never_open_a_console_window():
    import subprocess
    from unittest import mock

    import convert_hydra_replay_source as conversion
    import hydra_forecast

    hidden = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder)
        (source / "battle-setup.json").write_bytes(b"[{}]")
        (source / "battle-settings.json").write_bytes(b"{}")
        probe = source / "probe.exe"
        probe.write_bytes(b"")
        provenance = {"schema": 1, "type": "verified_hydra_replay_source"}
        with mock.patch.object(conversion, "_read_json", return_value=provenance), \
                mock.patch.object(conversion, "_original_file", return_value=b""), \
                mock.patch.object(conversion.subprocess, "run",
                                  return_value=mock.Mock(returncode=1, stderr="", stdout="")) as run:
            try:
                conversion.convert(source, probe)
            except conversion.ConversionError:
                pass
        assert run.call_args.kwargs.get("creationflags") == hidden
        (source / "battle-setup.msgpack").write_bytes(b"x")
        (source / "battle-settings.msgpack").write_bytes(b"x")
        strategy = {"bossMode": "hydra", "rules": [{"action": {"type": "cast"}}]}
        with mock.patch.object(hydra_forecast.subprocess, "Popen", side_effect=OSError("stop")) as popen:
            try:
                hydra_forecast.run_forecast(probe, source, strategy)
            except OSError:
                pass
        assert popen.call_args.kwargs.get("creationflags") == hidden


if __name__ == "__main__":
    for name, function in list(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print("PASS", name)
