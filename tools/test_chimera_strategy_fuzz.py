from __future__ import annotations

import random

from chimera_controller import SUPPORTED_CONDITION_KEYS, evaluate, matches
from test_chimera_strategy_tree import sample_state


def main() -> int:
    randomizer = random.Random(20260809)
    state = sample_state()
    keys = tuple(SUPPORTED_CONDITION_KEYS)
    malformed_values = [
        None,
        True,
        False,
        -1,
        0,
        1,
        0.5,
        "x",
        "",
        [],
        [1, "x"],
        {},
        {"kind": "Fear"},
        {"turnsAtLeast": "x"},
        {"heroTypeId": 12345, "count": 2},
    ]
    for _ in range(20_000):
        matches(
            {randomizer.choice(keys): randomizer.choice(malformed_values)},
            state,
        )

    node_types = [
        "rule",
        "cast",
        "priority",
        "selector",
        "branch",
        "condition",
        "pause",
        "typo",
        None,
        1,
    ]
    for _ in range(10_000):
        value = randomizer.choice(malformed_values)
        node = {
            "type": randomizer.choice(node_types),
            "when": {randomizer.choice(keys): value},
            "action": randomizer.choice(malformed_values),
            "children": randomizer.choice(malformed_values),
            "then": randomizer.choice(malformed_values),
            "else": randomizer.choice(malformed_values),
        }
        evaluate({"strategyTree": node}, state)
    print("chimera-strategy-fuzz-ok: 30000 malformed cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
