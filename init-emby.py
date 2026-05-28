"""
Emby 初始化辅助脚本

适配当前媒体栈：
- Emby 容器：mpv2-emby
- 宿主机访问：http://NAS-IP:7096
- 容器内访问：http://emby:8096
- 媒体目录：/volume1/media-data/media

功能：
1. 等待 Emby API 就绪
2. 使用管理员账号登录，或使用已有 EMBY_API_KEY
3. 创建/检查媒体库：
   - 默认按二级地区目录建库，例如 真人电影-大陆、真人剧集-大陆、动漫电影-日韩
   - 首页排序：真人电影{地区} → 真人剧集{地区} → 动漫电影{地区} → 动漫剧集{地区}
     → 综艺{地区} → 纪录片{地区} → 私享影库{地区} → 短剧 → 小电影
   - 地区顺序：大陆 → 港澳台 → 日韩 → 欧美 → 东南亚 → 其他地区
4. 删除旧媒体库：切换为二级库时，可先删除脚本管理范围内的旧一级库
5. 为所有用户写入首页媒体库排序（OrderedViews）
6. 同步脚本管理媒体库的 LibraryOptions（中文元数据、实时监控、字幕语言等）
7. 尝试创建 MoviePilot API Key，并写回 .env
8. 触发媒体库扫描
9. 打印当前 VirtualFolders / Users / ServerInfo

依赖：
pip install requests

.env 必填/可选：
EMBY_USER=你的Emby管理员用户名
EMBY_PASSWORD=你的Emby管理员密码

# 如果你已经在 Emby 页面手动生成了 API Key，也可以直接填：
# EMBY_API_KEY=你的EmbyApiKey

# 可选，不填则默认使用 http://{host}:7096
# EMBY_URL=http://192.168.3.111:7096

# 媒体库创建模式：secondary=按二级地区目录建库；primary=按一级目录建库
# EMBY_LIBRARY_MODE=secondary

# 普通媒体二级地区，可按需删减
# EMBY_REGIONS=大陆,港澳台,日韩,欧美,东南亚,其他地区

# 私享影库电影地区，可按需删减
# EMBY_PRIVATE_REGIONS=国产,日韩,欧美,其他地区

# 是否删除旧媒体库。默认 true：删除不在当前目标列表中的、由本脚本管理的旧库。
# 例如从一级库切到二级库时，会删除 真人电影/真人剧集/动漫电影/动漫剧集/综艺/纪录片/私享影库 等旧库。
# 注意：只是删除 Emby 媒体库配置，不删除磁盘上的真实媒体文件。
# EMBY_DELETE_OLD_LIBRARIES=true

# 是否连当前目标库也删除后重建。默认 false，避免重复运行脚本时频繁重建库 ID。
# 如果你想彻底重建所有脚本管理的 Emby 库，可以临时改成 true，执行完再改回 false。
# EMBY_RECREATE_EXISTING_LIBRARIES=false

# 是否为所有 Emby 用户应用首页媒体库排序（OrderedViews）。默认 true。
# EMBY_APPLY_LIBRARY_ORDER=true

# 是否同步脚本管理媒体库的 LibraryOptions（中文元数据、实时监控、字幕语言等）。默认 true。
# EMBY_SYNC_LIBRARY_OPTIONS=true

# 是否让电影库从元数据下载器导入合集信息。默认 true。
# 对应 Emby 媒体库设置里的“从元数据下载器导入合集信息”。
# EMBY_IMPORT_COLLECTIONS=true

# 是否尝试为所有用户的电影库开启“按合集分组显示”。默认 true。
# 对应 Emby 电影库页面右上角三个点里的“Group items into collections / 按合集分组”。
# 注意：这是用户端显示偏好，不是媒体库扫描选项；部分 Emby 客户端可能仍需手动打开一次。
# EMBY_GROUP_ITEMS_INTO_COLLECTIONS=true

# “按合集分组显示”属于客户端显示偏好，默认写入 Emby 常用 client=emby。
# 如果你发现某个客户端无效，可把它在 Emby 的 Client 名称追加进来，例如：emby,Emby Web,Android
# EMBY_COLLECTION_DISPLAY_CLIENTS=emby

DATA_DIR=/volume1/media-data
EMBY_API_KEY_APP_NAME=MoviePilotV2
EMBY_CREATE_API_KEY=true

注意：
- Emby 首次初始化向导、管理员账号创建，建议先在 Web 页面完成。
- 该脚本主要负责“创建媒体库 + 生成/写入 API Key + 扫描媒体库”。
"""

from __future__ import annotations

import json
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests
from env_utils import get_env, read_env_file, require_env, update_env_file
from log_utils import log


def get_bool_env(env_values: dict[str, str], key: str, default: bool) -> bool:
    value = get_env(env_values, key, "")
    if value == "":
        return default
    return value.lower() in ("1", "true", "yes", "y", "on")


DEFAULT_REGIONS = ["大陆", "港澳台", "日韩", "欧美", "东南亚", "其他地区"]
DEFAULT_PRIVATE_REGIONS = ["国产", "日韩", "欧美", "其他地区"]
DEFAULT_COLLECTION_DISPLAY_CLIENTS = ["emby"]

# 二级媒体库：先按分类，再按地区；地区顺序见 DEFAULT_REGIONS。
LIBRARY_CATEGORY_SPECS: tuple[tuple[str, str], ...] = (
    ("真人电影", "movies"),
    ("真人剧集", "tvshows"),
    ("动漫电影", "movies"),
    ("动漫剧集", "tvshows"),
    ("综艺", "tvshows"),
    ("纪录片", "tvshows"),
)


def emby_sort_by_order(values: list[str], preferred: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in preferred:
        if item in values and item not in seen:
            ordered.append(item)
            seen.add(item)
    for item in values:
        if item not in seen:
            ordered.append(item)
            seen.add(item)
    return ordered


def get_csv_env(env_values: dict[str, str], key: str, default_values: list[str]) -> list[str]:
    raw_value = get_env(env_values, key, "")
    if not raw_value:
        return list(default_values)

    result: list[str] = []
    for item in raw_value.split(","):
        value = item.strip()
        if value and value not in result:
            result.append(value)

    return result or list(default_values)


def build_emby_url(host: str, config: dict[str, str]) -> str:
    env_url = get_env(config, "EMBY_URL", "")
    if env_url:
        return env_url.rstrip("/")

    host = host.strip().rstrip("/")
    if host.startswith("http://") or host.startswith("https://"):
        return host

    return f"http://{host}:7096"


def get_lan_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("223.5.5.5", 80))
            ip = sock.getsockname()[0]
        except OSError:
            ip = socket.gethostbyname(socket.gethostname())
    if not ip or ip.startswith("127."):
        raise RuntimeError("未能自动获取真实内网 IP，请由主模块传入 host_ip。")
    return ip


def auth_header(token: str, user_id: str | None = None) -> str:
    """
    Emby 用户认证常用 Header。
    API Key 认证时也可配合 X-Emby-Token 使用。
    """
    parts = [
        'MediaBrowser Client="media-stack-init"',
        'Device="python"',
        f'DeviceId="{uuid.uuid5(uuid.NAMESPACE_DNS, "media-stack-init")}"',
        'Version="1.0.0"',
    ]

    if user_id:
        parts.append(f'UserId="{user_id}"')

    if token:
        parts.append(f'Token="{token}"')

    return ", ".join(parts)


def emby_headers(token: str = "", user_id: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Emby-Authorization": auth_header(token, user_id),
    }

    if token:
        headers["X-Emby-Token"] = token

    return headers


# =========================
# Emby API 基础函数
# =========================

def wait_for_emby_api(base_url: str, timeout: int = 90) -> None:
    base_url = base_url.rstrip("/")
    deadline = time.monotonic() + timeout
    last_error = ""

    while time.monotonic() < deadline:
        for path in ("/System/Info/Public", "/emby/System/Info/Public"):
            try:
                resp = requests.get(f"{base_url}{path}", timeout=5)
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        version = data.get("Version") or data.get("ServerName") or ""
                    except Exception:
                        version = resp.text[:80]
                    log(f"Emby API 已就绪：{base_url}{path} {version}", "success")
                    return
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except requests.RequestException as exc:
                last_error = str(exc)

        time.sleep(2)

    raise RuntimeError(f"Emby API 未就绪：{base_url}。最后错误：{last_error}")


def emby_request(
    session: requests.Session,
    method: str,
    base_url: str,
    path: str,
    *,
    token: str = "",
    user_id: str | None = None,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
    timeout: int = 20,
) -> requests.Response:
    """
    Emby 有些部署支持 /emby 前缀，有些直接根路径。
    这里优先使用原路径，404 时再自动尝试 /emby 前缀。
    """
    base_url = base_url.rstrip("/")
    path = "/" + path.lstrip("/")

    paths = [path]
    if not path.startswith("/emby/"):
        paths.append("/emby" + path)

    last_resp: requests.Response | None = None

    for p in paths:
        resp = session.request(
            method.upper(),
            f"{base_url}{p}",
            headers=emby_headers(token, user_id),
            json=json_body,
            params=params,
            timeout=timeout,
        )

        last_resp = resp

        if resp.status_code != 404:
            return resp

    assert last_resp is not None
    return last_resp


def emby_login(
    base_url: str,
    username: str,
    password: str,
) -> tuple[requests.Session, str, str]:
    """
    返回：session, access_token, user_id
    """
    session = requests.Session()

    payload_candidates = [
        {"Username": username, "Pw": password},
        {"Username": username, "Password": password},
        {"username": username, "password": password},
    ]

    last_error = ""

    for payload in payload_candidates:
        resp = emby_request(
            session,
            "POST",
            base_url,
            "/Users/AuthenticateByName",
            json_body=payload,
            timeout=15,
        )

        if resp.status_code == 200:
            data = resp.json()
            token = data.get("AccessToken") or data.get("access_token") or ""
            user = data.get("User") or {}
            user_id = user.get("Id") or user.get("id") or ""

            if not token:
                raise RuntimeError(f"Emby 登录返回中没有 AccessToken：{data}")

            log(f"Emby 登录成功：{base_url}，username={username}", "success")
            return session, token, user_id

        last_error = f"{resp.status_code} {resp.text}"

    raise RuntimeError(f"Emby 登录失败：{last_error}")


def emby_session_from_config(
    base_url: str,
    config: dict[str, str],
) -> tuple[requests.Session, str, str]:
    """
    优先使用 EMBY_API_KEY。
    如果没有 API Key，则使用 EMBY_USER/EMBY_PASSWORD 登录。
    """
    api_key = get_env(config, "EMBY_API_KEY", "")
    if api_key:
        session = requests.Session()
        log("使用 .env 中已有 EMBY_API_KEY 访问 Emby。", "success")
        return session, api_key, ""

    username = get_env(config, "EMBY_USER", "")
    password = get_env(config, "EMBY_PASSWORD", "")

    if not username or not password:
        raise RuntimeError(
            ".env 缺少 Emby 登录信息。请配置 EMBY_API_KEY，"
            "或配置 EMBY_USER / EMBY_PASSWORD。"
        )

    return emby_login(base_url, username, password)


def emby_get_system_info(
    session: requests.Session,
    base_url: str,
    token: str,
    user_id: str = "",
) -> dict[str, Any]:
    resp = emby_request(
        session,
        "GET",
        base_url,
        "/System/Info",
        token=token,
        user_id=user_id or None,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"读取 Emby SystemInfo 失败：{resp.status_code} {resp.text}")

    return resp.json()


def emby_get_virtual_folders(
    session: requests.Session,
    base_url: str,
    token: str,
    user_id: str = "",
) -> list[dict[str, Any]]:
    resp = emby_request(
        session,
        "GET",
        base_url,
        "/Library/VirtualFolders",
        token=token,
        user_id=user_id or None,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"读取 Emby 媒体库失败：{resp.status_code} {resp.text}")

    data = resp.json()
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("Items", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value

    raise RuntimeError(f"无法识别 Emby 媒体库返回结构：{data}")


def emby_folder_item_id(folder: dict[str, Any]) -> str:
    for key in ("ItemId", "Id", "id"):
        value = folder.get(key)
        if value:
            return str(value)
    return ""


def emby_find_folder(folders: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for folder in folders:
        if str(folder.get("Name", "")).strip() == name:
            return folder
    return None


def emby_library_needs_adult_metadata(library_name: str) -> bool:
    return (
        library_name.startswith("私享影库")
        or library_name.startswith("私享电影")
        or library_name == "小电影"
    )


def emby_build_library_options(
    path: str,
    *,
    library_name: str = "",
    import_collections: bool = True,
) -> dict[str, Any]:
    return {
        "EnableArchiveMediaFiles": False,
        "EnablePhotos": False,
        "EnableRealtimeMonitor": True,
        "EnableChapterImageExtraction": False,
        "ExtractChapterImagesDuringLibraryScan": False,
        "DownloadImagesInAdvance": False,
        "CacheImages": True,
        "ExcludeFromSearch": False,
        "EnablePlexIgnore": True,
        "PathInfos": [
            {
                "Path": path,
                "NetworkPath": "",
                "Username": "",
                "Password": "",
            }
        ],
        "IgnoreHiddenFiles": True,
        "SaveLocalMetadata": False,
        "SaveMetadataHidden": False,
        "ImportPlaylists": True,
        "EnableAutomaticSeriesGrouping": True,
        "EnableEmbeddedTitles": False,
        "AutomaticRefreshIntervalDays": 0,
        "PreferredMetadataLanguage": "zh-CN",
        "PreferredImageLanguage": "zh-CN",
        "MetadataCountryCode": "CN",
        "MetadataSavers": [],
        "DisabledLocalMetadataReaders": [],
        "LocalMetadataReaderOrder": [],
        "DisabledSubtitleFetchers": [],
        "SubtitleFetcherOrder": [],
        "SkipSubtitlesIfEmbeddedSubtitlesPresent": False,
        "SkipSubtitlesIfAudioTrackMatches": False,
        "SubtitleDownloadLanguages": ["chi", "zho"],
        "RequirePerfectSubtitleMatch": False,
        "SaveSubtitlesWithMedia": False,
        "ForcedSubtitlesOnly": False,
        "HearingImpairedSubtitlesOnly": False,
        "CollapseSingleItemFolders": False,
        "ForceCollapseSingleItemFolders": False,
        "EnableAdultMetadata": emby_library_needs_adult_metadata(library_name),
        # 电影合集：从元数据下载器导入合集信息，例如 TMDb collection。
        # 这个选项只负责“生成/导入合集关系”，不负责前端是否把电影库按合集折叠显示。
        "ImportCollections": import_collections,
        "EnableMultiVersionByFiles": True,
        "EnableMultiVersionByMetadata": True,
        "EnableMultiPartItems": True,
        "MinCollectionItems": 2,
    }


def emby_create_virtual_folder(
    session: requests.Session,
    base_url: str,
    token: str,
    *,
    name: str,
    collection_type: str,
    path: str,
    import_collections: bool = True,
    refresh_library: bool = False,
    user_id: str = "",
) -> None:
    """
    创建 Emby 媒体库。
    """
    library_options = emby_build_library_options(
        path,
        library_name=name,
        import_collections=import_collections,
    )

    payload = {
        "Name": name,
        "CollectionType": collection_type,
        "RefreshLibrary": refresh_library,
        "Paths": [path],
        "LibraryOptions": library_options,
    }

    resp = emby_request(
        session,
        "POST",
        base_url,
        "/Library/VirtualFolders",
        token=token,
        user_id=user_id or None,
        json_body=payload,
        timeout=30,
    )

    if resp.status_code not in (200, 204):
        raise RuntimeError(
            f"创建 Emby 媒体库失败：{name}，"
            f"状态码={resp.status_code}，返回={resp.text}"
        )

    log(f"创建 Emby 媒体库成功：{name} -> {path}", "success")


def emby_update_library_options(
    session: requests.Session,
    base_url: str,
    token: str,
    *,
    library_id: str,
    library_name: str,
    path: str,
    import_collections: bool = True,
    user_id: str = "",
) -> None:
    library_options = emby_build_library_options(
        path,
        library_name=library_name,
        import_collections=import_collections,
    )
    payload = {
        "Id": library_id,
        "LibraryOptions": library_options,
    }
    resp = emby_request(
        session,
        "POST",
        base_url,
        "/Library/VirtualFolders/LibraryOptions",
        token=token,
        user_id=user_id or None,
        json_body=payload,
        timeout=30,
    )
    if resp.status_code not in (200, 204):
        raise RuntimeError(
            f"更新 Emby 媒体库选项失败：{library_name}，"
            f"状态码={resp.status_code}，返回={resp.text}"
        )


def emby_sync_managed_library_options(
    session: requests.Session,
    base_url: str,
    token: str,
    config: dict[str, str],
    folders: list[dict[str, Any]],
    *,
    user_id: str = "",
) -> None:
    if not get_bool_env(config, "EMBY_SYNC_LIBRARY_OPTIONS", True):
        log("EMBY_SYNC_LIBRARY_OPTIONS=false，跳过媒体库选项同步。", "warning")
        return

    desired_libraries = emby_build_libraries(config)
    import_collections = get_bool_env(config, "EMBY_IMPORT_COLLECTIONS", True)
    updated = 0
    for library in desired_libraries:
        name = library["name"]
        path = library["path"]
        folder = emby_find_folder(folders, name)
        if not folder:
            log(f"媒体库不存在，跳过选项同步：{name}", "warning")
            continue

        library_id = emby_folder_item_id(folder)
        if not library_id:
            log(f"媒体库缺少 ItemId，跳过选项同步：{name}", "warning")
            continue

        try:
            emby_update_library_options(
                session,
                base_url,
                token,
                library_id=library_id,
                library_name=name,
                path=path,
                import_collections=import_collections,
                user_id=user_id,
            )
            updated += 1
            log(f"已同步 Emby 媒体库选项：{name}", "success")
        except Exception as exc:
            log(f"同步 Emby 媒体库选项失败：{name}，{exc}", "warning")

    if updated:
        log(
            "媒体库选项：中文元数据(zh-CN)、实时监控、字幕语言(chi/zho)、"
            f"导入合集信息={import_collections}、私享/小电影启用成人元数据；已同步 {updated} 个库。",
            "success",
        )


def emby_library_exists(folders: list[dict[str, Any]], name: str) -> bool:
    for folder in folders:
        if str(folder.get("Name", "")).strip() == name:
            return True
    return False


def emby_build_libraries(config: dict[str, str]) -> list[dict[str, str]]:
    """
    构建 Emby 媒体库列表。

    默认使用二级地区目录建库，这样 Emby 首页可以直接显示：
    - 真人电影-大陆 / 真人电影-日韩 / 真人电影-欧美
    - 真人剧集-大陆 / 真人剧集-日韩 / 真人剧集-欧美
    - 动漫电影-日韩 / 动漫剧集-日韩
    - 综艺-大陆 / 综艺-日韩
    - 纪录片-大陆 / 纪录片-日韩 / 纪录片-欧美

    可通过 .env 切回一级目录建库：
      EMBY_LIBRARY_MODE=primary
    """
    data_dir = get_env(config, "DATA_DIR", "/volume1/media-data")
    media_root = get_env(config, "EMBY_MEDIA_ROOT", f"{data_dir}/media")
    library_mode = get_env(config, "EMBY_LIBRARY_MODE", "secondary").strip().lower()

    if library_mode in ("primary", "level1", "one", "一级"):
        return [
            {"name": "真人电影", "collection_type": "movies", "path": f"{media_root}/真人电影"},
            {"name": "真人剧集", "collection_type": "tvshows", "path": f"{media_root}/真人剧集"},
            {"name": "动漫电影", "collection_type": "movies", "path": f"{media_root}/动漫电影"},
            {"name": "动漫剧集", "collection_type": "tvshows", "path": f"{media_root}/动漫剧集"},
            {"name": "综艺", "collection_type": "tvshows", "path": f"{media_root}/综艺"},
            {"name": "纪录片", "collection_type": "tvshows", "path": f"{media_root}/纪录片"},
            {"name": "私享影库", "collection_type": "movies", "path": f"{media_root}/私享影库"},
            {"name": "短剧", "collection_type": "tvshows", "path": f"{media_root}/短剧"},
            {"name": "小电影", "collection_type": "movies", "path": f"{media_root}/小电影"},
        ]

    if library_mode not in ("secondary", "level2", "two", "二级"):
        raise RuntimeError(
            "EMBY_LIBRARY_MODE 不支持："
            f"{library_mode}，允许值：secondary/primary"
        )

    regions = emby_sort_by_order(get_csv_env(config, "EMBY_REGIONS", DEFAULT_REGIONS), DEFAULT_REGIONS)
    private_regions = emby_sort_by_order(
        get_csv_env(config, "EMBY_PRIVATE_REGIONS", DEFAULT_PRIVATE_REGIONS),
        DEFAULT_PRIVATE_REGIONS,
    )
    private_mode = get_env(config, "EMBY_PRIVATE_LIBRARY_MODE", "region").strip().lower()

    libraries: list[dict[str, str]] = []

    def add(name: str, collection_type: str, path: str) -> None:
        libraries.append({
            "name": name,
            "collection_type": collection_type,
            "path": path,
        })

    for category, collection_type in LIBRARY_CATEGORY_SPECS:
        for region in regions:
            add(f"{category}-{region}", collection_type, f"{media_root}/{category}/{region}")

    # 短剧、小电影不做地区二级分类。
    if private_mode in ("region", "mixed", "movies", "movie", "电影", "地区"):
        for region in private_regions:
            add(f"私享影库-{region}", "movies", f"{media_root}/私享影库/{region}")
    elif private_mode in ("split", "type", "类型"):
        for region in private_regions:
            add(f"私享电影-{region}", "movies", f"{media_root}/私享影库/电影/{region}")
    elif private_mode in ("none", "off", "关闭"):
        pass
    else:
        raise RuntimeError(
            "EMBY_PRIVATE_LIBRARY_MODE 不支持："
            f"{private_mode}，允许值：region/split/none"
        )

    add("短剧", "tvshows", f"{media_root}/短剧")
    add("小电影", "movies", f"{media_root}/小电影")

    return libraries


def emby_build_managed_library_names(config: dict[str, str]) -> set[str]:
    """
    本脚本可能创建/管理的 Emby 媒体库名称集合。

    删除旧库时只删除这个集合里的库，避免误删用户手工创建的其它媒体库。
    """
    regions = get_csv_env(config, "EMBY_REGIONS", DEFAULT_REGIONS)
    private_regions = get_csv_env(config, "EMBY_PRIVATE_REGIONS", DEFAULT_PRIVATE_REGIONS)

    names: set[str] = {
        "真人电影",
        "真人剧集",
        "动漫电影",
        "动漫剧集",
        "综艺",
        "纪录片",
        "短剧",
        "小电影",
        "私享影库",
    }

    for region in regions:
        names.update({
            f"真人电影-{region}",
            f"动漫电影-{region}",
            f"真人剧集-{region}",
            f"动漫剧集-{region}",
            f"综艺-{region}",
            f"纪录片-{region}",
        })

    for region in private_regions:
        names.update({
            f"私享影库-{region}",
            f"私享电影-{region}",
            f"私享剧集-{region}",
        })

    return names


def emby_delete_virtual_folder(
    session: requests.Session,
    base_url: str,
    token: str,
    *,
    name: str,
    user_id: str = "",
    refresh_library: bool = False,
) -> None:
    """
    删除 Emby 媒体库配置。

    这里删除的是 Emby 的 VirtualFolder 配置，不会删除磁盘上的真实视频文件。

    注意：部分 Emby 版本虽然暴露了 DELETE /Library/VirtualFolders，但实际前端删除
    更常走 POST /Library/VirtualFolders/Delete。直接 DELETE 在一些版本里会返回：
    Object reference not set to an instance of an object。

    因此这里优先走 POST /Library/VirtualFolders/Delete，并用 query/body 两种参数
    形式兜底；最后才尝试 DELETE 接口。
    """
    refresh = bool(refresh_library)
    refresh_str = str(refresh).lower()

    attempts: list[dict[str, Any]] = [
        {
            "method": "POST",
            "path": "/Library/VirtualFolders/Delete",
            "params": {"name": name, "refreshLibrary": refresh_str},
            "json_body": None,
            "label": "POST query lowercase",
        },
        {
            "method": "POST",
            "path": "/Library/VirtualFolders/Delete",
            "params": {"Name": name, "RefreshLibrary": refresh_str},
            "json_body": None,
            "label": "POST query uppercase",
        },
        {
            "method": "POST",
            "path": "/Library/VirtualFolders/Delete",
            "params": None,
            "json_body": {"Name": name, "RefreshLibrary": refresh},
            "label": "POST body uppercase",
        },
        {
            "method": "POST",
            "path": "/Library/VirtualFolders/Delete",
            "params": None,
            "json_body": {"name": name, "refreshLibrary": refresh},
            "label": "POST body lowercase",
        },
        {
            "method": "DELETE",
            "path": "/Library/VirtualFolders",
            "params": {"name": name, "refreshLibrary": refresh_str},
            "json_body": None,
            "label": "DELETE query lowercase",
        },
        {
            "method": "DELETE",
            "path": "/Library/VirtualFolders",
            "params": {"Name": name, "RefreshLibrary": refresh_str},
            "json_body": None,
            "label": "DELETE query uppercase",
        },
    ]

    errors: list[str] = []
    for attempt in attempts:
        resp = emby_request(
            session,
            attempt["method"],
            base_url,
            attempt["path"],
            token=token,
            user_id=user_id or None,
            params=attempt["params"],
            json_body=attempt["json_body"],
            timeout=30,
        )

        if resp.status_code in (200, 204):
            log(f"删除 Emby 媒体库成功：{name}，方式={attempt['label']}", "success")
            return

        # 某些版本删除成功后会返回空 500/404，但库已经不存在；后面调用方会重新获取列表。
        # 这里仍记录错误，不直接吞掉，避免误判。
        errors.append(f"{attempt['label']} -> {resp.status_code} {resp.text[:300]}")

    raise RuntimeError("删除 Emby 媒体库失败：" + name + "，尝试过的接口：\n- " + "\n- ".join(errors))


def emby_delete_old_libraries_if_needed(
    session: requests.Session,
    base_url: str,
    token: str,
    config: dict[str, str],
    desired_libraries: list[dict[str, str]],
    folders: list[dict[str, Any]],
    *,
    user_id: str = "",
) -> list[dict[str, Any]]:
    """
    删除旧媒体库，再返回删除后的最新媒体库列表。

    默认行为：
    - 删除“脚本管理范围内、但不在当前目标列表中”的旧库；
    - 不删除当前目标库，避免每次运行都重建库 ID。

    典型场景：从一级库切到二级库时，删除 真人电影/真人剧集/动漫电影/动漫剧集/综艺/纪录片/私享影库，
    然后创建 真人电影-大陆/真人电影-日韩/... 这些新库。
    """
    delete_old = get_bool_env(config, "EMBY_DELETE_OLD_LIBRARIES", True)
    recreate_existing = get_bool_env(config, "EMBY_RECREATE_EXISTING_LIBRARIES", False)

    if not delete_old and not recreate_existing:
        log("EMBY_DELETE_OLD_LIBRARIES=false 且 EMBY_RECREATE_EXISTING_LIBRARIES=false，跳过删除旧媒体库。", "warning")
        return folders

    desired_names = {item["name"] for item in desired_libraries}
    managed_names = emby_build_managed_library_names(config)

    folders_to_delete: list[dict[str, Any]] = []
    for folder in folders:
        name = str(folder.get("Name", "")).strip()
        if not name or name not in managed_names:
            continue

        if recreate_existing or name not in desired_names:
            folders_to_delete.append(folder)

    if not folders_to_delete:
        log("未发现需要删除的旧 Emby 媒体库。", "success")
        return folders

    log("准备删除旧 Emby 媒体库：")
    log(json.dumps(
        [
            {
                "Name": item.get("Name"),
                "CollectionType": item.get("CollectionType"),
                "Locations": item.get("Locations"),
                "ItemId": item.get("ItemId"),
            }
            for item in folders_to_delete
        ],
        ensure_ascii=False,
        indent=2,
    ))

    ignore_delete_errors = get_bool_env(config, "EMBY_IGNORE_DELETE_ERRORS", False)

    for folder in folders_to_delete:
        name = str(folder.get("Name", "")).strip()
        try:
            emby_delete_virtual_folder(
                session,
                base_url,
                token,
                name=name,
                user_id=user_id,
                refresh_library=False,
            )
        except Exception as exc:
            if not ignore_delete_errors:
                raise
            log(f"删除 Emby 媒体库失败但已按 EMBY_IGNORE_DELETE_ERRORS=true 跳过：{name}，错误：{exc}", "warning")

    time.sleep(1)
    return emby_get_virtual_folders(session, base_url, token, user_id)


def emby_init_libraries(
    session: requests.Session,
    base_url: str,
    token: str,
    config: dict[str, str],
    *,
    user_id: str = "",
    refresh_library: bool = False,
) -> None:
    folders = emby_get_virtual_folders(session, base_url, token, user_id)

    log("当前 Emby 媒体库：")
    log(json.dumps(
        [
            {
                "Name": item.get("Name"),
                "CollectionType": item.get("CollectionType"),
                "Locations": item.get("Locations"),
                "ItemId": item.get("ItemId"),
            }
            for item in folders
        ],
        ensure_ascii=False,
        indent=2,
    ))

    desired_libraries = emby_build_libraries(config)
    import_collections = get_bool_env(config, "EMBY_IMPORT_COLLECTIONS", True)
    folders = emby_delete_old_libraries_if_needed(
        session,
        base_url,
        token,
        config,
        desired_libraries,
        folders,
        user_id=user_id,
    )

    for library in desired_libraries:
        name = library["name"]
        if emby_library_exists(folders, name):
            log(f"Emby 媒体库已存在，跳过：{name}", "warning")
            continue

        emby_create_virtual_folder(
            session,
            base_url,
            token,
            name=name,
            collection_type=library["collection_type"],
            path=library["path"],
            import_collections=import_collections,
            refresh_library=refresh_library,
            user_id=user_id,
        )

    folders_after = emby_get_virtual_folders(session, base_url, token, user_id)
    log("初始化后的 Emby 媒体库：")
    log(json.dumps(
        [
            {
                "Name": item.get("Name"),
                "CollectionType": item.get("CollectionType"),
                "Locations": item.get("Locations"),
                "ItemId": item.get("ItemId"),
                "Guid": item.get("Guid"),
            }
            for item in folders_after
        ],
        ensure_ascii=False,
        indent=2,
    ))

    emby_sync_managed_library_options(
        session,
        base_url,
        token,
        config,
        folders_after,
        user_id=user_id,
    )

    emby_apply_library_display_order(
        session,
        base_url,
        token,
        config,
        user_id=user_id,
    )

    emby_apply_collection_display_grouping(
        session,
        base_url,
        token,
        config,
        user_id=user_id,
    )


def emby_build_ordered_view_ids(
    folders: list[dict[str, Any]],
    desired_names: list[str],
) -> list[str]:
    name_to_id: dict[str, str] = {}
    for folder in folders:
        name = str(folder.get("Name", "")).strip()
        item_id = emby_folder_item_id(folder)
        if name and item_id:
            name_to_id[name] = item_id

    ordered: list[str] = []
    seen: set[str] = set()
    for name in desired_names:
        item_id = name_to_id.get(name)
        if item_id and item_id not in seen:
            ordered.append(item_id)
            seen.add(item_id)

    for folder in folders:
        item_id = emby_folder_item_id(folder)
        if item_id and item_id not in seen:
            ordered.append(item_id)
            seen.add(item_id)

    return ordered


def emby_is_movie_library(library: dict[str, Any]) -> bool:
    """判断脚本配置项是否为电影类媒体库。"""
    return str(library.get("collection_type", "")).strip().lower() == "movies"


def emby_get_collection_display_clients(config: dict[str, str]) -> list[str]:
    """
    Emby 的“按合集分组显示”属于 DisplayPreferences，通常按 User + Client + Library 维度保存。
    Web 端常见 client 值是 emby；如果实际客户端不生效，可以在 .env 中扩展。
    """
    return get_csv_env(
        config,
        "EMBY_COLLECTION_DISPLAY_CLIENTS",
        DEFAULT_COLLECTION_DISPLAY_CLIENTS,
    )


def emby_get_display_preferences(
    session: requests.Session,
    base_url: str,
    token: str,
    display_preference_id: str,
    target_user_id: str,
    *,
    client: str = "emby",
    user_id: str = "",
) -> dict[str, Any]:
    """
    读取指定用户在指定客户端、指定媒体库上的显示偏好。

    Emby/Jellyfin 系 API 一般为：
      GET /DisplayPreferences/{Id}?UserId={userId}&Client={client}
    不同版本大小写兼容性略有差异，因此这里做两组参数兜底。
    """
    attempts = [
        {"UserId": target_user_id, "Client": client},
        {"userId": target_user_id, "client": client},
    ]
    errors: list[str] = []

    for params in attempts:
        resp = emby_request(
            session,
            "GET",
            base_url,
            f"/DisplayPreferences/{display_preference_id}",
            token=token,
            user_id=user_id or None,
            params=params,
            timeout=20,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                return data
            raise RuntimeError(f"无法识别 Emby DisplayPreferences 返回结构：{data}")

        errors.append(f"{params} -> {resp.status_code} {resp.text[:300]}")

    raise RuntimeError("读取 Emby DisplayPreferences 失败：\n- " + "\n- ".join(errors))


def emby_set_display_preferences(
    session: requests.Session,
    base_url: str,
    token: str,
    display_preference_id: str,
    target_user_id: str,
    display_preferences: dict[str, Any],
    *,
    client: str = "emby",
    user_id: str = "",
) -> None:
    """写入指定用户在指定客户端、指定媒体库上的显示偏好。"""
    attempts = [
        {"UserId": target_user_id, "Client": client},
        {"userId": target_user_id, "client": client},
    ]
    errors: list[str] = []

    for params in attempts:
        resp = emby_request(
            session,
            "POST",
            base_url,
            f"/DisplayPreferences/{display_preference_id}",
            token=token,
            user_id=user_id or None,
            params=params,
            json_body=display_preferences,
            timeout=20,
        )
        if resp.status_code in (200, 204):
            return

        errors.append(f"{params} -> {resp.status_code} {resp.text[:300]}")

    raise RuntimeError("写入 Emby DisplayPreferences 失败：\n- " + "\n- ".join(errors))


def emby_enable_group_items_into_collections_pref(
    display_preferences: dict[str, Any],
    *,
    display_preference_id: str,
    client: str,
) -> dict[str, Any]:
    """
    在 DisplayPreferences 中开启“Group items into collections / 按合集分组”。

    这个字段在不同 Emby 客户端/版本里可能存在命名差异；为提高兼容性，
    同时写入常见顶层字段和 CustomPrefs 字段。未知字段通常会被服务端忽略。
    """
    patched = dict(display_preferences)
    patched["Id"] = str(patched.get("Id") or display_preference_id)
    patched["Client"] = client

    custom_prefs = patched.get("CustomPrefs")
    if not isinstance(custom_prefs, dict):
        custom_prefs = {}

    # 常见 Web/Android/TV 客户端使用字符串形式保存自定义偏好。
    for key in (
        "GroupItemsIntoCollections",
        "groupItemsIntoCollections",
        "GroupByCollection",
        "GroupByCollections",
        "CollectionGrouping",
        "collectionGrouping",
    ):
        custom_prefs[key] = "true"

    patched["CustomPrefs"] = custom_prefs

    # 兼容可能采用顶层字段保存的客户端。
    patched["GroupItemsIntoCollections"] = True
    patched["GroupByCollection"] = True
    patched["GroupByCollections"] = True
    patched["CollectionGrouping"] = True

    # 部分排序/筛选条件会让 Emby 客户端自动禁用合集分组。这里不强行覆盖已有排序，
    # 但新建偏好时给一个稳定默认值，避免首次进入库时被空值影响。
    patched.setdefault("SortBy", "SortName")
    patched.setdefault("SortOrder", "Ascending")

    return patched


def emby_apply_collection_display_grouping(
    session: requests.Session,
    base_url: str,
    token: str,
    config: dict[str, str],
    *,
    user_id: str = "",
) -> None:
    """
    尝试为所有用户的电影库开启“按合集分组显示”。

    说明：
    - ImportCollections 是媒体库扫描/元数据选项，用于生成合集关系；
    - DisplayPreferences 是前端显示偏好，用于把电影库里的多部电影折叠成合集入口；
    - 这里仅对 movies 类型媒体库处理，避免影响剧集/综艺筛选体验。
    """
    if not get_bool_env(config, "EMBY_GROUP_ITEMS_INTO_COLLECTIONS", True):
        log("EMBY_GROUP_ITEMS_INTO_COLLECTIONS=false，跳过电影库按合集分组显示。", "warning")
        return

    desired_libraries = [library for library in emby_build_libraries(config) if emby_is_movie_library(library)]
    if not desired_libraries:
        log("未发现电影类媒体库，跳过按合集分组显示。", "warning")
        return

    folders = emby_get_virtual_folders(session, base_url, token, user_id)
    movie_view_ids: list[tuple[str, str]] = []
    for library in desired_libraries:
        name = library["name"]
        folder = emby_find_folder(folders, name)
        if not folder:
            continue
        item_id = emby_folder_item_id(folder)
        if item_id:
            movie_view_ids.append((name, item_id))

    if not movie_view_ids:
        log("未找到电影媒体库 ItemId，跳过按合集分组显示。", "warning")
        return

    users = emby_list_users(session, base_url, token, user_id)
    if not users:
        log("未找到 Emby 用户，跳过按合集分组显示。", "warning")
        return

    clients = emby_get_collection_display_clients(config)
    updated = 0
    for user in users:
        target_user_id = str(user.get("Id") or "").strip()
        user_name = str(user.get("Name") or target_user_id).strip()
        if not target_user_id:
            continue

        for library_name, view_id in movie_view_ids:
            for client in clients:
                try:
                    preferences = emby_get_display_preferences(
                        session,
                        base_url,
                        token,
                        view_id,
                        target_user_id,
                        client=client,
                        user_id=user_id,
                    )
                    patched = emby_enable_group_items_into_collections_pref(
                        preferences,
                        display_preference_id=view_id,
                        client=client,
                    )
                    emby_set_display_preferences(
                        session,
                        base_url,
                        token,
                        view_id,
                        target_user_id,
                        patched,
                        client=client,
                        user_id=user_id,
                    )
                    updated += 1
                    log(f"已尝试开启按合集分组显示：用户={user_name}，库={library_name}，client={client}", "success")
                except Exception as exc:
                    log(
                        f"开启按合集分组显示失败：用户={user_name}，库={library_name}，"
                        f"client={client}，{exc}",
                        "warning",
                    )

    if updated:
        log(
            f"电影库按合集分组显示已写入 {updated} 项 DisplayPreferences；"
            "如个别客户端仍不生效，需要在该客户端电影库右上角三个点里手动打开一次。",
            "success",
        )


def emby_get_user_configuration(
    session: requests.Session,
    base_url: str,
    token: str,
    target_user_id: str,
    *,
    user_id: str = "",
) -> dict[str, Any]:
    resp = emby_request(
        session,
        "GET",
        base_url,
        f"/Users/{target_user_id}/Configuration",
        token=token,
        user_id=user_id or None,
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"读取 Emby 用户配置失败：{target_user_id}，"
            f"状态码={resp.status_code}，返回={resp.text}"
        )

    data = resp.json()
    if isinstance(data, dict):
        return data
    raise RuntimeError(f"无法识别 Emby 用户配置返回结构：{data}")


def emby_set_user_configuration(
    session: requests.Session,
    base_url: str,
    token: str,
    target_user_id: str,
    configuration: dict[str, Any],
    *,
    user_id: str = "",
) -> None:
    resp = emby_request(
        session,
        "POST",
        base_url,
        f"/Users/{target_user_id}/Configuration",
        token=token,
        user_id=user_id or None,
        json_body=configuration,
        timeout=20,
    )
    if resp.status_code not in (200, 204):
        raise RuntimeError(
            f"写入 Emby 用户配置失败：{target_user_id}，"
            f"状态码={resp.status_code}，返回={resp.text}"
        )


def emby_apply_library_display_order(
    session: requests.Session,
    base_url: str,
    token: str,
    config: dict[str, str],
    *,
    user_id: str = "",
) -> None:
    if not get_bool_env(config, "EMBY_APPLY_LIBRARY_ORDER", True):
        log("EMBY_APPLY_LIBRARY_ORDER=false，跳过媒体库排序。", "warning")
        return

    folders = emby_get_virtual_folders(session, base_url, token, user_id)
    desired_names = [library["name"] for library in emby_build_libraries(config)]
    ordered_view_ids = emby_build_ordered_view_ids(folders, desired_names)
    if not ordered_view_ids:
        log("未找到可排序的媒体库 ItemId，跳过 OrderedViews。", "warning")
        return

    users = emby_list_users(session, base_url, token, user_id)
    if not users:
        log("未找到 Emby 用户，跳过媒体库排序。", "warning")
        return

    for user in users:
        target_user_id = str(user.get("Id") or "").strip()
        user_name = str(user.get("Name") or target_user_id).strip()
        if not target_user_id:
            continue
        try:
            configuration = emby_get_user_configuration(
                session,
                base_url,
                token,
                target_user_id,
                user_id=user_id,
            )
            configuration["OrderedViews"] = ordered_view_ids
            emby_set_user_configuration(
                session,
                base_url,
                token,
                target_user_id,
                configuration,
                user_id=user_id,
            )
            log(f"已更新 Emby 用户{user_name}媒体库排序：{ordered_view_ids}", "success")
        except Exception as exc:
            log(f"更新 Emby 用户媒体库排序失败：{user_name}，{exc}", "warning")

    log(
        "媒体库排序："
        + " → ".join(desired_names[:6])
        + (" → ..." if len(desired_names) > 6 else ""),
        "success",
    )


def emby_scan_library(
    session: requests.Session,
    base_url: str,
    token: str,
    user_id: str = "",
) -> None:
    resp = emby_request(
        session,
        "POST",
        base_url,
        "/Library/Refresh",
        token=token,
        user_id=user_id or None,
        timeout=20,
    )

    if resp.status_code not in (200, 204):
        raise RuntimeError(f"触发 Emby 扫描失败：{resp.status_code} {resp.text}")

    log("已触发 Emby 媒体库扫描。", "success")


# =========================
# API Key 处理
# =========================

def emby_list_api_keys(
    session: requests.Session,
    base_url: str,
    token: str,
    user_id: str = "",
) -> list[dict[str, Any]]:
    """
    不同版本返回结构略有差异，尽量兼容。
    """
    paths = [
        "/Auth/Keys",
        "/ApiKeys",
    ]

    last_error = ""

    for path in paths:
        resp = emby_request(
            session,
            "GET",
            base_url,
            path,
            token=token,
            user_id=user_id or None,
            timeout=20,
        )

        if resp.status_code == 200:
            data = resp.json()

            if isinstance(data, list):
                return data

            if isinstance(data, dict):
                for key in ("Items", "items", "AuthenticationInfos", "Keys", "data"):
                    value = data.get(key)
                    if isinstance(value, list):
                        return value

                # 某些版本 Auth/Keys 返回 {"Items": [...]} 以外的结构
                # 返回整个 dict 包一层，便于调试
                return [data]

        last_error = f"{resp.status_code} {resp.text}"

    log(f"读取 Emby API Keys 失败，后续将跳过自动生成 API Key：{last_error}", "warning")
    return []


def _extract_api_key(item: dict[str, Any]) -> str:
    for key in ("AccessToken", "Token", "Key", "ApiKey", "api_key"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _extract_api_key_app(item: dict[str, Any]) -> str:
    for key in ("AppName", "Name", "App", "DeviceName"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def emby_find_api_key_by_app(
    keys: list[dict[str, Any]],
    app_name: str,
) -> str:
    for item in keys:
        if _extract_api_key_app(item) == app_name:
            token = _extract_api_key(item)
            if token:
                return token
    return ""


def emby_create_api_key(
    session: requests.Session,
    base_url: str,
    token: str,
    *,
    app_name: str,
    user_id: str = "",
) -> str:
    """
    尝试通过 API 创建 Key。
    不同版本接口可能不同，如果失败则返回空字符串。
    """
    before = emby_list_api_keys(session, base_url, token, user_id)
    existing = emby_find_api_key_by_app(before, app_name)
    if existing:
        log(f"Emby API Key 已存在：{app_name}", "success")
        return existing

    attempts = [
        ("POST", "/Auth/Keys", {"app": app_name}, None),
        ("POST", "/Auth/Keys", {"App": app_name}, None),
        ("POST", "/Auth/Keys", None, {"App": app_name}),
        ("POST", "/ApiKeys", {"app": app_name}, None),
        ("POST", "/ApiKeys", None, {"App": app_name}),
    ]

    last_error = ""

    for method, path, params, body in attempts:
        resp = emby_request(
            session,
            method,
            base_url,
            path,
            token=token,
            user_id=user_id or None,
            params=params,
            json_body=body,
            timeout=20,
        )

        if resp.status_code in (200, 204):
            # 有些版本创建后不直接返回 key，需要重新列表查
            try:
                if resp.text.strip():
                    data = resp.json()
                    if isinstance(data, dict):
                        created_key = _extract_api_key(data)
                        if created_key:
                            log(f"创建 Emby API Key 成功：{app_name}", "success")
                            return created_key
            except Exception:
                pass

            time.sleep(1)
            after = emby_list_api_keys(session, base_url, token, user_id)
            created_key = emby_find_api_key_by_app(after, app_name)
            if created_key:
                log(f"创建 Emby API Key 成功：{app_name}", "success")
                return created_key

            # 有的接口只返回空，但列表项字段不含明文 token
            log(f"已调用创建 API Key 接口，但未能从返回中提取明文 Key：{path}", "warning")
            return ""

        last_error = f"{method} {path} -> {resp.status_code} {resp.text[:300]}"

    log(
        "自动创建 Emby API Key 失败。建议在 Emby 后台手动创建："
        "设置/高级/API密钥，新建名称 MoviePilot。",
        "warning",
    )
    log(f"最后错误：{last_error}", "warning")
    return ""


def emby_ensure_api_key(
    session: requests.Session,
    base_url: str,
    token: str,
    stack_dir: Path,
    config: dict[str, str],
    user_id: str = "",
) -> None:
    env_path = stack_dir / ".env"
    existing = get_env(config, "EMBY_API_KEY", "")
    if existing:
        log("EMBY_API_KEY 已存在，跳过生成。", "success")
        return

    if not get_bool_env(config, "EMBY_CREATE_API_KEY", True):
        log("EMBY_CREATE_API_KEY=false，跳过自动生成 API Key。", "warning")
        return

    app_name = get_env(config, "EMBY_API_KEY_APP_NAME", "MoviePilotV2")
    api_key = emby_create_api_key(
        session,
        base_url,
        token,
        app_name=app_name,
        user_id=user_id,
    )

    if api_key:
        update_env_file(
            env_path,
            {
                "EMBY_API_KEY": api_key,
                "MP_EMBY_HOST": get_env(config, "MP_EMBY_HOST", "http://emby:8096"),
            },
            log_func=lambda msg: log(msg, "success"),
        )
    else:
        log(
            "未能自动写入 EMBY_API_KEY。你需要手动在 Emby 后台创建 API Key 后写入 .env。",
            "warning",
        )


# =========================
# 用户与私享影库提示
# =========================

def emby_list_users(
    session: requests.Session,
    base_url: str,
    token: str,
    user_id: str = "",
) -> list[dict[str, Any]]:
    resp = emby_request(
        session,
        "GET",
        base_url,
        "/Users",
        token=token,
        user_id=user_id or None,
        timeout=20,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"读取 Emby 用户失败：{resp.status_code} {resp.text}")

    data = resp.json()
    if isinstance(data, list):
        return data
    return []


def emby_print_users_and_private_hint(
    session: requests.Session,
    base_url: str,
    token: str,
    user_id: str = "",
) -> None:
    try:
        users = emby_list_users(session, base_url, token, user_id)
    except Exception as exc:
        log(f"读取用户失败，跳过权限提示：{exc}", "warning")
        return

    log("当前 Emby 用户：")
    log(json.dumps(
        [
            {
                "Name": user.get("Name"),
                "Id": user.get("Id"),
                "HasPassword": user.get("HasPassword"),
                "Policy": {
                    "IsAdministrator": (user.get("Policy") or {}).get("IsAdministrator"),
                    "EnableAllFolders": (user.get("Policy") or {}).get("EnableAllFolders"),
                    "EnabledFolders": (user.get("Policy") or {}).get("EnabledFolders"),
                },
            }
            for user in users
        ],
        ensure_ascii=False,
        indent=2,
    ))

    log(
        "私享影库权限建议：初始化后到 Emby 后台手动控制用户媒体库访问权限，"
        "只给指定用户开放“私享影库-*”或“私享电影-*”。不要把它开放给普通家庭账号。",
        "warning",
    )


# =========================
# 总入口
# =========================

def init_emby(host: str, stack_dir: Path, timeout: int = 90, scan: bool = False) -> None:
    env_path = stack_dir / ".env"
    config = read_env_file(env_path)

    base_url = build_emby_url(host, config)

    wait_for_emby_api(base_url, timeout=timeout)

    session, token, user_id = emby_session_from_config(base_url, config)

    info = emby_get_system_info(session, base_url, token, user_id)
    log("Emby 系统信息：")
    log(json.dumps({
        "ServerName": info.get("ServerName"),
        "Version": info.get("Version"),
        "OperatingSystem": info.get("OperatingSystem"),
        "Id": info.get("Id"),
    }, ensure_ascii=False, indent=2))

    emby_init_libraries(
        session,
        base_url,
        token,
        config,
        user_id=user_id,
        refresh_library=False,
    )

    emby_ensure_api_key(
        session,
        base_url,
        token,
        stack_dir,
        config,
        user_id=user_id,
    )

    emby_print_users_and_private_hint(session, base_url, token, user_id)

    if scan:
        emby_scan_library(session, base_url, token, user_id)

    log("Emby 初始化流程执行完成。", "success")


def dump_emby(host: str, stack_dir: Path, timeout: int = 90) -> None:
    env_path = stack_dir / ".env"
    config = read_env_file(env_path)

    base_url = build_emby_url(host, config)
    wait_for_emby_api(base_url, timeout=timeout)

    session, token, user_id = emby_session_from_config(base_url, config)

    info = emby_get_system_info(session, base_url, token, user_id)
    folders = emby_get_virtual_folders(session, base_url, token, user_id)
    keys = emby_list_api_keys(session, base_url, token, user_id)
    users = emby_list_users(session, base_url, token, user_id)

    log("Emby SystemInfo：")
    log(json.dumps(info, ensure_ascii=False, indent=2))

    log("Emby VirtualFolders：")
    log(json.dumps(folders, ensure_ascii=False, indent=2))

    log("Emby API Keys：")
    log(json.dumps(keys, ensure_ascii=False, indent=2))

    log("Emby Users：")
    log(json.dumps(users, ensure_ascii=False, indent=2))


def run_init_emby(
    stack_dir: Path,
    host_ip: str | None = None,
    timeout: int = 90,
) -> None:
    stack_dir = stack_dir.expanduser().resolve()
    host_ip = host_ip or get_lan_ip()
    init_emby(host_ip, stack_dir, timeout=timeout, scan=False)
