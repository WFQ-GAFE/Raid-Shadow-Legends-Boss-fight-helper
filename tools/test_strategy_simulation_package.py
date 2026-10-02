"""Portable strategy inputs: exact opening bytes and automatic save/import flow.

Every fixture and strategy store lives in a temporary directory. No game is
opened, injected, controlled, or read by these regression checks.
"""
from __future__ import annotations

import copy
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch
import zlib


def write_opening(folder: Path, boss_mode: str = "hydra", *, stage_id: int | None = None,
                  type_ids: list[int] | None = None) -> tuple[dict, dict]:
    """Generate an opening through the same identity validator as a capture."""
    if boss_mode == "hydra":
        from hydra_replay_source import validate_replay_source
        from test_hydra_replay_source import records
        source, decision, account = records()
        validator = lambda: validate_replay_source(source, decision, account, account["accountName"])
    else:
        from chimera_replay_source import validate_chimera_replay_source
        from test_chimera_capture import ACCOUNT, records
        source, decision = records()
        validator = lambda: validate_chimera_replay_source(source, decision, ACCOUNT, ACCOUNT["accountName"])
    setup = json.loads(source["battleSetupsJson"])[0]
    if stage_id is not None:
        source["stageId"] = setup["i"] = stage_id
        decision["chimeraStageId"] = stage_id
        decision[f"{boss_mode}StartSelection"]["stageId"] = stage_id
    if type_ids is not None:
        assert len(type_ids) == len(setup["f"]["h"])
        for slot, hero, type_id in zip(setup["f"]["h"], decision["heroes"], type_ids):
            slot["i"] = hero["typeId"] = type_id
        decision[f"{boss_mode}StartSelection"]["heroTypeIds"] = type_ids
        if boss_mode == "chimera":
            decision["activeHeroTypeId"] = type_ids[0]
    # Non-canonical spacing/newlines make a parse/dump round-trip insufficient:
    # the exact captured bytes must survive export and materialization.
    source["battleSetupsJson"] = json.dumps([setup], indent=2) + "\n"
    source["battleSettingsJson"] = "\n" + source["battleSettingsJson"] + "\n"
    provenance, setups, settings = validator()
    folder.mkdir(parents=True)
    (folder / "battle-setup.json").write_bytes(setups)
    (folder / "battle-settings.json").write_bytes(settings)
    (folder / "capture-provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    (folder / "decision-static.json").write_text('{"trialLookup":{"8000501":{"target":7}}}\n', encoding="utf-8")
    with gzip.open(folder / "decision-states.jsonl.gz", "wb") as stream:
        stream.write(json.dumps(decision, separators=(",", ":")).encode() + b"\n")
        stream.write(b'{"type":"decision_state","later":true}\n')
    # Portable packages carry simulation JSON, never executables or cached
    # MessagePack generated for an installation's runtime.
    (folder / "GameAssembly.dll").write_bytes(b"binary-must-stay-local")
    (folder / "packed").mkdir()
    (folder / "packed" / "battle-setup.msgpack").write_bytes(b"messagepack-must-stay-local")
    return provenance, decision


def team_fixture(provenance: dict, boss_mode: str = "hydra") -> tuple[dict, dict, dict]:
    """A live team's own configuration and all account bonus parts."""
    type_ids = provenance["teamHeroTypeIds"]
    hero_ids = provenance["teamHeroIds"]
    heroes = [{"model": {"heroId": hero_id, "typeId": type_id, "grade": 6, "level": 60,
                         "skills": [{"i": type_id * 10 + 1, "l": 5}], "masteries": [500101]},
               "parts": {"artifacts": [{"i": 9100 + index, "k": 1, "r": 6}], "sets": [],
                         "blessing": None, "relics": None,
                         "academy": {"g": [index + 1]}, "building": {"p": {"h": 1 << 32}},
                         "capitol": {"p": {"h": 2 << 32}}}}
              for index, (hero_id, type_id) in enumerate(zip(hero_ids, type_ids))]
    bonuses = {"heroes": {str(hero["model"]["typeId"]): {key: copy.deepcopy(hero["parts"][key])
                                                            for key in ("academy", "building", "capitol")}
                           for hero in heroes},
               "observatory": {"6": {"p": {"h": 3 << 32}}, "7": {"p": {"h": 3 << 32}}, "8": {"p": {"h": 4 << 32}}}}
    preview = {"status": "captured", "bossMode": boss_mode, "capturedAt": "2026-10-02 09:00:00",
               "observatory": copy.deepcopy(bonuses["observatory"]),
               "heroes": [{"heroId": hero_id, "typeId": type_id, "battle": hero, "power": 12345,
                           "level": 60, "grade": 6, "stats": {"total": [1.0] * 10}}
                          for hero_id, type_id, hero in zip(hero_ids, type_ids, heroes)]}
    from team_setups import preview_team
    team = preview_team(preview, boss_mode)
    assert team is not None
    return team, bonuses, preview


def test_opening_round_trip_preserves_converter_identity() -> None:
    from convert_hydra_replay_source import _original_file
    from strategy_simulation_package import materialize_opening, pack_opening, unpack_opening
    for boss_mode in ("hydra", "chimera"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            provenance, _ = write_opening(source, boss_mode)
            package = json.loads(json.dumps(pack_opening(source, boss_mode)))
            contents = unpack_opening(package, boss_mode)
            assert contents is not None
            for filename in ("battle-setup.json", "battle-settings.json", "capture-provenance.json"):
                assert contents[filename] == (source / filename).read_bytes()
            assert not any(name.endswith((".dll", ".exe", ".msgpack")) for name in contents)
            assert "binary-must-stay-local" not in json.dumps(package)
            restored = materialize_opening(package, root / "imported", boss_mode)
            assert restored.parent == (root / "imported" / boss_mode).resolve()
            assert materialize_opening(package, root / "imported", boss_mode) == restored
            restored_provenance = json.loads((restored / "capture-provenance.json").read_text(encoding="utf-8"))
            for field, filename in (("battleSetups", "battle-setup.json"),
                                    ("battleSettings", "battle-settings.json")):
                data = _original_file(restored, restored_provenance[field], filename)
                assert data == (source / filename).read_bytes()
                assert restored_provenance[field]["sha256"] == hashlib.sha256(data).hexdigest()
            assert restored_provenance["seed"] == provenance["seed"]
            assert restored_provenance["stageId"] == provenance["stageId"]
            assert (restored / "decision-static.json").read_bytes() == (source / "decision-static.json").read_bytes()


def test_opening_refuses_wrong_mode_corruption_and_oversize() -> None:
    from strategy_simulation_package import pack_opening, unpack_opening
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "source"
        write_opening(source)
        package = pack_opening(source, "hydra")
        for invalid in (None, [], {}, {**package, "schema": 99}):
            assert unpack_opening(invalid, "hydra") is None
        assert unpack_opening(package, "chimera") is None
        original = (source / "battle-setup.json").read_bytes()
        for invalid in (original + b" ", b"x" * (2 * 1024 * 1024 + 1)):
            (source / "battle-setup.json").write_bytes(invalid)
            try:
                pack_opening(source, "hydra")
            except ValueError:
                pass
            else:
                raise AssertionError("corrupt or oversized opening was packaged")


def test_opening_refuses_false_provenance_and_bounded_gzip_expansion() -> None:
    from strategy_simulation_package import MAX_FILE_BYTES, pack_opening, unpack_opening
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "source"
        provenance, _ = write_opening(source)
        package = pack_opening(source, "hydra")
        for wrong in ({**provenance, "seed": provenance["seed"] + 1},
                      {**provenance, "activeEngineVersion": provenance["activeEngineVersion"] + 1},
                      {**provenance, "schema": True}):
            (source / "capture-provenance.json").write_text(json.dumps(wrong), encoding="utf-8")
            try:
                pack_opening(source, "hydra")
            except ValueError:
                pass
            else:
                raise AssertionError("provenance different from native input identity was packaged")
        # The outer zlib package is small, but the nested optional gzip stream
        # expands beyond the first-decision bound. Reject it before JSON parsing.
        body = json.loads(zlib.decompress(base64.b64decode(package["data"])))
        expanded = b'{"oversized":"' + b"x" * MAX_FILE_BYTES + b'"}'
        body["decision-states.jsonl.gz"] = base64.b64encode(gzip.compress(expanded)).decode("ascii")
        oversized = {**package, "data": base64.b64encode(zlib.compress(json.dumps(body).encode())).decode("ascii")}
        assert unpack_opening(oversized, "hydra") is None
        appended = {**package, "data": base64.b64encode(base64.b64decode(package["data"]) + b"trailing-data").decode("ascii")}
        assert unpack_opening(appended, "hydra") is None


def test_account_bonuses_survive_json_key_conversion_and_require_every_hero() -> None:
    from strategy_simulation_package import valid_account_bonuses
    with tempfile.TemporaryDirectory() as temporary:
        provenance, _ = write_opening(Path(temporary) / "source")
        _, bonuses, _ = team_fixture(provenance)
        ids = provenance["teamHeroTypeIds"]
        integer_keys = {**bonuses, "heroes": {int(key): value for key, value in bonuses["heroes"].items()}}
        assert valid_account_bonuses(integer_keys, ids, "hydra")
        assert valid_account_bonuses(json.loads(json.dumps(integer_keys)), ids, "hydra")
        incomplete = copy.deepcopy(bonuses)
        incomplete["heroes"].pop(str(ids[-1]))
        assert not valid_account_bonuses(incomplete, ids, "hydra")
        assert not valid_account_bonuses(None, ids, "hydra")


def test_opening_selection_uses_stage_then_order_and_ignores_other_boss() -> None:
    from strategy_simulation_package import select_opening
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        base_ids = [10000 + index for index in range(6)]
        correct = root / "correct"
        reordered = root / "reordered"
        wrong_stage = root / "wrong-stage"
        wrong_boss = root / "wrong-boss"
        write_opening(correct)
        write_opening(reordered, type_ids=list(reversed(base_ids)))
        write_opening(wrong_stage, stage_id=8019002)
        write_opening(wrong_boss, "chimera")
        for index, folder in enumerate((correct, reordered, wrong_stage, wrong_boss)):
            os.utime(folder, (1700000000 + index, 1700000000 + index))
        assert select_opening([root], "hydra", base_ids, 8019001) == correct
        assert select_opening([root], "hydra", base_ids, 8019002) == wrong_stage
        assert select_opening([root], "hydra", list(reversed(base_ids)), 8019001) == reordered
        assert select_opening([root], "chimera") == wrong_boss
        assert select_opening([root / "does-not-exist"], "hydra") is None


def test_package_status_rejects_stale_team_order_and_reports_missing_inputs() -> None:
    from strategy_simulation_package import pack_opening, package_status
    from team_preview import display_preview, portable_snapshot
    from team_setups import exported_team
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / "opening"
        provenance, _ = write_opening(source)
        team, bonuses, preview = team_fixture(provenance)
        snapshot = exported_team({**team, "display": portable_snapshot(display_preview(preview)),
                                  "savedAt": "2026-10-02 09:00:00"})
        assert snapshot is not None
        config = {"team": {"heroTypeIds": provenance["teamHeroTypeIds"]},
                  "simulationPackage": {"schema": 1, "bossMode": "hydra", "savedAt": "2026-10-02 09:00:00",
                                        "team": snapshot, "opening": pack_opening(source, "hydra"),
                                        "accountBonuses": bonuses}}
        status = package_status(config, "hydra")
        assert status["status"] == "complete" and status["team"] and status["opening"] and status["accountBonuses"]
        partial = copy.deepcopy(config)
        partial["simulationPackage"].pop("opening")
        assert package_status(partial, "hydra")["status"] == "partial"
        assert "opening" in package_status(partial, "hydra")["missing"]
        changed = copy.deepcopy(config)
        changed["team"]["heroTypeIds"].reverse()
        assert not package_status(changed, "hydra")["team"]
        assert not package_status(config, "chimera")["team"]
        assert package_status({}, "hydra")["status"] == "unavailable"


def isolated_service(root: Path, preview: dict | None = None, bonuses: dict | None = None):
    """Use real strategy storage and capture discovery with game reads stubbed."""
    from account_stores import StrategyAccount
    from chimera_simulation_service import SimulationService
    from chimera_web import ChimeraService
    from hydra_simulation_service import HydraSimulationService
    from team_preview import TeamSnapshotStore
    from team_setups import StrategyTeamStore, TeamSetupError
    service = object.__new__(ChimeraService)
    service.lock = threading.RLock()
    service._store_context = threading.local()
    service.stopping = threading.Event()
    service.package_refreshes = {}
    service.controller = SimpleNamespace(snapshot=lambda *args, **kwargs: {"running": False})
    service.processes = {}
    service.team_snapshots = TeamSnapshotStore(root / "snapshots")
    service.portable_capture_root = root / "portable-openings"
    service.strategy_account = lambda: StrategyAccount(None, "test", root / "strategy.json")
    team_store = StrategyTeamStore(root / "teams")
    service.strategy_team_store = lambda: team_store
    service.simulations = SimulationService(capture_root=root / "chimera-capture", simulation_root=root / "chimera-runs")
    service.hydra_simulations = HydraSimulationService(
        capture_roots={"capture": root / "hydra-capture", "forecast": root / "hydra-forecast"},
        simulation_root=root / "hydra-runs")
    service.prepared_team_preview = lambda *args, **kwargs: copy.deepcopy(preview)
    service.check_team_setups = lambda *args, **kwargs: None

    def read_team(*args, **kwargs):
        if preview is None:
            raise TeamSetupError("test has no game process")
        return copy.deepcopy(preview)

    def read_bonuses(*args, **kwargs):
        if bonuses is None:
            raise TeamSetupError("test has no game process")
        return copy.deepcopy(bonuses)

    service.team_data = read_team
    service.account_bonuses = read_bonuses
    return service


@patch("chimera_web.desktop_lifecycle_log", new=lambda *args, **kwargs: None)
def test_save_exports_and_imports_complete_inputs_without_snapshot_request() -> None:
    from chimera_web import strategy_template
    from strategy_simulation_package import package_status
    from strategy_storage import revision
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        provenance, _ = write_opening(root / "author" / "hydra-capture" / "opening")
        _, bonuses, preview = team_fixture(provenance)
        author = isolated_service(root / "author", preview, bonuses)
        config = strategy_template("hydra")
        config["name"] = "Complete portable Hydra"
        config["team"] = {"heroTypeIds": provenance["teamHeroTypeIds"],
                          "heroInstanceIds": provenance["teamHeroIds"]}
        # A normal save is sufficient. There is no separate snapshot request,
        # preparation-screen confirmation, or later export-time game read.
        saved = author.save_strategy(config, "hydra", pid=222)
        assert package_status(saved["config"], "hydra")["status"] == "complete"
        assert revision(saved["config"]) == saved["revision"]
        persisted = author.strategy("hydra")
        assert persisted["simulationPackage"] == saved["config"]["simulationPackage"]
        document = author.export_strategy_profile("default", "hydra")
        assert document["simulationPackage"] == persisted["simulationPackage"]
        assert document["teamSnapshot"] == document["simulationPackage"]["team"]
        assert "heroInstanceIds" not in document["strategy"]["team"]
        recipient = isolated_service(root / "recipient")
        received = recipient.import_strategy_profile(json.loads(json.dumps(document)), "hydra")
        received_config = received["config"]
        assert "heroInstanceIds" not in received_config["team"]
        assert package_status(received_config, "hydra")["status"] == "complete"
        # The importer can keep sharing the exact authored environment after
        # the original capture and game process have disappeared.
        passed_on = recipient.export_strategy_profile(received["activeStrategyId"], "hydra")
        assert passed_on["simulationPackage"] == document["simulationPackage"]
        chosen = recipient.simulation_team({"teamSource": "author", "strategyId": received["activeStrategyId"]},
                                           received_config, "hydra")
        assert chosen is not None
        assert chosen["heroes"][0]["parts"]["building"] == bonuses["heroes"][str(provenance["teamHeroTypeIds"][0])]["building"]
        assert len(chosen["heroes"]) == 6
        # The strategy itself is authoritative. A recipient has no separately
        # saved local cache, yet both saved-team selections use its full package.
        assert recipient.strategy_team_store().load("hydra", received["activeStrategyId"]) is None
        chosen_strategy = recipient.simulation_team({"teamSource": "strategy", "strategyId": received["activeStrategyId"]},
                                                    received_config, "hydra")
        assert chosen_strategy is not None and chosen_strategy["heroes"] == chosen["heroes"]
        # Binding an imported strategy to different local copies must not
        # relabel the author's equipment as those copies if a live read fails.
        copied = copy.deepcopy(received_config)
        copied["team"]["heroInstanceIds"] = [value + 1000 for value in provenance["teamHeroIds"]]
        rebound = recipient.save_strategy(copied, "hydra", strategy_id=received["activeStrategyId"],
                                          expected_revision=received["revision"])
        assert not package_status(rebound["config"], "hydra")["team"]


@patch("chimera_web.desktop_lifecycle_log", new=lambda *args, **kwargs: None)
def test_saved_package_wins_over_drifted_team_cache() -> None:
    from chimera_web import strategy_template
    from team_setups import unpack_simulation
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        provenance, _ = write_opening(root / "hydra-capture" / "opening")
        team, bonuses, preview = team_fixture(provenance)
        service = isolated_service(root, preview, bonuses)
        config = strategy_template("hydra")
        config["team"] = {"heroTypeIds": provenance["teamHeroTypeIds"],
                          "heroInstanceIds": provenance["teamHeroIds"]}
        saved = service.save_strategy(config, "hydra", pid=222)
        drifted = copy.deepcopy(team)
        drifted["heroes"][0]["parts"]["artifacts"][0]["i"] = 9999
        service.strategy_team_store().save("hydra", "default", drifted)
        chosen = service.simulation_team({"strategyId": "default", "teamSource": "strategy"}, saved["config"], "hydra")
        package_team = unpack_simulation(saved["config"]["simulationPackage"]["team"]["simulation"])
        assert chosen is not None and package_team is not None
        assert chosen["heroes"][0]["parts"]["artifacts"] == package_team["heroes"][0]["parts"]["artifacts"]


@patch("chimera_web.desktop_lifecycle_log", new=lambda *args, **kwargs: None)
def test_imported_package_start_uses_materialized_capture_and_saved_bonuses() -> None:
    from chimera_web import strategy_template
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "author" / "hydra-capture" / "opening"
        provenance, _ = write_opening(source)
        _, bonuses, preview = team_fixture(provenance)
        author = isolated_service(root / "author", preview, bonuses)
        config = strategy_template("hydra")
        config["team"] = {"heroTypeIds": provenance["teamHeroTypeIds"],
                          "heroInstanceIds": provenance["teamHeroIds"]}
        author.save_strategy(config, "hydra", pid=222)
        document = author.export_strategy_profile("default", "hydra")
        recipient = isolated_service(root / "recipient")
        received = recipient.import_strategy_profile(document, "hydra")
        calls = []

        def fake_start(*args, **kwargs):
            calls.append((args, kwargs))
            return {"id": "test-dispatch", "status": "running"}

        recipient.hydra_simulations.start = fake_start
        body = {"config": received["config"], "strategyId": received["activeStrategyId"],
                "captureId": f"strategy-package:{received['activeStrategyId']}", "teamSource": "author", "runs": 1}
        # Materialization uses the process's data root, so explicitly redirect it
        # even when this function is discovered outside main() by the test suite.
        with patch("chimera_web.PROJECT_ROOT", root / "recipient"):
            started = recipient.start_hydra_simulation(body)
        assert started["id"] == "test-dispatch" and len(calls) == 1
        args, kwargs = calls[0]
        assert args[4] is None
        materialized = kwargs["capture_folder"]
        assert materialized is not None and (root / "recipient") in materialized.parents
        assert (materialized / "battle-setup.json").read_bytes() == (source / "battle-setup.json").read_bytes()
        chosen = kwargs["team"]
        assert chosen is not None and [hero["model"]["heroId"] for hero in chosen["heroes"]] == list(range(1, 7))
        assert chosen["heroes"][0]["parts"]["building"] == bonuses["heroes"][str(provenance["teamHeroTypeIds"][0])]["building"]
        assert not recipient.hydra_simulations.capture_roots["capture"].exists()


@patch("chimera_web.desktop_lifecycle_log", new=lambda *args, **kwargs: None)
def test_failed_refresh_preserves_complete_package_only_for_unchanged_slots() -> None:
    from chimera_web import strategy_template
    from strategy_simulation_package import package_status
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        provenance, _ = write_opening(root / "hydra-capture" / "opening")
        _, bonuses, preview = team_fixture(provenance)
        service = isolated_service(root, preview, bonuses)
        config = strategy_template("hydra")
        config["team"] = {"heroTypeIds": provenance["teamHeroTypeIds"],
                          "heroInstanceIds": provenance["teamHeroIds"]}
        complete = service.save_strategy(config, "hydra", pid=222)
        assert package_status(complete["config"], "hydra")["status"] == "complete"
        offline = isolated_service(root)
        edited = copy.deepcopy(complete["config"])
        edited["name"] = "Rules changed while game is closed"
        retained = offline.save_strategy(edited, "hydra", expected_revision=complete["revision"])
        assert package_status(retained["config"], "hydra")["status"] == "complete"
        assert retained["config"]["simulationPackage"]["team"] == complete["config"]["simulationPackage"]["team"]
        reordered = copy.deepcopy(retained["config"])
        reordered["team"]["heroTypeIds"].reverse()
        reordered["team"]["heroInstanceIds"].reverse()
        stale = offline.save_strategy(reordered, "hydra", expected_revision=retained["revision"])
        assert not package_status(stale["config"], "hydra")["team"]


class InlineThread:
    """Run an offline background callback inline, without scheduling races."""

    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


def pending_opening_service(root: Path):
    from chimera_web import strategy_template
    from strategy_simulation_package import package_status
    # This capture supplies fixture identity but is deliberately outside the
    # service's discoverable capture roots when the normal save occurs.
    provenance, _ = write_opening(root / "fixture")
    _, bonuses, preview = team_fixture(provenance)
    service = isolated_service(root / "service", preview, bonuses)
    config = strategy_template("hydra")
    config["team"] = {"heroTypeIds": provenance["teamHeroTypeIds"],
                      "heroInstanceIds": provenance["teamHeroIds"]}
    saved = service.save_strategy(config, "hydra", pid=222)
    assert package_status(saved["config"], "hydra")["missing"] == ["opening"]
    return service, saved


@patch("chimera_web.desktop_lifecycle_log", new=lambda *args, **kwargs: None)
def test_later_opening_completes_saved_package_without_another_save() -> None:
    from strategy_simulation_package import package_status
    from strategy_storage import revision
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        service, saved = pending_opening_service(root)
        initial_revision = saved["revision"]
        write_opening(service.hydra_simulations.capture_roots["capture"] / "later")
        with patch("chimera_web.threading.Thread", InlineThread):
            service._refresh_pending_package(service.strategy_store(), "hydra", None)
        completed = service.strategy("hydra")
        assert package_status(completed, "hydra")["status"] == "complete"
        assert revision(completed) == initial_revision
        assert completed["rules"] == saved["config"]["rules"]
        assert service.strategy_bundle("hydra")["revision"] == initial_revision


@patch("chimera_web.desktop_lifecycle_log", new=lambda *args, **kwargs: None)
def test_background_package_completion_preserves_active_profile_selection() -> None:
    from boss_modes import active_strategy_id, strategy_for_mode
    from chimera_web import strategy_template
    from strategy_simulation_package import package_status
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        service, _ = pending_opening_service(root)
        write_opening(service.hydra_simulations.capture_roots["capture"] / "later")
        callbacks = []

        class DeferredThread(InlineThread):
            def start(self):
                callbacks.append(self.target)

        with patch("chimera_web.threading.Thread", DeferredThread):
            service._refresh_pending_package(service.strategy_store(), "hydra", None)
        assert len(callbacks) == 1
        other = strategy_template("hydra")
        other["team"] = {"heroTypeIds": []}
        selected = service.create_strategy_profile(other, "Another selected profile", "hydra")
        other_id = selected["activeStrategyId"]
        assert other_id != "default"
        callbacks[0]()
        store = service.strategy_store()
        assert active_strategy_id(store, "hydra") == other_id
        assert package_status(strategy_for_mode(store, "hydra", "default"), "hydra")["status"] == "complete"


@patch("chimera_web.desktop_lifecycle_log", new=lambda *args, **kwargs: None)
def test_background_refresh_does_not_overwrite_new_package_with_same_rule_revision() -> None:
    from boss_modes import update_mode_strategy
    from strategy_simulation_package import pack_opening
    from strategy_storage import revision, write_strategy_store
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        service, saved = pending_opening_service(root)
        write_opening(service.hydra_simulations.capture_roots["capture"] / "later-a")
        callbacks = []

        class DeferredThread(InlineThread):
            def start(self):
                callbacks.append(self.target)

        with patch("chimera_web.threading.Thread", DeferredThread):
            service._refresh_pending_package(service.strategy_store(), "hydra", None)
        assert len(callbacks) == 1
        newer_opening = root / "newer-opening"
        write_opening(newer_opening, stage_id=8019002)
        newer = copy.deepcopy(saved["config"])
        newer["simulationPackage"]["opening"] = pack_opening(newer_opening, "hydra")
        # Another metadata producer has committed its newer package while this
        # worker waits. A normal editor save intentionally ignores stale draft
        # metadata, so use the same atomic store operation as such a producer.
        write_strategy_store(service.store_path(), update_mode_strategy(service.strategy_store(), "hydra", newer, "default"))
        winner = service.strategy_bundle("hydra")
        assert winner["revision"] == saved["revision"] == revision(winner["config"])
        assert winner["config"]["simulationPackage"]["opening"]["stageId"] == 8019002
        callbacks[0]()
        assert service.strategy("hydra")["simulationPackage"]["opening"] == winner["config"]["simulationPackage"]["opening"]


def main() -> None:
    resource_root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="raid-strategy-package-") as temporary:
        # Configure modules before their first import; fixtures never use the
        # player's strategy file, caches, live account, or game process.
        os.environ["CHIMERA_PROJECT_ROOT"] = temporary
        os.environ["CHIMERA_RESOURCE_ROOT"] = str(resource_root)
        test_opening_round_trip_preserves_converter_identity()
        test_opening_refuses_wrong_mode_corruption_and_oversize()
        test_opening_refuses_false_provenance_and_bounded_gzip_expansion()
        test_account_bonuses_survive_json_key_conversion_and_require_every_hero()
        test_opening_selection_uses_stage_then_order_and_ignores_other_boss()
        test_package_status_rejects_stale_team_order_and_reports_missing_inputs()
        test_save_exports_and_imports_complete_inputs_without_snapshot_request()
        test_saved_package_wins_over_drifted_team_cache()
        test_imported_package_start_uses_materialized_capture_and_saved_bonuses()
        test_failed_refresh_preserves_complete_package_only_for_unchanged_slots()
        test_later_opening_completes_saved_package_without_another_save()
        test_background_package_completion_preserves_active_profile_selection()
        test_background_refresh_does_not_overwrite_new_package_with_same_rule_revision()
    print("strategy-simulation-package-tests-ok")


if __name__ == "__main__":
    main()
