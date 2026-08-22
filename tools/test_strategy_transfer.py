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

        service = object.__new__(ChimeraService)
        service.lock = threading.RLock()
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
    print("strategy-transfer-tests-ok")


if __name__ == "__main__":
    main()
