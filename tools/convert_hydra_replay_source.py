"""Convert verified, copied Hydra start JSON with the isolated original runtime.

This reads only local files. The original game DLL runs in the probe's
zero-capability AppContainer; no live process is opened or controlled.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
from pathlib import Path
import subprocess

from strategy_storage import atomic_write_bytes, atomic_write_json


MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_PACKED_BYTES = 1024 * 1024
# Hydra battle-start forecast and Chimera strategy simulation captures.
VERIFIED_SOURCE_TYPES = {"verified_hydra_replay_source", "verified_chimera_replay_source"}


class ConversionError(ValueError):
    pass


def _read_json(path: Path) -> dict:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_JSON_BYTES:
        raise ConversionError(f"Missing or oversized JSON: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ConversionError(f"Invalid JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise ConversionError(f"Expected JSON object: {path.name}")
    return value


def _original_file(source: Path, description: object, name: str) -> bytes:
    if not isinstance(description, dict) or description.get("file") != name:
        raise ConversionError(f"Unexpected source filename: {name}")
    path = source / name
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_JSON_BYTES:
        raise ConversionError(f"Missing or oversized source: {name}")
    data = path.read_bytes()
    if (description.get("bytes") != len(data)
            or description.get("sha256") != hashlib.sha256(data).hexdigest()):
        raise ConversionError(f"Source hash or length changed: {name}")
    return data


def _packed(observation: dict, field: str, length_field: str) -> bytes:
    encoded = observation.get(field)
    length = observation.get(length_field)
    if not isinstance(encoded, str) or type(length) is not int or not 0 < length <= MAX_PACKED_BYTES:
        raise ConversionError(f"Original converter omitted {field}")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ConversionError(f"Invalid original converter output: {field}") from error
    if len(data) != length:
        raise ConversionError(f"Original converter length mismatch: {field}")
    return data


def validate_conversion(wrapper: dict, provenance: dict) -> tuple[bytes, bytes, dict]:
    observation = wrapper.get("observation")
    if (type(wrapper.get("childExitCode")) is not int
            or wrapper["childExitCode"] != 0
            or wrapper.get("timedOut") is not False
            or wrapper.get("nativeFault") is not None
            or not isinstance(observation, dict)
            or observation.get("schema") != 1
            or observation.get("synthetic") is not False
            or observation.get("appContainer") is not True
            or type(observation.get("capabilityCount")) is not int
            or observation["capabilityCount"] != 0
            or observation.get("parentMemoryAccessDenied") is not True
            or observation.get("phase") != "original_setup_messagepack_executed"):
        raise ConversionError("Original conversion did not finish in the required isolation")
    expected = {
        "guidBytesHex": provenance.get("battleSetupId"),
        "seed": provenance.get("seed"),
        "stageId": provenance.get("stageId"),
        "teamOwnerId": provenance.get("accountUserId"),
        "inventoryHeroIds": provenance.get("teamHeroIds"),
        "activeEngineVersion": provenance.get("activeEngineVersion"),
        "warmupBattleRandomCount": provenance.get("warmupBattleRandomCount"),
        "maxTurnsInBattle": provenance.get("maxTurnsInBattle"),
    }
    for field, value in expected.items():
        if observation.get(field) != value:
            raise ConversionError(f"Converted {field} differs from captured source")
    setup = _packed(observation, "battleSetupMessagePackBase64", "battleSetupBytes")
    settings = _packed(observation, "battleSettingsMessagePackBase64", "battleSettingsBytes")
    report = {
        "schema": 1, "status": "original_json_converted_and_identity_verified",
        "battleSetupId": provenance["battleSetupId"],
        "seed": provenance["seed"], "stageId": provenance["stageId"],
        "battleGeneration": provenance["battleGeneration"],
        "activeEngineVersion": provenance["activeEngineVersion"],
        "warmupBattleRandomCount": provenance["warmupBattleRandomCount"],
        "maxTurnsInBattle": provenance["maxTurnsInBattle"],
        "battleSetup": {"file": "battle-setup.msgpack", "bytes": len(setup),
                        "sha256": hashlib.sha256(setup).hexdigest()},
        "battleSettings": {"file": "battle-settings.msgpack", "bytes": len(settings),
                           "sha256": hashlib.sha256(settings).hexdigest()},
        "isolationVerified": True,
        "battleProcessorReplayVerified": False,
        "futurePredictionVerified": False,
    }
    return setup, settings, report


def convert(source: Path, probe: Path) -> dict:
    provenance = _read_json(source / "capture-provenance.json")
    if provenance.get("schema") != 1 or provenance.get("type") not in VERIFIED_SOURCE_TYPES:
        raise ConversionError("Source provenance is not a verified Hydra or Chimera capture")
    _original_file(source, provenance.get("battleSetups"), "battle-setup.json")
    _original_file(source, provenance.get("battleSettings"), "battle-settings.json")
    if not probe.is_file():
        raise ConversionError("Isolated original-runtime probe is missing")
    try:
        # The probe is a console program; the desktop app runs it at every
        # Hydra battle start and must not flash a console window.
        process = subprocess.run([str(probe), "json-convert", str(source)],
                                 capture_output=True, text=True, encoding="utf-8",
                                 timeout=60, check=False,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as error:
        raise ConversionError("Isolated original conversion timed out") from error
    if process.returncode != 0:
        raise ConversionError("Isolated original conversion failed: " +
                              process.stderr[-400:].strip())
    try:
        wrapper = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise ConversionError("Isolated original conversion returned invalid JSON") from error
    if not isinstance(wrapper, dict):
        raise ConversionError("Isolated original conversion returned no report")
    setup, settings, report = validate_conversion(wrapper, provenance)
    destination = source / "packed"
    destination.mkdir(exist_ok=False)
    atomic_write_bytes(destination / "battle-setup.msgpack", setup)
    atomic_write_bytes(destination / "battle-settings.msgpack", settings)
    atomic_write_json(destination / "conversion-report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="capture/replay-source directory")
    parser.add_argument("--probe", required=True, type=Path,
                        help="the same-version isolated original-runtime executable")
    args = parser.parse_args()
    print(json.dumps(convert(args.source.resolve(), args.probe.resolve()), ensure_ascii=False))
