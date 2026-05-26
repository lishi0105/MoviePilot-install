#!/usr/bin/env python3
"""Shared .env helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Callable


def get_env(values: dict[str, str], key: str, default: str = "") -> str:
    value = values.get(key)
    if value is None or value == "":
        return default
    return value


def read_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        raise RuntimeError(f"未找到 .env 文件：{env_path}")

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def require_env(values: dict[str, str], key: str, env_path: Path) -> str:
    value = values.get(key)
    if not value:
        raise RuntimeError(f"{env_path} 缺少必要配置：{key}")
    return value


def update_env_file(
    env_path: Path,
    updates: dict[str, str],
    log_func: Callable[[str], None] | None = None,
) -> None:
    if not env_path.exists():
        raise RuntimeError(f"未找到 .env 文件：{env_path}")

    seen: set[str] = set()
    lines: list[str] = []
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#") or "=" not in raw_line:
            lines.append(raw_line)
            continue

        key, _ = raw_line.split("=", 1)
        key = key.strip()
        if key in updates:
            lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            lines.append(raw_line)

    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if log_func:
        log_func(f"已更新环境变量：{env_path}")

    if env_path.exists():
        try:
            from path_utils import FILE_MODE_SECRET, apply_permissions, resolve_app_ids

            uid, gid = resolve_app_ids(read_env_file(env_path))
            apply_permissions(env_path, uid=uid, gid=gid, mode=FILE_MODE_SECRET)
        except Exception:
            pass
