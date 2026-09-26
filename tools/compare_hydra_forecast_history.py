"""Compare a policy-driven Hydra forecast with the same battle's recorded actions.

Inputs are a forecast report from ``hydra_forecast.py`` and the historical
``historical-actions.jsonl`` export of the live controller. The first
difference in turn, actor, skill, target or pre-command RNG ends the
comparison; nothing after it is claimed to match.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def compare(forecast: dict[str, Any], actions: list[dict[str, Any]],
            observed_marks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    decisions = [item for item in forecast.get("decisions", []) if item.get("status") == "command"]
    by_turn = {item["turn"]: item for item in decisions}
    matched = 0
    rng_matched = 0
    first_difference = None
    for action in actions:
        turn = action.get("turnBefore")
        offline = by_turn.get(turn)
        expected = {
            "turn": turn,
            "playerTurnCount": action.get("playerTurnCountBefore"),
            "actorId": action.get("actorIdLive"),
            "skillTypeId": action.get("skillTypeId"),
            "skillSlot": action.get("skillSlot"),
            "targetId": action.get("targetIdLive"),
        }
        actual = None
        if offline is not None:
            command = offline.get("command") or {}
            actual = {
                "turn": offline.get("turn"),
                "playerTurnCount": offline.get("playerTurnCount"),
                "actorId": offline.get("activeHeroId"),
                "skillTypeId": command.get("skillTypeId"),
                "skillSlot": command.get("skillSlot"),
                "targetId": command.get("targetId"),
            }
        rng = action.get("rngBeforeObserved")
        rng_differs = (offline is not None and rng is not None and offline.get("rngBefore") != rng)
        if actual != expected or rng_differs:
            first_difference = {"actionIndex": action.get("index"), "expected": expected,
                                "forecast": actual,
                                "historicalRngBefore": rng,
                                "forecastRngBefore": offline.get("rngBefore") if offline else None}
            break
        matched += 1
        if rng is not None:
            rng_matched += 1
    report: dict[str, Any] = {
        "schema": 1,
        "scope": "policy_forecast_vs_recorded_live_actions",
        "historicalActions": len(actions),
        "matchedActions": matched,
        "matchedRngCheckpoints": rng_matched,
        "historicalRngCheckpoints": sum(item.get("rngBeforeObserved") is not None for item in actions),
        "firstDifference": first_difference,
        "lastMatchedTurn": actions[matched - 1].get("turnBefore") if matched else None,
    }
    if observed_marks is not None:
        predicted = forecast.get("marks", [])
        mark_rows = []
        for observed in observed_marks:
            candidates = [mark for mark in predicted
                          if mark["actorId"] == observed["actorId"]
                          and mark["applyTurn"] <= observed["firstObservedTurn"]]
            match = candidates[-1] if candidates else None
            mark_rows.append({"observed": observed,
                              "forecast": match,
                              "matched": match is not None
                              and predicted.index(match) == observed_marks.index(observed)})
        report["markComparison"] = mark_rows
        report["allObservedMarksMatched"] = all(row["matched"] for row in mark_rows)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecast", type=Path, required=True)
    parser.add_argument("--actions", type=Path, required=True)
    parser.add_argument("--mark-parity", type=Path,
                        help="report with observedMarkChanges (actorId, firstObservedTurn)")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    forecast = json.loads(args.forecast.read_text(encoding="utf-8"))
    actions = [json.loads(line) for line in args.actions.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    observed = None
    if args.mark_parity:
        parity = json.loads(args.mark_parity.read_text(encoding="utf-8"))
        observed = [{"actorId": change["marked"][0]["actorId"],
                     "heroTypeId": change["marked"][0]["heroTypeId"],
                     "firstObservedTurn": change["firstObservedTurn"]}
                    for change in parity.get("observedMarkChanges", [])]
    report = compare(forecast, actions, observed)
    body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(body, encoding="utf-8")
    print(body, end="")


if __name__ == "__main__":
    main()
