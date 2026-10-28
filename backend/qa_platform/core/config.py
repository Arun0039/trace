"""Shared process configuration for the QA Platform."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent
DATA_ROOT = REPOSITORY_ROOT / "qa-platform"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
ENV_FILE = DATA_ROOT / ".env"


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
    override = os.environ.get("QA_PLATFORM_DB") or os.environ.get("SQLITE_PATH")
    if not override:
        return DATA_ROOT / "qa_platform.sqlite3"
    path = Path(override)
    return path if path.is_absolute() else DATA_ROOT / path


def database_connection() -> Path | str:
    """Return the configured PostgreSQL URL or local SQLite path."""
    url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("QA_PLATFORM_DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or ""
    ).strip()
    if not url:
        return database_path()
    if not url.startswith(("postgres://", "postgresql://")):
        raise ValueError(
            "DATABASE_URL, QA_PLATFORM_DATABASE_URL, or POSTGRES_URL must use "
            "the postgres:// or postgresql:// scheme."
        )
    parsed = urlsplit(url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        sslmode = parse_qs(parsed.query).get("sslmode", [""])[0].lower()
        if sslmode not in {"require", "verify-ca", "verify-full"}:
            raise ValueError(
                "Remote PostgreSQL URLs must set sslmode=require or stronger."
            )
    # Fix postgres:// to postgresql:// if needed for compatibility
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def server_address() -> tuple[str, int]:
    """Read and validate the HTTP bind address and port."""
    host = (
        os.environ.get("HOST")
        or os.environ.get("QA_HOST")
        or os.environ.get("APP_HOST")
        or "127.0.0.1"
    )
    port_str = (
        os.environ.get("PORT")
        or os.environ.get("QA_PORT")
        or os.environ.get("APP_PORT")
        or "8001"
    )
    try:
        port = int(port_str)
    except ValueError as exc:
        raise ValueError(
            "PORT, QA_PORT, or APP_PORT must be an integer between 0 and 65535."
        ) from exc
    if not 0 <= port <= 65535:
        raise ValueError(
            "PORT, QA_PORT, or APP_PORT must be an integer between 0 and 65535."
        )
    return host, port


def cors_origins() -> frozenset[str]:
    """Return explicitly allowed browser origins for a separately served frontend."""
    origins_str = (
        os.environ.get("FRONTEND_URL")
        or os.environ.get("CORS_ORIGINS")
        or os.environ.get("QA_CORS_ORIGINS")
        or ""
    )
    return frozenset(
        origin.strip().rstrip("/")
        for origin in origins_str.split(",")
        if origin.strip()
    )


load_environment_file()
