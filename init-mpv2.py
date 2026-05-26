"""
MoviePilot V2 初始化辅助代码

功能：
1. 登录 MoviePilot V2
2. 读取、备份、写入系统配置
3. 初始化 Downloaders：qB-日常下载、qB-刷流专用
4. 初始化 MediaServers：Emby
5. 自动生成并写入 category.yaml，实现“内容类型 + 地区”组合分类
6. 初始化 Storages：本地媒体库根目录
7. 初始化 Directories：按叶子目录精确落库，例如 真人电影/大陆、动漫剧集/日韩、纪录片/欧美、综艺/欧美
8. 初始化 Transfer：移动整理、下载目录、排除刷流目录、命名格式
9. 初始化刮削/识别/重命名基础配置：SCRAP_SOURCE、RECOGNIZE_SOURCE、MOVIE_RENAME_FORMAT、TV_RENAME_FORMAT
10. 初始化 Rules：自定义规则、优先级规则组、下载排序规则
11. 增加配置校验：防止 brush 刷流目录参与整理，确保规则组与目录分类一致
12. 保留 Directories / Transfer 模板导入导出能力，用于版本结构不兼容时兜底

注意：
- Downloaders / MediaServers / Storages / Directories / Transfer / Rules 均支持直接 API 写入。
- category.yaml 通过文件写入，写入后建议重启 MoviePilot 容器。
- 如果当前 MoviePilot V2 版本字段结构变化，仍可先页面配置一次，再导出模板，后续自动写入。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests
from env_utils import get_env, read_env_file, require_env
from log_utils import log
from password_utils import INIT_MPV2_ENV_KEYS, validate_env_passwords
from path_utils import DIR_MODE_DATA, FILE_MODE, apply_permissions, ensure_directory, resolve_app_ids

SENSITIVE_SETTING_KEYS = {"GITHUB_TOKEN"}


def mp_base_url(host_ip: str) -> str:
    """
    host_ip 可以传：
    1. 192.168.3.111
    2. http://192.168.3.111:9443
    """
    host_ip = host_ip.rstrip("/")

    if host_ip.startswith("http://") or host_ip.startswith("https://"):
        return host_ip

    return f"http://{host_ip}:9443"


def unwrap_setting(data: Any) -> Any:
    """
    兼容不同版本返回：
    - 直接返回 value
    - {"data": value}
    - {"value": value}
    - {"result": value}
    """
    if isinstance(data, dict):
        for key in ("data", "value", "result"):
            if key in data:
                return data[key]
    return data


def mp_format_setting_for_log(key: str, value: Any) -> str:
    if key in SENSITIVE_SETTING_KEYS and value:
        return "<hidden>"
    return json.dumps(value, ensure_ascii=False)


def mp_sanitize_settings_for_log(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        key: "<hidden>" if key in SENSITIVE_SETTING_KEYS and value else value
        for key, value in settings.items()
    }


# =========================
# MoviePilot 登录与配置读写
# =========================

def mp_login(host_ip: str, stack_dir: Path) -> requests.Session:
    env_path = stack_dir / ".env"
    env_values = read_env_file(env_path)

    username = require_env(env_values, "MOVIEPILOT_USER", env_path)
    password = require_env(env_values, "MOVIEPILOT_PASSWORD", env_path)

    base_url = mp_base_url(host_ip)
    session = requests.Session()

    resp = session.post(
        f"{base_url}/api/v1/login/access-token",
        data={
            "username": username,
            "password": password,
        },
        timeout=10,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"MP 登录失败：{resp.status_code} {resp.text}")

    data = resp.json()
    token = data.get("access_token")

    if not token:
        raise RuntimeError(f"MP 登录返回中没有 access_token：{data}")

    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })

    log(f"MoviePilot 登录成功：{base_url}，username={username}", "success")
    return session


def mp_get_setting(host_ip: str, session: requests.Session, key: str) -> Any:
    base_url = mp_base_url(host_ip)

    resp = session.get(
        f"{base_url}/api/v1/system/setting/{key}",
        timeout=10,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"读取配置失败：{key}，{resp.status_code} {resp.text}")

    return unwrap_setting(resp.json())


def mp_set_setting(host_ip: str, session: requests.Session, key: str, value: Any) -> None:
    """
    不同版本的 setting 写入 body 可能略有差异。
    这里按常见方式依次尝试：
    1. POST 直接提交 value
    2. POST 提交 {"value": value}
    3. POST 提交 {"data": value}
    4. PUT 直接提交 value
    5. PUT 提交 {"value": value}
    6. PUT 提交 {"data": value}
    """
    base_url = mp_base_url(host_ip)
    url = f"{base_url}/api/v1/system/setting/{key}"

    payloads = [
        value,
        {"value": value},
        {"data": value},
    ]

    last_error = ""

    for method in ("post", "put"):
        for payload in payloads:
            request_func = getattr(session, method)
            resp = request_func(
                url,
                json=payload,
                timeout=10,
            )

            if resp.status_code in (200, 201, 204):
                log(f"写入配置成功：{key}，method={method.upper()}", "success")
                return

            last_error = f"{resp.status_code} {resp.text}"

    raise RuntimeError(f"写入配置失败：{key}，最后错误：{last_error}")


def mp_backup_setting_to_dir(stack_dir: Path, key: str, value: Any) -> Path:
    backup_dir = stack_dir / "moviepilot-v2" / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d-%H%M%S")
    path = backup_dir / f"mpv2-{key}-backup-{ts}.json"

    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log(f"已备份当前配置：{path}", "success")
    return path


def mp_dump_core_settings(host_ip: str, session: requests.Session, stack_dir: Path) -> dict[str, Any]:
    keys = [
        "Downloaders",
        "MediaServers",
        "Storages",
        "Directories",
        "Transfer",
        "CustomFilterRules",
        "UserFilterRuleGroups",
        "SearchFilterRuleGroups",
        "SubscribeFilterRuleGroups",
        "BestVersionFilterRuleGroups",
        "TorrentsPriority",
        # 以下为目录/刮削相关的独立配置键。
        # V2 前端“设定 -> 系统 -> 目录设置/基础设置/进阶设置”实际会维护这些键。
        "SCRAP_SOURCE",
        "RECOGNIZE_SOURCE",
        "SEARCH_SOURCE",
        "MOVIE_RENAME_FORMAT",
        "TV_RENAME_FORMAT",
        "TRANSFER_THREADS",
        "MEDIASERVER_SYNC_INTERVAL",
    ]

    result: dict[str, Any] = {}

    for key in keys:
        try:
            value = mp_get_setting(host_ip, session, key)
            result[key] = value

            mp_backup_setting_to_dir(stack_dir, key, value)

            # log(f"当前 {key}：")
            # log(json.dumps(value, ensure_ascii=False, indent=2))
        except Exception as exc:
            log(f"读取 {key} 失败：{exc}", "warning")

    return result


# =========================
# Downloaders 初始化
# =========================

def _qb_credential(config: dict[str, str], prefix: str) -> tuple[str, str, str | None]:
    """
    qBittorrent 5.2+ 支持独立 WebAPI API key，优先给 MoviePilot 使用。
       QB_MEDIA_API_KEY=xxx
       QB_BRUSH_API_KEY=xxx

    如果 qB 版本不支持或创建失败，回退 WebUI 用户名密码：
       QB_MEDIA_PASSWORD=xxx
       QB_BRUSH_PASSWORD=xxx
    """

    user = config.get(f"QB_{prefix}_USER", "admin") 
    api_key = config.get(f"QB_{prefix}_API_KEY", "")
    password = config.get(f"QB_{prefix}_PASSWORD", "")

    if not user or not (api_key or password):
        raise RuntimeError(
            f".env 缺少 qB 登录信息：需要 QB_{prefix}_API_KEY 或 QB_{prefix}_PASSWORD。"
        )
    log(f"qB {prefix} API Key：{'<hidden>' if api_key else '<not set>'}", "success")
    return user, password, api_key or None


def mp_validate_qb_downloaders(downloaders: list[dict[str, Any]]) -> None:
    """
    Validate qB connectivity from inside the MoviePilot container.

    The configured downloader host is a Docker-network address, so validating
    from the host would test the wrong network path.
    """
    validation_script = r'''
import json
import sys
import urllib.parse
import urllib.request

downloaders = json.load(sys.stdin)
results = []

for downloader in downloaders:
    name = downloader.get("name") or "qBittorrent"
    config = downloader.get("config") or {}
    host = str(config.get("host") or "").rstrip("/")
    api_key = config.get("apikey") or config.get("api_key") or ""
    username = config.get("username") or ""
    password = config.get("password") or ""

    if not host:
        raise RuntimeError(f"{name}: missing host")

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    headers = {
        "User-Agent": "MoviePilot-init-validator/1.0",
        "Referer": host + "/",
        "Origin": host,
    }

    if api_key:
        req = urllib.request.Request(
            host + "/api/v2/app/version",
            headers={**headers, "Authorization": "Bearer " + api_key},
        )
        with opener.open(req, timeout=10) as resp:
            version = resp.read().decode("utf-8", errors="replace").strip()
    else:
        if not username or not password:
            raise RuntimeError(f"{name}: missing username/password and api_key")
        data = urllib.parse.urlencode({"username": username, "password": password}).encode()
        req = urllib.request.Request(host + "/api/v2/auth/login", data=data, headers=headers)
        with opener.open(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
            if resp.status not in (200, 204) or (body and body.lower() not in ("ok.", "ok")):
                raise RuntimeError(f"{name}: login failed, status={resp.status}, body={body}")
        req = urllib.request.Request(host + "/api/v2/app/version", headers=headers)
        with opener.open(req, timeout=10) as resp:
            version = resp.read().decode("utf-8", errors="replace").strip()

    if not version:
        raise RuntimeError(f"{name}: empty qB version response")
    results.append({"name": name, "host": host, "version": version, "auth": "api_key" if api_key else "password"})

print(json.dumps(results, ensure_ascii=False))
'''
    payload = json.dumps(downloaders, ensure_ascii=False)
    result = subprocess.run(
        ["docker", "exec", "-i", "mpv2-moviepilot", "python", "-c", validation_script],
        input=payload,
        check=False,
        text=True,
        capture_output=True,
    )
    output = (result.stdout.strip() or result.stderr.strip()).strip()
    if result.returncode != 0:
        raise RuntimeError(f"qB 下载器连通性验证失败：{output}")

    try:
        checked = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"qB 下载器连通性验证返回异常：{output}") from exc

    for item in checked:
        log(
            f"qB 下载器验证通过：{item['name']}，host={item['host']}，"
            f"version={item['version']}，auth={item['auth']}",
            "success",
        )


def mp_build_downloaders(config: dict[str, str]) -> list[dict[str, Any]]:
    qb_media_user, qb_media_password, qb_media_api_key = _qb_credential(config, "MEDIA")
    qb_brush_user, qb_brush_password, qb_brush_api_key = _qb_credential(config, "BRUSH")

    qb_media_host = get_env(config, "MP_QB_MEDIA_HOST", "http://qb-media:7097")
    qb_brush_host = get_env(config, "MP_QB_BRUSH_HOST", "http://qb-brush:7098")

    def _build_path_mapping(kind: str) -> list[list[str]]:
        kind = kind.upper()
        storage_path = get_env(config, f"MP_QB_{kind}_STORAGE_PATH")
        download_path = get_env(config, f"MP_QB_{kind}_DOWNLOAD_PATH", storage_path)

        storage_path = storage_path.rstrip("/") if storage_path else ""
        download_path = download_path.rstrip("/") if download_path else ""

        if not storage_path or not download_path:
            return []

        return [[storage_path, download_path]]

    media_path_mapping = _build_path_mapping(
        kind="MEDIA"
    )
    log(f"qB-日常下载路径映射：{media_path_mapping}", "success")
    brush_path_mapping = _build_path_mapping(
        kind="BRUSH"
    )
    log(f"qB-刷流下载路径映射：{brush_path_mapping}", "success")

    media_config = {
        "host": qb_media_host,
        "username": qb_media_user,
        "password": qb_media_password,
        # category=True 是 qB 自动分类管理；这里关闭，保存路径由 MoviePilot 整理目录传入。
        "category": False,
        "sequentail": False,
        "force_resume": False,
        "first_last_piece": False,
    }
    if qb_media_api_key:
        media_config["apikey"] = qb_media_api_key

    brush_config = {
        "host": qb_brush_host,
        "username": qb_brush_user,
        "password": qb_brush_password,
        "category": False,
        "sequentail": False,
        "force_resume": False,
        "first_last_piece": False,
    }
    if qb_brush_api_key:
        brush_config["apikey"] = qb_brush_api_key

    downloaders = [
        {
            "name": "qB-日常下载",
            "type": "qbittorrent",
            "enabled": True,
            "default": True,
            "path_mapping": media_path_mapping,
            "config": media_config,
        },
        {
            "name": "qB-刷流专用",
            "type": "qbittorrent",
            "enabled": True,
            "default": False,
            "path_mapping": brush_path_mapping,
            "config": brush_config,
        },
    ]
    if mp_bool_env(config, "MP_VALIDATE_QB_DOWNLOADERS", "true"):
        mp_validate_qb_downloaders(downloaders)

    return downloaders


def mp_validate_downloaders_written(expected: list[dict[str, Any]], checked: Any) -> None:
    checked = unwrap_setting(checked)
    if not isinstance(checked, list):
        raise RuntimeError(f"MoviePilot Downloaders 写入验证失败：返回不是列表，返回={checked}")

    checked_by_name = {
        item.get("name"): item
        for item in checked
        if isinstance(item, dict)
    }
    errors: list[str] = []

    for item in expected:
        name = item.get("name")
        expected_config = item.get("config") or {}
        checked_item = checked_by_name.get(name)
        if not isinstance(checked_item, dict):
            errors.append(f"缺少下载器：{name}")
            continue

        checked_config = checked_item.get("config") or {}
        if checked_item.get("type") != item.get("type"):
            errors.append(f"{name} type 不一致：expected={item.get('type')} actual={checked_item.get('type')}")
        if checked_config.get("host") != expected_config.get("host"):
            errors.append(
                f"{name} host 不一致：expected={expected_config.get('host')} actual={checked_config.get('host')}"
            )
        expected_libraries = item.get("sync_libraries") or []
        checked_libraries = checked_item.get("sync_libraries") or []
        if expected_libraries and set(checked_libraries) != set(expected_libraries):
            errors.append(
                f"{name} sync_libraries 不一致："
                f"expected={expected_libraries} actual={checked_libraries}"
            )

    if errors:
        raise RuntimeError("MoviePilot Downloaders 写入验证失败：\n- " + "\n- ".join(errors))

    log("MoviePilot Downloaders 写入验证通过。", "success")


def mp_set_downloaders(
    host_ip: str,
    session: requests.Session,
    stack_dir: Path,
    config: dict[str, str],
) -> None:
    old_value = mp_get_setting(host_ip, session, "Downloaders")
    mp_backup_setting_to_dir(stack_dir, "Downloaders", old_value)

    # log("当前 Downloaders：")
    # log(json.dumps(old_value, ensure_ascii=False, indent=2))

    new_value = mp_build_downloaders(config)
    mp_set_setting(host_ip, session, "Downloaders", new_value)

    checked = mp_get_setting(host_ip, session, "Downloaders")
    mp_validate_downloaders_written(new_value, checked)
    # log("写入后的 Downloaders：")
    # log(json.dumps(checked, ensure_ascii=False, indent=2))


# 兼容你之前的函数名
def mp_set_downloader(host_ip: str, session: requests.Session, config: dict[str, str]) -> None:
    stack_dir = Path(get_env(config, "STACK_DIR", "/volume1/media-stack"))
    mp_set_downloaders(host_ip, session, stack_dir, config)


# =========================
# MediaServers 初始化
# =========================

def mp_fetch_emby_libraries(host: str, api_key: str) -> list[dict[str, str]]:
    validation_script = r'''
import json
import sys
import urllib.parse
import urllib.request

payload = json.load(sys.stdin)
host = str(payload["host"]).rstrip("/")
api_key = payload["api_key"]

def get_json(path):
    separator = "&" if "?" in path else "?"
    url = host + path + separator + urllib.parse.urlencode({"api_key": api_key})
    req = urllib.request.Request(url, headers={"User-Agent": "MoviePilot-init-validator/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            raise RuntimeError(f"{path}: status={resp.status}")
        return json.loads(resp.read().decode("utf-8", errors="replace"))

views = get_json("/Library/VirtualFolders")
libraries = []
for item in views or []:
    item_id = item.get("ItemId") or item.get("Id") or item.get("Guid")
    name = item.get("Name") or item.get("CollectionType") or item_id
    if item_id:
        libraries.append({"id": str(item_id), "name": str(name)})

print(json.dumps(libraries, ensure_ascii=False))
'''
    payload = json.dumps({"host": host, "api_key": api_key}, ensure_ascii=False)
    result = subprocess.run(
        ["docker", "exec", "-i", "mpv2-moviepilot", "python", "-c", validation_script],
        input=payload,
        check=False,
        text=True,
        capture_output=True,
    )
    output = (result.stdout.strip() or result.stderr.strip()).strip()
    if result.returncode != 0:
        raise RuntimeError(f"Emby 媒体库列表获取失败：{output}")

    try:
        libraries = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Emby 媒体库列表返回异常：{output}") from exc

    if not isinstance(libraries, list):
        raise RuntimeError(f"Emby 媒体库列表返回异常：{libraries}")

    return [
        {"id": str(item["id"]), "name": str(item.get("name") or item["id"])}
        for item in libraries
        if isinstance(item, dict) and item.get("id")
    ]


def mp_validate_emby_media_servers(media_servers: list[dict[str, Any]]) -> None:
    validation_script = r'''
import json
import sys
import urllib.parse
import urllib.request

media_servers = json.load(sys.stdin)
results = []

for media_server in media_servers:
    if media_server.get("type") != "emby":
        continue

    name = media_server.get("name") or "Emby"
    config = media_server.get("config") or {}
    host = str(config.get("host") or "").rstrip("/")
    api_key = config.get("apikey") or config.get("api_key") or ""

    if not host:
        raise RuntimeError(f"{name}: missing host")
    if not api_key:
        raise RuntimeError(f"{name}: missing api key")

    url = host + "/System/Info?" + urllib.parse.urlencode({"api_key": api_key})
    req = urllib.request.Request(url, headers={"User-Agent": "MoviePilot-init-validator/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            raise RuntimeError(f"{name}: system info failed, status={resp.status}")
        data = json.loads(resp.read().decode("utf-8", errors="replace"))

    results.append({
        "name": name,
        "host": host,
        "server": data.get("ServerName") or data.get("Name") or "",
        "version": data.get("Version") or "",
    })

print(json.dumps(results, ensure_ascii=False))
'''
    payload = json.dumps(media_servers, ensure_ascii=False)
    result = subprocess.run(
        ["docker", "exec", "-i", "mpv2-moviepilot", "python", "-c", validation_script],
        input=payload,
        check=False,
        text=True,
        capture_output=True,
    )
    output = (result.stdout.strip() or result.stderr.strip()).strip()
    if result.returncode != 0:
        raise RuntimeError(f"Emby 媒体服务器连通性验证失败：{output}")

    try:
        checked = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Emby 媒体服务器连通性验证返回异常：{output}") from exc

    if not checked:
        raise RuntimeError("Emby 媒体服务器连通性验证失败：未发现 Emby 配置")

    for item in checked:
        log(
            f"Emby 媒体服务器验证通过：{item['name']}，host={item['host']}，"
            f"server={item['server'] or '<unknown>'}，version={item['version'] or '<unknown>'}",
            "success",
        )


def mp_build_media_servers(config: dict[str, str]) -> list[dict[str, Any]]:
    """
    MoviePilot V2 媒体服务器配置。

    与 Downloaders 一样，Emby 的连接参数也放在 config 子对象内。
    """
    emby_api_key = config.get("EMBY_API_KEY", "")

    if not emby_api_key:
        log("未找到 EMBY_API_KEY，MoviePilot 媒体服务器将无法配置 Emby", "warning")
        raise RuntimeError(".env 缺少 EMBY_API_KEY，无法配置 MoviePilot 媒体服务器")

    emby_host = get_env(config, "MP_EMBY_HOST", "http://emby:8096")
    emby_username = get_env(config, "EMBY_USER", "")
    sync_libraries: list[str] = []
    if mp_bool_env(config, "MP_SYNC_ALL_EMBY_LIBRARIES", "true"):
        libraries = mp_fetch_emby_libraries(emby_host, emby_api_key)
        if not libraries:
            raise RuntimeError("Emby 未返回可同步的媒体库，无法勾选同步媒体库。")
        sync_libraries = [item["id"] for item in libraries]
        library_names = [item["name"] for item in libraries]
        log(f"Emby 同步媒体库已设置为全部：{library_names}", "success")

    media_servers = [
        {
            "name": "Emby",
            "type": "emby",
            "enabled": True,
            "sync_libraries": sync_libraries,
            "config": {
                "host": emby_host,
                "username": emby_username,
                # 不同版本字段可能是 apikey 或 api_key，同时保留。
                "apikey": emby_api_key,
                "api_key": emby_api_key,
            },
        }
    ]
    if mp_bool_env(config, "MP_VALIDATE_EMBY_MEDIA_SERVERS", "true"):
        mp_validate_emby_media_servers(media_servers)

    return media_servers


def mp_validate_media_servers_written(expected: list[dict[str, Any]], checked: Any) -> None:
    checked = unwrap_setting(checked)
    if not isinstance(checked, list):
        raise RuntimeError(f"MoviePilot MediaServers 写入验证失败：返回不是列表，返回={checked}")

    checked_by_name = {
        item.get("name"): item
        for item in checked
        if isinstance(item, dict)
    }
    errors: list[str] = []

    for item in expected:
        name = item.get("name")
        expected_config = item.get("config") or {}
        checked_item = checked_by_name.get(name)
        if not isinstance(checked_item, dict):
            errors.append(f"缺少媒体服务器：{name}")
            continue

        checked_config = checked_item.get("config") or {}
        if checked_item.get("type") != item.get("type"):
            errors.append(f"{name} type 不一致：expected={item.get('type')} actual={checked_item.get('type')}")
        if checked_config.get("host") != expected_config.get("host"):
            errors.append(
                f"{name} host 不一致：expected={expected_config.get('host')} actual={checked_config.get('host')}"
            )

    if errors:
        raise RuntimeError("MoviePilot MediaServers 写入验证失败：\n- " + "\n- ".join(errors))

    log("MoviePilot MediaServers 写入验证通过。", "success")

def mp_set_media_servers(
    host_ip: str,
    session: requests.Session,
    stack_dir: Path,
    config: dict[str, str],
) -> None:
    old_value = mp_get_setting(host_ip, session, "MediaServers")
    mp_backup_setting_to_dir(stack_dir, "MediaServers", old_value)

    # log("当前 MediaServers：")
    # log(json.dumps(old_value, ensure_ascii=False, indent=2))

    new_value = mp_build_media_servers(config)
    mp_set_setting(host_ip, session, "MediaServers", new_value)

    checked = mp_get_setting(host_ip, session, "MediaServers")
    mp_validate_media_servers_written(new_value, checked)
    # log("写入后的 MediaServers：")
    # log(json.dumps(checked, ensure_ascii=False, indent=2))


# =========================
# 刮削 / 识别 / 重命名基础设置
# =========================

def mp_movie_rename_format(config: dict[str, str]) -> str:
    return get_env(
        config,
        "MP_MOVIE_RENAME_FORMAT",
        "{{title}}{% if year %} ({{year}}){% endif %}/"
        "{{title}}{% if year %} ({{year}}){% endif %}"
        "{% if part %}-{{part}}{% endif %}"
        "{% if videoFormat %} - {{videoFormat}}{% endif %}{{fileExt}}",
    )


def mp_tv_rename_format(config: dict[str, str]) -> str:
    return get_env(
        config,
        "MP_TV_RENAME_FORMAT",
        "{{title}}{% if year %} ({{year}}){% endif %}/"
        "Season {{season}}/"
        "{{title}} - {{season_episode}}"
        "{% if part %}-{{part}}{% endif %}"
        "{% if episode %} - 第 {{episode}} 集{% endif %}{{fileExt}}",
    )


def mp_build_scrape_core_settings(config: dict[str, str]) -> dict[str, Any]:
    """
    这些是 MoviePilot V2 目录/刮削页面对应的独立配置键。

    不能只写 Transfer 大对象，否则部分版本页面中“刮削来源、识别来源、
    重命名格式、整理线程、媒体服务器同步间隔”可能不会同步显示。
    """
    settings: dict[str, Any] = {
        "SCRAP_SOURCE": get_env(config, "MP_SCRAP_SOURCE", "themoviedb"),
        "RECOGNIZE_SOURCE": get_env(config, "MP_RECOGNIZE_SOURCE", "themoviedb"),
        "SEARCH_SOURCE": get_env(config, "MP_SEARCH_SOURCE", "themoviedb"),
        "MOVIE_RENAME_FORMAT": mp_movie_rename_format(config),
        "TV_RENAME_FORMAT": mp_tv_rename_format(config),
        "TRANSFER_THREADS": int(get_env(config, "MP_TRANSFER_THREADS", "1")),
        "MEDIASERVER_SYNC_INTERVAL": int(get_env(config, "MP_MEDIASERVER_SYNC_INTERVAL", "6")),
    }
    github_token = get_env(config, "GITHUB_TOKEN")
    if github_token:
        settings["GITHUB_TOKEN"] = github_token
    return settings


def mp_set_scrape_core_settings(
    host_ip: str,
    session: requests.Session,
    stack_dir: Path,
    config: dict[str, str],
) -> None:
    """
    写入刮削、识别、重命名和 Emby 同步相关基础配置。
    失败时不中断主流程，因为不同 MoviePilot V2 版本可能把这些配置合并到了结构化配置中。
    """
    for key, value in mp_build_scrape_core_settings(config).items():
        try:
            old_value = mp_get_setting(host_ip, session, key)
            mp_backup_setting_to_dir(stack_dir, key, old_value)
            # log(f"当前 {key}：{mp_format_setting_for_log(key, old_value)}")
        except Exception as exc:
            log(f"读取 {key} 失败，继续尝试写入：{exc}", "warning")

        try:
            mp_set_setting(host_ip, session, key, value)
            checked = mp_get_setting(host_ip, session, key)
            log(f"写入后的 {key}：{mp_format_setting_for_log(key, checked)}")
        except Exception as exc:
            log(f"写入 {key} 失败，已跳过。若页面已正确显示可忽略：{exc}", "warning")



# =========================
# Rules 初始化：自定义规则 / 优先级规则组 / 下载排序
# =========================

def mp_build_custom_filter_rules() -> list[dict[str, Any]]:
    """
    MoviePilot V2 自定义规则。

    这些规则对应“设定 -> 规则 -> 自定义规则”。
    规则 ID 会在优先级规则组 rule_string 中被引用，所以 ID 不要随意改名。
    """
    return [
        {
            "id": "Complete",
            "name": "Complete",
            # 优化点：原规则 \\d 只能匹配 1 位数字，这里改为 \\d+，可匹配 全12集/共24期。
            "include": r"(全|共)\d+(集|期)|完结|合集|Complete",
            "exclude": "",
        },
        {
            "id": "filterGlobal",
            "name": "filterGlobal",
            "include": "",
            "exclude": r"(?i)日语无字|先行|DV|MiniBD|DIY原盘|iPad|UPSCALE|AV1|BDMV|RMVB|DVD|vcd|480p|OPUS",
            "seeders": "",
        },
        {
            # 保留 filerGroup 这个拼写，是因为很多现成规则组都按这个 ID 引用。
            "id": "filerGroup",
            "name": "filerGroup",
            "include": "",
            "exclude": r"(?i)SubsPlease|Up to 21°C|VARYG|TELESYNC|NTb|sGnb|BHYS|DBD|HDH|COLLECTiVE|SRVFI|HDSPad",
        },
        {
            "id": "filterMovie",
            "name": "filterMovie",
            "include": "",
            "exclude": "",
            # 单部电影最大 22GB，避免订阅时误下超大 REMUX / 原盘。
            "size_range": "0-22000",
            "seeders": "",
        },
        {
            "id": "filterSeries",
            "name": "filterSeries",
            "include": "",
            "exclude": "",
            # 剧集/整季最大 100GB，给合集留空间。
            "size_range": "0-102400",
        },
        {
            "id": "AnimeGroup",
            "name": "AnimeGroup",
            "include": r"7³ACG|VCB-Studio",
            "exclude": "",
            "size_range": "",
        },
        {
            "id": "Audiences",
            "name": "Audiences",
            "include": r"ADE|ADWeb",
            "exclude": "",
            "seeders": "",
        },
        {
            "id": "HHWEB",
            "name": "HHWEB",
            "include": r"HHWEB",
            "exclude": "",
        },
        {
            "id": "Crunchyroll",
            "name": "Crunchyroll",
            # 优化点：CR 太短，加单词边界，降低误匹配。
            "include": r"\b(CR|Crunchyroll)\b",
            "exclude": "",
        },
        {
            "id": "Netflix",
            "name": "Netflix",
            # 优化点：NF 太短，加单词边界，降低误匹配。
            "include": r"\b(NF|Netflix)\b",
            "exclude": "",
        },
        {
            "id": "BGlobal",
            "name": "B-Global",
            # 优化点：BG 太短，加单词边界；ID 不使用 '-'，避免规则表达式解析歧义。
            "include": r"\b(BG|B-Global)\b",
            "exclude": "",
        },
        {
            "id": "AMZN",
            "name": "AMZN",
            "include": r"AMZN|Amazon",
            "exclude": "",
        },
        {
            "id": "HQ",
            "name": "HQ",
            "include": r"HQ|高码|EDR",
            "exclude": "",
            "size_range": "",
        },
        {
            "id": "DDP",
            "name": "DDP",
            "include": r"DDP",
            "exclude": "",
        },
        {
            "id": "AnimeKeyword",
            "name": "AnimeKeyword",
            "include": ANIME_CANDIDATE_KEYWORD_PATTERN,
            "exclude": "",
            "size_range": "",
        },
        {
            "id": "PrivateCandidate",
            "name": "PrivateCandidate",
            "include": PRIVATE_CANDIDATE_KEYWORD_PATTERN,
            "exclude": "",
            "size_range": "",
        },
    ]


def _rule_group(
    name: str,
    rule_string: str,
    media_type: str = "",
    category: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "rule_string": rule_string,
        "media_type": media_type,
        "category": category,
    }


def _foreign_movie_rule() -> str:
    return (
        "SPECSUB & 4K & BLURAY & H265 & filterMovie > "
        "CNSUB & 4K & BLURAY & H265 & filterMovie > "
        "CNSUB & 1080P & BLURAY & filterMovie > "
        "CNSUB & 4K & filterMovie > "
        "CNSUB & 1080P & filterMovie"
    )


def _chinese_movie_rule() -> str:
    return (
        "4K & WEBDL & HQ & filterMovie > "
        "4K & WEBDL & filterMovie > "
        "1080P & WEBDL & filterMovie > "
        "4K & filterMovie > "
        "1080P & filterMovie"
    )


def _anime_movie_rule() -> str:
    return (
        "AnimeGroup & CNSUB & 1080P & BLURAY > "
        "CNSUB & 4K & BLURAY > "
        "CNSUB & 1080P & BLURAY > "
        "CNSUB & 4K > "
        "CNSUB & 1080P"
    )


def _anime_series_rule(region: str) -> str:
    if region in ("大陆", "港澳台"):
        return (
            "4K & Audiences & HHWEB & DDP > "
            "4K & Audiences & HHWEB > "
            "1080P & Audiences & HHWEB > "
            "4K > "
            "1080P > "
            "720P"
        )

    return (
        "AnimeGroup & CNSUB & BLURAY & 1080P > "
        "Audiences & H265 & BLURAY & 1080P > "
        "Audiences & AMZN & HHWEB & CNSUB & 1080P > "
        "Audiences & Crunchyroll & CNSUB & 1080P > "
        "Audiences & Netflix & HHWEB & CNSUB & 1080P > "
        "Audiences & BGlobal & 4K & CNSUB > "
        "Audiences & BGlobal & 1080P & CNSUB > "
        "Audiences & HHWEB & CNSUB & 1080P > "
        "CNSUB & BLURAY & 1080P > "
        "1080P & CNSUB > "
        "1080P"
    )


def _variety_rule() -> str:
    return (
        "4K & WEBDL & Complete > "
        "4K & WEBDL & HHWEB > "
        "WEBDL & 1080P & HHWEB > "
        "4K & WEBDL & Audiences > "
        "1080P & Audiences & WEBDL > "
        "1080P"
    )


def _documentary_rule() -> str:
    return (
        "4K & WEBDL & CNSUB > "
        "1080P & WEBDL & CNSUB > "
        "4K & WEBDL > "
        "1080P & WEBDL > "
        "1080P"
    )


def _chinese_series_rule() -> str:
    return (
        "4K & WEBDL & HQ & filterSeries > "
        "4K & WEBDL & filterSeries > "
        "1080P & WEBDL & filterSeries > "
        "1080P & filterSeries > "
        "720P & filterSeries"
    )


def _foreign_series_rule(region: str) -> str:
    if region == "欧美":
        return (
            "SPECSUB & 1080P & BLURAY & filterSeries > "
            "1080P & WEBDL & CNSUB & filterSeries > "
            "CNSUB & filterSeries"
        )
    if region == "日韩":
        return (
            "SPECSUB & 1080P & BLURAY & filterSeries > "
            "CNSUB & 1080P & filterSeries > "
            "1080P & CNSUB & filterSeries > "
            "CNSUB & filterSeries"
        )
    return (
        "CNSUB & 1080P & WEBDL & filterSeries > "
        "CNSUB & 1080P & filterSeries > "
        "1080P & filterSeries"
    )


def mp_build_user_filter_rule_groups() -> list[dict[str, Any]]:
    """
    MoviePilot V2 优先级规则组。

    这里的 category 名称与本脚本生成的 category.yaml 保持一致：
    - 动漫电影-地区
    - 真人电影-地区
    - 动漫剧集-地区
    - 纪录片-地区
    - 综艺-地区
    - 真人剧集-地区
    """
    groups: list[dict[str, Any]] = []

    # 全局前置过滤：所有搜索/订阅先过一遍，排除低质、老格式、特殊格式和黑名单发布组。
    groups.append(_rule_group(
        name="前置过滤",
        rule_string="filterGlobal & !BLU & !REMUX & !3D & !DOLBY & filerGroup",
    ))

    for region in REGIONS:
        groups.append(_rule_group(
            name=f"动漫电影-{region}",
            rule_string=_anime_movie_rule(),
            media_type="电影",
            category=f"动漫电影-{region}",
        ))

    for region in REGIONS:
        rule_string = _chinese_movie_rule() if region in ("大陆", "港澳台") else _foreign_movie_rule()
        groups.append(_rule_group(
            name=f"真人电影-{region}",
            rule_string=rule_string,
            media_type="电影",
            category=f"真人电影-{region}",
        ))

    for region in REGIONS:
        groups.append(_rule_group(
            name=f"动漫剧集-{region}",
            rule_string=_anime_series_rule(region),
            media_type="电视剧",
            category=f"动漫剧集-{region}",
        ))

    for region in REGIONS:
        groups.append(_rule_group(
            name=f"纪录片-{region}",
            rule_string=_documentary_rule(),
            media_type="电视剧",
            category=f"纪录片-{region}",
        ))

    for region in REGIONS:
        groups.append(_rule_group(
            name=f"综艺-{region}",
            rule_string=_variety_rule(),
            media_type="电视剧",
            category=f"综艺-{region}",
        ))

    for region in REGIONS:
        rule_string = _chinese_series_rule() if region in ("大陆", "港澳台") else _foreign_series_rule(region)
        groups.append(_rule_group(
            name=f"真人剧集-{region}",
            rule_string=rule_string,
            media_type="电视剧",
            category=f"真人剧集-{region}",
        ))

    # 保留给手动指定短剧目录时使用。
    groups.append(_rule_group(
        name="短剧",
        rule_string="Complete & 1080P > 1080P > 720P",
        media_type="电视剧",
        category="短剧",
    ))

    return groups


def mp_build_torrents_priority(config: dict[str, str] | None = None) -> list[str]:
    """
    下载规则：同步命中多个资源时的排序优先级。

    对应前端选项：
    - torrent：资源优先级
    - site：站点优先级
    - upload：站点上传量
    - seeder：资源做种数

    默认与截图保持一致：资源优先级 > 站点上传量 > 资源做种数。
    如需调整，可在 .env 中设置：
      MP_TORRENTS_PRIORITY=torrent,upload,seeder
    """
    config = config or {}
    raw_value = get_env(config, "MP_TORRENTS_PRIORITY", "torrent,upload,seeder")
    valid_values = {"torrent", "site", "upload", "seeder"}

    values = [
        item.strip()
        for item in raw_value.split(",")
        if item.strip()
    ]

    invalid_values = [item for item in values if item not in valid_values]
    if invalid_values:
        raise RuntimeError(
            "MP_TORRENTS_PRIORITY 存在无效值："
            f"{invalid_values}，允许值：torrent,site,upload,seeder"
        )

    # 去重并保留顺序
    result: list[str] = []
    for item in values:
        if item not in result:
            result.append(item)

    return result or ["torrent", "upload", "seeder"]


def mp_set_rules(
    host_ip: str,
    session: requests.Session,
    stack_dir: Path,
    config: dict[str, str],
) -> None:
    """
    一次性配置 MoviePilot 规则：
    1. CustomFilterRules：自定义规则
    2. UserFilterRuleGroups：优先级规则组
    3. SearchFilterRuleGroups：搜索默认启用的规则组
    4. SubscribeFilterRuleGroups：订阅默认启用的规则组
    5. BestVersionFilterRuleGroups：洗版默认启用的规则组
    6. TorrentsPriority：下载排序规则

    可通过 .env 控制是否把规则组自动应用到搜索/订阅/洗版：
      MP_APPLY_RULE_GROUPS_TO_GLOBAL=true
    """
    custom_rules = mp_build_custom_filter_rules()
    rule_groups = mp_build_user_filter_rule_groups()
    rule_group_names = [item["name"] for item in rule_groups]
    apply_global = get_env(config, "MP_APPLY_RULE_GROUPS_TO_GLOBAL", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    items: dict[str, Any] = {
        "CustomFilterRules": custom_rules,
        "UserFilterRuleGroups": rule_groups,
        "TorrentsPriority": mp_build_torrents_priority(config),
    }

    if apply_global:
        items.update({
            "SearchFilterRuleGroups": rule_group_names,
            "SubscribeFilterRuleGroups": rule_group_names,
            "BestVersionFilterRuleGroups": rule_group_names,
        })

    for key, value in items.items():
        try:
            old_value = mp_get_setting(host_ip, session, key)
            mp_backup_setting_to_dir(stack_dir, key, old_value)

            # log(f"当前 {key}：")
            # log(json.dumps(old_value, ensure_ascii=False, indent=2))
        except Exception as exc:
            log(f"读取 {key} 失败，继续尝试写入：{exc}", "warning")

        log(f"开始写入 {key} ...")
        mp_set_setting(host_ip, session, key, value)

        checked = mp_get_setting(host_ip, session, key)
        # log(f"写入后的 {key}：")
        # log(json.dumps(checked, ensure_ascii=False, indent=2))

    log("MoviePilot V2 规则初始化完成。", "success")



# =========================
# 自动分类 category.yaml + Storages / Directories / Transfer
# =========================

REGIONS = {
    "大陆": {
        "movie_countries": "CN",
        "tv_countries": "CN",
    },
    "港澳台": {
        "movie_countries": "HK,MO,TW",
        "tv_countries": "HK,MO,TW",
    },
    "欧美": {
        "movie_countries": "US,GB,FR,DE,IT,ES,NL,SE,NO,DK,BE,CA,AU,NZ,PT,RU,PL,CZ,CH,IE,FI,AT,GR",
        "tv_countries": "US,GB,FR,DE,IT,ES,NL,SE,NO,DK,BE,CA,AU,NZ,PT,RU,PL,CZ,CH,IE,FI,AT,GR",
    },
    "日韩": {
        "movie_countries": "JP,KR,KP",
        "tv_countries": "JP,KR,KP",
    },
    "东南亚": {
        "movie_countries": "TH,SG,MY,VN,PH,ID,LA,MM,KH,BN",
        "tv_countries": "TH,SG,MY,VN,PH,ID,LA,MM,KH,BN",
    },
    "其他地区": {
        "movie_countries": "",
        "tv_countries": "",
    },
}

PRIVATE_REGIONS = {
    "国产": {
        "movie_countries": "CN,HK,MO,TW",
    },
    "欧美": {
        "movie_countries": REGIONS["欧美"]["movie_countries"],
    },
    "日韩": {
        "movie_countries": "JP,KR,KP",
    },
    "其他地区": {
        "movie_countries": "",
    },
}

# 严格限制级分级。注意：NR/UNRATED/NOT RATED 不再作为自动私享影库条件，
# 因为“未分级”大量存在于普通电影、导演剪辑版、老片中，误判率很高。
PRIVATE_CONTENT_RATINGS = (
    "18",
    "18+",
    "R18",
    "R18+",
    "R-18",
    "R21",
    "NC-17",
    "X",
    "XXX",
    "TV-MA",
    "MA",
    "III",
    "19",
    "R19",
)

# TMDB 详情接口中的 adult 是一级字段，category.yaml 官方支持匹配 TMDB 详情 API 的一级字段。
# 因此自动私享影库只使用 adult=true 做保守匹配；其它分级建议通过 NFO/Emby/人工复核处理。
PRIVATE_ADULT_VALUES = "true,True,1"
PRIVATE_FALLBACK_REGION = "日韩"
PRIVATE_UNCLASSIFIED_MOVIE_CATEGORY = f"私享影库/{PRIVATE_FALLBACK_REGION}-未识别"

# 这些关键词只作为“规则候选/人工复核”使用，不直接决定目录归类。
# 目录归类仍以 TMDB 的 genre_ids / countries / adult 字段为准。
PRIVATE_CANDIDATE_KEYWORD_PATTERN = (
    r"(?i)18\+|R18|R-18|R18\+|NC-17|TV-MA|XXX|Adult|情色|三级|限制级|成人|无码|有码"
)

ANIME_CANDIDATE_KEYWORD_PATTERN = (
    r"(?i)Anime|ANi|ANiCORN|Lilith-Raws|LoliHouse|NC-Raws|VCB-Studio|"
    r"Moozzi2|SweetSub|喵萌|桜都|动漫|动画|剧场版"
)


def mp_storage_root(config: dict[str, str]) -> str:
    """
    MoviePilot V2 本地存储根目录。

    这里默认使用 /volume1/media-data，而不是 /volume1/media-data/media。
    原因是下载目录和媒体库目录在同一个挂载根目录下，移动/硬链接整理都更稳定。
    """
    data_dir = get_env(config, "DATA_DIR", "/volume1/media-data")
    return get_env(config, "MP_STORAGE_ROOT", data_dir)


def mp_media_root(config: dict[str, str]) -> str:
    data_dir = get_env(config, "DATA_DIR", "/volume1/media-data")
    return get_env(config, "MP_MEDIA_ROOT", f"{data_dir}/media")


def mp_download_media_dir(config: dict[str, str]) -> str:
    data_dir = get_env(config, "DATA_DIR", "/volume1/media-data")
    return get_env(config, "MP_MEDIA_DOWNLOAD_PATH", f"{data_dir}/downloads/media")


def mp_download_manual_dir(config: dict[str, str]) -> str:
    data_dir = get_env(config, "DATA_DIR", "/volume1/media-data")
    return get_env(config, "MP_MANUAL_DOWNLOAD_PATH", f"{data_dir}/downloads/manual")


def mp_download_brush_dir(config: dict[str, str]) -> str:
    data_dir = get_env(config, "DATA_DIR", "/volume1/media-data")
    return get_env(config, "MP_BRUSH_DOWNLOAD_PATH", f"{data_dir}/downloads/brush")


def mp_category_yaml_path(stack_dir: Path) -> Path:
    return stack_dir / "moviepilot-v2" / "config" / "category.yaml"


def mp_private_category(region: str) -> str:
    return f"私享影库/{region}"


def mp_build_category_yaml() -> str:
    """
    自动生成 MoviePilot V2 二级分类策略。

    设计原则：
    1. 动漫/真人只依赖 TMDB 的 genre_ids=16 判断；这是 MoviePilot category.yaml 能稳定利用的字段。
    2. 私享影库不再使用 content_rating 自动归类，因为分级信息通常不在 TMDB 详情一级字段里，
       MoviePilot 的 category.yaml 不一定能拿到；并且 NR/UNRATED 误判率很高。
    3. 私享影库只针对电影使用 TMDB adult=true 做保守自动匹配，其它疑似内容建议进入人工确认流程。
    4. 未匹配电影进入私享影库/日韩，未匹配剧集仍进入“未分类剧集”兜底。
    """
    lines: list[str] = []
    lines.append("# MoviePilot V2 自动分类策略")
    lines.append("# 由初始化脚本生成；如需手工调整，建议先备份。")
    lines.append("# 说明：分类按从上到下顺序匹配；同一分类下多个条件为 AND。")
    lines.append("# 私享影库仅用于电影；adult=true 按地区匹配，无法分类电影默认进入私享影库/日韩。")
    lines.append("")
    lines.append("movie:")

    # 私享影库：仅使用 TMDB 详情一级字段 adult=true。不要使用 NR/UNRATED 自动归类。
    for region, data in PRIVATE_REGIONS.items():
        lines.append(f"  {mp_private_category(region)}:")
        lines.append(f"    adult: '{PRIVATE_ADULT_VALUES}'")
        if data["movie_countries"]:
            lines.append(f"    production_countries: '{data['movie_countries']}'")
        lines.append("")

    # 动漫电影：TMDB Animation 类型 + 地区
    for region, data in REGIONS.items():
        lines.append(f"  动漫电影-{region}:")
        lines.append("    genre_ids: '16'")
        if data["movie_countries"]:
            lines.append(f"    production_countries: '{data['movie_countries']}'")
        lines.append("")

    # 真人电影：排除 Animation + 地区
    for region, data in REGIONS.items():
        lines.append(f"  真人电影-{region}:")
        lines.append("    genre_ids: '!16'")
        if data["movie_countries"]:
            lines.append(f"    production_countries: '{data['movie_countries']}'")
        lines.append("")

    # 未匹配电影兜底：进入私享影库/日韩，便于在 Emby 侧统一做权限控制。
    lines.append(f"  {PRIVATE_UNCLASSIFIED_MOVIE_CATEGORY}:")
    lines.append("")

    lines.append("tv:")

    # 动漫剧集：TMDB Animation 类型 + 地区
    for region, data in REGIONS.items():
        lines.append(f"  动漫剧集-{region}:")
        lines.append("    genre_ids: '16'")
        if data["tv_countries"]:
            lines.append(f"    origin_country: '{data['tv_countries']}'")
        lines.append("")

    # 纪录片：TMDB Documentary 类型 + 地区，作为电视节目整理。
    for region, data in REGIONS.items():
        lines.append(f"  纪录片-{region}:")
        lines.append("    genre_ids: '99'")
        if data["tv_countries"]:
            lines.append(f"    origin_country: '{data['tv_countries']}'")
        lines.append("")

    # 综艺：TMDB Reality / Talk 类型 + 地区
    for region, data in REGIONS.items():
        lines.append(f"  综艺-{region}:")
        lines.append("    genre_ids: '10764,10767'")
        if data["tv_countries"]:
            lines.append(f"    origin_country: '{data['tv_countries']}'")
        lines.append("")

    # 真人剧集：排除 Animation / Reality / Talk / Documentary + 地区
    for region, data in REGIONS.items():
        lines.append(f"  真人剧集-{region}:")
        lines.append("    genre_ids: '!16,!99,!10764,!10767'")
        if data["tv_countries"]:
            lines.append(f"    origin_country: '{data['tv_countries']}'")
        lines.append("")

    # 未匹配剧集兜底。
    lines.append("  未分类剧集:")
    lines.append("")

    return "\n".join(lines) + "\n"


def mp_write_category_yaml(stack_dir: Path, overwrite: bool = True) -> Path:
    path = mp_category_yaml_path(stack_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        ts = time.strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_suffix(f".backup-{ts}.yaml")
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        log(f"已备份原 category.yaml：{backup_path}", "success")

        if not overwrite:
            log(f"category.yaml 已存在，overwrite=False，跳过写入：{path}", "warning")
            return path

    path.write_text(mp_build_category_yaml(), encoding="utf-8")
    uid, gid = resolve_app_ids({})
    env_path = stack_dir / ".env"
    if env_path.exists():
        try:
            uid, gid = resolve_app_ids(read_env_file(env_path))
        except Exception:
            pass
    apply_permissions(path, uid=uid, gid=gid, mode=FILE_MODE)
    log(f"已写入 MoviePilot V2 分类策略：{path}", "success")
    log("注意：category.yaml 写入后建议重启 MoviePilot V2 容器：docker restart mp-moviepilot", "warning")
    return path


def mp_build_storages(config: dict[str, str]) -> list[dict[str, Any]]:
    """
    MoviePilot V2 存储配置。

    本地存储根目录建议是 /volume1/media-data：
    - 下载目录：/volume1/media-data/downloads/media
    - 媒体库：/volume1/media-data/media
    - 刷流目录：/volume1/media-data/downloads/brush

    这样下载和整理后的媒体在同一挂载根下，移动/硬链接整理都稳定。
    """
    storage_root = mp_storage_root(config)

    return [
        {
            "name": "本地",
            "type": "local",
            # 不同版本前端显示字段略有差异，root/path/base_path 都保留，便于页面显示“已配置”。
            "config": {
                "root": storage_root,
                "path": storage_root,
                "base_path": storage_root,
            },
        }
    ]


def mp_normalize_transfer_type(value: str | None) -> str:
    """
    MoviePilot V2 目录整理方式使用：
    - copy：复制
    - move：移动
    - link：硬链接
    - softlink：软链接

    兼容旧习惯里的 hardlink/symlink 写法。
    """
    raw = (value or "move").strip().lower()
    mapping = {
        "hardlink": "link",
        "hard_link": "link",
        "hard-link": "link",
        "硬链接": "link",
        "link": "link",
        "copy": "copy",
        "move": "move",
        "softlink": "softlink",
        "soft_link": "softlink",
        "soft-link": "softlink",
        "symlink": "softlink",
        "symboliclink": "softlink",
        "软链接": "softlink",
    }
    if raw not in mapping:
        raise RuntimeError(
            f"MP_TRANSFER_TYPE 不支持：{value}，允许值：copy/move/link/softlink/hardlink/symlink"
        )
    return mapping[raw]


def mp_bool_env(config: dict[str, str], key: str, default: str = "false") -> bool:
    return get_env(config, key, default).lower() in ("1", "true", "yes", "on")


def mp_private_regions(config: dict[str, str]) -> list[str]:
    """
    私享影库目录。无法识别地区的电影默认进入“日韩”，不再额外创建“其他地区”兜底。

    可在 .env 中覆盖：
      MP_PRIVATE_REGIONS=国产,日韩,欧美
    """
    raw_value = get_env(config, "MP_PRIVATE_REGIONS", "国产,日韩,欧美,其他地区")
    values = [item.strip() for item in raw_value.split(",") if item.strip()]

    result: list[str] = []
    for item in values:
        if item not in PRIVATE_REGIONS:
            log(f"MP_PRIVATE_REGIONS 包含未知地区，已跳过：{item}", "warning")
            continue
        if item not in result:
            result.append(item)

    return result or ["国产", "日韩", "欧美", "其他地区"]


def _directory_item(
    name: str,
    download_path: str,
    library_path: str,
    media_type: str,
    category: str,
    priority: int,
    config: dict[str, str],
    monitor_type: str | None = None,
    remark: str = "",
) -> dict[str, Any]:
    """
    MoviePilot V2 目录规则项。

    V2 当前页面和后端实际使用字段：
    - storage / download_path：资源存储和资源目录
    - library_storage / library_path：媒体库存储和媒体库目录
    - media_type：电影 / 电视剧
    - media_category：category.yaml 生成的二级分类名
    - monitor_type：downloader / monitor / manual / 空
    - transfer_type：copy / move / link / softlink
    """
    selected_monitor_type = monitor_type
    if selected_monitor_type is None:
        selected_monitor_type = get_env(config, "MP_DIRECTORY_MONITOR_TYPE", "downloader")

    # 目录页中“手动整理”对应 manual；如果为空则只作为目录匹配，不自动整理。
    if selected_monitor_type not in ("", "downloader", "monitor", "manual"):
        raise RuntimeError(
            f"MP_DIRECTORY_MONITOR_TYPE 不支持：{selected_monitor_type}，允许值：downloader/monitor/manual/空"
        )

    return {
        "name": name,
        "priority": priority,
        "storage": "local",
        "download_path": download_path,
        "media_type": media_type,
        "media_category": category,
        # 兼容部分版本字段命名。页面主要看 media_category，category 作为兜底。
        "category": category,
        "download_type_folder": False,
        "download_category_folder": False,
        "monitor_type": selected_monitor_type,
        "monitor_mode": get_env(config, "MP_DIRECTORY_MONITOR_MODE", "fast"),
        "transfer_type": mp_normalize_transfer_type(get_env(config, "MP_TRANSFER_TYPE", "move")),
        "overwrite_mode": get_env(config, "MP_TRANSFER_OVERWRITE_MODE", "never"),
        "library_storage": "local",
        "library_path": library_path,
        # 兼容部分版本字段命名。页面主要看 library_path，save_path 作为兜底。
        "save_path": library_path,
        "library_type_folder": False,
        "library_category_folder": False,
        "renaming": mp_bool_env(config, "MP_RENAMING", "true"),
        "scraping": mp_bool_env(config, "MP_SCRAP_METADATA", "true"),
        "notify": mp_bool_env(config, "MP_TRANSFER_NOTIFY", "true"),
        "remark": remark,
    }


def mp_build_directories(config: dict[str, str]) -> list[dict[str, Any]]:
    """
    MoviePilot V2 目录规则使用“资源目录 + 媒体库叶子目录”结构：
    - 资源目录：默认 /downloads/media，同时为 /downloads/manual 生成目录监控规则
    - 媒体库目录：按内容类型 + 地区落到最终叶子目录

    例如：
    - 动漫电影-大陆：资源目录 /downloads/media，媒体库目录 /media/动漫电影/大陆
    - 手动-动漫电影-大陆：资源目录 /downloads/manual，媒体库目录 /media/动漫电影/大陆
    - 真人剧集-欧美：资源目录 /downloads/media，媒体库目录 /media/真人剧集/欧美
    - 纪录片-欧美：资源目录 /downloads/media，媒体库目录 /media/纪录片/欧美
    """
    media_root = mp_media_root(config)
    media_download_path = mp_download_media_dir(config)
    manual_download_path = mp_download_manual_dir(config)
    directories: list[dict[str, Any]] = []
    priority = 0

    def add(
        name: str,
        library_path: str,
        media_type: str,
        category: str,
        remark: str,
        monitor_type: str | None = None,
        source_download_path: str | None = None,
    ) -> None:
        nonlocal priority
        directories.append(_directory_item(
            name=name,
            download_path=source_download_path or media_download_path,
            library_path=library_path,
            media_type=media_type,
            category=category,
            priority=priority,
            config=config,
            monitor_type=monitor_type,
            remark=remark,
        ))
        priority += 1

    def add_auto_pair(
        name: str,
        library_path: str,
        media_type: str,
        category: str,
        remark: str,
    ) -> None:
        add(
            name=name,
            library_path=library_path,
            media_type=media_type,
            category=category,
            remark=remark,
        )
        add(
            name=f"手动-{name}",
            library_path=library_path,
            media_type=media_type,
            category=category,
            remark=f"手动下载目录自动整理：{remark}",
            monitor_type=get_env(config, "MP_MANUAL_DIRECTORY_MONITOR_TYPE", "monitor"),
            source_download_path=manual_download_path,
        )

    # 电影
    for region in REGIONS:
        add_auto_pair(
            name=f"动漫电影-{region}",
            library_path=f"{media_root}/动漫电影/{region}",
            media_type="电影",
            category=f"动漫电影-{region}",
            remark="自动分类：动漫电影 + 地区",
        )

    for region in REGIONS:
        add_auto_pair(
            name=f"真人电影-{region}",
            library_path=f"{media_root}/真人电影/{region}",
            media_type="电影",
            category=f"真人电影-{region}",
            remark="自动分类：真人电影 + 地区",
        )

    # 剧集
    for region in REGIONS:
        add_auto_pair(
            name=f"动漫剧集-{region}",
            library_path=f"{media_root}/动漫剧集/{region}",
            media_type="电视剧",
            category=f"动漫剧集-{region}",
            remark="自动分类：动漫剧集 + 地区",
        )

    for region in REGIONS:
        add_auto_pair(
            name=f"纪录片-{region}",
            library_path=f"{media_root}/纪录片/{region}",
            media_type="电视剧",
            category=f"纪录片-{region}",
            remark="自动分类：纪录片 + 地区",
        )

    for region in REGIONS:
        add_auto_pair(
            name=f"综艺-{region}",
            library_path=f"{media_root}/综艺/{region}",
            media_type="电视剧",
            category=f"综艺-{region}",
            remark="自动分类：综艺 + 地区",
        )

    for region in REGIONS:
        add_auto_pair(
            name=f"真人剧集-{region}",
            library_path=f"{media_root}/真人剧集/{region}",
            media_type="电视剧",
            category=f"真人剧集-{region}",
            remark="自动分类：真人剧集 + 地区",
        )

    # 短剧保留为电视剧分类，可用于订阅或手动指定。
    add_auto_pair(
        name="短剧",
        library_path=f"{media_root}/短剧",
        media_type="电视剧",
        category="短剧",
        remark="短剧自动整理",
    )

    # 无法分类的电影默认进入私享影库/日韩，便于统一权限控制。
    add_auto_pair(
        name=f"私享影库电影-{PRIVATE_FALLBACK_REGION}-未识别",
        library_path=f"{media_root}/私享影库/{PRIVATE_FALLBACK_REGION}",
        media_type="电影",
        category=PRIVATE_UNCLASSIFIED_MOVIE_CATEGORY,
        remark=f"兜底分类：TMDB 元数据不足或分类未命中，默认进入私享影库/{PRIVATE_FALLBACK_REGION}",
    )
    add_auto_pair(
        name="未分类剧集",
        library_path=f"{media_root}/未分类/剧集",
        media_type="电视剧",
        category="未分类剧集",
        remark="兜底分类：TMDB 元数据不足或分类未命中",
    )

    # 私享影库只用于电影；一般没有限制级剧集，不生成电视剧整理规则。
    # 如确实要自动整理 adult=true 的资源，可在 .env 中设置 MP_PRIVATE_LIBRARY_MONITOR_TYPE=downloader。
    for region in mp_private_regions(config):
        add(
            name=f"私享影库电影-{region}",
            library_path=f"{media_root}/私享影库/{region}",
            media_type="电影",
            category=mp_private_category(region),
            remark="自动分类：adult=true 电影 + 地区；建议在 Emby 单独控制权限",
            monitor_type=get_env(config, "MP_PRIVATE_LIBRARY_MONITOR_TYPE", "downloader"),
        )
        add(
            name=f"手动-私享影库电影-{region}",
            library_path=f"{media_root}/私享影库/{region}",
            media_type="电影",
            category=mp_private_category(region),
            remark="手动下载目录自动整理：adult=true 电影 + 地区；建议在 Emby 单独控制权限",
            monitor_type=get_env(config, "MP_MANUAL_DIRECTORY_MONITOR_TYPE", "monitor"),
            source_download_path=manual_download_path,
        )

    return directories


def mp_build_transfer(config: dict[str, str]) -> dict[str, Any]:
    """
    MoviePilot V2 整理与刮削配置。

    目标：
    - 默认使用移动整理，避免下载目录长期积累。
    - 只整理 media/manual 下载目录。
    - 明确排除 brush 刷流目录。
    """
    media_root = mp_media_root(config)
    media_download_dir = mp_download_media_dir(config)
    manual_download_dir = mp_download_manual_dir(config)
    brush_download_dir = mp_download_brush_dir(config)

    return {
        "scrap_source": get_env(config, "MP_SCRAP_SOURCE", "themoviedb"),
        "transfer_type": mp_normalize_transfer_type(get_env(config, "MP_TRANSFER_TYPE", "move")),
        "transfer_threads": int(get_env(config, "MP_TRANSFER_THREADS", "1")),
        "download_dirs": [
            media_download_dir,
            manual_download_dir,
        ],
        "media_root": media_root,
        "exclude_dirs": [
            brush_download_dir,
        ],
        "movie_rename_format": mp_movie_rename_format(config),
        "tv_rename_format": mp_tv_rename_format(config),
        # 兼容官方配置键名称。
        "MOVIE_RENAME_FORMAT": mp_movie_rename_format(config),
        "TV_RENAME_FORMAT": mp_tv_rename_format(config),
        "RECOGNIZE_SOURCE": get_env(config, "MP_RECOGNIZE_SOURCE", "themoviedb"),
        "SCRAP_SOURCE": get_env(config, "MP_SCRAP_SOURCE", "themoviedb"),
        "overwrite": get_env(config, "MP_TRANSFER_OVERWRITE", "false").lower() in ("1", "true", "yes", "on"),
        "delete_source": get_env(config, "MP_TRANSFER_DELETE_SOURCE", "false").lower() in ("1", "true", "yes", "on"),
        "scrap_metadata": get_env(config, "MP_SCRAP_METADATA", "true").lower() in ("1", "true", "yes", "on"),
        "refresh_mediaserver": get_env(config, "MP_REFRESH_MEDIASERVER", "true").lower() in ("1", "true", "yes", "on"),
    }


def mp_ensure_media_paths(config: dict[str, str]) -> None:
    """
    尝试创建本机媒体目录，并按 .env 中的 PUID/PGID 修正权限。
    如果脚本不是在 NAS 宿主机上执行，目录可能无法创建，此时只打印 warning。
    """
    media_root = mp_media_root(config)
    uid, gid = resolve_app_ids(config)

    paths = [
        Path(mp_download_media_dir(config)),
        Path(mp_download_manual_dir(config)),
        Path(mp_download_brush_dir(config)),
        Path(get_env(config, "DATA_DIR", "/volume1/media-data")) / "downloads" / "private",
    ]

    for base in ("真人电影", "真人剧集", "动漫电影", "动漫剧集", "综艺", "纪录片"):
        for region in REGIONS:
            paths.append(Path(f"{media_root}/{base}/{region}"))

    paths.append(Path(f"{media_root}/短剧"))
    paths.append(Path(f"{media_root}/未分类/剧集"))

    for region in mp_private_regions(config):
        paths.append(Path(f"{media_root}/私享影库/{region}"))

    for path in paths:
        try:
            ensure_directory(path, uid=uid, gid=gid, mode=DIR_MODE_DATA)
        except Exception as exc:
            log(f"目录创建失败，可能不是在宿主机执行：{path}，错误：{exc}", "warning")

    log(f"媒体目录检查/创建完成（PUID={uid} PGID={gid}，mode={oct(DIR_MODE_DATA)}）。", "success")


def mp_validate_generated_plan(config: dict[str, str]) -> None:
    """
    校验生成结果，避免最常见的三类问题：
    1. 刷流目录被加入整理目录；
    2. 目录规则没有真实媒体库路径；
    3. 规则组引用了不存在的目录分类。
    """
    directories = mp_build_directories(config)
    transfer = mp_build_transfer(config)
    brush_dir = mp_download_brush_dir(config).rstrip("/")

    errors: list[str] = []

    if brush_dir in [item.rstrip("/") for item in transfer.get("download_dirs", [])]:
        errors.append(f"刷流目录被加入 download_dirs：{brush_dir}")

    if brush_dir not in [item.rstrip("/") for item in transfer.get("exclude_dirs", [])]:
        errors.append(f"刷流目录没有加入 exclude_dirs：{brush_dir}")

    for item in directories:
        download_path = str(item.get("download_path", "")).rstrip("/")
        library_path = str(item.get("library_path", "")).rstrip("/")

        if not library_path:
            errors.append(f"目录 {item.get('name')} 没有设置媒体库真实路径")

        if download_path.startswith(brush_dir):
            errors.append(f"目录 {item.get('name')} 的资源目录指向了刷流目录：{download_path}")

        if "/downloads/brush" in library_path:
            errors.append(f"目录 {item.get('name')} 的媒体库目录包含刷流路径：{library_path}")

    directory_categories: set[str] = {
        category
        for item in directories
        if isinstance(category := item.get("media_category"), str) and category
    }
    rule_categories: set[str] = {
        category
        for item in mp_build_user_filter_rule_groups()
        if isinstance(category := item.get("category"), str) and category
    }
    missing_categories = sorted(rule_categories - directory_categories)
    if missing_categories:
        errors.append(f"规则组引用了不存在的目录分类：{missing_categories}")

    if errors:
        raise RuntimeError("MoviePilot V2 初始化配置校验失败：\n- " + "\n- ".join(errors))

    log("MoviePilot V2 目录/刮削配置校验通过：刷流目录已排除，目录分类与规则组一致。", "success")


def mp_log_emby_path_hint(config: dict[str, str]) -> None:
    media_root = mp_media_root(config)
    emby_media_root = get_env(config, "MP_EMBY_MEDIA_ROOT", media_root)

    if media_root != emby_media_root:
        log(
            "Emby 容器媒体库路径与 MoviePilot V2 路径不同："
            f"MoviePilot V2={media_root}，Emby={emby_media_root}。"
            "这不影响整理入库，但 Emby 建库时应使用 Emby 容器内可见的媒体库路径；"
            "如你的版本支持媒体服务器路径映射，请在页面中补充这组映射。",
            "warning",
        )


def mp_set_storage_directory_transfer(
    host_ip: str,
    session: requests.Session,
    stack_dir: Path,
    config: dict[str, str],
    create_paths: bool = True,
) -> None:
    """
    一次性配置：
    1. Storages
    2. Directories
    3. Transfer
    """
    mp_validate_generated_plan(config)
    mp_log_emby_path_hint(config)

    if create_paths:
        mp_ensure_media_paths(config)

    items = {
        "Storages": mp_build_storages(config),
        "Directories": mp_build_directories(config),
        "Transfer": mp_build_transfer(config),
    }

    for key, value in items.items():
        try:
            old_value = mp_get_setting(host_ip, session, key)
            mp_backup_setting_to_dir(stack_dir, key, old_value)

            # log(f"当前 {key}：")
            # log(json.dumps(old_value, ensure_ascii=False, indent=2))
        except Exception as exc:
            log(f"读取 {key} 失败，继续尝试写入：{exc}", "warning")

        log(f"开始写入 {key} ...")
        mp_set_setting(host_ip, session, key, value)

        checked = mp_get_setting(host_ip, session, key)
        # log(f"写入后的 {key}：")
        # log(json.dumps(checked, ensure_ascii=False, indent=2))

    log("Storages / Directories / Transfer 初始化完成。", "success")


def mp_set_category_and_paths(
    host_ip: str,
    session: requests.Session,
    stack_dir: Path,
    config: dict[str, str],
    create_paths: bool = True,
    overwrite_category: bool = True,
) -> None:
    """
    自动实现：
    1. 生成 category.yaml
    2. 写入 Storages
    3. 写入 Directories 叶子目录
    4. 写入 Transfer
    """
    mp_write_category_yaml(stack_dir, overwrite=overwrite_category)
    mp_set_storage_directory_transfer(
        host_ip=host_ip,
        session=session,
        stack_dir=stack_dir,
        config=config,
        create_paths=create_paths,
    )
    log("自动分类和转移目录初始化完成。请重启 MoviePilot 使 category.yaml 生效。", "success")


# =========================
# Directories / Transfer 模板机制
# =========================

def template_dir(stack_dir: Path) -> Path:
    path = stack_dir / "moviepilot-v2" / "templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def template_path(stack_dir: Path, key: str) -> Path:
    return template_dir(stack_dir) / f"{key}.json"


def load_json_template(stack_dir: Path, key: str) -> Any:
    path = template_path(stack_dir, key)

    if not path.exists():
        raise RuntimeError(
            f"模板文件不存在：{path}\n"
            f"请先在 MoviePilot V2 页面配置一次 {key}，然后执行 export-template 导出模板。"
        )

    return json.loads(path.read_text(encoding="utf-8"))


def mp_export_setting_template(
    host_ip: str,
    session: requests.Session,
    stack_dir: Path,
    key: str,
) -> Path:
    value = mp_get_setting(host_ip, session, key)
    path = template_path(stack_dir, key)

    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log(f"已导出模板：{path}", "success")
    return path


def mp_export_templates(
    host_ip: str,
    session: requests.Session,
    stack_dir: Path,
    keys: list[str] | None = None,
) -> None:
    keys = keys or ["Directories", "Transfer"]

    for key in keys:
        mp_export_setting_template(host_ip, session, stack_dir, key)


def mp_set_setting_from_template(
    host_ip: str,
    session: requests.Session,
    stack_dir: Path,
    key: str,
) -> None:
    old_value = mp_get_setting(host_ip, session, key)
    mp_backup_setting_to_dir(stack_dir, key, old_value)

    new_value = load_json_template(stack_dir, key)
    mp_set_setting(host_ip, session, key, new_value)

    checked = mp_get_setting(host_ip, session, key)
    log(f"写入后的 {key}：")
    log(json.dumps(checked, ensure_ascii=False, indent=2))


def mp_apply_templates(
    host_ip: str,
    session: requests.Session,
    stack_dir: Path,
    keys: list[str] | None = None,
    strict: bool = False,
) -> None:
    keys = keys or ["Directories", "Transfer"]

    for key in keys:
        path = template_path(stack_dir, key)

        if not path.exists():
            message = (
                f"未发现 {key} 模板，暂不自动写入：{path}\n"
                f"请先在页面配置一次后执行 export-template 导出模板。"
            )
            if strict:
                raise RuntimeError(message)
            log(message, "warning")
            continue

        log(f"发现模板，开始写入 {key}：{path}")
        mp_set_setting_from_template(host_ip, session, stack_dir, key)


# =========================
# 初始化入口
# =========================

def mp_continue_init(
    host_ip: str,
    session: requests.Session,
    stack_dir: Path,
    config: dict[str, str],
    set_paths: bool = True,
    apply_templates: bool = False,
    set_rules: bool = True,
) -> None:
    """
    继续初始化：
    1. 备份并打印 Downloaders / MediaServers / Storages / Directories / Transfer
    2. 写入 MediaServers：Emby
    3. 写入 category.yaml + Storages / Directories / Transfer
    4. 如 set_paths=False 且 apply_templates=True，则使用模板兜底写入
    """

    log("开始读取并备份 MoviePilot V2 核心配置 ...")
    mp_dump_core_settings(host_ip, session, stack_dir)

    log("开始配置 MoviePilot V2 媒体服务器 Emby ...")
    mp_set_media_servers(host_ip, session, stack_dir, config)

    log("开始配置 MoviePilot V2 刮削、识别、重命名基础设置 ...")
    mp_set_scrape_core_settings(host_ip, session, stack_dir, config)

    if set_rules:
        log("开始配置 MoviePilot V2 自定义规则、优先级规则组、下载排序规则 ...")
        mp_set_rules(host_ip, session, stack_dir, config)

    if set_paths:
        log("开始配置 MoviePilot V2 自动分类、存储、目录、整理规则 ...")
        mp_set_category_and_paths(host_ip, session, stack_dir, config)
    elif apply_templates:
        log("开始检查并应用 Directories / Transfer 模板 ...")
        mp_apply_templates(host_ip, session, stack_dir, ["Directories", "Transfer"], strict=False)


def init_moviepilot(
    host_ip: str,
    stack_dir: Path,
    set_downloaders: bool = True,
    set_media_servers: bool = True,
    set_paths: bool = True,
    apply_templates: bool = False,
    set_rules: bool = True,
) -> None:
    env_path = stack_dir / ".env"
    config = read_env_file(env_path)

    session = mp_login(host_ip, stack_dir)

    log("开始读取并备份 MoviePilot V2 核心配置 ...")
    mp_dump_core_settings(host_ip, session, stack_dir)

    if set_downloaders:
        log("开始配置 MoviePilot V2 下载器 ...")
        mp_set_downloaders(host_ip, session, stack_dir, config)

    if set_media_servers:
        log("开始配置 MoviePilot V2 媒体服务器 ...")
        mp_set_media_servers(host_ip, session, stack_dir, config)

    log("开始配置 MoviePilot V2 刮削、识别、重命名基础设置 ...")
    mp_set_scrape_core_settings(host_ip, session, stack_dir, config)

    if set_rules:
        log("开始配置 MoviePilot V2 自定义规则、优先级规则组、下载排序规则 ...")
        mp_set_rules(host_ip, session, stack_dir, config)

    if set_paths:
        log("开始配置 MoviePilot V2 自动分类、存储、目录、整理规则 ...")
        mp_set_category_and_paths(host_ip, session, stack_dir, config)
    elif apply_templates:
        log("开始检查并应用 Directories / Transfer 模板 ...")
        mp_apply_templates(host_ip, session, stack_dir, ["Directories", "Transfer"], strict=False)

    log("MoviePilot V2 初始化流程执行完成。", "success")
    log("下一步：重启 MoviePilot V2 容器，让 category.yaml 生效；然后在 Emby 中只添加 /media 下的七个一级媒体库。", "warning")


def run_init_mpv2(
    stack_dir: Path,
    host_ip: str | None = None,
    set_downloaders: bool = True,
    set_media_servers: bool = True,
    set_paths: bool = True,
    apply_templates: bool = False,
    set_rules: bool = True,
) -> None:
    stack_dir = stack_dir.expanduser().resolve()
    if not host_ip:
        raise RuntimeError("host_ip 不能为空，必须由主模块传入。")

    validate_env_passwords(read_env_file(stack_dir / ".env"), INIT_MPV2_ENV_KEYS)

    init_moviepilot(
        host_ip=host_ip,
        stack_dir=stack_dir,
        set_downloaders=set_downloaders,
        set_media_servers=set_media_servers,
        set_paths=set_paths,
        apply_templates=apply_templates,
        set_rules=set_rules,
    )

def run_set_mpv2_rules(
    stack_dir: Path,
    host_ip: str | None = None,
) -> None:
    """
    单独初始化 MoviePilot 的自定义规则 / 优先级规则组 / 下载排序。
    """
    stack_dir = stack_dir.expanduser().resolve()
    if not host_ip:
        raise RuntimeError("host_ip 不能为空，必须由主模块传入。")

    config = read_env_file(stack_dir / ".env")

    session = mp_login(host_ip, stack_dir)
    mp_set_rules(host_ip, session, stack_dir, config)


def run_set_mpv2_paths(
    stack_dir: Path,
    host_ip: str | None = None,
) -> None:
    """
    单独初始化 MoviePilot V2 的 category.yaml / Storages / Directories / Transfer。
    适合在 Downloaders / MediaServers 已经配置好后单独执行。
    """
    stack_dir = stack_dir.expanduser().resolve()
    if not host_ip:
        raise RuntimeError("host_ip 不能为空，必须由主模块传入。")

    config = read_env_file(stack_dir / ".env")

    session = mp_login(host_ip, stack_dir)
    mp_set_scrape_core_settings(host_ip, session, stack_dir, config)
    mp_set_category_and_paths(host_ip, session, stack_dir, config)


def run_write_category_yaml(
    stack_dir: Path,
) -> None:
    """
    仅生成 category.yaml。
    """
    stack_dir = stack_dir.expanduser().resolve()

    mp_write_category_yaml(stack_dir, overwrite=True)
