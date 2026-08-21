from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from agent_ipc import AgentIpc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--hero-count", type=int, default=5)
    parser.add_argument("--min-skill-count", type=int, default=3)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    samples: dict[int, dict[str, Any]] = {}
    observed_sequences: set[int] = set()
    error: str | None = None
    deadline = time.monotonic() + args.timeout
    try:
        with AgentIpc(args.pid) as ipc:
            while time.monotonic() < deadline:
                account = ipc.account() or {}
                if (
                    account.get("accountName") != args.account_name
                    or account.get("userId") != args.user_id
                ):
                    raise RuntimeError("bound game account changed during sampling")
                lifecycle = ipc.lifecycle() or {}
                if lifecycle.get("screen") == "result":
                    break
                decision = ipc.decision() or {}
                sequence = decision.get("sequence")
                battle = decision.get("battle") or {}
                hero_type_id = decision.get("activeHeroTypeId")
                visible_skills = [
                    skill
                    for skill in decision.get("skills", [])
                    if isinstance(skill, dict) and skill.get("passive") is not True
                ]
                if (
                    isinstance(sequence, int)
                    and sequence not in observed_sequences
                    and battle.get("waitingForManualCommand") is True
                    and isinstance(hero_type_id, int)
                    and hero_type_id > 0
                    and visible_skills
                ):
                    observed_sequences.add(sequence)
                    candidate = {
                            "sequence": sequence,
                            "heroId": decision.get("activeHeroId"),
                            "heroTypeId": hero_type_id,
                            "heroName": decision.get("activeHeroName"),
                            "round": battle.get("round"),
                            "turn": battle.get("turn"),
                            "playerTurnCount": battle.get("playerTurnCount"),
                            "skills": [
                                {
                                    "slot": skill.get("slot"),
                                    "typeId": skill.get("typeId"),
                                    "name": skill.get("name"),
                                    "ready": skill.get("ready"),
                                    "blocked": skill.get("blocked"),
                                    "validTargetIds": skill.get("validTargetIds"),
                                }
                                for skill in visible_skills
                            ],
                        }
                    previous = samples.get(hero_type_id)
                    if previous is None or len(candidate["skills"]) > len(previous["skills"]):
                        samples[hero_type_id] = candidate
                    if len(samples) >= args.hero_count and all(
                        len(sample["skills"]) >= args.min_skill_count
                        for sample in samples.values()
                    ):
                        break
                time.sleep(0.01)
    except Exception as exc:
        error = str(exc)

    result = {
        "ok": error is None
        and len(samples) >= args.hero_count
        and all(
            len(sample["skills"]) >= args.min_skill_count
            for sample in samples.values()
        ),
        "pid": args.pid,
        "accountName": args.account_name,
        "userId": args.user_id,
        "sampleCount": len(samples),
        "samples": list(samples.values()),
        "error": error,
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(args.output)
    print(encoded, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
