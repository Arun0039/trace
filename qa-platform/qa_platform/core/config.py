"""Shared process configuration for the QA Platform."""

from __future__ import annotations

import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"


def load_environment_file(env_file: Path = ENV_FILE) -> None:
    """Load missing environment values from the local .env file."""
    if not env_file.is_file():
        return
    for line_number, line in enumerate(env_file.read_text(encoding="utf-8").splitlines(), 1):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        key, separator, value = entry.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid environment entry in .env at line {line_number}.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def database_path() -> Path:
    """Return the configured database path without creating or modifying it."""
    return Path(os.environ.get("QA_PLATFORM_DB", ROOT / "qa_platform.sqlite3"))


def server_address() -> tuple[str, int]:
    """Read and validate the HTTP bind address and port."""
    host = os.environ.get("QA_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("QA_PORT", "8001"))
    except ValueError as exc:
        raise ValueError("QA_PORT must be an integer between 0 and 65535.") from exc
    if not 0 <= port <= 65535:
        raise ValueError("QA_PORT must be an integer between 0 and 65535.")
    return host, port


load_environment_file()
