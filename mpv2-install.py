#!/usr/bin/env python3
"""Install files for the MoviePilot v2 media stack."""

from __future__ import annotations

import argparse
import importlib.util
import os
import socket
import secrets
import shutil
import string
import subprocess
import sys
import time
from pathlib import Path

from env_utils import read_env_file, update_env_file
from log_utils import log
from path_utils import (
    DIR_MODE,
    DIR_MODE_APP,
    DIR_MODE_DATA,
    DIR_MODE_DB,
    FILE_MODE_NORMAL,
    FILE_MODE_SECRET,
    POSTGRES_DATA_GID,
    POSTGRES_DATA_UID,
    REDIS_DATA_GID,
    REDIS_DATA_UID,
    ensure_directory,
    write_file_with_permissions,
)

DEFAULT_STACK_DIR = Path("/volume1/docker/media-stack")
DEFAULT_DATA_DIR = Path("/volume1/media-data")
DEFAULT_MPV2_PASSWORD = ""
DEFAULT_QB_MEDIA_PASSWORD = ""
DEFAULT_QB_BRUSH_PASSWORD = ""

REGIONS = ("大陆", "港澳台", "欧美", "日韩", "东南亚", "其他地区")
MEDIA_CATEGORIES = ("真人电影", "真人剧集", "动漫电影", "动漫剧集", "综艺", "纪录片")
PRIVATE_REGIONS = ("国产", "日韩", "欧美", "其他地区")


COMPOSE_YML = """services:
  moviepilot:
    image: jxxghp/moviepilot-v2:latest
    container_name: mpv2-moviepilot
    hostname: mpv2-moviepilot
    restart: always
    stdin_open: true
    tty: true
    networks:
      - media_net
    ports:
      - "9443:3000"
      - "3001:3001"
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ${DATA_DIR}:${DATA_DIR}
      - ${STACK_DIR}/moviepilot-v2/config:/config
      - ${STACK_DIR}/moviepilot-v2/core:/moviepilot/.cloakbrowser
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      - NGINX_PORT=3000
      - PORT=3001
      - PUID=${PUID}
      - PGID=${PGID}
      - UMASK=000
      - SUPERUSER=${MOVIEPILOT_USER}
      - SUPERUSER_PASSWORD=${MOVIEPILOT_PASSWORD}
      - MOVIEPILOT_AUTO_UPDATE=false
      - DB_TYPE=postgresql
      - DB_POSTGRESQL_HOST=postgresql
      - DB_POSTGRESQL_PORT=5432
      - DB_POSTGRESQL_DATABASE=${POSTGRES_DB}
      - DB_POSTGRESQL_USERNAME=${POSTGRES_USER}
      - DB_POSTGRESQL_PASSWORD=${POSTGRES_PASSWORD}
      - CACHE_BACKEND_TYPE=redis
      - CACHE_BACKEND_URL=redis://:${REDIS_PASSWORD}@redis:6379
      - CACHE_REDIS_MAXMEMORY=512mb
      - GLOBAL_IMAGE_CACHE=true
    depends_on:
      postgresql:
        condition: service_healthy
      redis:
        condition: service_healthy

  postgresql:
    image: postgres:17
    container_name: mpv2-postgresql
    hostname: postgresql
    restart: unless-stopped
    networks:
      - media_net
    environment:
      - POSTGRES_DB=${POSTGRES_DB}
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ${STACK_DIR}/postgresql/data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 10

  redis:
    image: redis:latest
    container_name: mpv2-redis
    hostname: redis
    restart: unless-stopped
    networks:
      - media_net
    command: redis-server --appendonly yes --save 600 1 --requirepass ${REDIS_PASSWORD}
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ${STACK_DIR}/redis/data:/data
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a ${REDIS_PASSWORD} ping | grep PONG"]
      interval: 10s
      timeout: 5s
      retries: 10

  qb-media:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: mpv2-qb-media
    hostname: mpv2-qb-media
    restart: unless-stopped
    stop_grace_period: 30m
    networks:
      - media_net
    ports:
      - "7097:7097"
      - "16881:16881"
      - "16881:16881/udp"
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ${STACK_DIR}/qb-media/config:/config
      - ${DATA_DIR}:${DATA_DIR}
    environment:
      - PUID=${PUID}
      - PGID=${PGID}
      - WEBUI_PORT=7097
      - TORRENTING_PORT=16881

  qb-brush:
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: mpv2-qb-brush
    hostname: mpv2-qb-brush
    restart: unless-stopped
    stop_grace_period: 30m
    networks:
      - media_net
    ports:
      - "7098:7098"
      - "16882:16882"
      - "16882:16882/udp"
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ${STACK_DIR}/qb-brush/config:/config
      - ${DATA_DIR}:${DATA_DIR}
    environment:
      - PUID=${PUID}
      - PGID=${PGID}
      - WEBUI_PORT=7098
      - TORRENTING_PORT=16882

  emby:
    image: emby/embyserver:latest
    container_name: mpv2-emby
    hostname: mpv2-emby
    restart: unless-stopped
    networks:
      - media_net
    ports:
      - "7096:8096"
      - "7020:8920"
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ${STACK_DIR}/emby/config:/config
      - ${DATA_DIR}/media:${DATA_DIR}/media
    devices:
      - /dev/dri:/dev/dri
    environment:
      - UID=${PUID}
      - GID=${PGID}
      - GIDLIST=${PGID}

  chinesesubfinder:
    image: allanpk716/chinesesubfinder:latest
    container_name: mpv2-chinesesubfinder
    hostname: mpv2-chinesesubfinder
    restart: unless-stopped
    networks:
      - media_net
    ports:
      - "7035:19035"
      - "7037:19037"
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ${STACK_DIR}/chinesesubfinder/config:/config
      - ${STACK_DIR}/chinesesubfinder/browser:/root/.cache/rod/browser
      - ${DATA_DIR}/media:/media
    environment:
      - PUID=${PUID}
      - PGID=${PGID}
      - PERMS=true
      - UMASK=022

networks:
  media_net:
    name: media_net
    driver: bridge
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create MoviePilot v2 media-stack directories, .env, and docker-compose.yml."
    )
    parser.add_argument("--stack-dir", default=str(DEFAULT_STACK_DIR), help="compose and config root")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="downloads and media root")
    parser.add_argument("--puid", type=int, default=os.getuid(), help="container PUID")
    parser.add_argument("--pgid", type=int, default=os.getgid(), help="container PGID")
    parser.add_argument("--moviepilot-user", default="admin", help="MoviePilot superuser")
    parser.add_argument(
        "--password",
        help="unified password for MoviePilotV2, qB media, and qB brush; cannot be used with individual passwords",
    )
    parser.add_argument("--moviepilot-password", help="MoviePilot superuser password, defaults to a random 16-character value")
    parser.add_argument("--github-token", help="GitHub token for MoviePilot plugin/resource requests")
    parser.add_argument("--postgres-db", default="moviepilotv2", help="PostgreSQL database name")
    parser.add_argument("--postgres-user", default="moviepilotv2", help="PostgreSQL user")
    parser.add_argument("--postgres-password", help="PostgreSQL password, defaults to a random 16-character value")
    parser.add_argument("--redis-password", help="Redis password, defaults to a random 16-character value")
    parser.add_argument(
        "--qb-media-password",
        help="qBittorrent media WebUI password, defaults to a random 16-character value",
    )
    parser.add_argument(
        "--qb-brush-password",
        help="qBittorrent brush WebUI password, defaults to a random 16-character value",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing .env and docker-compose.yml")
    parser.add_argument("--clean", action="store_true", help="stop containers and remove generated stack/download contents")
    parser.add_argument("--stop", action="store_true", help="stop containers only")
    parser.add_argument("--init-qb", action="store_true", help="initialize both qBittorrent instances")
    parser.add_argument("--init-emby", action="store_true", help="initialize Emby server binding in MoviePilot via init-mpv2.py")
    parser.add_argument("--init-mpv2", action="store_true", help="initialize MoviePilot settings via init-mpv2.py")
    parser.add_argument("--host-ip", help="host LAN IP forwarded to init-qb.py and init-mpv2.py")
    args = parser.parse_args()
    individual_passwords = [
        args.moviepilot_password,
        args.qb_media_password,
        args.qb_brush_password,
    ]
    if args.password and any(individual_passwords):
        parser.error("--password cannot be used with --moviepilot-password, --qb-media-password, or --qb-brush-password")
    return args


def random_secret(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def configured_password(cli_value: str | None, default_value: str) -> str:
    return cli_value or default_value or random_secret()


def selected_password(args: argparse.Namespace, attr: str, default_value: str) -> str:
    return args.password or configured_password(getattr(args, attr), default_value)


def get_lan_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("223.5.5.5", 80))
            ip = sock.getsockname()[0]
        except OSError:
            ip = socket.gethostbyname(socket.gethostname())
    if not ip or ip.startswith("127."):
        raise RuntimeError("未能自动获取真实内网 IP，请使用 --host-ip 指定。")
    return ip


def resolve_host_ip(args: argparse.Namespace) -> str:
    if args.host_ip:
        return args.host_ip
    return get_lan_ip()


def build_directory_specs(stack_dir: Path, data_dir: Path, puid: int, pgid: int) -> list[tuple[Path, int, int, int]]:
    specs: list[tuple[Path, int, int, int]] = []

    def add(path: Path, uid: int, gid: int, mode: int) -> None:
        specs.append((path, uid, gid, mode))

    add(stack_dir, puid, pgid, DIR_MODE_APP)
    for relative in (
        "moviepilot-v2/config",
        "moviepilot-v2/core",
        "qb-media/config",
        "qb-brush/config",
        "emby/config",
        "chinesesubfinder/config",
        "chinesesubfinder/browser",
    ):
        add(stack_dir / relative, puid, pgid, DIR_MODE_APP)

    add(stack_dir / "postgresql" / "data", POSTGRES_DATA_UID, POSTGRES_DATA_GID, DIR_MODE_DB)
    add(stack_dir / "redis" / "data", REDIS_DATA_UID, REDIS_DATA_GID, DIR_MODE_DB)

    add(data_dir / "downloads", puid, pgid, DIR_MODE_DATA)
    for relative in ("media", "brush", "manual", "private"):
        add(data_dir / "downloads" / relative, puid, pgid, DIR_MODE_DATA)

    add(data_dir / "media", puid, pgid, DIR_MODE_DATA)
    add(data_dir / "media" / "短剧", puid, pgid, DIR_MODE_DATA)
    add(data_dir / "media" / "小电影", puid, pgid, DIR_MODE_DATA)
    for category in MEDIA_CATEGORIES:
        for region in REGIONS:
            add(data_dir / "media" / category / region, puid, pgid, DIR_MODE_DATA)
    for region in PRIVATE_REGIONS:
        add(data_dir / "media" / "私享影库" / region, puid, pgid, DIR_MODE_DATA)

    return specs


def build_directories(stack_dir: Path, data_dir: Path) -> list[Path]:
    return [path for path, _, _, _ in build_directory_specs(stack_dir, data_dir, 0, 0)]


def build_clean_paths(stack_dir: Path, data_dir: Path) -> list[Path]:
    return build_stack_paths(stack_dir) + [data_dir / "downloads"]


def build_stack_paths(stack_dir: Path) -> list[Path]:
    return [
        stack_dir / "moviepilot-v2",
        stack_dir / "postgresql",
        stack_dir / "redis",
        stack_dir / "qb-media",
        stack_dir / "qb-brush",
        stack_dir / "emby",
        stack_dir / "chinesesubfinder",
        stack_dir / ".env",
        stack_dir / "docker-compose.yml",
    ]


def env_text(args: argparse.Namespace, stack_dir: Path, data_dir: Path) -> str:
    values = {
        "STACK_DIR": stack_dir,
        "DATA_DIR": data_dir,
        "PUID": args.puid,
        "PGID": args.pgid,
        "MOVIEPILOT_USER": args.moviepilot_user,
        "MOVIEPILOT_PASSWORD": selected_password(args, "moviepilot_password", DEFAULT_MPV2_PASSWORD),
        "POSTGRES_DB": args.postgres_db,
        "POSTGRES_USER": args.postgres_user,
        "POSTGRES_PASSWORD": args.postgres_password or random_secret(),
        "REDIS_PASSWORD": args.redis_password or random_secret(),
        "QB_MEDIA_PASSWORD": selected_password(args, "qb_media_password", DEFAULT_QB_MEDIA_PASSWORD),
        "QB_BRUSH_PASSWORD": selected_password(args, "qb_brush_password", DEFAULT_QB_BRUSH_PASSWORD),
        "MP_QB_MEDIA_STORAGE_PATH": data_dir / "downloads" / "media",
        "MP_QB_BRUSH_STORAGE_PATH": data_dir / "downloads" / "brush",
    }
    if args.github_token:
        values["GITHUB_TOKEN"] = args.github_token
    return "".join(f"{key}={value}\n" for key, value in values.items())


def write_file(path: Path, content: str, force: bool, *, puid: int, pgid: int, mode: int) -> str:
    return write_file_with_permissions(
        path,
        content,
        uid=puid,
        gid=pgid,
        mode=mode,
        force=force,
    )


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, capture_output=True)


_COMPOSE_CMD: list[str] | None = None


def check_docker_available() -> None:
    result = run_command(["docker", "info"])
    if result.returncode == 0:
        log("Docker 可用，当前用户有权限。", "success")
        return

    output = (result.stderr or result.stdout).strip()
    lowered = output.lower()
    if "permission denied" in lowered:
        raise RuntimeError(
            "当前用户无 Docker 权限。请将用户加入 docker 组后重新登录，或使用 root/sudo 执行。\n"
            f"详情：{output}"
        )
    if "cannot connect" in lowered or "is the docker daemon running" in lowered:
        raise RuntimeError(
            "无法连接 Docker 守护进程，请确认 Docker / Container Manager 已启动。\n"
            f"详情：{output}"
        )
    raise RuntimeError(f"Docker 检查失败：{output or '未知错误'}")


def detect_compose_command() -> list[str]:
    global _COMPOSE_CMD
    if _COMPOSE_CMD is not None:
        return _COMPOSE_CMD

    candidates = (
        (["docker", "compose"], "Docker Compose 插件"),
        (["docker-compose"], "docker-compose 独立命令"),
    )
    errors: list[str] = []
    for command, label in candidates:
        result = run_command([*command, "version"])
        if result.returncode == 0:
            version_line = next(
                (line.strip() for line in (result.stdout or result.stderr).splitlines() if line.strip()),
                "",
            )
            _COMPOSE_CMD = command
            log(
                f"Docker Compose 可用：{' '.join(command)}"
                + (f"（{version_line}）" if version_line else ""),
                "success",
            )
            return _COMPOSE_CMD
        errors.append(f"{' '.join(command)} -> {((result.stderr or result.stdout).strip() or '执行失败')}")

    raise RuntimeError(
        "未检测到 Docker Compose。请安装 Compose 插件（推荐 `docker compose`）"
        "或独立命令 `docker-compose`。\n"
        + "\n".join(f"- {item}" for item in errors)
    )


def compose_command(*subcommand: str) -> list[str]:
    return [*detect_compose_command(), *subcommand]


def ensure_docker_environment() -> None:
    check_docker_available()
    detect_compose_command()


def stop_containers(stack_dir: Path) -> None:
    compose_file = stack_dir / "docker-compose.yml"
    if compose_file.exists():
        command = compose_command("down")
        result = subprocess.run(command, cwd=stack_dir, check=False)
        if result.returncode != 0:
            log("警告：docker compose down 执行失败，请检查 Docker 状态。", "warning", sys.stderr)
        return

    containers = (
        "mpv2-moviepilot",
        "mpv2-postgresql",
        "mpv2-redis",
        "mpv2-qb-media",
        "mpv2-qb-brush",
        "mpv2-emby",
        "mpv2-chinesesubfinder",
    )
    command = ["docker", "rm", "-f", *containers]
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        log("警告：docker rm -f 执行失败，可能是容器不存在或 Docker 未运行。", "warning", sys.stderr)


def run_init_qb(args: argparse.Namespace, host_ip: str, stack_dir: Path, data_dir: Path) -> None:
    script_path = Path(__file__).with_name("init-qb.py")
    if not script_path.exists():
        raise RuntimeError(f"缺少脚本：{script_path}")
    spec = importlib.util.spec_from_file_location("init_qb_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载脚本：{script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "run_init_qb"):
        raise RuntimeError(f"{script_path} 缺少 run_init_qb()")

    log(f"调用 qB 初始化模块：{script_path}")
    module.run_init_qb(
        stack_dir=stack_dir,
        data_dir=data_dir,
        host_ip=host_ip,
    )


def run_init_mpv2(
    args: argparse.Namespace,
    host_ip: str,
    stack_dir: Path,
    set_downloaders: bool,
    set_media_servers: bool,
    apply_templates: bool,
) -> None:
    script_path = Path(__file__).with_name("init-mpv2.py")
    if not script_path.exists():
        raise RuntimeError(f"缺少脚本：{script_path}")
    spec = importlib.util.spec_from_file_location("init_mpv2_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载脚本：{script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run_init_mpv2"):
        raise RuntimeError(f"{script_path} 缺少 run_init_mpv2()")

    log(f"调用 MoviePilot 初始化模块：{script_path}")
    module.run_init_mpv2(
        stack_dir=stack_dir,
        host_ip=host_ip,
        set_downloaders=set_downloaders,
        set_media_servers=set_media_servers,
        apply_templates=apply_templates,
    )


def run_init_emby(args: argparse.Namespace, host_ip: str, stack_dir: Path) -> None:
    script_path = Path(__file__).with_name("init-emby.py")
    if not script_path.exists():
        raise RuntimeError(f"缺少脚本：{script_path}")
    spec = importlib.util.spec_from_file_location("init_emby_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载脚本：{script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run_init_emby"):
        raise RuntimeError(f"{script_path} 缺少 run_init_emby()")

    log(f"调用 Emby 初始化模块：{script_path}")
    module.run_init_emby(
        stack_dir=stack_dir,
        host_ip=host_ip,
        timeout=90,
    )


def check_emby_env_before_init(stack_dir: Path) -> None:
    env_path = stack_dir / ".env"
    env_values = read_env_file(env_path)
    missing = [key for key in ("EMBY_USER", "EMBY_PASSWORD") if not env_values.get(key)]
    if missing:
        raise RuntimeError(f"初始化 Emby 前必须先在 .env 配置：{', '.join(missing)}")
    if not env_values.get("EMBY_API_KEY"):
        log("提示：当前 .env 未发现 EMBY_API_KEY，初始化时会尝试自动创建并写回。", "warning")


def set_init_flag(stack_dir: Path, key: str, value: bool) -> None:
    env_path = stack_dir / ".env"
    update_env_file(
        env_path,
        {key: "true" if value else "false"},
        log_func=lambda msg: log(msg, "success"),
    )


def safe_set_init_flag(stack_dir: Path, key: str, value: bool) -> None:
    try:
        set_init_flag(stack_dir, key, value)
    except Exception as exc:
        log(f"写入 {key} 失败：{exc}", "warning", sys.stderr)


def apply_cli_env_overrides(args: argparse.Namespace, stack_dir: Path) -> None:
    updates: dict[str, str] = {}
    if args.password:
        updates.update(
            {
                "MOVIEPILOT_PASSWORD": args.password,
                "QB_MEDIA_PASSWORD": args.password,
                "QB_BRUSH_PASSWORD": args.password,
            }
        )
    if args.moviepilot_password:
        updates["MOVIEPILOT_PASSWORD"] = args.moviepilot_password
    if args.qb_media_password:
        updates["QB_MEDIA_PASSWORD"] = args.qb_media_password
    if args.qb_brush_password:
        updates["QB_BRUSH_PASSWORD"] = args.qb_brush_password
    if args.github_token:
        updates["GITHUB_TOKEN"] = args.github_token

    if not updates:
        return

    update_env_file(
        stack_dir / ".env",
        updates,
        log_func=lambda msg: log(msg, "success"),
    )


def check_init_flags_for_mpv2(stack_dir: Path) -> None:
    env_path = stack_dir / ".env"
    env_values = read_env_file(env_path)
    # Backward compatible read: prefer *_INITIALED, fallback to legacy *-INITIALED.
    qb_ok = (
        env_values.get("QB_INITIALED", "").lower() == "true"
        or env_values.get("QB-INITIALED", "").lower() == "true"
    )
    emby_ok = (
        env_values.get("EMBY_INITIALED", "").lower() == "true"
        or env_values.get("EMBY-INITIALED", "").lower() == "true"
    )
    if not qb_ok or not emby_ok:
        raise RuntimeError(
            "调用 --init-mpv2 前必须满足 QB_INITIALED=true 且 EMBY_INITIALED=true。"
        )


def clean_generated_paths(stack_dir: Path, data_dir: Path) -> None:
    for path in build_clean_paths(stack_dir, data_dir):
        log(f"开始删除 {path} ...", "warning")
        if not path.exists():
            log(f"skip missing: {path}", "warning")
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        log(f"removed: {path}", "success")
    log("删除媒体库目录 ...", "warning")
    media_path = data_dir / "media"
    if not media_path.exists():
        log(f"跳过删除媒体库目录，目录不存在：{media_path}", "warning")
        return

    confirmation = input(f"是否删除媒体库目录 {media_path}？输入 Y 确认删除，直接回车默认不删除：[N] ").strip()
    if confirmation.lower() != "y":
        log(f"跳过删除媒体库目录：{media_path}", "success")
        return

    try:
        for seconds in range(5, 0, -1):
            log(f"{seconds}s 后删除媒体库目录：{media_path}。按 Ctrl+C 取消。", "warning")
            time.sleep(1)
    except KeyboardInterrupt:
        log(f"已取消删除媒体库目录：{media_path}", "warning")
        return

    if media_path.is_dir():
        shutil.rmtree(media_path)
    else:
        media_path.unlink()
    log(f"removed: {media_path}", "success")

def main() -> int:
    args = parse_args()
    ensure_docker_environment()
    stack_dir = Path(args.stack_dir).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve()

    if args.clean:
        stop_containers(stack_dir)
        clean_generated_paths(stack_dir, data_dir)
        log("清理完成。", "success")
        return 0

    if args.stop:
        stop_containers(stack_dir)
        log("容器已停止，未删除任何文件。", "success")
        return 0

    host_ip = resolve_host_ip(args)
    log(f"使用主机 IP: {host_ip}")
    if args.init_qb:
        apply_cli_env_overrides(args, stack_dir)
        try:
            run_init_qb(args, host_ip, stack_dir, data_dir)
            set_init_flag(stack_dir, "QB_INITIALED", True)
        except Exception:
            safe_set_init_flag(stack_dir, "QB_INITIALED", False)
            raise
        return 0

    if args.init_emby:
        apply_cli_env_overrides(args, stack_dir)
        log(f"使用主机 IP: {host_ip}")
        log("请先在 Emby Web 页面完成初始化向导并创建管理员账户，再执行 --init-emby。", "warning")
        log("请确认 .env 已配置 EMBY_USER 和 EMBY_PASSWORD，EMBY_API_KEY 可留空让脚本自动尝试写入。", "warning")
        check_emby_env_before_init(stack_dir)
        try:
            run_init_emby(args, host_ip, stack_dir)
            set_init_flag(stack_dir, "EMBY_INITIALED", True)
        except Exception:
            safe_set_init_flag(stack_dir, "EMBY_INITIALED", False)
            raise
        return 0

    if args.init_mpv2:
        apply_cli_env_overrides(args, stack_dir)
        check_init_flags_for_mpv2(stack_dir)
        host_ip = resolve_host_ip(args)
        log(f"使用主机 IP: {host_ip}")
        run_init_mpv2(args, host_ip, stack_dir, True, True, True)
        return 0

    for path, uid, gid, mode in build_directory_specs(stack_dir, data_dir, args.puid, args.pgid):
        ensure_directory(path, uid=uid, gid=gid, mode=mode)

    log(
        write_file(
            stack_dir / ".env",
            env_text(args, stack_dir, data_dir),
            args.force,
            puid=args.puid,
            pgid=args.pgid,
            mode=FILE_MODE_SECRET,
        ),
        "success",
    )
    log(
        write_file(
            stack_dir / "docker-compose.yml",
            COMPOSE_YML,
            args.force,
            puid=args.puid,
            pgid=args.pgid,
            mode=FILE_MODE_NORMAL,
        ),
        "success",
    )

    log(
        f"目录权限已设置为 PUID={args.puid} PGID={args.pgid} "
        f"(目录 {oct(DIR_MODE)}，普通文件 {oct(FILE_MODE_NORMAL)}，.env {oct(FILE_MODE_SECRET)}；"
        f"PostgreSQL/Redis 数据目录所有者 999:999)。",
        "success",
    )

    log("目录和部署文件已准备完成。", "success")
    compose_bin = " ".join(detect_compose_command())
    log(f"下一步：cd {stack_dir} && {compose_bin} up -d")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as exc:
        log(f"错误：{exc}", "error", sys.stderr)
        raise SystemExit(1)
