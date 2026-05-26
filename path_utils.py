#!/usr/bin/env python3
"""Shared path permission helpers for the media stack install scripts."""

from __future__ import annotations

import os
from pathlib import Path

from log_utils import log

# official postgres:17 / redis:latest 镜像内常见 uid/gid
POSTGRES_DATA_UID = 999
POSTGRES_DATA_GID = 999
REDIS_DATA_UID = 999
REDIS_DATA_GID = 999

DIR_MODE = 0o777
DIR_MODE_APP = DIR_MODE
DIR_MODE_DATA = DIR_MODE
DIR_MODE_DB = DIR_MODE
FILE_MODE = 0o777
FILE_MODE_NORMAL = FILE_MODE
FILE_MODE_SECRET = 0o600


def can_change_owner() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def apply_permissions(path: Path, *, uid: int, gid: int, mode: int) -> None:
    if not path.exists():
        return

    if can_change_owner():
        try:
            os.chown(path, uid, gid)
        except OSError as exc:
            log(f"修改所有者失败：{path} -> {uid}:{gid}，错误：{exc}", "warning")
    else:
        try:
            stat_result = path.stat()
        except OSError as exc:
            log(f"读取路径权限失败：{path}，错误：{exc}", "warning")
            stat_result = None

        if stat_result and (stat_result.st_uid != uid or stat_result.st_gid != gid):
            log(
                f"当前非 root，无法将所有者改为 {uid}:{gid}：{path} "
                f"(当前 {stat_result.st_uid}:{stat_result.st_gid})。"
                "请用 root 执行安装，或安装后手动 chown。",
                "warning",
            )

    try:
        os.chmod(path, mode)
    except OSError as exc:
        log(f"修改权限失败：{path} -> {oct(mode)}，错误：{exc}", "warning")


def ensure_directory(path: Path, *, uid: int, gid: int, mode: int = DIR_MODE_APP) -> None:
    path.mkdir(parents=True, exist_ok=True)
    apply_permissions(path, uid=uid, gid=gid, mode=mode)


def write_file_with_permissions(
    path: Path,
    content: str,
    *,
    uid: int,
    gid: int,
    mode: int,
    force: bool = False,
) -> str:
    if path.exists() and not force:
        apply_permissions(path, uid=uid, gid=gid, mode=mode)
        return f"skip exists: {path}"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    apply_permissions(path, uid=uid, gid=gid, mode=mode)
    return f"wrote: {path}"


def resolve_app_ids(config: dict[str, str]) -> tuple[int, int]:
    uid = int(config.get("PUID") or os.getuid())
    gid = int(config.get("PGID") or os.getgid())
    return uid, gid
