"""Prepared-team preview: decoding the agent's team_preview slot."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from team_preview import TeamSnapshotStore, decode_preview, icon_name, portable_snapshot, team_key


ONE = 1 << 32


def stats(**values: float) -> str:
    return json.dumps({key: int(value * ONE) for key, value in values.items()})


def raw_preview(status: str = "captured", schema: int = 2) -> dict:
    hero = {"i": 29968, "t": 7376, "g": 6, "l": 60, "e": 0, "p": 75766.4,
            "s": [{"i": 1, "t": 73701, "l": 3}, {"i": 2, "t": 73702, "l": 5}],
            "m": {"m": [500313, 500113], "c": 0}, "da": {"g": 1, "b": 5102, "r": False}}
    return {
        "schema": schema, "type": "team_preview", "bossMode": "chimera", "observedAtTick": 9, "status": status,
        "reason": None if status == "captured" else "heroes_unavailable", "heroIds": [29968, 12],
        "heroes": [
            {"heroId": 29968, "heroJson": json.dumps(hero), "power": 75766.4,
             "artifacts": [{"kind": kind, "set": 47 if kind <= 4 else 1, "rank": 6, "rarity": 5} for kind in range(1, 10)],
             "relic": {"id": 7, "typeId": 53, "rank": 4, "level": 15},
             "baseStats": stats(h=19650, a=936, d=1332, s=110, r=40, t=0, u=0.15, m=0.5),
             "bonusStats": stats(h=77280, a=1067, d=1300, s=179, r=267, t=161, u=0.12, m=0.78)},
            {"heroId": 12, "heroJson": json.dumps(dict(hero, i=12, t=8736, da={"g": 0})), "artifacts": [],
             "statsReason": "hero_stats_failed"},
            {"heroId": 13, "heroJson": "not json"},
        ],
        "names": {"sets": {"47": "护佑", "1": "生命"}, "blessings": {"5102": "宁静"}, "relics": {"53": "暗影乌鸦"},
                  "masteries": {"500313": "英雄气概"}, "bad": 3},
        "icons": {"sets": {"47": "UI/Sets/Protection", "1": "../evil path"},
                  "blessings": {"5102": "UI/BlessingIcons/Tranquility"}}}


def test_decodes_hero_screen_stats_sets_and_names() -> None:
    preview = decode_preview(raw_preview())
    assert preview["status"] == "captured" and preview["heroIds"] == [29968, 12]
    ava = preview["heroes"][0]
    assert ava["typeId"] == 7376 and ava["level"] == 60 and ava["grade"] == 6 and ava["awakened"] == 1
    assert ava["power"] == 75766 and ava["blessing"] == 5102 and ava["masteries"] == [500313, 500113]
    assert ava["skills"] == [{"typeId": 73701, "level": 3}, {"typeId": 73702, "level": 5}]
    assert ava["stats"]["total"][:4] == [96930.0, 2003.0, 2632.0, 289.0]
    assert ava["stats"]["total"][6:8] == [0.27, 1.28]
    assert ava["sets"] == [{"set": 1, "pieces": 5}, {"set": 47, "pieces": 4}] and ava["equipped"] == 9
    assert ava["relic"] == {"typeId": 53, "rank": 4, "level": 15}
    second = preview["heroes"][1]
    assert "stats" not in second and second["statsReason"] == "hero_stats_failed" and "blessing" not in second
    assert len(preview["heroes"]) == 2  # an undecodable hero is left out
    assert preview["names"]["relics"] == {"53": "暗影乌鸦"} and "bad" not in preview["names"]
    assert preview["icons"] == {"sets": {"47": "Protection"}, "blessings": {"5102": "Tranquility"}}


def test_unavailable_or_older_agent_payloads() -> None:
    unavailable = decode_preview(raw_preview("unavailable"))
    assert unavailable["status"] == "unavailable" and unavailable["reason"] == "heroes_unavailable"
    outdated = decode_preview(raw_preview(schema=1))
    assert outdated["status"] == "unavailable" and outdated["reason"] == "agent_outdated"
    assert decode_preview({"type": "other"}) is None


def test_icon_names_are_plain_sprite_names() -> None:
    assert icon_name("Artifacts/Speed_Unknown") == "Speed_Unknown"
    assert icon_name("a\\b\\600190.png") == "600190"
    assert icon_name("x/../../etc passwd") is None and icon_name("") is None and icon_name(None) is None


def test_snapshots_are_portable_and_found_by_team() -> None:
    preview = decode_preview(raw_preview())
    shared = portable_snapshot(preview)
    assert all("heroId" not in hero for hero in shared["heroes"]) and shared["schema"] == 2
    with tempfile.TemporaryDirectory() as temporary:
        store = TeamSnapshotStore(Path(temporary))
        store.remember(preview)
        found = store.find([8736, 7376])
        assert found is not None and found["heroes"][0]["stats"]["total"][0] == 96930.0
        assert store.find([7376]) is None
    assert team_key([300, 100, 0, 200]) == team_key([100, 200, 300]) == "100-200-300"
