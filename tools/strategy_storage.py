"""Durable JSON storage, explicit recovery, and strategy revisions."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


class StrategyStoreError(ValueError):
    pass


def read_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise StrategyStoreError(f"无法读取策略文件 {path.name}；原文件已保留，请恢复备份：{error}") from error
    if not isinstance(value, dict):
        raise StrategyStoreError(f"策略文件 {path.name} 的根节点必须是对象；原文件已保留")
    if "modes" in value:
        modes = value["modes"]
        if not isinstance(modes, dict) or not modes:
            raise StrategyStoreError("策略模式数据损坏；原文件已保留")
        for section in modes.values():
            if not isinstance(section, dict):
                raise StrategyStoreError("策略组数据损坏；原文件已保留")
            if "strategies" in section:
                profiles = section["strategies"]
                if not isinstance(profiles, dict) or not profiles or any(
                    not isinstance(item, dict) or not isinstance(item.get("rules"), list)
                    for item in profiles.values()
                ):
                    raise StrategyStoreError("策略规则数据损坏；原文件已保留")
            elif not isinstance(section.get("rules"), list):
                raise StrategyStoreError("策略规则数据损坏；原文件已保留")
    elif not isinstance(value.get("rules"), list):
        raise StrategyStoreError("文件不包含有效策略；原文件已保留")
    return value


def revision(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            # Cleanup cannot turn a completed atomic save into a reported
            # failure, or obscure the original write/replace error.
            pass


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def backup_paths(path: Path) -> list[Path]:
    return [path.with_name(path.name + f".bak.{index}") for index in range(1, 6)]


def write_strategy_store(path: Path, value: dict[str, Any]) -> None:
    # Validate before touching backups; never rotate a corrupt file into them.
    previous = read_object(path)
    if previous is not None:
        _record_backup(path, path.read_bytes())
    atomic_write_json(path, value)
    _record_backup(path, path.read_bytes())


def _record_backup(path: Path, payload: bytes) -> None:
    backups = backup_paths(path)
    if backups[0].is_file() and backups[0].read_bytes() == payload:
        return
    for source, destination in reversed(list(zip(backups, backups[1:]))):
        if source.is_file():
            atomic_write_bytes(destination, source.read_bytes())
    atomic_write_bytes(backups[0], payload)


def storage_health(path: Path) -> dict[str, Any]:
    available = []
    for backup in backup_paths(path):
        try:
            if read_object(backup) is not None:
                available.append(backup.name)
        except StrategyStoreError:
            pass
    try:
        read_object(path)
        return {"ok": True, "backups": available}
    except StrategyStoreError as error:
        return {"ok": False, "error": str(error), "backups": available}


def recover_strategy_store(path: Path) -> None:
    for backup in backup_paths(path):
        try:
            value = read_object(backup)
        except StrategyStoreError:
            continue
        if value is None:
            continue
        restore_strategy_value(path, value)
        return
    raise StrategyStoreError("没有可恢复的有效备份；原策略文件已保留")


def restore_strategy_value(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        preserved = path.with_name(path.name + f".recovery-{time.time_ns()}.json")
        atomic_write_bytes(preserved, path.read_bytes())
    atomic_write_json(path, value)
    _record_backup(path, path.read_bytes())
