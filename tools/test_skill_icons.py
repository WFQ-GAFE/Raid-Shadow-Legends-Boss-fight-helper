"""Skill icons come from the game's VisualSkill records, by skill type id."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import chimera_icons as icons
from chimera_web import hero_catalog_identity


def test_avatar_names_with_a_suffix_still_name_the_hero() -> None:
    for avatar in ("HeroAvatars/1770_temp", "HeroAvatars/1770temp", "HeroAvatars/1770_avatar", "HeroAvatars/1770"):
        assert icons._hero_base_id({"avatar": avatar}, 1776) == 1770, avatar
        assert hero_catalog_identity(1776, {"avatar": avatar}) == 1770, avatar


def test_a_skill_takes_the_icon_its_record_names_not_its_slot() -> None:
    with TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "passive.png").write_bytes(b"png")
        (root / "transform.png").write_bytes(b"png")
        # Alternate-form passive 882505 has its own sprite; slot 5 of the base form is another.
        manifest = {"version": 1, "skills": {"8250:1:5": "transform.png"},
                    "skillIds": {"8250": {"882505": "passive.png"}}}
        (root / "game-assets.json").write_text(json.dumps(manifest), encoding="utf-8")
        with patch.object(icons, "ASSET_CACHE_DIR", root), patch.object(icons, "GAME_ASSET_MANIFEST", root / "game-assets.json"), \
                patch.object(icons, "_GAME_ASSET_MANIFEST_MEMORY", None), patch.object(icons, "_hero_skill_bundle_sources", lambda base: []), \
                patch.object(icons, "_GAME_SKILL_CACHE_CHECKED", set()), patch.object(icons, "_GAME_SKILL_PATHS_MEMORY", {}), \
                patch.object(icons, "_GAME_SKILL_ID_PATHS_MEMORY", {}):
            hero = {"avatar": "HeroAvatars/8250"}
            assert icons.game_skill_asset(8256, hero, {"typeId": 882505, "slot": 5}) == root / "passive.png"
            # Without a record the form and slot still find a sprite.
            assert icons.game_skill_asset(8256, hero, {"typeId": 82599, "slot": 5, "formIndex": 0}) == root / "transform.png"


def test_a_passive_shared_by_a_range_of_heroes_takes_the_range_s_icon() -> None:
    with TemporaryDirectory() as folder:
        root = Path(folder) / "cache"
        resources = Path(folder) / "resources"
        root.mkdir()
        # The Assassin's Creed heroes (10240-10280) share 102505, which no hero bundle records.
        (resources / "SkillIcons_10240_10280_Passive_11.67.0" / "0001").mkdir(parents=True)
        (root / "native-skill-shared-10240-10280-11.67.0.png").write_bytes(b"png")
        (root / "game-assets.json").write_text(json.dumps({"version": 1, "skills": {}, "skillIds": {}}), encoding="utf-8")
        with patch.object(icons, "ASSET_CACHE_DIR", root), patch.object(icons, "GAME_ASSET_MANIFEST", root / "game-assets.json"), \
                patch.object(icons, "_GAME_ASSET_MANIFEST_MEMORY", None), patch.object(icons, "_hero_skill_bundle_sources", lambda base: []), \
                patch.object(icons, "_GAME_SKILL_CACHE_CHECKED", set()), patch.object(icons, "_GAME_SKILL_PATHS_MEMORY", {}), \
                patch.object(icons, "_GAME_SKILL_ID_PATHS_MEMORY", {}), patch.object(icons, "game_resource_directory", lambda: resources):
            shared = root / "native-skill-shared-10240-10280-11.67.0.png"
            assert icons.game_skill_asset(10276, {"avatar": "HeroAvatars/10270"}, {"typeId": 102505, "slot": 5}) == shared
            assert icons.game_skill_asset(10306, {"avatar": "HeroAvatars/10300"}, {"typeId": 103005, "slot": 5}) is None


def test_hero_bundles_are_found_downloaded_with_a_lettered_prefix_and_shipped_with_the_build() -> None:
    with TemporaryDirectory() as folder:
        resources, build = Path(folder) / "resources", Path(folder) / "build"
        for name in ("0245a_BloodMage_1510_11.67.0", "0081b_coldheart_id40_11.67.0", "0081b_coldheart_id40_Res_11.30.0",
                     "0081b_coldheart_id40_11.30.0", "1032_Order_10270_11.75.0", "HeroAvatars_19_11.75.0"):
            (resources / name / "0001").mkdir(parents=True)
            (resources / name / "0001" / "__data").write_bytes(b"x")
        # Starter heroes' bundles ship with the build (Kael's icons are only there).
        for name in ("1510_BloodMage_1510", "1510_BloodMage_Res"):
            shipped = build / "Raid_Data" / "StreamingAssets" / "AssetBundles" / name / "11.70.0" / "5" / "WindowsPlayer"
            shipped.mkdir(parents=True)
            (shipped / f"{name}_11.70.0.unity3d").write_bytes(b"x")
        with patch.object(icons, "game_resource_directory", lambda: resources), patch.object(icons, "game_build_directory", lambda: build):
            assert sorted(path.name for path in icons._hero_skill_bundle_sources(1510)) == ["1510_BloodMage_1510_11.70.0.unity3d", "__data"]
            assert [path.parents[1].name for path in icons._hero_skill_bundle_sources(40)] == ["0081b_coldheart_id40_11.67.0"]
            assert [path.parents[1].name for path in icons._hero_skill_bundle_sources(10270)] == ["1032_Order_10270_11.75.0"]
