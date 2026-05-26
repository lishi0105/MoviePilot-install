#!/usr/bin/env python3
"""Shared logging helpers."""

from __future__ import annotations

import sys
import time

COLORS = {
    "info": "\033[36m",
    "success": "\033[32m",
    "warning": "\033[33m",
    "error": "\033[31m",
    "reset": "\033[0m",
}


def log(message: str, level: str = "info", stream=None) -> None:
    stream = stream or sys.stdout
    color = COLORS.get(level, COLORS["info"])
    reset = COLORS["reset"]
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{color}[{timestamp}] {message}{reset}", file=stream)
