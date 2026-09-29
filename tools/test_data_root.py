"""The 1.1.1 data folder and the one-time copy of earlier versions' data."""
import json
import tempfile
from pathlib import Path

from data_root import DATA_DIR_NAME, MARKER, legacy_source, migrate_legacy_data


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def legacy(base: Path, name: str, strategy: str) -> Path:
    root = base / name
    write(root / "config/raid-boss-strategies.user.json", strategy)
    write(root / "config/preview-seeded.json", "{}")
    write(root / "cache/chimera-capture/battle-1/setup.json", "{}")
    write(root / "cache/chimera-visual-assets/native-hero.png", "png")
    write(root / "runtime/agents/abc/RaidChimeraAgent.dll", "dll")
    write(root / "logs/decisions-1.jsonl", "{}\n")
    return root


def test_copies_the_newest_previous_version_once_without_rebuildable_content():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        legacy(base, "WFQ-GAFE/RaidBossStrategyStudio", '{"from": "1.0.5"}')
        flow = legacy(base, "WFQ-GAFE/RaidBossStrategyStudio-Flow-1.0.6", '{"from": "1.1.0"}')
        assert legacy_source(base) == flow
        target = base / DATA_DIR_NAME
        record = migrate_legacy_data(target)
        assert record["from"] == str(flow) and record["files"] == 3
        assert json.loads((target / "config/raid-boss-strategies.user.json").read_text()) == {"from": "1.1.0"}
        assert (target / "cache/chimera-capture/battle-1/setup.json").is_file()
        assert (target / "logs/decisions-1.jsonl").is_file()
        for rebuilt in ("runtime", "cache/chimera-visual-assets", "config/preview-seeded.json"):
            assert not (target / rebuilt).exists()
        assert (target / MARKER).is_file()
        # The old folders stay as they were.
        assert (flow / "runtime/agents/abc/RaidChimeraAgent.dll").is_file()
        # Once migrated, later starts leave the new folder alone.
        (target / "config/raid-boss-strategies.user.json").write_text('{"edited": true}', encoding="utf-8")
        write(flow / "logs/decisions-2.jsonl", "{}\n")
        assert migrate_legacy_data(target) is None
        assert json.loads((target / "config/raid-boss-strategies.user.json").read_text()) == {"edited": True}
        assert not (target / "logs/decisions-2.jsonl").exists()


def test_a_1_1_1_test_build_folder_comes_first_and_its_marker_is_not_copied():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        legacy(base, "WFQ-GAFE/RaidBossStrategyStudio-Flow-1.0.6", '{"from": "1.1.0"}')
        test_build = legacy(base, "WFQ-GAFE/RSL-Boss-helper", '{"from": "1.1.1 test build"}')
        write(test_build / MARKER, '{"from": "the 1.1.0 folder"}')
        target = base / DATA_DIR_NAME
        record = migrate_legacy_data(target)
        assert record["from"] == str(test_build) and record["files"] == 3
        assert json.loads((target / "config/raid-boss-strategies.user.json").read_text()) == {"from": "1.1.1 test build"}
        assert json.loads((target / MARKER).read_text())["from"] == str(test_build)


def test_interrupted_copy_continues_without_overwriting_and_new_users_start_empty():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        old = legacy(base, "WFQ-GAFE/RaidBossStrategyStudio", '{"from": "1.0.5"}')
        target = base / DATA_DIR_NAME
        write(target / "config/raid-boss-strategies.user.json", '{"partial": true}')
        record = migrate_legacy_data(target)
        assert record["from"] == str(old) and record["files"] == 2
        assert json.loads((target / "config/raid-boss-strategies.user.json").read_text()) == {"partial": True}
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / DATA_DIR_NAME
        record = migrate_legacy_data(target)
        assert record["from"] is None and record["files"] == 0 and (target / MARKER).is_file()
