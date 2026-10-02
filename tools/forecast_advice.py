"""What went wrong and what to do: explanations for the opening forecasts and simulations.

Every opening forecast (Hydra devour order, Chimera whole battle) and every
strategy simulation that cannot conclude records a stable reason code. The
interface shows the code's explanation and a suggestion next to the
conclusion, in the interface language: both are message tokens (ui_text.py),
advice.<code>.why and advice.<code>.do.
"""
from __future__ import annotations

from ui_text import render, ui_text

# Appended to suggestions for problems the tool cannot fix by itself.
REPORT = ui_text("advice.report")
# Codes for rules that left a hero without an action.
STALLS = frozenset({"no_matching_rule", "rule_command_not_legal", "policy_stopped", "no_progress"})


def _entry(code: str, explain: str, action: str) -> dict[str, str]:
    return {"code": code, "explain": explain, "action": action}


def _detail(detail: str | None) -> dict[str, object]:
    return {"hasDetail": bool(detail), "detail": detail or ""}


def _engine_failure(code: str) -> dict[str, str]:
    return _entry(code, ui_text("advice.engineFailed.why"), ui_text("advice.engineFailed.do") + REPORT)


def advice(boss: str, code: str | None, detail: str | None = None, *, report: bool = False) -> dict[str, str] | None:
    """Explanation and suggestion for a reason code; None when the code needs none.

    ``report``: the forecast saved a report, which shows where the rules stalled.
    """
    note = _advice(boss, code, detail)
    if report and note is not None and note["code"] in STALLS:
        note["action"] = ui_text("advice.stallReport.do")
    return note


def _advice(boss: str, code: str | None, detail: str | None) -> dict[str, str] | None:
    if not code or code in ("retry", "continue"):
        return None
    head, _, rest = code.partition(":")
    detail = detail or rest or None
    note = _detail(detail)
    if head == "not_opening":
        return _entry(head, ui_text("advice.notOpening.why"), ui_text("advice.notOpening.do"))
    if head in ("capture_timeout", "capture_unavailable"):
        return _entry(head, ui_text("advice.capture.why", **note), ui_text("advice.capture.do") + REPORT)
    if head == "opening_window_passed":
        return _entry(head, ui_text("advice.openingWindowPassed.why"), ui_text("advice.openingWindowPassed.do"))
    if head == "opening_validation_failed":
        return _entry(head, ui_text("advice.openingInvalid.why", **note), ui_text("advice.openingInvalid.do") + REPORT)
    if head == "setup_failed":
        return error_advice(boss, detail or "")
    if head in ("isolated_engine_failed", "probe_report_missing", "simulation_channel_failed",
                "isolated_engine_not_policy_driven", "no_result", "engine_stop_unknown"):
        return _engine_failure(head)
    if head in ("forecast_timeout", "simulation_timeout"):
        return _entry(head, ui_text("advice.timeout.why"), ui_text("advice.timeout.do"))
    if head in ("mark_event_streams_disagree", "decision_state_unavailable", "enemy_ai_command_unavailable",
                "input_missing", "decision_request_invalid", "unexpected_probe_output", "decision_request_exceeds_bound"):
        return _entry(head, ui_text("advice.inconsistentData.why", **note), ui_text("advice.inconsistentData.do") + REPORT)
    if head in ("policy_stopped", "no_matching_rule") and (not detail or "no_skill_command" in detail
                                                           or detail == "no_matching_rule" or head == "no_matching_rule"):
        return _entry("no_matching_rule", ui_text("advice.noMatchingRule.why"), ui_text("advice.noMatchingRule.do", boss=boss))
    if head in ("policy_command_rejected", "rule_command_not_legal", "engine_rejected_command") or (
            head == "policy_stopped" and detail and "not_legal" in detail):
        return _entry("rule_command_not_legal", ui_text("advice.ruleCommandNotLegal.why"),
                      ui_text("advice.ruleCommandNotLegal.do", boss=boss))
    if head in ("policy_stopped", "no_progress"):
        return _entry(head, ui_text("advice.policyStopped.why", **note), ui_text("advice.policyStopped.do"))
    if head == "policy_evaluation_failed":
        return _entry(head, ui_text("advice.policyEvaluationFailed.why", **note), ui_text("advice.reportOnly"))
    if head == "condition_not_forecastable":
        return _entry(head, ui_text("advice.conditionNotForecastable.why", **note), ui_text("advice.conditionNotForecastable.do"))
    if head == "action_not_forecastable":
        return _entry(head, ui_text("advice.actionNotForecastable.why"), ui_text("advice.actionNotForecastable.do"))
    if head == "strategy_tree_not_supported":
        return _entry(head, ui_text("advice.strategyTree.why"), ui_text("advice.strategyTree.do"))
    if head == "strategy_has_no_rules":
        return _entry(head, ui_text("advice.noRules.why"), ui_text("advice.noRules.do"))
    if head == "opening_mark_differs":
        return _entry(head, ui_text("advice.openingMarkDiffers.why"), ui_text("advice.openingMarkDiffers.do") + REPORT)
    if head == "no_live_windows":
        return _entry(head, ui_text("advice.noLiveWindows.why"), ui_text("advice.noLiveWindows.do"))
    if head in ("live_window_differs", "live_turn_differs"):
        return _entry(head, ui_text("advice.liveDiffers.why", **note), ui_text("advice.liveDiffers.do"))
    if head == "damage_threshold_too_close":
        return _entry(head, ui_text("advice.damageThreshold.why"), ui_text("advice.damageThreshold.do"))
    if head == "simulation_cancelled":
        return _entry(head, ui_text("advice.cancelled.why"), ui_text("advice.cancelled.do"))
    return _entry(head, ui_text("advice.unfinished.why", code=code), ui_text("advice.unfinished.do") + REPORT)


def error_advice(boss: str, text: str) -> dict[str, str]:
    """Explanation for an engine-preparation or conversion error message."""
    # Errors are message tokens or older plain text; both read as their Chinese here.
    chinese = render(text, "zh-CN")
    if "游戏安装目录" in chinese:
        return _entry("game_not_found", ui_text("advice.gameNotFound.why", detail=text), ui_text("advice.gameNotFound.do"))
    if "静态数据" in chinese:
        return _entry("static_data_missing", ui_text("advice.staticDataMissing.why"), ui_text("advice.staticDataMissing.do"))
    if "离线引擎文件" in chinese:
        return _entry("engine_copy_failed", ui_text("advice.engineCopyFailed.why", detail=text), ui_text("advice.engineCopyFailed.do"))
    if "raid_offline_probe" in chinese or "probe is missing" in chinese:
        return _entry("probe_missing", ui_text("advice.probeMissing.why"), ui_text("advice.probeMissing.do"))
    if "conversion" in chinese.lower() or "converter" in chinese.lower() or "JSON" in chinese:
        return _entry("conversion_failed", ui_text("advice.conversionFailed.why", detail=text), ui_text("advice.conversionFailed.do") + REPORT)
    return _entry("setup_failed", ui_text("advice.setupFailed.why", detail=text), ui_text("advice.setupFailed.do") + REPORT)


def failure_advice(boss: str, error: BaseException) -> dict[str, str] | None:
    """Explanation for an exception that stopped a strategy simulation (None when its message is already guidance)."""
    name = type(error).__name__
    if name in ("ForecastSetupError", "ConversionError"):
        return error_advice(boss, str(error))
    if name in ("TeamSetupError", "ValueError"):
        return None  # These messages already say what to do.
    if name == "OSError" or isinstance(error, OSError):
        return _entry("file_error", ui_text("advice.fileError.why", detail=str(error)), ui_text("advice.fileError.do"))
    return _entry("internal_error", ui_text("advice.internalError.why", name=name), ui_text("advice.reportOnly"))


__all__ = ["advice", "error_advice", "failure_advice"]
