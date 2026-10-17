"""Synthetic catalog recovery tests. No game, accounts, agent or user cache."""
from __future__ import annotations

import copy
import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import hero_data
from hero_catalog_recovery import catalog_from_hero_data, merge_hero_catalog
from hero_data import HeroDataService, compact


def raw_report() -> dict:
    def champion(identifier: int, forms: list[list[int]]) -> dict:
        return {"id": identifier, "hero": {"Id": identifier, "Forms": [{"SkillTypeIds": ids} for ids in forms]},
                "skills": [{"Id": value} for ids in forms for value in ids]}
    base = champion(1000, [[10001, 10002, 10003]])
    ascended = champion(1006, [[10001, 10022, 10003]])
    ascended["hero"]["Forms"][0]["AdditionalSkillTypeIds"] = [10004, 10005, 10006]
    ascended["skills"] += [{"Id": 10004, "Group": "Passive"},
                            {"Id": 10005, "Visibility": "HiddenOnHud"},
                            {"Id": 10006, "UseLevelOfSkillId": {"hasValue": True, "value": 10003}}]
    mythical = champion(2000, [[20001, 20002, 20003, 20004], [20101, 20102, 20103, 20104]])
    all_ids = {row["Id"] for hero in (base, ascended, mythical) for row in hero["skills"]}
    texts = {f"l10n:skill/name?id={identifier}#static": f"Named ability {identifier}" for identifier in all_ids}
    texts.update({"l10n:hero-type/name?id=1000#static": "First champion",
                  "l10n:hero-type/name?id=2000#static": "Two forms"})
    return {"heroes": [base, ascended, mythical], "texts": texts}


def test_compact_preserves_full_replacement_and_additional_order() -> None:
    data = compact(raw_report())
    hero = data["heroes"]["1000"]
    assert data["schema"] == 5
    assert hero["catalogForms"] == {"0": [[10001, 10002, 10003]],
                                    "6": [[10001, 10022, 10003, 10004, 10005, 10006]]}
    # Existing picker-compatible deltas remain available, but cannot infer slots.
    assert hero["ascension"]["6"]["skills"] == [[10022]]


def test_empty_catalog_recovers_names_avatars_aliases_and_castable_skills() -> None:
    original: dict = {}
    result = merge_hero_catalog(original, compact(raw_report()))
    assert result.changed and result.added == 2 and result.updated == 0 and result.source_count == 2
    assert original == {}
    first = result.catalog[1000]
    assert first["name"] == "First champion" and first["avatar"] == "HeroAvatars/1000"
    assert first["runtimeTypeIds"] == [1000, 1006]
    assert [(row["typeId"], row["slot"]) for row in first["skills"]] == [(10001, 1), (10022, 2), (10003, 3)]
    assert all(row["activeSkill"] and not row["hiddenOnHud"] for row in first["skills"])
    assert all(row["name"].startswith("Named ability ") for row in first["skills"])


def test_multiform_kit_is_preserved_without_form_or_slot_collisions() -> None:
    hero = catalog_from_hero_data(compact(raw_report()))[2000]
    assert hero["isMetamorph"] is True
    assert [(row["formIndex"], row["slot"]) for row in hero["skills"]] == [
        (0, 1), (0, 2), (0, 3), (0, 4), (1, 1), (1, 2), (1, 3), (1, 4)]


def test_old_compact_deltas_do_not_invent_duplicate_a4() -> None:
    data = compact(raw_report())
    del data["heroes"]["1000"]["catalogForms"]
    first = catalog_from_hero_data(data)[1000]
    assert [row["typeId"] for row in first["skills"]] == [10001, 10002, 10003]
    assert [row["slot"] for row in first["skills"]] == [1, 2, 3]


def test_authoritative_fields_and_skill_positions_survive_recovery() -> None:
    existing = {1000: {"typeId": 1000, "name": "Name read from game", "avatar": "HeroAvatars/1000_custom",
                       "runtimeTypeIds": [1006, 1003], "isMetamorph": False, "customMeta": {"skin": 9},
                       "skills": [{"typeId": 10003, "formIndex": 0, "slot": 1, "name": "Game A1",
                                   "description": "Game description", "defaultCooldown": 7, "skinIcon": "real"},
                                  {"typeId": 10001, "formIndex": 0, "slot": 2, "name": "Game A2"},
                                  {"typeId": 10002, "formIndex": 0, "slot": 3, "name": "Game A3"}]}}
    before = copy.deepcopy(existing)
    result = merge_hero_catalog(existing, compact(raw_report()))
    hero = result.catalog[1000]
    assert existing == before
    assert hero["name"] == before[1000]["name"] and hero["avatar"] == before[1000]["avatar"]
    assert hero["runtimeTypeIds"] == [1006, 1003, 1000]
    assert hero["customMeta"] == {"skin": 9} and hero["isMetamorph"] is False
    assert [(row["typeId"], row["slot"]) for row in hero["skills"]] == [(10003, 1), (10001, 2), (10002, 3)]
    assert all(hero["skills"][0][key] == value for key, value in before[1000]["skills"][0].items())


def test_partial_existing_skills_are_filled_and_other_forms_added() -> None:
    existing = {2000: {"typeId": 2000, "name": "Two forms", "skills": [
        {"typeId": 20001, "slot": 1, "formIndex": 0, "name": "技能 1"},
        {"typeId": 20003, "slot": 3, "formIndex": 0, "name": "Live third"}]}}
    result = merge_hero_catalog(existing, compact(raw_report()))
    assert result.updated == 1
    skills = result.catalog[2000]["skills"]
    assert skills[0]["name"] == "Named ability 20001" and skills[1]["name"] == "Live third"
    assert skills[1]["slot"] == 3
    assert {(row["formIndex"], row["slot"]) for row in skills} == {
        (form, slot) for form in (0, 1) for slot in (1, 2, 3, 4)}


def test_no_change_returns_the_original_catalog_object() -> None:
    data = compact(raw_report())
    first = merge_hero_catalog({}, data).catalog
    result = merge_hero_catalog(first, data)
    assert not result.changed and result.catalog is first and result.added == result.updated == 0


def test_malformed_empty_or_unlocalized_results_cannot_replace_healthy_data() -> None:
    existing = {123: {"typeId": 123, "name": "Healthy", "skills": [{"typeId": 1231, "slot": 1}]}}
    cases = [None, {}, {"heroes": {}, "skills": {}}, {"heroes": [], "skills": {}},
             {"heroes": {"1000": {"name": "Champion 1000", "forms": [{"skills": []}]}}, "skills": {}},
             {"heroes": {"1000": {"name": "l10n:hero-type/name?id=1000#static", "forms": []}}, "skills": {}},
             {"heroes": {"0": {"name": "Bad id", "forms": [{"skills": []}]}}, "skills": {}}]
    for bad in cases:
        result = merge_hero_catalog(existing, bad)
        assert not result.changed and result.catalog is existing and result.source_count == 0


def _wait(service: HeroDataService) -> dict:
    for _ in range(200):
        result = service.snapshot(None)
        if result["status"] != "building":
            return result
        time.sleep(0.01)
    raise AssertionError("Synthetic data service did not finish")


def test_cold_start_builds_even_with_a_recent_system_boot() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        service = HeroDataService(root / "cache", bundle_provider=lambda pid: root / "bundle",
                                  runner=lambda probe, bundle: raw_report())
        with patch.object(hero_data.time, "monotonic", return_value=10.0):
            assert service.snapshot(None)["status"] == "building"
        assert _wait(service)["status"] == "ready"


def test_failed_or_empty_export_is_not_cached_and_can_retry_without_game() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        calls = []

        def runner(probe: Path, bundle: Path) -> dict:
            calls.append(bundle)
            return {} if len(calls) == 1 else raw_report()

        service = HeroDataService(root / "cache", bundle_provider=lambda pid: root / "bundle", runner=runner)
        assert service.snapshot(None)["status"] == "building"
        failed = _wait(service)
        assert failed["status"] == "unavailable" and failed["reason"]
        assert not list((root / "cache").glob("*.json"))
        assert service.snapshot(None)["status"] == "unavailable" and len(calls) == 1
        assert service.snapshot(None, force_retry=True)["status"] == "building"
        assert _wait(service)["status"] == "ready" and len(calls) == 2


def test_retry_during_build_does_not_start_a_second_export() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        entered, release = threading.Event(), threading.Event()
        calls = []

        def runner(probe: Path, bundle: Path) -> dict:
            calls.append(bundle)
            entered.set()
            assert release.wait(3)
            return raw_report()

        service = HeroDataService(root / "cache", bundle_provider=lambda pid: root / "bundle", runner=runner)
        assert service.snapshot(None)["status"] == "building" and entered.wait(2)
        try:
            for _ in range(5):
                assert service.snapshot(None, force_retry=True)["status"] == "building"
            assert len(calls) == 1
        finally:
            release.set()
        assert _wait(service)["status"] == "ready"


def test_automatic_retry_after_cooldown_and_healthy_refresh_failure() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        calls = []

        def provider(pid: int | None) -> Path:
            calls.append(pid)
            if len(calls) in (1, 3):
                raise RuntimeError("Synthetic installation temporarily unavailable")
            return root / "bundle"

        service = HeroDataService(root / "cache", bundle_provider=provider,
                                  runner=lambda probe, bundle: raw_report())
        service.snapshot(None)
        assert _wait(service)["status"] == "unavailable"
        with service.lock:
            service.checked_at -= hero_data.RETRY_SECONDS + 1
        service.snapshot(None)
        ready = _wait(service)
        assert ready["status"] == "ready"
        healthy = ready["data"]
        with service.lock:
            service.checked_at -= hero_data.RECHECK_SECONDS + 1
        service.snapshot(None)
        for _ in range(200):
            if not service.building:
                break
            time.sleep(0.01)
        after = service.snapshot(None)
        assert after["status"] == "ready" and after["data"] is healthy
        assert after["refreshError"] == "Synthetic installation temporarily unavailable"


def test_old_and_empty_schema_caches_are_rebuilt_instead_of_ready() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cache = root / "cache"
        cache.mkdir()
        (cache / "empty.json").write_text(json.dumps({"schema": 5, "heroes": {}, "skills": {}}), encoding="utf-8")
        old = compact(raw_report())
        old["schema"] = 4
        (cache / "old.json").write_text(json.dumps(old), encoding="utf-8")
        calls = []

        def runner(probe: Path, bundle: Path) -> dict:
            calls.append(bundle)
            return raw_report()

        service = HeroDataService(cache, bundle_provider=lambda pid: root / "bundle", runner=runner)
        assert service.snapshot(None)["status"] == "building"
        assert _wait(service)["status"] == "ready" and len(calls) == 1


def test_unresolved_localization_is_unavailable_and_retry_rebuilds() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        calls = []

        def runner(probe: Path, bundle: Path) -> dict:
            calls.append(bundle)
            raw = raw_report()
            if len(calls) == 1:
                for key in list(raw["texts"]):
                    if "hero-type/name" in key:
                        raw["texts"][key] = key
            return raw

        service = HeroDataService(root / "cache", bundle_provider=lambda pid: root / "bundle", runner=runner)
        service.snapshot(None)
        assert _wait(service)["status"] == "unavailable"
        assert not list((root / "cache").glob("*.json"))
        service.snapshot(None, force_retry=True)
        assert _wait(service)["status"] == "ready" and len(calls) == 2


def test_skill_level_links_accept_schema4_and_schema5_fragments_without_a_ready_catalog() -> None:
    with tempfile.TemporaryDirectory() as directory:
        for schema in (4, 5):
            root = Path(directory) / str(schema)
            root.mkdir()
            path = root / "skills-only.json"
            path.write_text(json.dumps({"schema": schema, "skills": {
                "47105": {"levelOf": 47101}, "47101": {}, "20004": {"levelOf": 20003}}}), encoding="utf-8")
            assert HeroDataService._load(path) is None  # Strict ready validation stays intact.
            assert hero_data.skill_level_links(root) == {47105: 47101, 20004: 20003}


def test_skill_level_links_reject_malformed_fragment_rows_and_unsupported_schemas() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "invalid-rows.json"
        path.write_text(json.dumps({"schema": 5, "skills": {
            "47105": {"levelOf": 47101}, "bad": {"levelOf": 47101}, "0": {"levelOf": 47101},
            "-1": {"levelOf": 47101}, "10": {"levelOf": True}, "11": {"levelOf": 0},
            "12": {"levelOf": -5}, "13": {"levelOf": "47101"}, "14": None}}), encoding="utf-8")
        assert hero_data.skill_level_links(root) == {47105: 47101}
        for schema in (3, 6):
            other = root / str(schema)
            other.mkdir()
            (other / "unsupported.json").write_text(json.dumps({"schema": schema,
                "skills": {"47105": {"levelOf": 47101}}}), encoding="utf-8")
            assert hero_data.skill_level_links(other) == {}
