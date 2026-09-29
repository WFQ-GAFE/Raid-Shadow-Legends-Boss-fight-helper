"""Every forecast or simulation failure comes with an explanation and a suggestion."""
from __future__ import annotations

from forecast_advice import advice, error_advice, failure_advice

CODES = (
    "not_opening", "capture_timeout", "capture_unavailable", "opening_window_passed", "opening_validation_failed",
    "isolated_engine_failed", "probe_report_missing:tail", "simulation_channel_failed:OSError",
    "isolated_engine_not_policy_driven", "no_result", "forecast_timeout", "simulation_timeout",
    "mark_event_streams_disagree", "decision_state_unavailable", "enemy_ai_command_unavailable", "input_missing:x",
    "policy_stopped:policy_returned_no_skill_command", "policy_stopped:policy_command_not_legal_in_supplied_state",
    "policy_stopped:decision_turn_not_monotonic", "policy_command_rejected:x", "engine_rejected_command",
    "policy_evaluation_failed:KeyError", "condition_not_forecastable:currentDamageAtLeast",
    "action_not_forecastable:executeTrialRecipe", "strategy_tree_not_supported", "strategy_has_no_rules",
    "opening_mark_differs", "no_live_windows", "live_window_differs", "live_turn_differs",
    "damage_threshold_too_close", "simulation_cancelled", "something_new",
)


def test_every_failure_code_is_explained_in_both_languages() -> None:
    for boss in ("hydra", "chimera"):
        for code in CODES:
            note = advice(boss, code)
            assert note is not None, code
            for key in ("explain", "action", "explainEn", "actionEn"):
                assert note[key].strip(), (code, key)
    assert advice("hydra", "retry") is None and advice("hydra", "continue") is None and advice("hydra", None) is None


def test_specific_codes_get_specific_suggestions() -> None:
    assert advice("hydra", "policy_stopped:policy_returned_no_skill_command")["code"] == "no_matching_rule"
    assert "兜底规则" in advice("hydra", "policy_stopped:policy_returned_no_skill_command")["action"]
    assert advice("chimera", "policy_stopped:policy_command_not_legal_in_supplied_state")["code"] == "rule_command_not_legal"
    assert "开始接管" in advice("chimera", "not_opening")["action"]
    assert "（打过的第 3 回合）" in advice("hydra", "live_window_differs", "打过的第 3 回合")["explain"]
    assert advice("hydra", "something_new")["explain"] == "推演没有完成（something_new）。"


def test_engine_preparation_errors_are_classified() -> None:
    assert error_advice("hydra", "找不到游戏安装目录；请先打开游戏")["code"] == "game_not_found"
    assert error_advice("hydra", "未找到游戏本地静态数据缓存")["code"] == "static_data_missing"
    assert error_advice("hydra", "缺少离线引擎文件：GameAssembly.dll")["code"] == "engine_copy_failed"
    assert error_advice("hydra", "未找到离线战斗引擎（raid_offline_probe.exe）")["code"] == "probe_missing"
    assert error_advice("chimera", "Isolated original conversion failed: x")["code"] == "conversion_failed"
    assert advice("hydra", "setup_failed", "找不到游戏安装目录；请先打开游戏")["code"] == "game_not_found"

    class ForecastSetupError(RuntimeError):
        pass

    assert failure_advice("hydra", ForecastSetupError("未找到游戏本地静态数据缓存"))["code"] == "static_data_missing"
    assert failure_advice("chimera", ValueError("队伍需要 1 到 5 名英雄")) is None  # already says what to do
    assert failure_advice("chimera", KeyError("x"))["code"] == "internal_error"


def test_damage_is_printed_in_game_notation() -> None:
    from damage_units import damage_text

    assert damage_text(2_094_830_000) == "2.09B"
    assert damage_text(123_456_789) == "123.5M"
    assert damage_text(12_345_678) == "12.3M"
    assert damage_text(123_456) == "123.5K"
    assert damage_text(950) == "950"
