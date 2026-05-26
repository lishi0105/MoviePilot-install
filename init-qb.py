"""qBittorrent initialization helpers for the MoviePilot v2 stack."""

from __future__ import annotations

import json
import re
import requests
import subprocess
import time
from pathlib import Path

from env_utils import get_env, read_env_file, update_env_file
from log_utils import log
from password_utils import stack_random_secret, validate_password_map


def mask_qb_prefs_for_log(prefs: dict) -> dict:
    sensitive_keys = {"web_ui_password", "web_ui_api_key"}
    return {key: "<hidden>" if key in sensitive_keys and value else value for key, value in prefs.items()}


def run_capture(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=False, text=True, capture_output=True)


def ensure_container_running(container_name: str) -> None:
    result = run_capture(["docker", "inspect", "-f", "{{.State.Running}}", container_name])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"容器不存在或无法检查：{container_name}。{detail}")
    if result.stdout.strip().lower() != "true":
        raise RuntimeError(f"容器未正常运行：{container_name}")


def try_parse_qb_initial_credentials_from_logs(container_name: str) -> tuple[str, str] | None:
    result = run_capture(["docker", "logs", container_name])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"读取容器日志失败：{container_name}。{detail}")

    logs = "\n".join(part for part in (result.stdout, result.stderr) if part)
    username_patterns = (
        r"The WebUI administrator username is:\s*(\S+)",
        r"WebUI administrator username.*?:\s*(\S+)",
    )
    password_patterns = (
        r"temporary password .*?:\s*(\S+)",
        r"The WebUI administrator password is:\s*(\S+)",
        r"WebUI administrator password.*?:\s*(\S+)",
    )

    username = "admin"
    for pattern in username_patterns:
        match = re.search(pattern, logs, re.IGNORECASE)
        if match:
            username = match.group(1).strip()
            break

    password = ""
    for pattern in password_patterns:
        matches = re.findall(pattern, logs, re.IGNORECASE)
        if matches:
            password = matches[-1].strip()
            break

    if not password:
        return None

    return username, password


def resolve_qb_login_credentials(
    container_name: str,
    env_values: dict[str, str],
    prefix: str,
) -> tuple[str, str, str, bool]:
    """
    解析 qB 登录凭据。

    返回：username, password, source, already_initialized
    - 优先使用容器首次启动日志中的临时密码（首次初始化）
    - 日志不可用时回退 .env 中的 QB_{prefix}_USER / QB_{prefix}_PASSWORD
    - 能使用 .env 登录说明 WebUI 已完成初始化，后续不再改用户名密码
    """
    from_logs = try_parse_qb_initial_credentials_from_logs(container_name)
    if from_logs:
        username, password = from_logs
        return username, password, "docker logs", False

    username = get_env(env_values, f"QB_{prefix}_USER", "admin")
    password = get_env(env_values, f"QB_{prefix}_PASSWORD", "")
    if not password:
        raise RuntimeError(
            f"未能从 {container_name} 日志中解析 qB 初始密码，"
            f"且 .env 缺少 QB_{prefix}_PASSWORD，无法登录。"
        )

    log(
        f"未能从 {container_name} 日志中解析 qB 初始密码，"
        f"将尝试使用 .env 中的 QB_{prefix}_USER / QB_{prefix}_PASSWORD 登录。",
        "warning",
    )
    return username, password, ".env", True


def qb_set_preferences(session: requests.Session, base_url: str, prefs: dict) -> None:
    resp = session.post(
        f"{base_url}/api/v2/app/setPreferences",
        data={"json": json.dumps(prefs, ensure_ascii=False)},
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"qB 设置参数失败：{base_url}，状态码={resp.status_code}，返回={resp.text}")
    log(f"qB 设置参数成功：{base_url}，参数={mask_qb_prefs_for_log(prefs)}", "success")


def qb_create_or_update_category(session: requests.Session, base_url: str, category: str, save_path: str) -> None:
    resp = session.post(
        f"{base_url}/api/v2/torrents/createCategory",
        data={"category": category, "savePath": save_path},
        timeout=10,
    )
    if resp.status_code == 409:
        resp = session.post(
            f"{base_url}/api/v2/torrents/editCategory",
            data={"category": category, "savePath": save_path},
            timeout=10,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"qB 分类设置失败：{base_url}，分类={category}，状态码={resp.status_code}，返回={resp.text}")


def qb_login(base_url: str, username: str, password: str) -> requests.Session:
    base_url = base_url.rstrip("/")
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": f"{base_url}/",
        "Origin": base_url,
    }

    resp = session.post(
        f"{base_url}/api/v2/auth/login",
        data={"username": username, "password": password},
        headers=headers,
        timeout=10,
    )
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"qB 登录请求失败：{base_url}，状态码={resp.status_code}，返回={resp.text}")
    if resp.status_code == 200:
        text = resp.text.strip()
        if text and text.lower() not in ("ok.", "ok"):
            raise RuntimeError(f"qB 登录失败：{base_url}，状态码={resp.status_code}，返回={resp.text}")

    session.headers.update(headers)
    check = session.get(f"{base_url}/api/v2/app/version", timeout=10)
    if check.status_code != 200:
        raise RuntimeError(f"qB 登录后验证失败：{base_url}，状态码={check.status_code}，返回={check.text}")
    log(f"qB 登录成功：{base_url}，username={username}，版本：{check.text.strip()}", "success")
    return session


def qb_verify_api_key(base_url: str, api_key: str) -> None:
    base_url = base_url.rstrip("/")
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": f"{base_url}/",
            "Origin": base_url,
            "Authorization": f"Bearer {api_key}",
        }
    )
    resp = session.get(f"{base_url}/api/v2/app/version", timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"qB API key 验证失败：状态码={resp.status_code}，返回={resp.text}")


def qb_rotate_api_key(session: requests.Session, base_url: str) -> str | None:
    resp = session.post(f"{base_url}/api/v2/app/rotateAPIKey", timeout=10)
    if resp.status_code == 404:
        log(f"qB 当前版本不支持 WebAPI API key：{base_url}，MoviePilot 将回退用户名密码。", "warning")
        return None
    if resp.status_code != 200:
        log(
            f"qB 创建 API key 失败：{base_url}，状态码={resp.status_code}，返回={resp.text}；"
            "MoviePilot 将回退用户名密码。",
            "warning",
        )
        return None

    try:
        api_key = resp.json().get("apiKey", "")
    except ValueError:
        log(f"qB 创建 API key 返回非 JSON：{base_url}，返回={resp.text}；MoviePilot 将回退用户名密码。", "warning")
        return None

    if not api_key:
        log(f"qB 创建 API key 返回为空：{base_url}；MoviePilot 将回退用户名密码。", "warning")
        return None
    if not api_key.startswith("qbt_") or len(api_key) != 32:
        log(f"qB 创建 API key 格式异常：{base_url}；MoviePilot 将回退用户名密码。", "warning")
        return None

    try:
        qb_verify_api_key(base_url, api_key)
    except Exception as exc:  # noqa: BLE001
        log(f"qB API key 验证失败：{base_url}，{exc}；MoviePilot 将回退用户名密码。", "warning")
        return None

    log(f"qB API key 创建并验证成功：{base_url}，api_key=<hidden>", "success")
    return api_key


def resolve_qb_api_key(
    session: requests.Session,
    base_url: str,
    env_values: dict[str, str],
    prefix: str,
) -> str:
    """
    优先复用 .env 中仍有效的 API key；无效或缺失时再轮换生成。
    """
    existing = get_env(env_values, f"QB_{prefix}_API_KEY", "")
    if existing:
        try:
            qb_verify_api_key(base_url, existing)
            log(f"qB 已有 API key 有效，跳过轮换：{base_url}", "success")
            return existing
        except Exception as exc:  # noqa: BLE001
            log(f"qB .env 中 API key 无效，将尝试轮换：{exc}", "warning")

    return qb_rotate_api_key(session, base_url) or ""


def set_qb_webui_credentials(session: requests.Session, base_url: str, username: str, password: str) -> None:
    qb_set_preferences(session, base_url, {"web_ui_username": username, "web_ui_password": password})
    # qB occasionally applies WebUI credentials asynchronously.
    last_error = ""
    for _ in range(5):
        try:
            qb_login(base_url, username, password)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(1)
    raise RuntimeError(
        f"qB 新凭据验证失败：{base_url}，username={username}。"
        f"请检查是否触发了 IP 登录封禁或浏览器缓存问题。最后错误：{last_error}"
    )


def harden_webui_login_compat(session: requests.Session, base_url: str) -> None:
    # Recover from legacy/broken WebUI settings that can cause browser login
    # to fail while API login still works.
    qb_set_preferences(
        session,
        base_url,
        {
            "web_ui_ban_duration": 0,
            "web_ui_max_auth_fail_count": 50,
            "web_ui_banned_ips": "",
            "alternative_webui_enabled": False,
            "use_https": False,
            "web_ui_secure_cookie_enabled": False,
            "web_ui_host_header_validation_enabled": False,
            "bypass_auth_subnet_whitelist_enabled": False,
        },
    )
    log(f"qB WebUI 兼容性设置已应用：{base_url}")


def wait_for_qb_api(base_url: str, timeout: int = 60) -> None:
    base_url = base_url.rstrip("/")
    deadline = time.monotonic() + timeout
    last_error = ""
    headers = {"User-Agent": "Mozilla/5.0", "Referer": f"{base_url}/", "Origin": base_url}
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{base_url}/api/v2/app/version", headers=headers, timeout=5)
            if resp.status_code == 200:
                log(f"qB Web API 已就绪：{base_url}，版本：{resp.text}")
                return
            if resp.status_code in (401, 403):
                log(
                    f"qB Web API 可达：{base_url}，未登录返回 HTTP {resp.status_code}（正常），继续执行登录流程。"
                )
                return
            last_error = f"HTTP {resp.status_code}: {resp.text}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(2)
    raise RuntimeError(f"qB Web API 未就绪：{base_url}。最后错误：{last_error}")


def qb_get_locale(session: requests.Session, base_url: str) -> str:
    base_url = base_url.rstrip("/")

    resp = session.get(
        f"{base_url}/api/v2/app/preferences",
        timeout=10,
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"读取 qB 配置失败：{base_url}，"
            f"状态码={resp.status_code}，返回={resp.text}"
        )

    return resp.json().get("locale", "")


def init_qb_instance(
    label: str,
    base_url: str,
    container_name: str,
    save_path: Path,
    categories: list[tuple[str, Path]],
    prefs: dict,
    env_values: dict[str, str],
    prefix: str,
    target_password: str,
) -> tuple[str, bool]:
    new_username = "admin"
    new_password = target_password

    ensure_container_running(container_name)
    wait_for_qb_api(base_url)
    login_username, login_password, source, already_initialized = resolve_qb_login_credentials(
        container_name,
        env_values,
        prefix,
    )
    log(f"qB {label} 当前登录来源：{source}，username={login_username}", "success")

    session = qb_login(base_url, login_username, login_password)
    merged_prefs = {
        "locale": "zh_CN",
        "save_path": str(save_path),
        "temp_path_enabled": False,
        "dht": False,
        "pex": False,
        "lsd": False,
        "queueing_enabled": True,
        # 默认启用自动 Torrent 管理模式：选分类后按分类 savePath 下载
        "auto_tmm_enabled": True,
        "torrent_changed_tmm_enabled": True,
        "category_changed_tmm_enabled": True,
    }
    merged_prefs.update(prefs)
    qb_set_preferences(session, base_url, merged_prefs)
    for category, category_path in categories:
        qb_create_or_update_category(session, base_url, category, str(category_path))

    if already_initialized:
        log(f"qB {label} 已完成初始化，跳过 WebUI 用户名密码修改。", "success")
    else:
        set_qb_webui_credentials(session, base_url, new_username, new_password)

    harden_webui_login_compat(session, base_url)
    log(f"qB {label} 本地化语言设置：{qb_get_locale(session, base_url)}")
    api_key = resolve_qb_api_key(session, base_url, env_values, prefix)
    if already_initialized:
        log(f"qB {label} 当前登录信息：username={login_username}, password=<hidden>", "success")
    else:
        log(f"qB {label} 新登录信息：username={new_username}, password=<hidden>", "success")
    log(f"qB {label} WebUI: {base_url}", "success")
    return api_key or "", already_initialized

def run_init_qb(
    stack_dir: Path,
    data_dir: Path,
    host_ip: str | None = None,
) -> None:
    stack_dir = stack_dir.expanduser().resolve()
    data_dir = data_dir.expanduser().resolve()
    if not host_ip:
        raise RuntimeError("host_ip 不能为空，必须由主模块传入。")
    env_path = stack_dir / ".env"
    env_values = read_env_file(env_path)
    media_save_path = Path(
        get_env(env_values, "MP_QB_MEDIA_STORAGE_PATH", str(data_dir / "downloads" / "media"))
    )
    brush_save_path = Path(
        get_env(env_values, "MP_QB_BRUSH_STORAGE_PATH", str(data_dir / "downloads" / "brush"))
    )
    media_password = get_env(env_values, "QB_MEDIA_PASSWORD", "") or stack_random_secret()
    brush_password = get_env(env_values, "QB_BRUSH_PASSWORD", "") or stack_random_secret()
    validate_password_map(
        {
            "QB_MEDIA_PASSWORD": media_password,
            "QB_BRUSH_PASSWORD": brush_password,
        }
    )
    update_env_file(
        env_path,
        {
            "MP_QB_MEDIA_STORAGE_PATH": str(media_save_path),
            "MP_QB_BRUSH_STORAGE_PATH": str(brush_save_path),
        },
        log_func=log,
    )
    env_values["MP_QB_MEDIA_STORAGE_PATH"] = str(media_save_path)
    env_values["MP_QB_BRUSH_STORAGE_PATH"] = str(brush_save_path)

    log(f"qB WebUI 访问 IP：{host_ip}")
    media_api_key, media_initialized = init_qb_instance(
        label="media",
        base_url=f"http://{host_ip}:7097",
        container_name="mpv2-qb-media",
        save_path=media_save_path,
        categories=[
            ("media", media_save_path),
            ("manual", data_dir / "downloads" / "manual"),
            ("private", data_dir / "downloads" / "private"),
        ],
        prefs={
            "max_active_downloads": 10,
            "max_active_uploads": 20,
            "max_active_torrents": 30,
            "up_limit": 0,
            "dl_limit": 0,
        },
        env_values=env_values,
        prefix="MEDIA",
        target_password=media_password,
    )

    brush_api_key, brush_initialized = init_qb_instance(
        label="brush",
        base_url=f"http://{host_ip}:7098",
        container_name="mpv2-qb-brush",
        save_path=brush_save_path,
        categories=[("brush", brush_save_path)],
        prefs={
            "max_active_downloads": 10,
            "max_active_uploads": 50,
            "max_active_torrents": 80,
            "up_limit": 10 * 1024 * 1024,
            "dl_limit": 50 * 1024 * 1024,
        },
        env_values=env_values,
        prefix="BRUSH",
        target_password=brush_password,
    )
    env_updates: dict[str, str] = {
        "QB_MEDIA_API_KEY": media_api_key,
        "QB_BRUSH_API_KEY": brush_api_key,
    }
    if not media_initialized:
        env_updates["QB_MEDIA_PASSWORD"] = media_password
    if not brush_initialized:
        env_updates["QB_BRUSH_PASSWORD"] = brush_password

    update_env_file(env_path, env_updates, log_func=log)
    log("qBittorrent 初始化完成。", "success")
    
