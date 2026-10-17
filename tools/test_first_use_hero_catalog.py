"""Fresh-install display recovery using synthetic data and no game requests."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
from unittest.mock import patch

import chimera_web as web


def hero_data() -> dict:
    return {"schema": 5, "heroes": {"8730": {
        "name": "Marius the Gallant", "forms": [{"skills": [87301, 87302, 87303]}],
        "catalogForms": {"0": [[87301, 87302, 87303]], "6": [[87301, 87302, 87303]]},
    }}, "skills": {
        "87301": {"name": "First skill", "desc": "An enemy attack."},
        "87302": {"name": "Second skill", "cd": 4},
        "87303": {"name": "Third skill", "cd": 6},
    }, "statusEffects": {}}


@contextmanager
def isolated_service():
    with TemporaryDirectory() as folder:
        path = Path(folder) / "hero-catalog.json"
        service = object.__new__(web.ChimeraService)
        service.lock = threading.RLock()
        service.hero_catalog = {}
        service.catalog_epoch = 0
        service.catalog_session = "fresh-install"
        service.hydra_head_catalog = {}
        service.effect_options = []
        service.ui_catalog = {"difficulties": []}
        service.hero_data = SimpleNamespace(snapshot=lambda pid, **kwargs: {"status": "building"})
        service.hero_catalog_mtime_ns = 0
        saved, logs = [], []

        def save(catalog):
            saved.append(copy.deepcopy(catalog))
            path.write_text(json.dumps(catalog), encoding="utf-8")

        with (
            patch.object(web, "HERO_CATALOG", path),
            patch.object(web, "save_hero_catalog", side_effect=save),
            patch.object(web, "desktop_lifecycle_log", side_effect=logs.append),
            patch.object(web, "identify_account", side_effect=AssertionError("No live account reads")),
            patch.object(web, "request_account_bonuses", side_effect=AssertionError("No live agent requests")),
            patch.object(web, "game_avatar_cache_revision", return_value=0),
        ):
            yield service, path, saved, logs


def test_first_use_ready_data_repairs_names_skills_and_revision_without_a_boss() -> None:
    with isolated_service() as (service, path, saved, logs):
        before = service.catalog_payload()["catalogRevision"]
        assert service.hero_data_snapshot(123)["status"] == "building"
        assert not service.hero_catalog and not saved
        data = hero_data()
        service.hero_data.snapshot = lambda pid, **kwargs: {"status": "ready", "data": data}
        service.warm_hero_catalog(123)
        result = service.catalog_payload(before)
        assert result["catalogRevision"] != before
        hero = next(row for row in result["heroes"] if row["typeId"] == 8730)
        assert hero["name"] == "Marius the Gallant"
        assert hero["avatar"] == "HeroAvatars/8730" and 8736 in hero["runtimeTypeIds"]
        assert [(row["slot"], row["name"]) for row in hero["skills"]] == [
            (1, "First skill"), (2, "Second skill"), (3, "Third skill")]
        assert path.is_file() and len(saved) == 1
        assert any("hero_catalog_recovered source=offline" in line for line in logs)
        # Warm polling neither rewrites the cache nor sends full catalogs.
        service.warm_hero_catalog(123)
        assert len(saved) == 1
        assert service.catalog_payload(result["catalogRevision"]) == {"catalogRevision": result["catalogRevision"]}


def test_normal_state_poll_consumes_recovered_data_on_a_non_boss_screen() -> None:
    with isolated_service() as (service, path, saved, logs):
        data = hero_data()
        service.hero_data.snapshot = lambda pid, **kwargs: {"status": "ready", "data": data}
        service.live_state = lambda pid, mode: {"modeReady": False, "statusLabel": "Main screen"}
        service.controller = SimpleNamespace(snapshot=lambda mode, after=None: {"running": False})
        service.store_path = lambda: path.parent / "strategies.json"
        service.strategy_store = web.default_store
        service.prepared_team_preview = lambda pid: None
        service._refresh_pending_package = lambda *args: None
        service.team_sources = lambda *args: {}
        service.strategy_account = lambda: SimpleNamespace(public=lambda: {})
        service.simulation_overview = lambda: {}
        service.team_preview_summary = lambda *args: None
        with patch.object(web, "storage_health", return_value={"ok": True}):
            payload = service.state_payload(123, "chimera")
        assert payload["state"]["modeReady"] is False
        assert payload["heroes"][0]["name"] == "Marius the Gallant"
        assert len(saved) == 1


def test_failed_or_invalid_metadata_never_clears_a_healthy_catalog() -> None:
    with isolated_service() as (service, path, saved, logs):
        original = {8730: {"typeId": 8730, "name": "Game name", "skills": []}}
        service.hero_catalog = copy.deepcopy(original)
        for response in ({"status": "unavailable", "reason": "Resources are still loading"},
                         {"status": "ready", "data": {"heroes": {}, "skills": {}}}):
            service.hero_data.snapshot = lambda pid, **kwargs: response
            assert service.hero_data_snapshot(123)["status"] == "unavailable"
            assert service.hero_catalog == original and service.catalog_epoch == 0
        assert not saved


def test_explicit_metadata_retry_forwards_force_and_then_recovers() -> None:
    with isolated_service() as (service, path, saved, logs):
        calls = []

        def snapshot(pid, **kwargs):
            calls.append((pid, kwargs))
            return {"status": "ready", "data": data} if kwargs.get("force_retry") else {
                "status": "unavailable", "reason": "Resources are still loading"}

        data = hero_data()
        service.hero_data.snapshot = snapshot
        assert service.hero_data_snapshot(123)["status"] == "unavailable"
        assert service.hero_data_snapshot(123, retry=True)["status"] == "ready"
        assert calls == [(123, {}), (123, {"force_retry": True})]
        assert service.hero_catalog[8730]["name"] == "Marius the Gallant"


def test_failed_cache_write_keeps_the_recovered_window_usable() -> None:
    with isolated_service() as (service, path, saved, logs):
        data = hero_data()
        service.hero_data.snapshot = lambda pid, **kwargs: {"status": "ready", "data": data}
        with patch.object(web, "save_hero_catalog", side_effect=PermissionError("Busy synthetic cache")):
            assert service.hero_data_snapshot(123)["status"] == "ready"
        assert service.hero_catalog[8730]["name"] == "Marius the Gallant"
        assert any("hero_catalog_cache_save_failed" in line for line in logs)
        service.warm_hero_catalog(123)
        assert not saved


def test_replaced_partial_catalog_is_repaired_from_the_same_static_snapshot() -> None:
    with isolated_service() as (service, path, saved, logs):
        data = hero_data()
        service.hero_data.snapshot = lambda pid, **kwargs: {"status": "ready", "data": data}
        service.warm_hero_catalog(123)
        service.hero_catalog = {}
        service._catalog_changed()
        service.warm_hero_catalog(123)
        assert service.hero_catalog[8730]["name"] == "Marius the Gallant" and len(saved) == 2


def test_empty_catalog_portraits_use_local_base_assets_without_network() -> None:
    with isolated_service() as (service, path, saved, logs):
        portrait = path.parent / "portrait.png"
        portrait.write_bytes(b"synthetic portrait")
        with (
            patch.object(web, "game_hero_asset", return_value=portrait) as local,
            patch.object(web, "cache_visual_asset", side_effect=AssertionError("No remote asset request")),
        ):
            assert service.asset("hero", ["8736"]) == portrait
            local.assert_called_once_with(8730, {})
            assert service.asset("hero", ["0"]) is None
            assert service.asset("hero", [str(2**31)]) is None


def test_live_avatar_mapping_remains_authoritative() -> None:
    with isolated_service() as (service, path, saved, logs):
        hero = {"typeId": 8730, "name": "Mapped hero", "runtimeTypeIds": [8736], "avatar": "HeroAvatars/9990-skin"}
        service.hero_catalog = {8730: hero}
        portrait = path.parent / "skin.png"
        portrait.write_bytes(b"synthetic skin")
        with patch.object(web, "game_hero_asset", return_value=portrait) as local:
            assert service.asset("hero", ["8736"]) == portrait
            local.assert_called_once_with(9990, hero)


def test_portrait_availability_changes_revision_even_without_catalog_edits() -> None:
    with isolated_service() as (service, path, saved, logs):
        first = service.catalog_payload()["catalogRevision"]
        with patch.object(web, "game_avatar_cache_revision", return_value=1):
            second = service.catalog_payload(first)
        assert second["catalogRevision"] != first and service.catalog_epoch == 0

