"""Read an existing Hydra BattleSetup from the game's Windows PlayerPrefs cache.

This tool never opens Raid.exe, calls a game API, writes to PlayerPrefs, or starts
a battle. The cache is temporary: the game can clear it during finish/cancel,
and PlayerPrefs.SetString may not have flushed it to the registry yet.

Run without --output to probe for a cache value. To save one BattleSetup, supply
the battle ID, seed, and all six inventory hero IDs observed independently for
that same live battle. Output contains private account battle data; keep it local.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import uuid


MAX_REGISTRY_KEYS = 20_000
MAX_DEPTH = 12
MAX_COMPRESSED_CHARS = 8 * 1024 * 1024
MAX_JSON_BYTES = 32 * 1024 * 1024
CACHE_MARKER = "BattleSetupCache_"
CACHE_KEY_PATTERN = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9_]*_)?BattleSetupCache_([1-9][0-9]*)(?:_h[0-9]+)?$"
)


def _field(model: dict, short: str, long: str) -> object:
    return model.get(short, model.get(long))


def _positive_integer(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _guid_forms(value: object) -> set[str]:
    if not isinstance(value, str):
        raise ValueError("BattleSetup.Id is missing or is not a GUID string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise ValueError("BattleSetup.Id is not a valid GUID") from error
    # The native battleRandom snapshot logs the Guid's raw 16 memory bytes.
    return {parsed.bytes.hex(), parsed.bytes_le.hex()}


def normalize_expected_guid(value: str) -> str:
    compact = value.replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", compact):
        raise ValueError("--battle-setup-id must contain exactly 16 hex bytes")
    return compact


def decode_cache_value(value: object) -> list[dict]:
    """Decode the game's Base64(GZip(UTF-8 JSON List<BattleSetup>))."""
    if not isinstance(value, str) or not value or len(value) > MAX_COMPRESSED_CHARS:
        raise ValueError("PlayerPrefs value is absent or exceeds the size limit")
    try:
        packed = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("PlayerPrefs value is not valid Base64") from error
    if len(packed) > MAX_COMPRESSED_CHARS or not packed.startswith(b"\x1f\x8b"):
        raise ValueError("PlayerPrefs value is not a bounded GZip stream")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(packed), mode="rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
    except OSError as error:
        raise ValueError("PlayerPrefs GZip stream cannot be decoded") from error
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("Decompressed battle cache exceeds the size limit")
    try:
        models = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Decompressed battle cache is not UTF-8 JSON") from error
    if not isinstance(models, list) or not all(isinstance(item, dict) for item in models):
        raise ValueError("Battle cache is not a JSON List<BattleSetup>")
    stack = [(models, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise ValueError("Battle cache JSON exceeds the nesting limit")
        if isinstance(node, dict):
            stack.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)
    return models


def cache_user_id_from_value_name(name: str) -> int:
    match = CACHE_KEY_PATTERN.fullmatch(name)
    if match is None:
        raise ValueError("PlayerPrefs value name does not identify a BattleSetupCache account")
    return int(match.group(1))


def summarize_hydra_setup(model: dict) -> dict | None:
    kind = _field(model, "k", "KindId")
    if kind != 5:
        return None
    seed = _field(model, "r", "RandomSeed")
    if type(seed) is not int:
        raise ValueError("Hydra BattleSetup.RandomSeed is missing")
    setup_id = _field(model, "z", "Id")
    guid_forms = _guid_forms(setup_id)
    stage = _positive_integer(_field(model, "i", "StageId"), "Hydra StageId")
    team = _field(model, "f", "FirstTeam")
    if not isinstance(team, dict):
        raise ValueError("Hydra FirstTeam is missing")
    team_owner_id = _positive_integer(_field(team, "i", "TeamOwnerId"), "Hydra TeamOwnerId")
    slots = _field(team, "h", "HeroSlotSetups")
    if not isinstance(slots, list) or len(slots) != 6 or not all(isinstance(x, dict) for x in slots):
        raise ValueError("Hydra FirstTeam does not have exactly six HeroSlotSetups")
    ordered = []
    for index, slot in enumerate(slots):
        slot_number = _field(slot, "t", "Slot")
        if type(slot_number) is not int or not 0 <= slot_number <= 6:
            raise ValueError("Hydra hero Slot is invalid")
        inventory_id = _positive_integer(
            _field(slot, "h", "InventoryHeroId"), "InventoryHeroId"
        )
        type_id = _positive_integer(_field(slot, "i", "HeroTypeId"), "HeroTypeId")
        ordered.append((slot_number, inventory_id, type_id))
    ordered.sort()
    slot_numbers = [item[0] for item in ordered]
    if slot_numbers not in (list(range(6)), list(range(1, 7))):
        raise ValueError("Hydra hero Slots are duplicated or incomplete")
    return {
        "battleSetupIdJson": str(setup_id),
        "battleSetupIdForms": sorted(guid_forms),
        "seed": seed,
        "stageId": stage,
        "teamOwnerId": team_owner_id,
        "inventoryHeroIds": [item[1] for item in ordered],
        "heroTypeIds": [item[2] for item in ordered],
    }


def select_exact_battle(
    models: list[dict], battle_id: str, seed: int, inventory_hero_ids: list[int],
    cache_user_id: int, stage_id: int | None = None,
    hero_type_ids: list[int] | None = None,
) -> tuple[dict, dict]:
    expected_id = normalize_expected_guid(battle_id)
    if len(inventory_hero_ids) != 6 or any(type(x) is not int or x <= 0 for x in inventory_hero_ids):
        raise ValueError("exactly six positive inventory hero IDs are required")
    matches: list[tuple[dict, dict]] = []
    for model in models:
        summary = summarize_hydra_setup(model)
        if summary is not None and expected_id in summary["battleSetupIdForms"]:
            matches.append((model, summary))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one Hydra BattleSetup with this ID; found {len(matches)}")
    model, summary = matches[0]
    if summary["seed"] != seed:
        raise ValueError("BattleSetup seed differs from the independently observed seed")
    if summary["teamOwnerId"] != cache_user_id:
        raise ValueError("BattleSetup team owner differs from the PlayerPrefs cache account")
    if summary["inventoryHeroIds"] != inventory_hero_ids:
        raise ValueError("BattleSetup hero instances or slot order differ from the selected team")
    if stage_id is not None and summary["stageId"] != stage_id:
        raise ValueError("BattleSetup stage differs from the observed battle")
    if hero_type_ids is not None and summary["heroTypeIds"] != hero_type_ids:
        raise ValueError("BattleSetup hero types or slot order differ from the selected team")
    return model, summary


def _winreg_module():
    if os.name != "nt":
        raise RuntimeError("PlayerPrefs registry extraction requires Windows")
    import winreg

    return winreg


def _value_names_without_data(registry_key) -> list[str]:
    """Enumerate names only; winreg.EnumValue would read unrelated values."""
    winreg = _winreg_module()
    count = winreg.QueryInfoKey(registry_key)[1]
    enum_value = ctypes.WinDLL("advapi32", use_last_error=True).RegEnumValueW
    enum_value.argtypes = [
        wintypes.HKEY, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p,
    ]
    enum_value.restype = wintypes.LONG
    names: list[str] = []
    for index in range(count):
        capacity = 512
        while True:
            buffer = ctypes.create_unicode_buffer(capacity)
            used = wintypes.DWORD(capacity)
            kind = wintypes.DWORD()
            code = enum_value(
                wintypes.HKEY(int(registry_key)), index, buffer,
                ctypes.byref(used), None, ctypes.byref(kind), None, None,
            )
            if code == 234 and capacity < 32768:  # ERROR_MORE_DATA
                capacity *= 2
                continue
            if code != 0:
                raise OSError(code, "RegEnumValueW failed during name-only scan")
            names.append(buffer.value)
            break
    return names


def _normalize_registry_subkey(value: str) -> str:
    key = value.replace("/", "\\").strip("\\")
    for prefix in ("HKEY_CURRENT_USER\\", "HKCU\\"):
        if key.upper().startswith(prefix.upper()):
            key = key[len(prefix):]
    if not key.lower().startswith("software\\"):
        raise ValueError("--registry-subkey must be under HKCU\\Software")
    return key


def scan_cache_names(registry_subkey: str | None = None) -> tuple[list[tuple[str, str]], dict]:
    winreg = _winreg_module()
    software = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software", 0, winreg.KEY_READ)
    roots: list[str] = []
    if registry_subkey:
        roots = [_normalize_registry_subkey(registry_subkey)]
    else:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(software, index)
            except OSError:
                break
            if any(token in name.lower() for token in ("plarium", "raid", "unity")):
                roots.append("Software\\" + name)
            index += 1
        roots.append("Software\\AppDataLow\\Software")
    software.Close()
    hits: list[tuple[str, str]] = []
    scanned = 0
    denied = 0
    truncated = False

    def visit(subkey: str, depth: int) -> None:
        nonlocal scanned, denied, truncated
        if scanned >= MAX_REGISTRY_KEYS or depth > MAX_DEPTH:
            truncated = True
            return
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ)
        except OSError:
            denied += 1
            return
        try:
            scanned += 1
            for name in _value_names_without_data(key):
                if CACHE_MARKER in name:
                    hits.append((subkey, name))
            index = 0
            while True:
                try:
                    child = winreg.EnumKey(key, index)
                except OSError:
                    break
                visit(subkey + "\\" + child, depth + 1)
                index += 1
        finally:
            key.Close()

    for root in sorted(set(roots)):
        visit(root, 0)
    return hits, {
        "registryRoots": sorted(set(roots)),
        "registryKeysScanned": scanned,
        "registryKeysUnreadable": denied,
        "registryScanTruncated": truncated,
    }


def _stable_read_value(subkey: str, name: str) -> str:
    winreg = _winreg_module()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ) as key:
        first, kind = winreg.QueryValueEx(key, name)
        second, second_kind = winreg.QueryValueEx(key, name)
    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or kind != second_kind:
        raise ValueError("BattleSetupCache registry value is not a stable string")
    if first != second:
        raise ValueError("BattleSetupCache changed while being read; retry from a new snapshot")
    return first


def parse_hero_ids(value: str) -> list[int]:
    try:
        ids = [int(part.strip()) for part in value.split(",")]
    except ValueError as error:
        raise ValueError("--hero-instance-ids must be six comma-separated integers") from error
    if len(ids) != 6 or any(x <= 0 for x in ids):
        raise ValueError("--hero-instance-ids must contain exactly six positive IDs")
    return ids


def capture(args: argparse.Namespace) -> dict:
    hits, scan = scan_cache_names(args.registry_subkey)
    result = {
        "schema": 1,
        "source": "HKCU Unity PlayerPrefs, read only",
        **scan,
        "matchingCacheValueNames": len(hits),
        "status": "cache_value_missing",
        "reason": "No persisted BattleSetupCache value was visible. It may have been cleared at finish/cancel or not yet flushed by PlayerPrefs.SetString.",
    }
    if not hits:
        return result
    valid: list[tuple[str, str, str, dict, dict]] = []
    invalid = 0
    hydra_count = 0
    for subkey, name in hits:
        try:
            cache_user_id = cache_user_id_from_value_name(name)
            value = _stable_read_value(subkey, name)
            models = decode_cache_value(value)
            account_hydra_count = 0
            for item in models:
                summary = summarize_hydra_setup(item)
                if summary is not None:
                    if summary["teamOwnerId"] != cache_user_id:
                        raise ValueError("Hydra team owner differs from the PlayerPrefs cache account")
                    account_hydra_count += 1
            if args.output is not None:
                model, summary = select_exact_battle(
                    models, args.battle_setup_id, args.seed, args.hero_instance_ids,
                    cache_user_id, getattr(args, "stage_id", None),
                    getattr(args, "hero_type_ids", None),
                )
                valid.append((subkey, name, value, model, summary))
            hydra_count += account_hydra_count
        except (OSError, ValueError):
            invalid += 1
    result["hydraBattleSetupsInReadableCaches"] = hydra_count
    result["unreadableOrNonmatchingCacheValues"] = invalid
    if args.output is None:
        result["status"] = "cache_found" if hydra_count else "cache_found_without_hydra"
        result["reason"] = "Probe only; no registry value was copied."
        return result
    if len(valid) != 1:
        result["status"] = "exact_battle_not_verified"
        result["reason"] = "No unique cache entry matched the observed battle ID, seed, and six hero instances."
        return result
    subkey, name, value, model, summary = valid[0]
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    original = output / "playerprefs-value.txt"
    decoded = output / "battle-setup.json"
    original.write_text(value, encoding="ascii")
    decoded.write_text(json.dumps(model, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    provenance = {
        "schema": 1,
        "source": "existing HKCU Unity PlayerPrefs value, read only",
        "readAtUtc": datetime.now(timezone.utc).isoformat(),
        "registrySubkey": "HKCU\\" + subkey,
        "registryValueNameSha256": hashlib.sha256(name.encode("utf-8")).hexdigest(),
        "playerPrefsValueSha256": hashlib.sha256(value.encode("ascii")).hexdigest(),
        "battleSetupJsonSha256": hashlib.sha256(decoded.read_bytes()).hexdigest(),
        "battleSetupId": summary["battleSetupIdJson"],
        "cacheUserId": summary["teamOwnerId"],
        "seed": summary["seed"],
        "stageId": summary["stageId"],
        "heroInstanceIdsInSlotOrder": summary["inventoryHeroIds"],
        "heroTypeIdsInSlotOrder": summary["heroTypeIds"],
        "battleSettingsCaptured": False,
        "originalMessagePackProduced": False,
        "completeReplayInput": False,
    }
    (output / "capture-provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result["status"] = "verified_battle_setup_saved"
    result["reason"] = "The existing cache matched the observed battle and was saved locally; BattleSettings are still required."
    result["outputDirectory"] = str(output)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-subkey", help="Optional exact HKCU\\Software\\... key to scan")
    parser.add_argument("--output", type=Path, help="New local output directory; omitted for probe only")
    parser.add_argument("--battle-setup-id", help="Battle ID from the read-only battleRandom snapshot")
    parser.add_argument("--seed", type=int, help="Seed from the same battleRandom snapshot")
    parser.add_argument("--hero-instance-ids", type=parse_hero_ids,
                        help="Six selected inventory hero IDs in slot order, comma-separated")
    parser.add_argument("--hero-type-ids", type=parse_hero_ids,
                        help="Optional six selected hero type IDs in slot order")
    parser.add_argument("--stage-id", type=int,
                        help="Optional stage ID independently observed from the battle UI")
    args = parser.parse_args(argv)
    if args.output is not None and (
        args.battle_setup_id is None or args.seed is None or args.hero_instance_ids is None
    ):
        parser.error("--output requires --battle-setup-id, --seed, and --hero-instance-ids")
    try:
        report = capture(args)
    except (OSError, ValueError, RuntimeError) as error:
        report = {"schema": 1, "status": "error", "reason": str(error)}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] in ("cache_found", "cache_found_without_hydra", "verified_battle_setup_saved") else 2


if __name__ == "__main__":
    sys.exit(main())
