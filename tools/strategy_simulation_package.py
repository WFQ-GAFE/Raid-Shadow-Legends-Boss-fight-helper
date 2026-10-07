"""Portable simulation inputs saved with a strategy; never accesses the game.

Only captured JSON and an optional first decision snapshot travel in an opening.
Game binaries remain on the recipient's computer. Original bytes are preserved
because the converter verifies the captured input's lengths and hashes.
"""
from __future__ import annotations

import base64
import binascii
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import time
from typing import Any, Iterable
import zlib

from boss_stages import chimera_health_lost
from strategy_storage import atomic_write_bytes
from team_setups import ordered_team_matches, validate_simulation_snapshot
from ui_text import ui_text
from hydra_replay_source import GUID_HEX
from extract_hydra_playerprefs import summarize_hydra_setup
from chimera_replay_source import summarize_chimera_setup

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_OPENING_BYTES = 8 * 1024 * 1024
MAX_ENCODED_BYTES = 4 * 1024 * 1024
FILES = ("battle-setup.json", "battle-settings.json", "capture-provenance.json")
OPTIONAL_FILES = ("decision-static.json", "decision-states.jsonl.gz")
SOURCE_TYPES = {"hydra": "verified_hydra_replay_source", "chimera": "verified_chimera_replay_source"}


def _json(data: bytes) -> Any:
    return json.loads(data.decode("utf-8-sig"))


def _valid_files(files: dict[str, bytes], boss_mode: str) -> bool:
    try:
        if boss_mode not in SOURCE_TYPES or set(files) - set(FILES + OPTIONAL_FILES):
            return False
        if any(name not in files for name in FILES):
            return False
        if any(not 0 < len(value) <= MAX_FILE_BYTES for value in files.values()):
            return False
        provenance = _json(files["capture-provenance.json"])
        settings = _json(files["battle-settings.json"])
        setup = _json(files["battle-setup.json"])
        if not isinstance(provenance, dict) or provenance.get("type") != SOURCE_TYPES[boss_mode]:
            return False
        if type(provenance.get("schema")) is not int or provenance["schema"] != 1 \
                or not isinstance(provenance.get("battleSetupId"), str) \
                or GUID_HEX.fullmatch(provenance["battleSetupId"]) is None \
                or type(provenance.get("battleGeneration")) is not int or provenance["battleGeneration"] < 0:
            return False
        if not isinstance(setup, list) or len(setup) != 1 or not isinstance(setup[0], dict):
            return False
        if not isinstance(settings, dict) or not settings:
            return False
        for key in ("ActiveEngineVersion", "WarmupBattleRandomCount", "MaxTurnsInBattle"):
            if type(settings.get(key)) is not int or settings[key] < (0 if key == "WarmupBattleRandomCount" else 1):
                return False
        summary = (summarize_hydra_setup if boss_mode == "hydra" else summarize_chimera_setup)(setup[0])
        if summary is None or provenance["battleSetupId"] not in summary["battleSetupIdForms"]:
            return False
        expected = {"seed": summary["seed"], "stageId": summary["stageId"],
                    "accountUserId": summary["teamOwnerId"], "teamHeroIds": summary["inventoryHeroIds"],
                    "teamHeroTypeIds": summary["heroTypeIds"], "activeEngineVersion": settings["ActiveEngineVersion"],
                    "warmupBattleRandomCount": settings["WarmupBattleRandomCount"],
                    "maxTurnsInBattle": settings["MaxTurnsInBattle"]}
        if any(provenance.get(key) != value for key, value in expected.items()):
            return False
        for name, key in (("battle-setup.json", "battleSetups"), ("battle-settings.json", "battleSettings")):
            recorded = provenance.get(key)
            data = files[name]
            if not isinstance(recorded, dict) or recorded.get("file") != name \
                    or recorded.get("bytes") != len(data) \
                    or recorded.get("sha256") != hashlib.sha256(data).hexdigest():
                return False
        if "decision-static.json" in files and not isinstance(_json(files["decision-static.json"]), dict):
            return False
        if "decision-states.jsonl.gz" in files:
            with gzip.GzipFile(fileobj=io.BytesIO(files["decision-states.jsonl.gz"])) as stream:
                line = stream.read(MAX_FILE_BYTES + 1)
            if len(line) > MAX_FILE_BYTES or not isinstance(_json(line), dict):
                return False
        return True
    except (ValueError, TypeError, KeyError, OSError, EOFError, zlib.error):
        return False


def pack_opening(folder: Path, boss_mode: str) -> dict[str, Any]:
    files = {name: (folder / name).read_bytes() for name in FILES}
    static = folder / "decision-static.json"
    if static.is_file():
        files[static.name] = static.read_bytes()
    decisions = folder / "decision-states.jsonl.gz"
    if decisions.is_file():
        with gzip.open(decisions, "rb") as stream:
            line = stream.readline(MAX_FILE_BYTES + 1)
        if 0 < len(line) <= MAX_FILE_BYTES:
            files[decisions.name] = gzip.compress(line, mtime=0)
    if not _valid_files(files, boss_mode):
        raise ValueError(ui_text("package.invalidData"))
    body = json.dumps({name: base64.b64encode(value).decode("ascii") for name, value in files.items()},
                      separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_OPENING_BYTES:
        raise ValueError(ui_text("package.invalidData"))
    provenance = _json(files["capture-provenance.json"])
    data = base64.b64encode(zlib.compress(body, 9)).decode("ascii")
    if len(data) > MAX_ENCODED_BYTES:
        raise ValueError(ui_text("package.invalidData"))
    return {"schema": 1, "format": "zlib+base64", "bossMode": boss_mode, "data": data,
            "sourceId": folder.name, "capturedAt": time.strftime("%Y-%m-%d %H:%M", time.localtime(folder.stat().st_mtime)),
            **{key: provenance.get(key) for key in ("stageId", "seed", "bossHeroTypeId", "activeEngineVersion")}}


def unpack_opening(package: Any, boss_mode: str) -> dict[str, bytes] | None:
    if not isinstance(package, dict) or type(package.get("schema")) is not int or package["schema"] != 1 \
            or package.get("format") != "zlib+base64" or package.get("bossMode") != boss_mode \
            or not isinstance(package.get("data"), str) or len(package["data"]) > MAX_ENCODED_BYTES:
        return None
    try:
        inflate = zlib.decompressobj()
        raw = inflate.decompress(base64.b64decode(package["data"], validate=True), MAX_OPENING_BYTES + 1)
        if len(raw) > MAX_OPENING_BYTES or inflate.unconsumed_tail or not inflate.eof or inflate.unused_data:
            return None
        body = _json(raw)
        if not isinstance(body, dict) or any(not isinstance(value, str) for value in body.values()):
            return None
        files = {name: base64.b64decode(value, validate=True) for name, value in body.items()}
        if not _valid_files(files, boss_mode):
            return None
        provenance = _json(files["capture-provenance.json"])
        if any(package.get(key) != provenance.get(key) for key in ("stageId", "seed", "activeEngineVersion")):
            return None
        return files
    except (binascii.Error, zlib.error, ValueError, TypeError, OSError, EOFError):
        return None


def materialize_opening(package: Any, root: Path, boss_mode: str) -> Path:
    files = unpack_opening(package, boss_mode)
    if files is None:
        raise ValueError(ui_text("package.invalidData"))
    digest = hashlib.sha256(str(package["data"]).encode("ascii")).hexdigest()
    folder = root.resolve() / boss_mode / digest
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        target = folder / name
        if not target.is_file() or target.read_bytes() != data:
            atomic_write_bytes(target, data)
    return folder


def select_opening(roots: Iterable[Path], boss_mode: str, team_ids: list[int] | None = None,
                   stage_id: int | None = None) -> Path | None:
    candidates = []
    for root in roots:
        try:
            folders = [folder for folder in root.iterdir() if folder.is_dir()]
        except OSError:
            continue
        for folder in folders:
            try:
                provenance = _json((folder / "capture-provenance.json").read_bytes())
                if not isinstance(provenance, dict) or provenance.get("type") != SOURCE_TYPES.get(boss_mode):
                    continue
                score = (stage_id is not None and provenance.get("stageId") == stage_id,
                         ordered_team_matches(provenance.get("teamHeroTypeIds"), team_ids),
                         folder.stat().st_mtime)
                candidates.append((score, folder))
            except (OSError, ValueError):
                continue
    for _, folder in sorted(candidates, key=lambda item: item[0], reverse=True):
        try:
            pack_opening(folder, boss_mode)
            return folder
        except (OSError, ValueError, EOFError):
            continue
    return None


def valid_account_bonuses(bonuses: Any, type_ids: list[int], boss_mode: str) -> bool:
    if not isinstance(bonuses, dict) or not isinstance(bonuses.get("heroes"), dict) \
            or not isinstance(bonuses.get("observatory"), dict) or boss_mode not in SOURCE_TYPES:
        return False
    if not type_ids or any(type(value) is not int or value <= 0 for value in type_ids):
        return False
    if not isinstance(bonuses["observatory"].get("6" if boss_mode == "hydra" else "7"), dict):
        return False
    for type_id in type_ids:
        parts = bonuses["heroes"].get(str(type_id), bonuses["heroes"].get(type_id))
        if not isinstance(parts, dict) or not isinstance(parts.get("building"), dict):
            return False
        if any(parts.get(key) is not None and not isinstance(parts[key], dict) for key in ("academy", "capitol")):
            return False
    return True


def sanitize_package(value: Any, boss_mode: str, team_ids: list[int] | None = None) -> dict[str, Any] | None:
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1 or value.get("bossMode") != boss_mode:
        return None
    result = {"schema": 1, "bossMode": boss_mode, "savedAt": str(value.get("savedAt") or "")[:40]}
    team = value.get("team")
    if validate_simulation_snapshot(team, boss_mode=boss_mode, strategy_team=team_ids):
        result["team"] = copy.deepcopy(team)
    ids = [hero.get("typeId") for hero in (result.get("team") or {}).get("heroes") or []]
    if valid_account_bonuses(value.get("accountBonuses"), ids, boss_mode):
        result["accountBonuses"] = copy.deepcopy(value["accountBonuses"])
    if unpack_opening(value.get("opening"), boss_mode) is not None:
        result["opening"] = copy.deepcopy(value["opening"])
    return result


def package_status(config: dict[str, Any], boss_mode: str) -> dict[str, Any]:
    team_ids = (config.get("team") or {}).get("heroTypeIds") if isinstance(config.get("team"), dict) else None
    package = sanitize_package(config.get("simulationPackage"), boss_mode, team_ids) or {}
    present = {"team": "team" in package, "opening": "opening" in package,
               "accountBonuses": "accountBonuses" in package}
    missing = [key for key, valid in present.items() if not valid]
    key = "package.noTeam" if not team_ids and not present["team"] else \
        "package.noTeamData" if not present["team"] else \
        "package.noOpening" if not present["opening"] else "package.noAccountBonuses"
    raw_package = config.get("simulationPackage") if isinstance(config.get("simulationPackage"), dict) else {}
    issue = raw_package.get("lastReadIssue")
    return {"status": "complete" if not missing else "partial" if any(present.values()) else "unavailable",
            "reason": (ui_text(key) + (f" · {issue}" if issue else "")) if missing else None,
            "warning": ui_text("package.previousSnapshot") + f" · {issue}" if issue and not missing else None,
            "missing": missing, **present}


def capture_row(package: dict[str, Any], strategy_id: str, boss_mode: str,
                strategy_name: str = "") -> dict[str, Any] | None:
    files = unpack_opening(package.get("opening"), boss_mode)
    if files is None:
        return None
    provenance = _json(files["capture-provenance.json"])
    setup = _json(files["battle-setup.json"])
    return {"id": f"strategy-package:{strategy_id}", "strategyId": strategy_id,
            "source": "strategy-package", "strategyName": strategy_name,
            "capturedAt": package["opening"].get("capturedAt"),
            "difficulty": provenance.get("stageId", 0) % 10,
            **({"bossHealthLost": chimera_health_lost(setup)} if boss_mode == "chimera" and isinstance(setup, list) else {}),
            **({"headTypeIds": [unit["i"] for unit in [*(setup[0]["s"].get("h") or []), *(setup[0]["s"].get("o") or [])]]}
               if boss_mode == "hydra" and isinstance(setup, list) and setup and isinstance(setup[0].get("s"), dict) else {}),
            **{key: provenance.get(key) for key in ("stageId", "seed", "bossHeroTypeId", "teamHeroTypeIds", "teamHeroIds")}}
