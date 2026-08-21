#!/usr/bin/env python3
"""Offline inventory for the local RAID and RSL Helper installations.

The script never opens a process and never writes to the game directory. It
prints a JSON report to stdout.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path
from typing import Any


DEFAULT_BUILD = Path(r"C:\EXTEND\PlariumPlay\StandAloneApps\raid-shadow-legends\build")
DEFAULT_HELPER = Path(r"C:\Program Files\RSL_Helper_V6")

REQUIRED_IL2CPP_EXPORTS = {
    "il2cpp_domain_get",
    "il2cpp_domain_get_assemblies",
    "il2cpp_assembly_get_image",
    "il2cpp_image_get_name",
    "il2cpp_class_from_name",
    "il2cpp_class_get_field_from_name",
    "il2cpp_class_get_method_from_name",
    "il2cpp_field_get_offset",
    "il2cpp_runtime_invoke",
    "il2cpp_thread_attach",
    "il2cpp_thread_detach",
}

REQUIRED_METADATA_NAMES = {
    "BattleProcessor",
    "ClientBattleViewContext",
    "ClientCommandGenerator",
    "CreateCmdManually",
    "IsWaitingForManualCommand",
    "ChimeraChallengeContext",
    "ChimeraForm",
    "ChimeraSequenceForms",
    "ChimeraTurnsCountBetweenForm",
}

INJECTION_IMPORTS = {
    "OpenProcess",
    "VirtualAllocEx",
    "WriteProcessMemory",
    "CreateRemoteThread",
    "ReadProcessMemory",
}


def _cstring(data: bytes, offset: int | None) -> str:
    if offset is None or not 0 <= offset < len(data):
        return ""
    end = data.find(b"\0", offset)
    if end < 0:
        end = min(offset + 1024, len(data))
    return data[offset:end].decode("ascii", "replace")


def inspect_pe(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ValueError(f"Not a PE file: {path}")

    machine, section_count = struct.unpack_from("<HH", data, pe_offset + 4)
    optional_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
    optional = pe_offset + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    is_x64 = magic == 0x20B
    directory = optional + (112 if is_x64 else 96)
    thunk_size = 8 if is_x64 else 4

    sections: list[tuple[str, int, int, int, int]] = []
    section_table = optional + optional_size
    for index in range(section_count):
        offset = section_table + index * 40
        name = data[offset : offset + 8].split(b"\0")[0].decode("ascii", "replace")
        virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from(
            "<IIII", data, offset + 8
        )
        sections.append((name, virtual_size, virtual_address, raw_size, raw_pointer))

    def rva_to_offset(rva: int) -> int | None:
        for _, virtual_size, virtual_address, raw_size, raw_pointer in sections:
            if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
                return raw_pointer + (rva - virtual_address)
        return rva if 0 <= rva < len(data) else None

    directories = [struct.unpack_from("<II", data, directory + index * 8) for index in range(16)]

    imports: dict[str, list[str]] = {}
    import_rva, _ = directories[1]
    import_offset = rva_to_offset(import_rva) if import_rva else None
    if import_offset is not None:
        for descriptor_index in range(2048):
            offset = import_offset + descriptor_index * 20
            original_thunk, _, _, name_rva, first_thunk = struct.unpack_from(
                "<IIIII", data, offset
            )
            if not any((original_thunk, name_rva, first_thunk)):
                break
            library = _cstring(data, rva_to_offset(name_rva))
            thunk_offset = rva_to_offset(original_thunk or first_thunk)
            names: list[str] = []
            if thunk_offset is not None:
                for thunk_index in range(65536):
                    value = struct.unpack_from(
                        "<Q" if thunk_size == 8 else "<I",
                        data,
                        thunk_offset + thunk_index * thunk_size,
                    )[0]
                    if value == 0:
                        break
                    ordinal_mask = 1 << (thunk_size * 8 - 1)
                    if value & ordinal_mask:
                        names.append(f"#{value & 0xFFFF}")
                    else:
                        name_offset = rva_to_offset(value)
                        if name_offset is not None:
                            names.append(_cstring(data, name_offset + 2))
            imports[library] = names

    exports: list[str] = []
    export_rva, _ = directories[0]
    export_offset = rva_to_offset(export_rva) if export_rva else None
    if export_offset is not None:
        values = struct.unpack_from("<IIHHIIIIIII", data, export_offset)
        name_count = values[7]
        address_of_names = values[9]
        names_offset = rva_to_offset(address_of_names)
        if names_offset is not None:
            for index in range(name_count):
                name_rva = struct.unpack_from("<I", data, names_offset + index * 4)[0]
                exports.append(_cstring(data, rva_to_offset(name_rva)))

    machine_name = {0x14C: "x86", 0x8664: "x64", 0xAA64: "arm64"}.get(
        machine, hex(machine)
    )
    return {
        "path": str(path),
        "machine": machine_name,
        "sections": [section[0] for section in sections],
        "imports": imports,
        "exports": exports,
    }


def metadata_names(path: Path) -> tuple[int, set[str]]:
    data = path.read_bytes()
    version = struct.unpack_from("<I", data, 4)[0]
    names: set[str] = set()
    for part in data.split(b"\0"):
        if not 1 <= len(part) <= 512:
            continue
        try:
            value = part.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if value and all(character.isprintable() for character in value):
            names.add(value)
    return version, names


def printable_strings(path: Path) -> set[str]:
    data = path.read_bytes()
    values = {
        match.decode("ascii", "ignore").strip()
        for match in re.findall(rb"[ -~]{4,}", data)
    }
    values.update(
        match.decode("utf-16le", "ignore").strip()
        for match in re.findall(rb"(?:[ -~]\x00){4,}", data)
    )
    return {value for value in values if value}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--helper", type=Path, default=DEFAULT_HELPER)
    args = parser.parse_args()

    build = args.build.resolve()
    helper = args.helper.resolve()
    manifest = json.loads((build / "manifest.json").read_text(encoding="utf-8"))

    metadata_version, names = metadata_names(
        build / "Raid_Data" / "il2cpp_data" / "Metadata" / "global-metadata.dat"
    )
    game_assembly = inspect_pe(build / "GameAssembly.dll")
    helper_exe = inspect_pe(helper / "RSLHelper.exe")

    helper_import_names = {
        name for imports in helper_exe["imports"].values() for name in imports
    }
    manifest_chimera_paths = sorted(
        chunk["path"]
        for chunk in manifest.get("chunks", [])
        if "chimera" in chunk.get("path", "").lower()
    )

    helper_main_strings = printable_strings(helper / "RSLHelper.exe")
    helper_agent_strings = printable_strings(helper / "rslhelper.dll")
    game_exports = set(game_assembly["exports"])

    report = {
        "gameBuild": str(build),
        "helperDirectory": str(helper),
        "metadataVersion": metadata_version,
        "counts": {
            "metadataNames": len(names),
            "metadataNamesContainingChimera": sum(
                "chimera" in name.lower() for name in names
            ),
            "helperMainStringsContainingChimera": sum(
                "chimera" in value.lower() for value in helper_main_strings
            ),
            "helperAgentStringsContainingChimera": sum(
                "chimera" in value.lower() for value in helper_agent_strings
            ),
            "manifestChimeraPaths": len(manifest_chimera_paths),
        },
        "runtimeReadiness": {
            "gameAssemblyMachine": game_assembly["machine"],
            "requiredIl2CppExports": {
                name: name in game_exports for name in sorted(REQUIRED_IL2CPP_EXPORTS)
            },
            "requiredMetadataNames": {
                name: name in names for name in sorted(REQUIRED_METADATA_NAMES)
            },
        },
        "rslHelperEvidence": {
            "machine": helper_exe["machine"],
            "injectionImports": {
                name: name in helper_import_names for name in sorted(INJECTION_IMPORTS)
            },
            "battleStatePipeNamePresent": "RSLPIPEBATTLESTATE" in helper_agent_strings,
            "manualCastPathPresent": any(
                "CreateCmdManually" in value for value in helper_agent_strings
            ),
        },
        "manifestChimeraPaths": manifest_chimera_paths,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
