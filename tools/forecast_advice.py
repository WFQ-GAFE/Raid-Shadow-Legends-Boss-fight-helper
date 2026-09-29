"""What went wrong and what to do: explanations for the opening forecasts and simulations.

Every opening forecast (Hydra devour order, Chimera whole battle) and every
strategy simulation that cannot conclude records a stable reason code. The
interface shows the code's explanation and a suggestion next to the
conclusion, in the interface language.
"""
from __future__ import annotations

REPORT = ("持续出现请到 Discord 反馈，并附上数据目录下 logs 文件夹里的日志。",
          "If it keeps happening, report it on Discord with the logs from the data folder's logs directory.")


def _entry(code: str, explain: tuple[str, str], action: tuple[str, str]) -> dict[str, str]:
    return {"code": code, "explain": explain[0], "action": action[0],
            "explainEn": explain[1], "actionEn": action[1]}


def _engine_failure(code: str) -> dict[str, str]:
    return _entry(code, ("离线引擎运行出错或提前退出，这一场没有得到结果。",
                         "The offline engine failed or stopped early; this battle has no result."),
                  ("如果游戏刚更新，先完全关闭并重新打开游戏，再重启工具；否则下一场会自动重试。" + REPORT[0],
                   "If the game was just updated, restart the game and then the tool; otherwise the next battle "
                   "retries automatically. " + REPORT[1]))


def advice(boss: str, code: str | None, detail: str | None = None) -> dict[str, str] | None:
    """Explanation and suggestion for a reason code; None when the code needs none."""
    if not code or code in ("retry", "continue"):
        return None
    head, _, rest = code.partition(":")
    detail = detail or rest or None
    note = f"（{detail}）" if detail else ""
    note_en = f" ({detail})" if detail else ""
    hydra = boss == "hydra"
    if head == "not_opening":
        return _entry(head, ("这场战斗不是从第一回合就由工具接管的，开局推演需要从开局开始的数据。",
                             "This battle was not taken over from its first turn; the opening forecast needs the battle "
                             "from the start."),
                      ("在战斗准备界面先点“开始接管”，再开始战斗。",
                       "Start the takeover on the preparation screen, then start the battle."))
    if head in ("capture_timeout", "capture_unavailable"):
        return _entry(head, (f"游戏内模块没有交出本场的开局数据{note}。",
                             f"The in-game module did not provide this battle's opening data{note_en}."),
                      ("如果游戏最近更新过或工具刚升级，请完全关闭游戏再重新打开。" + REPORT[0],
                       "If the game or the tool was just updated, close the game completely and open it again. "
                       + REPORT[1]))
    if head == "opening_window_passed":
        return _entry(head, ("取得开局数据时战斗已经过了第一次行动，无法从开局推演。",
                             "The battle was already past its first action when the opening data arrived."),
                      ("在战斗准备界面开始接管，不要在战斗进行中才开始。",
                       "Start the takeover on the preparation screen, not during the battle."))
    if head == "opening_validation_failed":
        return _entry(head, (f"本场开局数据没有通过校验{note}，为避免错误的推演已跳过。",
                             f"This battle's opening data failed verification{note_en}; skipped to avoid a wrong forecast."),
                      ("重启游戏后再试。" + REPORT[0], "Restart the game and try again. " + REPORT[1]))
    if head == "setup_failed":
        return error_advice(boss, detail or "")
    if head in ("isolated_engine_failed", "probe_report_missing", "simulation_channel_failed",
                "isolated_engine_not_policy_driven", "no_result", "engine_stop_unknown"):
        return _engine_failure(head)
    if head in ("forecast_timeout", "simulation_timeout"):
        return _entry(head, ("离线推演超过了时间上限，通常是电脑负载太高。",
                             "The offline run exceeded its time limit, usually because the computer was busy."),
                      ("关闭占用 CPU 的程序（包括正在进行的多场策略模拟）后再试。",
                       "Close programs that use a lot of CPU (including running strategy simulations) and try again."))
    if head in ("mark_event_streams_disagree", "decision_state_unavailable", "enemy_ai_command_unavailable",
                "input_missing", "decision_request_invalid", "unexpected_probe_output", "decision_request_exceeds_bound"):
        return _entry(head, (f"离线引擎读取战斗数据时出现不一致{note}，结果不可靠。",
                             f"The offline engine read inconsistent battle data{note_en}; the result is not reliable."),
                      ("属于偶发问题，下一场会重新推演。" + REPORT[0],
                       "This is occasional; the next battle runs again. " + REPORT[1]))
    if head in ("policy_stopped", "no_matching_rule") and (not detail or "no_skill_command" in detail
                                                           or detail == "no_matching_rule" or head == "no_matching_rule"):
        return _entry("no_matching_rule",
                      ("推演到某个回合时，轮到行动的英雄没有一条规则能给出可用的技能；实战走到这里接管也会暂停。",
                       "At some turn the acting champion had no rule giving a usable skill; a live takeover would pause "
                       "there too."),
                      ("给每个英雄加一条不带条件的兜底规则（例如 A1 → 生命最低" + ("蛇头" if hydra else "目标") + "），"
                       "或放宽该英雄规则的条件。在“策略模拟”卡片用最近的开局模拟一次，可以看到具体停在哪个回合。",
                       "Give every champion a fallback rule without conditions (for example A1 on the lowest-health "
                       "target) or relax that champion's conditions. Simulate the latest opening in Strategy Simulation "
                       "to see where it stops."))
    if head in ("policy_command_rejected", "rule_command_not_legal", "engine_rejected_command") or (
            head == "policy_stopped" and detail and "not_legal" in detail):
        return _entry("rule_command_not_legal",
                      ("规则选出的技能或目标在当时不可用（冷却中、目标不存在等），推演无法继续。",
                       "A rule chose a skill or target that was not usable at that moment (on cooldown, target "
                       "missing and so on)."),
                      ("检查相关规则的技能和目标设置，例如"
                       + ("“暴露蛇颈”或指定位置队友当时是否存在" if hydra else "指定目标当时是否存在")
                       + "。在“策略模拟”卡片模拟一次可以看到是哪条规则。",
                       "Check the skill and target of the rules involved (does the chosen target exist at that "
                       "moment?). Simulate once in Strategy Simulation to see which rule it was."))
    if head in ("policy_stopped", "no_progress"):
        return _entry(head, (f"推演中策略停止了{note}。", f"The strategy stopped during the forecast{note_en}."),
                      ("在“策略模拟”卡片用最近的开局模拟一次，报告会显示停在哪个回合以及原因。",
                       "Simulate the latest opening in Strategy Simulation; the report shows where and why it stopped."))
    if head == "policy_evaluation_failed":
        return _entry(head, (f"工具在判断规则时出现内部错误{note}。", f"The tool hit an internal error while evaluating the rules{note_en}."),
                      (REPORT[0], REPORT[1]))
    if head == "condition_not_forecastable":
        return _entry(head, (f"规则里用了开局推演不支持的条件{note}（1.1.0 及以前的版本不支持“当前伤害 ≥”）。",
                             f"A rule uses a condition the opening forecast does not support{note_en} (1.1.0 and earlier "
                             "did not support \"current damage ≥\")."),
                      ("更新到 1.1.1 或以上；在旧版本中可以去掉该条件，或改用回合条件。",
                       "Update to 1.1.1 or later; on older versions remove the condition or use a turn condition."))
    if head == "action_not_forecastable":
        return _entry(head, ("六头蛇策略里有“按当前试炼自动决策”动作，这是奇美拉专用动作，六头蛇推演不支持。",
                             "The Hydra strategy uses \"decide by current trial\", a Chimera-only action the Hydra forecast "
                             "does not support."),
                      ("删除这条规则，或改为普通的技能规则。", "Remove that rule or make it an ordinary skill rule."))
    if head == "strategy_tree_not_supported":
        return _entry(head, ("策略使用旧的流程图格式，推演不支持。", "The strategy uses the old flowchart format."),
                      ("用规则列表重新编写这个策略组后保存。", "Rebuild the strategy as a rule list and save it."))
    if head == "strategy_has_no_rules":
        return _entry(head, ("策略组里没有任何规则。", "The strategy group has no rules."),
                      ("至少为每个英雄添加一条规则。", "Add at least one rule for every champion."))
    if head == "opening_mark_differs":
        return _entry(head, ("推演中开局第一个吞噬标记落在的英雄与实战不同，推演没有正确复现本场战斗。",
                             "The forecast's first devour mark landed on a different champion than in the live battle."),
                      ("通常是游戏刚更新或本场数据异常，下一场会重新推演。" + REPORT[0],
                       "Usually the game was just updated or this battle's data was unusual; the next battle runs again. "
                       + REPORT[1]))
    if head == "no_live_windows":
        return _entry(head, ("推演算完时实战还没有可以核对的回合，无法确认推演与实战一致。",
                             "When the run finished there was no live turn to verify it against."),
                      ("偶发情况，无需处理。", "This is occasional; nothing to do."))
    if head in ("live_window_differs", "live_turn_differs"):
        return _entry(head, (f"已经打过的回合与推演不一致{note}，推演不再可信。",
                             f"Turns already played differ from the forecast{note_en}; it can no longer be trusted."),
                      ("接管期间不要手动操作游戏；修改策略后请重新开始接管。游戏更新后的第一场也可能出现。",
                       "Do not act in the game by hand during a takeover, and restart the takeover after changing the "
                       "strategy. It can also happen in the first battle after a game update."))
    if head == "damage_threshold_too_close":
        return _entry(head, ("推演中伤害恰好落在某条规则“当前伤害 ≥”的阈值附近，实战伤害计数的取整可能让这条规则的触发时机与推演不同。",
                             "The forecast's damage landed right at a rule's \"current damage ≥\" threshold; the live "
                             "counter's rounding could make that rule fire at a different moment."),
                      ("偶发情况；如果经常出现，可以把阈值改成别的数值，或改用回合条件。",
                       "This is occasional; if it happens often, change the threshold or use a turn condition."))
    if head == "simulation_cancelled":
        return _entry(head, ("推演被取消（接管已停止）。", "The run was cancelled (the takeover stopped)."),
                      ("无需处理。", "Nothing to do."))
    return _entry(head, (f"推演没有完成（{code}）。", f"The run did not finish ({code})."),
                  ("在“策略模拟”卡片用最近的开局模拟一次查看详情。" + REPORT[0],
                   "Simulate the latest opening in Strategy Simulation for details. " + REPORT[1]))


def error_advice(boss: str, text: str) -> dict[str, str]:
    """Explanation for an engine-preparation or conversion error message."""
    if "游戏安装目录" in text:
        return _entry("game_not_found", (f"找不到游戏安装目录（{text}）。", f"The game installation was not found ({text})."),
                      ("先打开游戏并登录，再重试。", "Open the game and log in, then try again."))
    if "静态数据" in text:
        return _entry("static_data_missing", ("找不到游戏的本地静态数据缓存。", "The game's local static data cache was not found."),
                      ("打开游戏并进入一次主界面，让游戏下载数据后再试。",
                       "Open the game and reach the main screen once so it downloads its data, then try again."))
    if "离线引擎文件" in text:
        return _entry("engine_copy_failed", (f"复制离线引擎文件失败（{text}）。", f"Copying the offline engine files failed ({text})."),
                      ("检查磁盘剩余空间，确认杀毒软件没有拦截工具的数据目录，然后重启工具。",
                       "Check free disk space and that antivirus software is not blocking the tool's data folder, "
                       "then restart the tool."))
    if "raid_offline_probe" in text or "probe is missing" in text:
        return _entry("probe_missing", ("工具安装包中缺少离线引擎。", "The offline engine is missing from the tool package."),
                      ("重新下载并解压完整的工具安装包。", "Download the complete tool package again."))
    if "conversion" in text.lower() or "converter" in text.lower() or "JSON" in text:
        return _entry("conversion_failed", (f"开局数据无法转换给离线引擎（{text}）。",
                                            f"The opening data could not be converted for the offline engine ({text})."),
                      ("如果游戏在这场开局之后更新过，请换一场更新的开局；否则下一场会重新抓取。" + REPORT[0],
                       "If the game was updated after this opening, pick a newer one; otherwise the next battle is "
                       "captured again. " + REPORT[1]))
    return _entry("setup_failed", (f"离线引擎没能准备好（{text}）。", f"The offline engine could not be prepared ({text})."),
                  ("确认游戏正在运行后重启工具再试。" + REPORT[0],
                   "Make sure the game is running, restart the tool and try again. " + REPORT[1]))


def failure_advice(boss: str, error: BaseException) -> dict[str, str] | None:
    """Explanation for an exception that stopped a strategy simulation (None when its message is already guidance)."""
    name = type(error).__name__
    if name in ("ForecastSetupError", "ConversionError"):
        return error_advice(boss, str(error))
    if name in ("TeamSetupError", "ValueError"):
        return None  # These messages already say what to do.
    if name == "OSError" or isinstance(error, OSError):
        return _entry("file_error", (f"读写数据文件失败（{error}）。", f"Reading or writing a data file failed ({error})."),
                      ("检查磁盘剩余空间和数据目录的权限后重试。", "Check free disk space and the data folder's permissions, then retry."))
    return _entry("internal_error", (f"工具内部错误（{name}）。", f"Internal tool error ({name})."), (REPORT[0], REPORT[1]))


__all__ = ["advice", "error_advice", "failure_advice"]
