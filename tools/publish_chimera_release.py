"""Publish a completed one-file build to the permanent desktop shortcut target.

Never launch or terminate the application. A sharing violation leaves the old
entry point and the source package in place so publication can be retried.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from strategy_storage import atomic_write_bytes, atomic_write_json


APPLICATION = "RSL-Boss-helper"
DEFAULT_RELEASE = Path(__file__).resolve().parent.parent / "build" / "release"


class ReleasePublishError(RuntimeError):
    pass


def owned_path(directory: Path, name: str) -> Path:
    path = directory / name
    if not path.resolve().is_relative_to(directory):
        raise ReleasePublishError(f"发布路径指向目标目录之外：{path}")
    return path


def publish(source: Path, version: str, expected_sha256: str,
            directory: Path = DEFAULT_RELEASE) -> dict:
    source = source.resolve(strict=True)
    directory = directory.resolve()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ReleasePublishError("发布版本号无效。")
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256.lower():
        raise ReleasePublishError("新包校验值不匹配；通用 release 未修改。")
    if source.suffix.lower() != ".exe" or not payload.startswith(b"MZ"):
        raise ReleasePublishError("发布源必须是已构建的 Windows EXE。")

    directory.mkdir(parents=True, exist_ok=True)
    target = owned_path(directory, APPLICATION + ".exe")
    manifest_path = owned_path(directory, "latest.json")
    checksum_path = owned_path(directory, APPLICATION + ".sha256")
    previous = target.read_bytes() if target.exists() else None
    changed = previous != payload
    backup = None
    now = datetime.now(timezone.utc)
    if changed:
        if previous is not None:
            previous_digest = hashlib.sha256(previous).hexdigest()
            archive = owned_path(directory, "archive")
            archive.mkdir(parents=True, exist_ok=True)
            backup = owned_path(archive.resolve(),
                                f"{APPLICATION}-{now:%Y%m%dT%H%M%S%fZ}-{previous_digest[:12]}.exe")
            atomic_write_bytes(backup, previous)
            if hashlib.sha256(backup.read_bytes()).hexdigest() != previous_digest:
                raise ReleasePublishError("旧文件备份校验失败；通用 release 未修改。")
        try:
            atomic_write_bytes(target, payload)
        except OSError as error:
            raise ReleasePublishError(
                f"无法替换通用 release，文件可能正在使用或没有写入权限。旧入口未替换。"
                f"请关闭工具后重试发布；新包保留在：{source}。系统错误：{error}"
            ) from error

    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise ReleasePublishError("通用 release 替换后的校验失败，请检查发布目录。")
    receipt = {
        "version": version,
        "executable": target.name,
        "sha256": digest,
        "byteCount": len(payload),
        "publishedAt": now.isoformat(),
        "source": str(source),
        "backup": str(backup) if backup else None,
    }
    try:
        atomic_write_bytes(checksum_path, f"{digest}  {target.name}\n".encode("ascii"))
        atomic_write_json(manifest_path, receipt)
    except OSError as error:
        raise ReleasePublishError(
            f"通用 EXE 已更新并校验成功，但发布信息保存失败；请重试发布以补全：{error}"
        ) from error
    return {**receipt, "target": str(target), "changed": changed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--release-directory", type=Path, default=DEFAULT_RELEASE)
    args = parser.parse_args()
    try:
        result = publish(args.source, args.version, args.expected_sha256, args.release_directory)
    except (OSError, ReleasePublishError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
