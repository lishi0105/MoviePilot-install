"""ChineseSubFinder initialization via ChineseSubFinderSettings.json."""

from __future__ import annotations

import json
import subprocess
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from env_utils import get_env, read_env_file, update_env_file
from log_utils import log
from password_utils import stack_random_secret, validate_cli_password_args, validate_password_map
from path_utils import FILE_MODE_NORMAL, resolve_app_ids, write_file_with_permissions

CSF_CONTAINER = "mpv2-chinesesubfinder"
CSF_SETTINGS_NAME = "ChineseSubFinderSettings.json"
CSF_MEDIA_ROOT = "/media"
CSF_EMBY_URL = "http://emby:8096"
CSF_SCAN_INTERVAL = "@every 6h"

DEFAULT_MOVIE_PATHS = (
    "/media/真人电影",
    "/media/动漫电影",
    "/media/小电影",
    "/media/私享影库",
)
DEFAULT_SERIES_PATHS = (
    "/media/真人剧集",
    "/media/动漫剧集",
    "/media/综艺",
    "/media/纪录片",
    "/media/短剧",
)


def resolve_csf_credentials(
    config: dict[str, str],
    *,
    cli_user: str | None,
    cli_password: str | None,
) -> tuple[str, str]:
    username = (cli_user or get_env(config, "CSF_USER", "admin")).strip() or "admin"
    password = (cli_password or get_env(config, "CSF_PASSWORD", "")).strip()
    if not password:
        password = stack_random_secret()
        log("未配置 CSF_PASSWORD，已生成符合规则的随机密码并写回 .env。", "warning")
    return username, password


def resolve_csf_api_key(config: dict[str, str], existing: dict[str, Any] | None) -> str:
    env_key = get_env(config, "CSF_API_KEY", "").strip()
    if env_key:
        return env_key

    if existing:
        api_settings = existing.get("experimental_function", {}).get("api_key_settings", {})
        if api_settings.get("enabled") and api_settings.get("key"):
            existing_key = str(api_settings["key"]).strip()
            if existing_key:
                log("使用已有 ChineseSubFinderSettings.json 中的 API Key。", "success")
                return existing_key

    api_key = str(uuid.uuid4())
    log("已生成新的 CSF API Key。", "success")
    return api_key


def ensure_container_running(container_name: str) -> None:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"容器不存在或无法检查：{container_name}。{detail}")
    if result.stdout.strip().lower() != "true":
        raise RuntimeError(f"容器未运行：{container_name}，请先 docker compose up -d。")


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def default_settings_template() -> dict[str, Any]:
    return {
        "SpeedDevMode": False,
        "user_info": {
            "username": "admin",
            "password": "",
        },
        "common_settings": {
            "interval_or_assign_or_custom": 0,
            "scan_interval": CSF_SCAN_INTERVAL,
            "threads": 1,
            "run_scan_at_start_up": True,
            "movie_paths": list(DEFAULT_MOVIE_PATHS),
            "series_paths": list(DEFAULT_SERIES_PATHS),
            "local_static_file_port": "19037",
        },
        "subtitle_sources": {
            "assrt_settings": {
                "enabled": True,
                "token": "",
            },
            "subtitle_best_settings": {
                "enabled": False,
                "api_key": "",
            },
        },
        "advanced_settings": {
            "proxy_settings": {
                "use_proxy": False,
                "use_which_proxy_protocol": "http",
                "local_http_proxy_server_port": "19036",
                "input_proxy_address": "127.0.0.1",
                "input_proxy_port": "10809",
                "need_pwd": False,
                "input_proxy_username": "",
                "input_proxy_password": "",
            },
            "tmdb_api_settings": {
                "enable": True,
                "api_key": "",
                "use_alternate_base_url": False,
            },
            "debug_mode": False,
            "save_full_season_tmp_subtitles": False,
            "sub_type_priority": 0,
            "sub_name_formatter": 1,
            "save_multi_sub": True,
            "custom_video_exts": [],
            "fix_time_line": False,
            "topic": 1,
        },
        "emby_settings": {
            "enable": True,
            "address_url": CSF_EMBY_URL,
            "api_key": "",
            "max_request_video_number": 500,
            "skip_watched": False,
            "movie_paths_mapping": {},
            "series_paths_mapping": {},
            "auto_or_manual": True,
            "threads": 1,
        },
        "developer_settings": {
            "enable": False,
            "bark_server_address": "",
        },
        "timeline_fixer_settings": {
            "max_offset_time": 700,
            "min_offset": 0.2,
            "thread_count": 1,
        },
        "experimental_function": {
            "auto_change_sub_encode": {
                "enable": False,
                "des_encode_type": 0,
            },
            "chs_cht_changer": {
                "enable": False,
                "des_chinese_language_type": 0,
            },
            "remote_chrome_settings": {
                "enable": False,
                "remote_docker_url": "",
                "remote_adblock_path": "",
                "remote_user_data_dir": "",
            },
            "api_key_settings": {
                "enabled": False,
                "key": "",
            },
            "local_chrome_settings": {
                "enabled": False,
                "local_chrome_exe_f_path": "",
            },
            "share_sub_settings": {
                "share_sub_enabled": False,
            },
            "extend_log": {
                "SysLog": {
                    "enable": False,
                    "network": "",
                    "address": "",
                    "priority": 0,
                    "tag": "",
                }
            },
        },
    }


def build_path_mapping(emby_media_root: str, csf_media_root: str = "/media") -> dict[str, str]:
    emby_root = emby_media_root.rstrip("/")
    csf_root = csf_media_root.rstrip("/") or "/media"
    return {emby_root: csf_root}


def build_csf_settings(
    config: dict[str, str],
    data_dir: Path,
    *,
    username: str,
    password: str,
    csf_api_key: str,
) -> dict[str, Any]:
    emby_media_root = str(data_dir / "media").rstrip("/")
    emby_api_key = get_env(config, "EMBY_API_KEY", "")

    if not emby_api_key:
        raise RuntimeError("启用 Emby 联动需要 .env 中已配置 EMBY_API_KEY（先执行 --init-emby）。")

    path_mapping = build_path_mapping(emby_media_root, CSF_MEDIA_ROOT)

    overlay = {
        "user_info": {
            "username": username,
            "password": password,
        },
        "common_settings": {
            "movie_paths": list(DEFAULT_MOVIE_PATHS),
            "series_paths": list(DEFAULT_SERIES_PATHS),
        },
        "emby_settings": {
            "enable": True,
            "address_url": CSF_EMBY_URL,
            "api_key": emby_api_key,
            "movie_paths_mapping": path_mapping,
            "series_paths_mapping": path_mapping,
        },
        "experimental_function": {
            "api_key_settings": {
                "enabled": True,
                "key": csf_api_key,
            },
        },
    }

    settings = default_settings_template()
    deep_merge(settings, overlay)
    return settings


def load_existing_settings(config_path: Path) -> dict[str, Any] | None:
    if not config_path.exists():
        return None
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"无法解析现有配置：{config_path}，{exc}") from exc


def write_settings(config_path: Path, settings: dict[str, Any], *, uid: int, gid: int) -> None:
    content = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    write_file_with_permissions(
        config_path,
        content,
        uid=uid,
        gid=gid,
        mode=FILE_MODE_NORMAL,
        force=True,
    )


def restart_csf_container() -> None:
    result = subprocess.run(
        ["docker", "restart", CSF_CONTAINER],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"重启 {CSF_CONTAINER} 失败：{detail}")
    log(f"已重启容器：{CSF_CONTAINER}", "success")


def run_init_csf(
    *,
    stack_dir: Path,
    data_dir: Path,
    csf_user: str | None = None,
    csf_password: str | None = None,
) -> None:
    env_path = stack_dir / ".env"
    config = read_env_file(env_path)
    config.setdefault("DATA_DIR", str(data_dir))

    emby_inited = (
        config.get("EMBY_INITIALED", "").lower() == "true"
        or config.get("EMBY-INITIALED", "").lower() == "true"
    )
    if not emby_inited:
        log("提示：EMBY_INITIALED 不为 true，Emby 联动可能尚未就绪。", "warning")

    ensure_container_running(CSF_CONTAINER)

    config_dir = stack_dir / "chinesesubfinder" / "config"
    config_path = config_dir / CSF_SETTINGS_NAME
    existing = load_existing_settings(config_path)

    username, password = resolve_csf_credentials(config, cli_user=csf_user, cli_password=csf_password)
    validate_password_map({"CSF_PASSWORD": password})
    csf_api_key = resolve_csf_api_key(config, existing)
    settings = build_csf_settings(
        config,
        data_dir,
        username=username,
        password=password,
        csf_api_key=csf_api_key,
    )

    if existing:
        settings = deep_merge(existing, settings)

    uid, gid = resolve_app_ids(config)
    config_dir.mkdir(parents=True, exist_ok=True)
    write_settings(config_path, settings, uid=uid, gid=gid)

    update_env_file(
        env_path,
        {
            "CSF_USER": username,
            "CSF_PASSWORD": password,
            "CSF_API_KEY": csf_api_key,
            "CSF_INITIALED": "true",
        },
        log_func=lambda msg: log(msg, "success"),
    )

    log(f"已写入 ChineseSubFinder 配置：{config_path}", "success")
    log(
        "Emby 联动："
        f" enable={settings['emby_settings']['enable']},"
        f" url={settings['emby_settings']['address_url']},"
        f" mapping={settings['emby_settings']['movie_paths_mapping']}",
        "success",
    )
    log(
        f"扫描目录：movie={settings['common_settings']['movie_paths']},"
        f" series={settings['common_settings']['series_paths']}",
        "success",
    )
    log("CSF API Key 已启用并写入 .env（CSF_API_KEY），供 MoviePilot 插件使用。", "success")

    restart_csf_container()
    log(
        f"ChineseSubFinder 初始化完成。WebUI：http://127.0.0.1:7035 ，用户 {username}",
        "success",
    )
    log("新入库补字幕：Emby 联动拉取近期更新 + 定时扫描 media 目录。", "success")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Initialize ChineseSubFinder settings.")
    parser.add_argument("--stack-dir", default="/volume1/docker/media-stack")
    parser.add_argument("--data-dir", default="/volume1/media-data")
    parser.add_argument("--csf-user", help="ChineseSubFinder WebUI username, default admin")
    parser.add_argument("--csf-password", help="ChineseSubFinder WebUI password")
    args = parser.parse_args()
    validate_cli_password_args(args, parser.error)

    run_init_csf(
        stack_dir=Path(args.stack_dir).expanduser().resolve(),
        data_dir=Path(args.data_dir).expanduser().resolve(),
        csf_user=args.csf_user,
        csf_password=args.csf_password,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
