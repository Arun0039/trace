"""Provider configuration, credential encryption, and Vercel AI SDK bridge."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qa_platform.core.config import BACKEND_ROOT

ENCRYPTION_KEY_ENV = "QA_AI_ENCRYPTION_KEY"
MAX_AI_RESPONSE_BYTES = 4 * 1024 * 1024
AI_TIMEOUT_SECONDS = 600
DEFAULT_WORKSPACE_ID = 1
PROVIDERS = {
    "local": {
        "name": "Local deterministic",
        "requires_api_key": False,
        "requires_base_url": False,
        "default_model": "deterministic",
        "default_base_url": "",
    },
    "ollama": {
        "name": "Ollama",
        "requires_api_key": False,
        "requires_base_url": True,
        "default_model": "",
        "default_base_url": "http://127.0.0.1:11434/v1",
    },
    "google": {
        "name": "Google Gemini",
        "requires_api_key": True,
        "requires_base_url": False,
        "default_model": "gemini-2.5-flash",
        "default_base_url": "",
    },
    "openai": {
        "name": "OpenAI",
        "requires_api_key": True,
        "requires_base_url": False,
        "default_model": "gpt-4o-mini",
        "default_base_url": "",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "requires_api_key": True,
        "requires_base_url": False,
        "default_model": "claude-3-5-sonnet-latest",
        "default_base_url": "",
    },
    "openai-compatible": {
        "name": "Other (OpenAI-compatible)",
        "requires_api_key": True,
        "requires_base_url": True,
        "default_model": "",
        "default_base_url": "",
    },
}


def _encryption_key() -> bytes:
    value = os.environ.get(ENCRYPTION_KEY_ENV, "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError(
            f"{ENCRYPTION_KEY_ENV} must be set to a 64-character hexadecimal key."
        )
    return bytes.fromhex(value)


def encrypt_api_key(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("Provider API key must contain 1 to 4096 characters.")
    if any(ord(character) < 32 for character in value):
        raise ValueError("Provider API key contains unsupported control characters.")
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(_encryption_key()).encrypt(nonce, value.encode("utf-8"), None)
    return nonce + encrypted


def decrypt_api_key(value: bytes | None) -> str:
    if not value:
        return ""
    if not isinstance(value, bytes) or len(value) <= 12:
        raise ValueError("Stored provider credentials are invalid.")
    try:
        plaintext = AESGCM(_encryption_key()).decrypt(value[:12], value[12:], None)
    except (InvalidTag, ValueError) as exc:
        raise ValueError(
            "Could not decrypt stored provider credentials. Check that "
            f"{ENCRYPTION_KEY_ENV} has not changed."
        ) from exc
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Stored provider credentials are not valid text.") from exc


def ollama_supported(tenant_id: int) -> bool:
    return tenant_id == DEFAULT_WORKSPACE_ID


def allowed_providers(tenant_id: int) -> set[str]:
    providers = set(PROVIDERS)
    if not ollama_supported(tenant_id):
        providers.discard("ollama")
    return providers


def validate_provider(value: Any, tenant_id: int = DEFAULT_WORKSPACE_ID) -> str:
    if not isinstance(value, str) or value not in allowed_providers(tenant_id):
        if value == "ollama":
            raise ValueError(
                "Ollama is only available in the Default Workspace. "
                "Choose Google Gemini, OpenAI, Anthropic Claude, or an OpenAI-compatible provider."
            )
        raise ValueError(
            "Choose Local deterministic, Google Gemini, OpenAI, Anthropic Claude, "
            "or OpenAI-compatible."
        )
    return value


def validate_model(value: Any, provider: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Model name must be text.")
    model = value.strip()
    if not model and provider in {"google", "ollama", "openai", "anthropic", "openai-compatible"}:
        raise ValueError("Enter a model name for the selected provider.")
    if len(model) > 200 or (model and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+-]*", model)):
        raise ValueError("Model name contains unsupported characters.")
    return model or "deterministic"


def validate_base_url(value: Any, provider: str) -> str:
    if provider not in {"ollama", "openai-compatible"}:
        return ""
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValueError("Enter the provider API base URL.")
    raw = value.strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Provider base URL must be valid.") from exc
    host = (parsed.hostname or "").lower()
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.port is not None and (port is None or not 1 <= port <= 65535))
        or (parsed.scheme == "http" and host not in local_hosts)
        or (provider == "ollama" and host not in local_hosts)
    ):
        raise ValueError(
            "Provider URL must use HTTPS, except loopback HTTP URLs; Ollama must use a local URL."
        )
    path = parsed.path.rstrip("/")
    if provider == "ollama" and path not in {"", "/v1"}:
        raise ValueError("Ollama base URL must be its local server URL, optionally ending in /v1.")
    if provider == "ollama" and not path:
        path = "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _normalize_tenant_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("Workspace is invalid.")
    return value


def ai_module_enabled(db: sqlite3.Connection, tenant_id: int = 1) -> bool:
    tenant_id = _normalize_tenant_id(tenant_id)
    row = db.execute(
        "SELECT enabled FROM tenant_ai_settings WHERE tenant_id=?", (tenant_id,)
    ).fetchone()
    if row is None:
        return tenant_id == DEFAULT_WORKSPACE_ID
    return bool(row["enabled"])


def set_ai_module_enabled(
    db: sqlite3.Connection, tenant_id: int = 1, enabled: bool = False
) -> dict[str, Any]:
    tenant_id = _normalize_tenant_id(tenant_id)
    if not isinstance(enabled, bool):
        raise ValueError("Choose whether to enable the AI module.")
    selected = db.execute(
        "SELECT provider FROM tenant_ai_settings WHERE tenant_id=?", (tenant_id,)
    ).fetchone()
    provider = selected["provider"] if selected and selected["provider"] else "google"
    if provider not in allowed_providers(tenant_id):
        provider = "google"
    db.execute(
        """INSERT INTO tenant_ai_settings(tenant_id,provider,enabled,updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(tenant_id) DO UPDATE SET enabled=excluded.enabled,
        updated_at=excluded.updated_at""",
        (tenant_id, provider, 1 if enabled else 0, timestamp()),
    )
    return provider_settings(db, authenticated=True, tenant_id=tenant_id)


def provider_settings(
    db: sqlite3.Connection, authenticated: bool = False, tenant_id: int = 1
) -> dict[str, Any]:
    tenant_id = _normalize_tenant_id(tenant_id)
    selected = db.execute(
        "SELECT provider FROM tenant_ai_settings WHERE tenant_id=?", (tenant_id,)
    ).fetchone()
    provider = selected["provider"] if selected and selected["provider"] else "google"
    if provider not in allowed_providers(tenant_id):
        provider = "google"
    rows = {
        row["provider"]: dict(row)
        for row in db.execute(
            """SELECT provider,model,base_url,api_key_ciphertext
            FROM tenant_ai_provider_configs WHERE tenant_id=?""",
            (tenant_id,),
        )
    }
    options = []
    for provider_id, definition in PROVIDERS.items():
        if provider_id not in allowed_providers(tenant_id):
            continue
        row = rows.get(provider_id, {})
        model = row.get("model") or definition["default_model"]
        base_url = row.get("base_url") or definition["default_base_url"]
        key_configured = bool(row.get("api_key_ciphertext"))
        configured = (
            provider_id == "local"
            or (
                bool(model)
                and (not definition["requires_base_url"] or bool(base_url))
                and (not definition["requires_api_key"] or key_configured)
            )
        )
        options.append({
            "id": provider_id,
            "name": definition["name"],
            "model": model,
            "base_url": base_url,
            "requires_api_key": definition["requires_api_key"],
            "requires_base_url": definition["requires_base_url"],
            "api_key_configured": key_configured,
            "configured": configured,
        })
    current = next(item for item in options if item["id"] == provider)
    needs_encryption = current["api_key_configured"] or PROVIDERS[provider]["requires_api_key"]
    try:
        _encryption_key()
        encryption_configured = True
        encryption_error = ""
    except ValueError as exc:
        encryption_configured = False
        encryption_error = str(exc)
    return {
        **current,
        "tenant_id": tenant_id,
        "enabled": ai_module_enabled(db, tenant_id),
        "ollama_supported": ollama_supported(tenant_id),
        "provider": provider,
        "configured": current["configured"] and (encryption_configured or not needs_encryption),
        "authenticated": authenticated,
        "admin_configured": len(os.environ.get("QA_ADMIN_PASSWORD", "").strip()) >= 12,
        "encryption_configured": encryption_configured,
        "configuration_error": encryption_error if needs_encryption and not encryption_configured else "",
        "providers": options,
    }


def save_provider_settings(
    db: sqlite3.Connection, data: dict[str, Any], tenant_id: int = 1
) -> dict[str, Any]:
    tenant_id = _normalize_tenant_id(tenant_id)
    if not ai_module_enabled(db, tenant_id):
        raise ValueError(
            "The AI module is disabled for this workspace. "
            "Ask a workspace administrator to enable it."
        )
    provider = validate_provider(data.get("provider"), tenant_id)
    model = validate_model(data.get("model", ""), provider)
    base_url = validate_base_url(data.get("base_url", ""), provider)
    api_key = data.get("api_key", "")
    clear_api_key = data.get("clear_api_key", False)
    if not isinstance(api_key, str) or not isinstance(clear_api_key, bool):
        raise ValueError("Provider credentials must be text and the clear-key option must be boolean.")
    if provider == "local" and api_key:
        raise ValueError("Local deterministic mode does not use an API key.")
    if len(api_key) > 4096 or any(ord(character) < 32 for character in api_key):
        raise ValueError("Provider API key is invalid.")

    previous = db.execute(
        """SELECT api_key_ciphertext FROM tenant_ai_provider_configs
        WHERE tenant_id=? AND provider=?""",
        (tenant_id, provider),
    ).fetchone()
    encrypted_key = previous["api_key_ciphertext"] if previous else None
    if clear_api_key:
        encrypted_key = None
    if api_key:
        encrypted_key = encrypt_api_key(api_key)

    db.execute(
        """INSERT INTO tenant_ai_provider_configs
        (tenant_id,provider,model,base_url,api_key_ciphertext,updated_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(tenant_id,provider) DO UPDATE SET model=excluded.model,
        base_url=excluded.base_url,api_key_ciphertext=excluded.api_key_ciphertext,
        updated_at=excluded.updated_at""",
        (tenant_id, provider, model, base_url, encrypted_key, timestamp()),
    )
    db.execute(
        """INSERT INTO tenant_ai_settings(tenant_id,provider,enabled,updated_at)
        VALUES(?,?,1,?)
        ON CONFLICT(tenant_id) DO UPDATE SET provider=excluded.provider,
        updated_at=excluded.updated_at""",
        (tenant_id, provider, timestamp()),
    )
    return provider_settings(db, authenticated=True, tenant_id=tenant_id)


def active_provider_config(db: sqlite3.Connection, tenant_id: int = 1) -> dict[str, str]:
    tenant_id = _normalize_tenant_id(tenant_id)
    if not ai_module_enabled(db, tenant_id):
        raise ValueError(
            "The AI module is disabled for this workspace. "
            "Ask a workspace administrator to enable it."
        )
    settings = provider_settings(db, tenant_id=tenant_id)
    provider = validate_provider(settings["provider"], tenant_id)
    definition = PROVIDERS[provider]
    if (
        (definition["requires_api_key"] or settings["api_key_configured"])
        and not settings["encryption_configured"]
    ):
        raise ValueError(settings["configuration_error"])
    model = validate_model(settings["model"], provider)
    base_url = validate_base_url(settings["base_url"], provider)
    row = db.execute(
        """SELECT api_key_ciphertext FROM tenant_ai_provider_configs
        WHERE tenant_id=? AND provider=?""",
        (tenant_id, provider),
    ).fetchone()
    api_key = decrypt_api_key(row["api_key_ciphertext"] if row else None)
    if definition["requires_api_key"] and not api_key:
        raise ValueError(f"Add the API key for {definition['name']} in Settings → AI Module.")
    if definition["requires_base_url"] and not base_url:
        raise ValueError(f"Set the API base URL for {definition['name']} in Settings → AI Module.")
    if provider != "local" and not model:
        raise ValueError(f"Set the model for {definition['name']} in Settings → AI Module.")
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
    }


def generate_text(
    configuration: dict[str, str],
    system: str,
    prompt: str,
    temperature: float,
    response_format: str | None = None,
) -> str:
    provider = configuration["provider"]
    if provider == "local":
        raise ValueError("Local deterministic mode does not use a remote AI model.")
    if response_format not in {None, "json"}:
        raise ValueError("AI response format must be JSON when specified.")
    node = shutil.which("node")
    if not node:
        raise ValueError(
            "AI analysis requires Node.js 22 or later. Install Node.js, then run "
            "`npm ci` in the backend folder."
        )

    request = {
        "provider": provider,
        "model": configuration["model"],
        "base_url": configuration["base_url"],
        "apiKey": configuration["api_key"],
        "system": system,
        "prompt": prompt,
        "temperature": temperature,
        **({"response_format": response_format} if response_format else {}),
    }
    try:
        result = subprocess.run(
            [node, str(BACKEND_ROOT / "ai_provider.mjs")],
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=AI_TIMEOUT_SECONDS,
            cwd=BACKEND_ROOT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"{PROVIDERS[provider]['name']} did not finish within {AI_TIMEOUT_SECONDS} seconds."
        ) from exc
    except OSError as exc:
        raise ValueError(f"Could not start the Vercel AI SDK provider: {exc}") from exc

    if len(result.stdout.encode("utf-8")) > MAX_AI_RESPONSE_BYTES:
        raise ValueError(f"{PROVIDERS[provider]['name']} response exceeded the 4 MB safety limit.")
    try:
        response = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("The Vercel AI SDK returned an invalid response.") from exc
    if isinstance(response, dict) and response.get("error"):
        message = response["error"]
        raise ValueError(
            message[:1000] if isinstance(message, str)
            else f"{PROVIDERS[provider]['name']} request failed."
        )
    if result.returncode != 0 or not isinstance(response, dict):
        raise ValueError("The Vercel AI SDK provider failed without a valid response.")
    text = response.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{PROVIDERS[provider]['name']} returned an empty response.")
    return text


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
