from __future__ import annotations

import os
import threading
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> None:
    resource_root = Path(__file__).resolve().parent.parent
    with TemporaryDirectory(prefix="raid-strategy-transfer-") as temporary:
        os.environ["CHIMERA_PROJECT_ROOT"] = temporary
        os.environ["CHIMERA_RESOURCE_ROOT"] = str(resource_root)

        from chimera_web import ChimeraService, strategy_template
        from team_preview import TeamSnapshotStore

        service = object.__new__(ChimeraService)
        service.lock = threading.RLock()
        service.team_snapshots = TeamSnapshotStore(Path(temporary) / "snapshots")
        config = strategy_template("hydra")
        config["name"] = "Portable Hydra"
        config["team"] = {
            "heroTypeIds": [100, 200, 300],
            "heroInstanceIds": [1001, 2001, 3001],
        }
        config["rules"] = [
            {
                "name": "portable rule",
                "when": {"activeHeroTypeId": [100]},
                "action": {
                    "type": "cast",
                    "skillSlot": 2,
                    "target": {"type": "devouringHead"},
                },
            }
        ]
        service.save_strategy(config, "hydra")
        document = service.export_strategy_profile("default", "hydra")
        assert document["format"] == "raid-boss-strategy"
        assert document["bossMode"] == "hydra"
        assert "heroInstanceIds" not in document["strategy"]["team"]

        imported = service.import_strategy_profile(document, "hydra")
        assert len(imported["strategyProfiles"]) == 2
        assert imported["activeStrategyId"] != "default"
        assert imported["config"]["team"]["heroTypeIds"] == [100, 200, 300]
        assert "heroInstanceIds" not in imported["config"]["team"]

        empty_document = {
            "format": "raid-boss-strategy",
            "version": 1,
            "bossMode": "hydra",
            "strategy": strategy_template("hydra"),
        }
        empty_import = service.import_strategy_profile(empty_document, "hydra")
        assert empty_import["config"]["rules"] == []

        try:
            service.import_strategy_profile(document, "chimera")
        except ValueError as error:
            assert "Boss" in str(error)
        else:
            raise AssertionError("cross-mode import must be rejected")

        # The author's prepared team travels with the export, without the
        # account's hero and artifact ids, and stays with the imported copy.
        assert "teamSnapshot" not in document
        service.team_snapshots.remember({
            "status": "captured", "bossMode": "hydra", "teamKey": "k", "names": {"sets": {"47": "Set"}},
            "heroes": [{"heroId": 1001 + index, "typeId": type_id, "level": 60,
                        "sets": [{"set": 47, "pieces": 4}], "stats": {"total": [1.0] * 10}}
                       for index, type_id in enumerate([300, 100, 200])]})
        shared = service.export_strategy_profile("default", "hydra")
        snapshot = shared["teamSnapshot"]
        assert [hero["typeId"] for hero in snapshot["heroes"]] == [300, 100, 200]
        assert all("heroId" not in hero and hero["sets"] == [{"set": 47, "pieces": 4}] for hero in snapshot["heroes"])
        received = service.import_strategy_profile(shared, "hydra")
        reference = received["config"]["referenceTeam"]
        assert reference["heroes"][1]["stats"]["total"][0] == 1.0 and reference["names"]["sets"]["47"] == "Set"
        service.team_snapshots = TeamSnapshotStore(Path(temporary) / "other")
        again = service.export_strategy_profile(received["activeStrategyId"], "hydra")
        assert again["teamSnapshot"]["heroes"][0]["typeId"] == 300

        # 1.1.1: the team as it was when the strategy group was saved. Its display
        # data and its battle data travel with the export (no account ids, no
        # account bonuses); the importer keeps them as the author's team and passes
        # them on unchanged.
        from team_setups import unpack_simulation
        heroes = [{"model": {"heroId": 1001 + index, "typeId": type_id, "grade": 6, "level": 60,
                             "skills": [{"i": type_id * 10, "l": 3}], "masteries": [500101]},
                   "parts": {"artifacts": [{"i": 9000 + index}], "sets": [], "academy": {"g": [1]}}}
                  for index, type_id in enumerate([100, 200, 300])]
        display = {"schema": 2, "bossMode": "hydra", "heroes": [{"typeId": 100, "equipped": 9}],
                   "names": {}, "icons": {}, "capturedAt": "2026-09-29 10:00:00"}
        service.strategy_team_store().save("hydra", "default", {
            "heroTypeIds": [100, 200, 300], "heroIds": [1001, 1002, 1003], "heroes": heroes}, display=display)
        own = service.export_strategy_profile("default", "hydra")
        assert own["teamSnapshot"]["heroes"] == display["heroes"] and own["teamSnapshot"]["savedAt"]
        author = unpack_simulation(own["teamSnapshot"]["simulation"])
        assert author["heroTypeIds"] == [100, 200, 300] and [hero["model"]["heroId"] for hero in author["heroes"]] == [1, 2, 3]
        assert "academy" not in author["heroes"][0]["parts"] and author["heroes"][2]["parts"]["artifacts"] == [{"i": 9002}]
        received = service.import_strategy_profile(own, "hydra")
        assert received["config"]["referenceTeam"]["simulation"] == own["teamSnapshot"]["simulation"]
        passed_on = service.export_strategy_profile(received["activeStrategyId"], "hydra")
        assert passed_on["teamSnapshot"]["simulation"] == own["teamSnapshot"]["simulation"]
        # A snapshot of another team (the strategy's team changed since) is not exported.
        service.strategy_team_store().save("hydra", "default", {
            "heroTypeIds": [100, 200, 400], "heroIds": [1001, 1002, 1004], "heroes": heroes}, display=display)
        assert "simulation" not in (service.export_strategy_profile("default", "hydra").get("teamSnapshot") or {})
    print("strategy-transfer-tests-ok")


if __name__ == "__main__":
    main()
