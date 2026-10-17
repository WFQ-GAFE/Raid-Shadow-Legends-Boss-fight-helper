"""Portrait recovery uses synthetic bundles and an inert thread launcher only."""
from __future__ import annotations

import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import chimera_icons as icons


@contextmanager
def avatar_cache():
    with TemporaryDirectory() as folder, ExitStack() as stack:
        root = Path(folder)
        clock = [0.0]
        sources: list[Path] = []
        scans: list[float] = []
        decoded: list[str] = []
        threads: list[object] = []
        cancelled = [False]
        broken: set[str] = set()

        class Thread:
            def __init__(self, *, target, name, daemon):
                assert name == "avatar-cache-retry" and daemon
                self.target = target

            def start(self):
                # No real thread can read a user's installed game or cache.
                threads.append(self)

        def bundles():
            scans.append(clock[0])
            return list(sources)

        def load(source):
            decoded.append(source)
            if source in broken:
                raise ValueError("incomplete synthetic download")
            identity = Path(source).read_text(encoding="utf-8")

            def save(destination, format=None):
                data = ("synthetic portrait " + identity).encode("utf-8")
                if hasattr(destination, "write"):
                    destination.write(data)
                else:
                    Path(destination).write_bytes(data)

            image = SimpleNamespace(save=save)
            sprite = SimpleNamespace(m_Name=identity, image=image)
            return SimpleNamespace(objects=[SimpleNamespace(type=SimpleNamespace(name="Sprite"), read=lambda: sprite)])

        values = {
            "ASSET_CACHE_DIR": root / "cache",
            "GAME_ASSET_MANIFEST": root / "cache" / "game-assets.json",
            "_GAME_ASSET_MANIFEST_MEMORY": None,
            "_GAME_AVATAR_CACHE_CHECKED": False,
            "_GAME_AVATAR_PATHS_MEMORY": {},
            "_GAME_AVATAR_NEXT_CHECK": 0.0,
            "_GAME_AVATAR_RETRY_NEEDED": False,
            "_GAME_AVATAR_RETRY_ACTIVE": False,
            "_GAME_AVATAR_CACHE_REVISION": 0,
            "_GAME_AVATAR_FAILED_SOURCES": {},
            "_GAME_AVATAR_MISSING_IDS": set(),
            "_GAME_AVATAR_CANCELLED": lambda: cancelled[0],
            "_avatar_bundle_sources": bundles,
        }
        for name, value in values.items():
            stack.enter_context(patch.object(icons, name, value))
        stack.enter_context(patch.object(icons.time, "monotonic", lambda: clock[0]))
        stack.enter_context(patch.object(icons.threading, "Thread", Thread))
        stack.enter_context(patch.dict("sys.modules", {"UnityPy": SimpleNamespace(load=load)}))
        yield SimpleNamespace(root=root, clock=clock, sources=sources, scans=scans, decoded=decoded,
                              threads=threads, cancelled=cancelled, broken=broken)


def test_missing_sources_recover_without_restarting_or_repeating_every_request() -> None:
    with avatar_cache() as case:
        assert icons.ensure_game_avatar_cache() == {}
        assert len(case.threads) == 1
        for _ in range(50):
            assert icons.ensure_game_avatar_cache() == {}
        assert len(case.scans) == 1 and len(case.threads) == 1
        source = case.root / "avatars.bundle"
        source.write_text("8730", encoding="utf-8")
        case.sources.append(source)

        def wait(_seconds):
            case.clock[0] += icons.AVATAR_SOURCE_RECHECK_SECONDS

        # Run the captured daemon synchronously: it must recover with no new
        # browser image request after the initial 404.
        with patch.object(icons.time, "sleep", wait):
            case.threads[0].target()
        assert icons.game_avatar_cache_revision() == 1
        assert icons.ensure_game_avatar_cache()["8730"].is_file()
        assert case.decoded == [str(source)]
        assert icons._GAME_AVATAR_RETRY_ACTIVE is False
        case.clock[0] += 20
        icons.ensure_game_avatar_cache()
        assert case.decoded == [str(source)], "unchanged bundles must not be decoded again"


def test_cold_start_retains_existing_portraits_and_real_avatar_mapping() -> None:
    with avatar_cache() as case:
        cached = icons.ASSET_CACHE_DIR / "old.png"
        cached.parent.mkdir()
        cached.write_bytes(b"old portrait")
        icons._GAME_ASSET_MANIFEST_MEMORY = {"version": 1, "avatars": {"8250": "old.png"}}
        assert icons.ensure_game_avatar_cache() == {"8250": cached}
        assert icons.game_avatar_cache_revision() == 1
        assert icons.game_hero_asset(8256, {"avatar": "HeroAvatars/8250_temp"}) == cached
        assert len(case.scans) == 1
        source = case.root / "new.bundle"
        source.write_text("8730", encoding="utf-8")
        case.sources.append(source)
        case.clock[0] = 11
        result = icons.ensure_game_avatar_cache()
        assert result["8250"] == cached and result["8730"].is_file()
        assert cached.read_bytes() == b"old portrait"


def test_missing_portrait_keeps_discovery_alive_after_the_first_batch_succeeds() -> None:
    with avatar_cache() as case:
        shipped = case.root / "local.bundle"
        shipped.write_text("8730", encoding="utf-8")
        case.sources.append(shipped)
        assert icons.ensure_game_avatar_cache()["8730"].is_file()
        assert not case.threads
        first_revision = icons.game_avatar_cache_revision()
        assert icons.game_hero_asset(8256, {"avatar": "HeroAvatars/8250"}) is None
        assert len(case.threads) == 1

        downloaded = case.root / "downloaded.bundle"
        downloaded.write_text("8250", encoding="utf-8")
        waits = [0]

        def wait(_seconds):
            waits[0] += 1
            case.clock[0] += icons.AVATAR_SOURCE_RECHECK_SECONDS
            if waits[0] == 2:
                # The first periodic scan sees the same shipped sources; it
                # must keep waiting without decoding that batch again.
                assert case.decoded == [str(shipped)]
                case.sources.append(downloaded)

        # No second game_hero_asset / browser request drives this recovery.
        with patch.object(icons.time, "sleep", wait):
            case.threads[0].target()
        assert icons._GAME_AVATAR_PATHS_MEMORY["8250"].is_file()
        assert icons.game_avatar_cache_revision() > first_revision
        assert case.decoded == [str(shipped), str(downloaded)]
        assert len(case.threads) == 1 and not icons._GAME_AVATAR_RETRY_ACTIVE
        assert not icons._GAME_AVATAR_MISSING_IDS


def test_changed_source_fingerprint_refreshes_existing_portrait_revision() -> None:
    with avatar_cache() as case:
        source = case.root / "avatars.bundle"
        source.write_text("8730", encoding="utf-8")
        case.sources.append(source)
        icons.ensure_game_avatar_cache()
        revision = icons.game_avatar_cache_revision()
        source.write_text("8730-1", encoding="utf-8")
        case.clock[0] = 11
        icons.ensure_game_avatar_cache()
        assert len(case.decoded) == 2
        assert icons.game_avatar_cache_revision() == revision + 1


def test_incomplete_source_has_decoder_backoff_and_keeps_usable_cache() -> None:
    with avatar_cache() as case:
        cached = icons.ASSET_CACHE_DIR / "old.png"
        cached.parent.mkdir()
        cached.write_bytes(b"usable portrait")
        icons._GAME_ASSET_MANIFEST_MEMORY = {"version": 1, "avatars": {"8250": "old.png"}}
        source = case.root / "download.bundle"
        source.write_text("8730", encoding="utf-8")
        case.sources.append(source)
        case.broken.add(str(source))
        assert icons.ensure_game_avatar_cache() == {"8250": cached}
        for second in range(1, 60):
            case.clock[0] = second
            assert icons.ensure_game_avatar_cache() == {"8250": cached}
        assert case.decoded == [str(source)]
        assert len(case.threads) == 1
        case.broken.clear()
        case.clock[0] = 61
        assert icons.ensure_game_avatar_cache()["8730"].is_file()
        assert len(case.decoded) == 2
        assert cached.read_bytes() == b"usable portrait"


def test_deleted_old_cache_entry_does_not_force_a_full_decode_every_refresh() -> None:
    with avatar_cache() as case:
        icons._GAME_ASSET_MANIFEST_MEMORY = {"version": 1, "avatars": {"8250": "already-deleted.png"}}
        source = case.root / "avatars.bundle"
        source.write_text("8730", encoding="utf-8")
        case.sources.append(source)
        icons.ensure_game_avatar_cache()
        case.clock[0] = 11
        icons.ensure_game_avatar_cache()
        assert case.decoded == [str(source)]
        assert set(icons._GAME_ASSET_MANIFEST_MEMORY["avatars"]) == {"8730"}


def test_healthy_bundle_is_not_decoded_or_rewritten_when_another_keeps_failing() -> None:
    with avatar_cache() as case:
        good, bad = case.root / "good.bundle", case.root / "bad.bundle"
        good.write_text("8730", encoding="utf-8")
        bad.write_text("8250", encoding="utf-8")
        case.sources.extend([good, bad])
        case.broken.add(str(bad))
        png = icons.ensure_game_avatar_cache()["8730"]
        revision = icons.game_avatar_cache_revision()
        modified = png.stat().st_mtime_ns
        for second in (61, 122, 183):
            case.clock[0] = second
            icons.ensure_game_avatar_cache()
            assert icons.game_avatar_cache_revision() == revision
            assert png.stat().st_mtime_ns == modified
        assert case.decoded.count(str(good)) == 1
        assert case.decoded.count(str(bad)) == 4
        assert [item["path"] for item in icons._GAME_ASSET_MANIFEST_MEMORY["avatarSources"]] == [str(good)]
        case.broken.clear()
        case.clock[0] = 244
        assert icons.ensure_game_avatar_cache()["8250"].is_file()
        assert case.decoded.count(str(good)) == 1
        assert icons.game_avatar_cache_revision() == revision + 1


def test_replaced_bundle_with_identical_png_does_not_rewrite_or_raise_revision() -> None:
    with avatar_cache() as case:
        source = case.root / "avatars.bundle"
        source.write_text("8730", encoding="utf-8")
        case.sources.append(source)
        png = icons.ensure_game_avatar_cache()["8730"]
        revision = icons.game_avatar_cache_revision()
        modified = png.stat().st_mtime_ns
        original = source.stat()
        os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000_000))
        case.clock[0] = 11
        icons.ensure_game_avatar_cache()
        assert len(case.decoded) == 2
        assert icons.game_avatar_cache_revision() == revision
        assert png.stat().st_mtime_ns == modified


def test_cached_portrait_does_not_wait_for_the_bundle_decode_lock() -> None:
    with avatar_cache() as case:
        source = case.root / "avatars.bundle"
        source.write_text("8730", encoding="utf-8")
        case.sources.append(source)
        png = icons.ensure_game_avatar_cache()["8730"]

        class CannotAcquire:
            def __enter__(self):
                raise AssertionError("cached portrait acquired the decoding lock")

            def __exit__(self, *_args):
                pass

        with patch.object(icons, "_GAME_ASSET_LOCK", CannotAcquire()):
            assert icons.game_hero_asset(8736, {"avatar": "HeroAvatars/8730"}) == png


def test_cancellation_stops_inside_a_bundle_before_the_next_object() -> None:
    with avatar_cache() as case:
        source = case.root / "avatars.bundle"
        source.write_text("synthetic", encoding="utf-8")
        case.sources.append(source)
        read: list[int] = []

        def read_sprite(number):
            read.append(number)
            case.cancelled[0] = True
            return SimpleNamespace(m_Name=str(number), image=SimpleNamespace(save=lambda out, format=None: out.write(b"png")))

        objects = [SimpleNamespace(type=SimpleNamespace(name="Sprite"), read=lambda number=number: read_sprite(number))
                   for number in (8730, 8250)]
        with patch.dict("sys.modules", {"UnityPy": SimpleNamespace(load=lambda _source: SimpleNamespace(objects=objects))}):
            icons.ensure_game_avatar_cache()
        assert read == [8730]
        assert not case.threads


def test_retry_worker_stops_when_the_service_closes() -> None:
    with avatar_cache() as case:
        icons.ensure_game_avatar_cache()

        def wait(_seconds):
            case.cancelled[0] = True

        with patch.object(icons.time, "sleep", wait):
            case.threads[0].target()
        assert len(case.scans) == 1
        assert len(case.threads) == 1
        assert icons._GAME_AVATAR_RETRY_ACTIVE is False


def test_shipped_portraits_do_not_require_a_downloaded_resources_directory() -> None:
    with TemporaryDirectory() as folder:
        root = Path(folder)
        shipped = root / "build" / "Raid_Data" / "StreamingAssets" / "AssetBundles" / "HeroAvatarsLocal_2"
        shipped.mkdir(parents=True)
        bundle = shipped / "portraits.unity3d"
        bundle.write_bytes(b"synthetic")
        with patch.object(icons, "game_resource_directory", lambda: root / "resources-not-created"), \
                patch.object(icons, "game_build_directory", lambda: root / "build"):
            assert icons._avatar_bundle_sources() == [bundle]


def test_revision_getter_does_not_discover_sources_or_start_workers() -> None:
    with avatar_cache() as case:
        assert icons.game_avatar_cache_revision() == 0
        assert not case.scans and not case.decoded and not case.threads
