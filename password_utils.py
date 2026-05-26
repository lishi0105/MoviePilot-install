"""Unified password validation and generation for the media stack."""

from __future__ import annotations

import argparse
import re
import secrets
import string
from collections.abc import Callable, Mapping, Sequence
from typing import NoReturn

# Shell-safe charset shared by MoviePilot / qB / CSF / Postgres / Redis.
STACK_PASSWORD_ALLOWED = re.compile(r"^[A-Za-z0-9!@#%*-]+$")
STACK_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#%-*"
STACK_PASSWORD_CHARS_DESC = "A-Za-z0-9!@#%-*"

STACK_ENV_PASSWORD_KEYS: tuple[str, ...] = (
    "MOVIEPILOT_PASSWORD",
    "QB_MEDIA_PASSWORD",
    "QB_BRUSH_PASSWORD",
    "CSF_PASSWORD",
    "POSTGRES_PASSWORD",
    "REDIS_PASSWORD",
)

ENV_PASSWORD_LABELS: dict[str, str] = {
    "MOVIEPILOT_PASSWORD": "MoviePilot 密码",
    "QB_MEDIA_PASSWORD": "qB-media 密码",
    "QB_BRUSH_PASSWORD": "qB-brush 密码",
    "CSF_PASSWORD": "ChineseSubFinder 密码",
    "POSTGRES_PASSWORD": "PostgreSQL 密码",
    "REDIS_PASSWORD": "Redis 密码",
}

CLI_PASSWORD_LABELS: dict[str, str] = {
    "password": "统一密码",
    "moviepilot_password": "MoviePilot 密码",
    "qb_media_password": "qB-media 密码",
    "qb_brush_password": "qB-brush 密码",
    "csf_password": "ChineseSubFinder 密码",
    "postgres_password": "PostgreSQL 密码",
    "redis_password": "Redis 密码",
}

CLI_PASSWORD_SPECS: tuple[tuple[str, str], ...] = (
    ("password", "--password"),
    ("moviepilot_password", "--moviepilot-password"),
    ("qb_media_password", "--qb-media-password"),
    ("qb_brush_password", "--qb-brush-password"),
    ("csf_password", "--csf-password"),
    ("postgres_password", "--postgres-password"),
    ("redis_password", "--redis-password"),
)

INDIVIDUAL_CLI_PASSWORD_ATTRS: tuple[str, ...] = (
    "moviepilot_password",
    "qb_media_password",
    "qb_brush_password",
    "csf_password",
)

INIT_QB_ENV_KEYS: tuple[str, ...] = ("QB_MEDIA_PASSWORD", "QB_BRUSH_PASSWORD")
INIT_MPV2_ENV_KEYS: tuple[str, ...] = (
    "MOVIEPILOT_PASSWORD",
    "QB_MEDIA_PASSWORD",
    "QB_BRUSH_PASSWORD",
)
INIT_CSF_ENV_KEYS: tuple[str, ...] = ("CSF_PASSWORD",)


def validate_stack_password(password: str, *, label: str = "密码") -> None:
    if not password:
        raise ValueError(f"{label}不能为空。")
    if not STACK_PASSWORD_ALLOWED.fullmatch(password):
        raise ValueError(
            f"{label}仅支持英文大小写、数字及 !@#%-*，不支持 _、$ 等其它符号。"
        )


def stack_random_secret(length: int = 16) -> str:
    return "".join(secrets.choice(STACK_PASSWORD_ALPHABET) for _ in range(length))


def validate_password_map(passwords: Mapping[str, str]) -> None:
    for key, value in passwords.items():
        if not value:
            continue
        validate_stack_password(value, label=ENV_PASSWORD_LABELS.get(key, key))


def validate_env_passwords(env: Mapping[str, str], keys: Sequence[str]) -> None:
    passwords = {key: env.get(key, "").strip() for key in keys if env.get(key, "").strip()}
    validate_password_map(passwords)


def validate_cli_password_args(
    args: argparse.Namespace,
    error: Callable[[str], NoReturn],
) -> None:
    individual_values = [getattr(args, attr, None) for attr in INDIVIDUAL_CLI_PASSWORD_ATTRS]
    if getattr(args, "password", None) and any(individual_values):
        error(
            "--password cannot be used with --moviepilot-password, --qb-media-password, "
            "--qb-brush-password, or --csf-password"
        )

    for attr, flag in CLI_PASSWORD_SPECS:
        value = getattr(args, attr, None)
        if not value:
            continue
        try:
            validate_stack_password(value, label=CLI_PASSWORD_LABELS.get(attr, flag))
        except ValueError as exc:
            error(f"{flag}: {exc}")


def build_install_password_values(args: argparse.Namespace) -> dict[str, str]:
    unified = getattr(args, "password", None)
    values = {
        "MOVIEPILOT_PASSWORD": unified or args.moviepilot_password or stack_random_secret(),
        "POSTGRES_PASSWORD": args.postgres_password or stack_random_secret(),
        "REDIS_PASSWORD": args.redis_password or stack_random_secret(),
        "QB_MEDIA_PASSWORD": unified or args.qb_media_password or stack_random_secret(),
        "QB_BRUSH_PASSWORD": unified or args.qb_brush_password or stack_random_secret(),
    }
    if unified:
        values["CSF_PASSWORD"] = unified
    elif getattr(args, "csf_password", None):
        values["CSF_PASSWORD"] = args.csf_password
    validate_password_map(values)
    return values
