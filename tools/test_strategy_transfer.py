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
    print("strategy-transfer-tests-ok")


if __name__ == "__main__":
    main()
