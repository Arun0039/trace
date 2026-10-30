"""Local-first QA test management MVP. Uses only the Python standard library."""

from __future__ import annotations

import csv
import errno
import hashlib
import ipaddress
import hmac
import io
import json
import math
import mimetypes
import os
import re
import sqlite3
import base64
import binascii
from collections import deque
import shutil
import secrets
import struct
import sys
import threading
import time
import traceback
import unicodedata
import uuid
import webbrowser
import zlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from qa_platform.core.config import (
    DATA_ROOT,
    FRONTEND_ROOT,
    cors_origins,
    database_connection,
    server_address,
    workspace_storage_quota_bytes,
)
from qa_platform.core.database import (
    DATABASE_ERRORS,
    DATABASE_INTEGRITY_ERRORS,
    close_database_pools,
    connect_database,
)
from qa_platform.core.product_owner import upgrade as upgrade_product_owner_schema
from qa_platform.core.trials import (
    PERMANENT_WORKSPACE_ID,
    TRIAL_EXPIRY_REASON,
    TRIAL_DURATION_DAYS,
    TRIAL_PLAN,
    reactivate_trial,
    trial_end,
    utc_timestamp,
    workspace_access,
)
from application_zip import (
    MAX_ARCHIVE_BYTES,
    analyze_application_zip,
    source_code_excerpts,
)
from ai_provider import (
    active_provider_config,
    generate_text as generate_provider_text,
    provider_settings,
    save_provider_settings,
    set_ai_module_enabled,
    validate_encryption_key_configuration,
)
from ollama_integration import (
    MAX_AD_HOC_INPUT_LENGTH,
    classify_requirements,
    classify_requirements_locally,
    validate_brd_analysis,
    generate_brd_analysis,
    generate_test_cases,
    generate_ad_hoc_test_cases,
    AD_HOC_TEST_CATEGORIES,
    brd_analysis_markdown,
    fallback_brd_analysis,
    validate_generated_requirements,
    validate_requirement_coverage,
)
from requirement_documents import (
    MAX_DOCUMENT_BYTES,
    extract_requirement_document,
    normalize_requirement_module,
)
from test_case_generator import MAX_GENERATED_CASES, TEST_TYPES, generate_project_cases


DB_PATH = database_connection()
MAX_BODY = 27 * 1024 * 1024
MAX_OPENAPI_SIZE = 1_000_000
MAX_EVIDENCE_SIZE = 5 * 1024 * 1024
MAX_ATTACHMENT_SIZE = 5 * 1024 * 1024
MAX_DEVELOPER_REPORT_SIZE = 64 * 1024
try:
    DEVELOPER_ERROR_RETENTION_DAYS = int(
        os.environ.get("QA_DEVELOPER_ERROR_RETENTION_DAYS", "90")
    )
except ValueError as exc:
    raise RuntimeError(
        "QA_DEVELOPER_ERROR_RETENTION_DAYS must be an integer from 1 to 3650."
    ) from exc
if not 1 <= DEVELOPER_ERROR_RETENTION_DAYS <= 3650:
    raise RuntimeError(
        "QA_DEVELOPER_ERROR_RETENTION_DAYS must be an integer from 1 to 3650."
    )
DEVELOPER_ERROR_SOURCES = ("frontend", "backend", "api", "job", "database")
DEVELOPER_ERROR_SEVERITIES = ("debug", "info", "warning", "error", "critical")
DEVELOPER_ERROR_STATUSES = ("open", "acknowledged", "resolved", "ignored")
AUTH_RATE_LIMIT_ATTEMPTS = 10
AUTH_RATE_LIMIT_WINDOW_SECONDS = 15 * 60
AUTH_RATE_LIMIT_MAX_KEYS = 5000
STORAGE_ITEM_KINDS = (
    "projects", "requirements", "test-cases", "test-runs", "defects",
    "analyses", "applications", "exports", "attachments", "evidence", "other",
)
PROCESS_STARTED_AT = time.time()
OPENAPI_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
STATUSES = ("Not Executed", "In Progress", "Pass", "Fail", "Blocked", "Skipped")
BUG_STATUSES = ("Open", "In Progress", "Resolved", "Closed")
CUSTOM_FIELD_MODULES = {
    "projects": ("projects", "Project"),
    "requirements": ("requirements", "Requirement"),
    "test-cases": ("test_cases", "Test Case"),
    "bugs": ("bugs", "Defect"),
}
CUSTOM_FIELD_TYPES = ("text", "number", "dropdown")
ATTACHMENT_TYPES = {
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".json": "application/json",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".txt": "text/plain",
    ".webp": "image/webp",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
}
PERMISSION_MODULES = {
    "projects": ("view", "create", "edit", "delete"),
    "requirements": ("view", "create", "edit", "delete"),
    "test-cases": ("view", "create", "edit", "delete"),
    "applications": ("view", "create", "edit", "delete"),
    "api-docs": ("view", "create", "edit", "delete"),
    "app-analysis": ("view", "create", "edit", "delete"),
    "bugs": ("view", "create", "edit", "delete"),
    "users": ("view", "create", "update", "edit", "delete", "activate", "deactivate", "change_role"),
    "teams": ("view", "create", "edit", "delete", "manage-members"),
    "roles": ("view", "create", "edit", "delete", "manage-permissions"),
    "reports": ("view",),
    "profile": ("view", "edit"),
    "storage": ("view", "delete"),
    "developer": ("view", "manage", "delete"),
}
ALL_PERMISSIONS = [
    f"{module}.{action}"
    for module, actions in PERMISSION_MODULES.items()
    for action in actions
]
WORKSPACE_OWNER_PERMISSIONS = [
    permission for permission in ALL_PERMISSIONS
    if not permission.startswith("developer.")
]
DEFAULT_ROLE_PERMISSIONS = {
    "Global Admin": ["*"],
    "Workspace Owner": WORKSPACE_OWNER_PERMISSIONS,
    "Developer": ["developer.view", "developer.manage"],
    "User": [
        "projects.view", "projects.create", "projects.edit",
        "requirements.view", "requirements.create", "requirements.edit",
        "test-cases.view", "test-cases.create", "test-cases.edit",
        "applications.view", "applications.create", "applications.edit",
        "api-docs.view", "api-docs.create", "api-docs.edit",
        "app-analysis.view", "app-analysis.create",
        "bugs.view", "bugs.create", "bugs.edit", "reports.view",
        "teams.view", "profile.view", "profile.edit",
    ],
    "QA Engineer": [
        "projects.view", "projects.create", "projects.edit",
        "requirements.view", "requirements.create", "requirements.edit",
        "test-cases.view", "test-cases.create", "test-cases.edit",
        "applications.view", "applications.create", "applications.edit",
        "api-docs.view", "api-docs.create", "api-docs.edit",
        "app-analysis.view", "app-analysis.create",
        "bugs.view", "bugs.create", "bugs.edit", "reports.view",
        "teams.view", "profile.view", "profile.edit",
    ],
    "Viewer": [
        "projects.view", "requirements.view", "test-cases.view",
        "applications.view", "api-docs.view", "app-analysis.view",
        "bugs.view", "reports.view", "profile.view", "profile.edit",
    ],
}
TENANT_ADMIN_EMAIL = "global-admin@local.qa"


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_date_filter(query, name: str) -> str:
    value = query.get(name, [""])[0].strip()
    if not value:
        return ""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"Invalid {name.replace('_', ' ')} date; use YYYY-MM-DD.")
    return value


def normalize_workspace_name(name: str) -> str:
    normalized = " ".join(name.split())
    if not normalized or len(normalized) > 80:
        raise ValueError("Workspace name must be between 1 and 80 characters.")
    if not any(character.isalnum() for character in normalized):
        raise ValueError("Workspace name must include a letter or number.")
    return normalized


def workspace_slug_base(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:70].rstrip("-") or "workspace"


def create_workspace(db, name: str, now: str) -> tuple[int, str]:
    base_slug = workspace_slug_base(name)
    suffix = 1
    while True:
        slug = base_slug if suffix == 1 else f"{base_slug}-{suffix}"
        inserted = db.execute(
            """INSERT OR IGNORE INTO tenants
            (name,slug,status,created_at,updated_at,public_tenant_id,public_workspace_id)
            VALUES(?,?,'Active',?,?,?,?)""",
            (name, slug, now, now, str(uuid.uuid4()), str(uuid.uuid4())),
        )
        if inserted.rowcount == 1:
            row = db.execute("SELECT id FROM tenants WHERE slug=?", (slug,)).fetchone()
            return row["id"], slug
        suffix += 1


def active_global_admin_count(db, tenant_id=None) -> int:
    tenant_clause = " AND m.tenant_id=?" if tenant_id is not None else ""
    parameters = (tenant_id,) if tenant_id is not None else ()
    return db.execute(
        """SELECT COUNT(DISTINCT u.id) FROM tenant_memberships m
        JOIN users u ON u.id=m.user_id JOIN roles r ON r.id=m.role_id
        WHERE r.name IN ('Admin','Global Admin')
        AND m.status='Active' AND u.status='Active'""" + tenant_clause,
        parameters,
    ).fetchone()[0]


def lock_global_admin_role(db) -> None:
    # A no-op update serializes final-admin checks on SQLite and PostgreSQL.
    db.execute(
        "UPDATE roles SET updated_at=updated_at WHERE name='Global Admin'"
    )


def integrity_error_is(exc, *identifiers: str) -> bool:
    constraint_name = getattr(getattr(exc, "diag", None), "constraint_name", "")
    message = str(exc)
    return any(identifier == constraint_name or identifier in message for identifier in identifiers)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def audit_event(db, actor_id, action, target_type, target_id, details=None, tenant_id=None):
    if tenant_id is None and actor_id is not None:
        membership = db.execute(
            """SELECT tenant_id FROM tenant_memberships
            WHERE user_id=? AND status='Active' ORDER BY tenant_id LIMIT 1""",
            (actor_id,),
        ).fetchone()
        tenant_id = membership["tenant_id"] if membership else None
    db.execute(
        """INSERT INTO audit_events
        (actor_user_id,actor_label,action,target_type,target_id,tenant_id,details_json,created_at)
        VALUES(?,?,?,?,?,?,?,?)""",
        (
            actor_id, "Global Admin" if actor_id is None else None,
            action, target_type, str(target_id) if target_id is not None else "",
            tenant_id,
            json.dumps(details or {}, ensure_ascii=True), timestamp(),
        ),
    )


def user_public_dict(db, user_id, tenant_id=None):
    membership_sql = """SELECT tenant_id,role_id FROM tenant_memberships
        WHERE user_id=?"""
    membership_params = [user_id]
    if tenant_id is not None:
        membership_sql += " AND tenant_id=?"
        membership_params.append(tenant_id)
    membership_sql += " ORDER BY CASE status WHEN 'Active' THEN 0 ELSE 1 END,tenant_id LIMIT 1"
    membership = db.execute(membership_sql, membership_params).fetchone()
    if not membership:
        return None
    row = db.execute(
        """SELECT         u.id,u.public_user_id,u.first_name,u.last_name,u.email,u.phone,
        CASE WHEN u.status='Active' THEN m.status ELSE u.status END status,u.created_at,
        u.last_login_at,r.id role_id,r.name role_name,m.tenant_id,
        GROUP_CONCAT(t.name, ', ') team_names,GROUP_CONCAT(t.id, ',') team_ids
        FROM users u
        JOIN tenant_memberships m ON m.user_id=u.id AND m.tenant_id=?
        JOIN roles r ON r.id=m.role_id
        LEFT JOIN team_memberships tm ON tm.user_id=u.id
        LEFT JOIN teams t ON t.id=tm.team_id AND t.tenant_id=m.tenant_id
        WHERE u.id=? GROUP BY u.id,m.tenant_id,m.status,r.id,r.name""",
        (membership["tenant_id"], user_id),
    ).fetchone()
    result = dict(row) if row else None
    if result:
        result["team_ids"] = [
            int(item) for item in (result.pop("team_ids") or "").split(",") if item
        ]
    return result


def sync_user_teams(db, user_id, tenant_id, team_ids, actor_id):
    if team_ids is None:
        return
    if not isinstance(team_ids, list):
        raise ValueError("Teams must be provided as a list.")
    normalized = []
    for value in team_ids:
        if isinstance(value, bool):
            raise ValueError("Team IDs must be positive integers.")
        try:
            team_id = int(value)
        except (TypeError, ValueError):
            raise ValueError("Team IDs must be positive integers.")
        if team_id <= 0:
            raise ValueError("Team IDs must be positive integers.")
        if team_id not in normalized:
            normalized.append(team_id)
    available = {
        row["id"] for row in db.execute(
            """SELECT id FROM teams WHERE tenant_id=? AND status='Active'
            AND id IN ({})""".format(",".join("?" for _ in normalized) or "NULL"),
            (tenant_id, *normalized),
        )
    }
    if available != set(normalized):
        raise ValueError("Choose active teams from the user's workspace.")
    previous = [
        row["team_id"] for row in db.execute(
            """SELECT tm.team_id FROM team_memberships tm
            JOIN teams t ON t.id=tm.team_id
            WHERE tm.user_id=? AND t.tenant_id=? ORDER BY tm.team_id""",
            (user_id, tenant_id),
        )
    ]
    for team_id in set(previous) - set(normalized):
        db.execute(
            "DELETE FROM team_memberships WHERE team_id=? AND user_id=?",
            (team_id, user_id),
        )
    for team_id in set(normalized) - set(previous):
        db.execute(
            """INSERT OR IGNORE INTO team_memberships(team_id,user_id,added_at)
            VALUES(?,?,?)""",
            (team_id, user_id, timestamp()),
        )
    if set(previous) != set(normalized):
        audit_event(
            db, actor_id, "user.team_changed", "user", user_id,
            {"previous": previous, "new": sorted(normalized)},
            tenant_id=tenant_id,
        )


def sync_role_permissions(db, role_id, permissions):
    db.execute("DELETE FROM role_permissions WHERE role_id=?", (role_id,))
    for permission in permissions:
        db.execute(
            "INSERT INTO role_permissions(role_id,permission_key) VALUES(?,?)",
            (role_id, permission),
        )


def role_permission_keys(db, role_id):
    return [
        row["permission_key"] for row in db.execute(
            "SELECT permission_key FROM role_permissions WHERE role_id=? ORDER BY permission_key",
            (role_id,),
        )
    ]


def project_storage_dir(storage_root: Path | str, project_id: int) -> Path:
    return Path(storage_root) / "projects" / str(project_id)


def ensure_project_storage(storage_root: Path | str, project_id: int) -> Path:
    project_dir = project_storage_dir(storage_root, project_id)
    for name in ("requirements", "applications", "exports"):
        (project_dir / name).mkdir(parents=True, exist_ok=True)
    return project_dir


def cleanup_record_form_data(db, module_key: str, record_id: int) -> None:
    db.execute(
        "DELETE FROM custom_field_values WHERE module_key=? AND record_id=?",
        (module_key, record_id),
    )
    db.execute(
        "DELETE FROM form_attachments WHERE module_key=? AND record_id=?",
        (module_key, record_id),
    )


def cleanup_project_form_data(db, project_id: int) -> None:
    db.execute(
        """DELETE FROM custom_field_values WHERE
        (module_key='projects' AND record_id=?) OR
        (module_key='requirements' AND record_id IN
            (SELECT id FROM requirements WHERE project_id=?)) OR
        (module_key='test-cases' AND record_id IN
            (SELECT id FROM test_cases WHERE project_id=?)) OR
        (module_key='bugs' AND record_id IN
            (SELECT id FROM bugs WHERE project_id=?))""",
        (project_id, project_id, project_id, project_id),
    )
    db.execute("DELETE FROM form_attachments WHERE project_id=?", (project_id,))



def storage_item_identifier(*parts) -> str:
    value = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def storage_file_kind(relative_path: str) -> str:
    first = Path(relative_path).parts[0] if Path(relative_path).parts else ""
    return {
        "requirements": "requirements",
        "applications": "applications",
        "exports": "exports",
    }.get(first, "other")


def directory_storage_size(path: Path) -> tuple[int, int]:
    total = 0
    files = 0
    if path.is_symlink() or not path.is_dir():
        return 0, 0
    for current, directories, filenames in os.walk(path, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name for name in directories
            if not (current_path / name).is_symlink()
        ]
        for filename in filenames:
            file_path = current_path / filename
            if file_path.is_symlink() or not file_path.is_file():
                continue
            try:
                total += file_path.stat().st_size
                files += 1
            except OSError:
                continue
    return total, files


def database_record_storage_groups(db, tenant_id: int) -> list[dict]:
    groups = []
    project_rows = db.execute(
        """SELECT id project_id,name project_name,project_key,created_at created_at,
        updated_at modified_at,
        1 records,64+LENGTH(project_key)+LENGTH(name)+LENGTH(product_type)+
        LENGTH(description)+LENGTH(version)+LENGTH(environment)+LENGTH(status)+
        LENGTH(qa_owner)+LENGTH(created_at)+LENGTH(updated_at) estimated_bytes
        FROM projects WHERE tenant_id=? ORDER BY id""",
        (tenant_id,),
    ).fetchall()
    for row in project_rows:
        groups.append({
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "project_key": row["project_key"],
            "kind": "projects",
            "records": row["records"],
            "size": row["estimated_bytes"] or 64,
            "created_at": row["created_at"],
            "modified_at": row["modified_at"],
        })

    record_types = (
        (
            "requirements", "requirements", "r",
            ("requirement_key", "title", "description", "acceptance_criteria",
             "business_rules", "validation_rules", "dependencies", "priority",
             "module_type", "module_category", "requirement_type", "actor",
             "preconditions", "trigger", "main_flow", "alternative_flow",
             "exception_flow", "related_requirements", "source_document",
             "source_section", "source_evidence", "created_at"),
            "r.project_id=p.id", "r.created_at", "r.created_at",
        ),
        (
            "test-cases", "test_cases", "r",
            ("case_key", "scenario", "description", "steps", "expected_result",
             "preconditions", "test_data", "use_case_id", "priority", "test_type",
             "title", "test_category", "classification", "assumptions",
             "source_reference", "created_at", "updated_at"),
            "r.project_id=p.id", "r.updated_at", "r.created_at",
        ),
        (
            "test-runs", "executions", "r",
            ("status", "actual_result", "comments", "executed_by", "executed_at"),
            "r.test_case_id IN (SELECT id FROM test_cases WHERE project_id=p.id)",
            "r.executed_at", "r.executed_at",
        ),
        (
            "defects", "bugs", "r",
            ("bug_key", "title", "description", "severity", "priority",
             "status", "assigned_to", "created_at", "updated_at"),
            "r.project_id=p.id", "r.updated_at", "r.created_at",
        ),
        (
            "analyses", "application_analyses", "r",
            ("input_path", "status", "summary_json", "error", "created_at",
             "started_at", "completed_at"),
            "r.project_id=p.id", "COALESCE(r.completed_at,r.created_at)",
            "r.created_at",
        ),
    )
    for kind, table, alias, columns, relation, modified_column, created_column in record_types:
        byte_expression = " + ".join(
            f"LENGTH(COALESCE(CAST({alias}.{column} AS TEXT),''))"
            for column in columns
        )
        rows = db.execute(
            f"""SELECT p.id project_id,p.name project_name,p.project_key,
            COUNT({alias}.id) records,
            COALESCE(SUM(64+{byte_expression}),0) estimated_bytes,
            MIN({created_column}) created_at,
            MAX({modified_column}) modified_at
            FROM projects p JOIN {table} {alias} ON {relation}
            WHERE p.tenant_id=? GROUP BY p.id,p.name,p.project_key
            HAVING COUNT({alias}.id)>0 ORDER BY p.id""",
            (tenant_id,),
        ).fetchall()
        groups.extend({
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "project_key": row["project_key"],
            "kind": kind,
            "records": row["records"],
            "size": row["estimated_bytes"],
            "created_at": row["created_at"],
            "modified_at": row["modified_at"],
        } for row in rows)
    return groups


def workspace_storage_scan(db, storage_root: Path | str, tenant_id: int) -> dict:
    projects = [
        dict(row) for row in db.execute(
            """SELECT id,project_key,name FROM projects
            WHERE tenant_id=? ORDER BY name COLLATE NOCASE,id""",
            (tenant_id,),
        )
    ]
    items: list[dict] = []
    project_by_id = {row["id"]: row for row in projects}
    document_rows = db.execute(
        """SELECT d.id,d.project_id,d.filename,d.storage_path,d.uploaded_at
        FROM requirement_documents d JOIN projects p ON p.id=d.project_id
        WHERE p.tenant_id=?""",
        (tenant_id,),
    ).fetchall()
    documents = {
        str(Path(row["storage_path"]).resolve()).casefold(): row
        for row in document_rows
    }
    upload_rows = db.execute(
        """SELECT a.id,a.project_id,a.filename,a.storage_path,a.uploaded_at
        FROM application_uploads a JOIN projects p ON p.id=a.project_id
        WHERE p.tenant_id=?""",
        (tenant_id,),
    ).fetchall()
    uploads = {
        str(Path(row["storage_path"]).resolve()).casefold(): row
        for row in upload_rows
    }

    for project in projects:
        project_id = project["id"]
        project_dir = project_storage_dir(storage_root, project_id)
        if project_dir.is_symlink() or not project_dir.is_dir():
            continue
        project_dir = project_dir.resolve()
        application_root = project_dir / "applications"
        application_dirs: list[Path] = []
        if application_root.is_dir() and not application_root.is_symlink():
            application_dirs = [
                child for child in application_root.iterdir()
                if child.is_dir() and not child.is_symlink()
            ]
            for application_dir in sorted(application_dirs, key=lambda item: item.name):
                resolved = application_dir.resolve()
                size, file_count = directory_storage_size(resolved)
                if size == 0 and file_count == 0:
                    continue
                upload = uploads.get(str(resolved).casefold())
                relative = resolved.relative_to(project_dir).as_posix()
                items.append({
                    "id": storage_item_identifier(
                        "db-dir" if upload else "dir", tenant_id, project_id, relative
                    ),
                    "name": upload["filename"] if upload else resolved.name,
                    "path": relative,
                    "kind": "applications",
                    "source": "filesystem",
                    "project_id": project_id,
                    "project_name": project["name"],
                    "project_key": project["project_key"],
                    "size": size,
                    "file_count": file_count,
                    "created_at": upload["uploaded_at"] if upload else None,
                    "modified_at": upload["uploaded_at"] if upload else None,
                    "downloadable": False,
                    "deletable": True,
                    "_absolute_path": str(resolved),
                    "_db_table": "application_uploads" if upload else "",
                    "_db_id": upload["id"] if upload else None,
                })

        for current, directories, filenames in os.walk(project_dir, followlinks=False):
            current_path = Path(current)
            directories[:] = [
                name for name in directories
                if not (current_path / name).is_symlink()
                and not (
                    current_path == project_dir
                    and name == "applications"
                )
            ]
            for filename in filenames:
                file_path = current_path / filename
                if file_path.is_symlink() or not file_path.is_file():
                    continue
                try:
                    stat_result = file_path.stat()
                except OSError:
                    continue
                resolved = file_path.resolve()
                try:
                    relative = resolved.relative_to(project_dir).as_posix()
                except ValueError:
                    continue
                document = documents.get(str(resolved).casefold())
                kind = storage_file_kind(relative)
                items.append({
                    "id": storage_item_identifier("file", tenant_id, project_id, relative),
                    "name": file_path.name,
                    "path": relative,
                    "kind": kind,
                    "source": "filesystem",
                    "project_id": project_id,
                    "project_name": project["name"],
                    "project_key": project["project_key"],
                    "size": stat_result.st_size,
                    "file_count": 1,
                    "created_at": document["uploaded_at"] if document else None,
                    "modified_at": datetime.fromtimestamp(
                        stat_result.st_mtime, timezone.utc
                    ).isoformat(timespec="seconds"),
                    "downloadable": True,
                    "deletable": True,
                    "_absolute_path": str(resolved),
                    "_db_table": "requirement_documents" if document else "",
                    "_db_id": document["id"] if document else None,
                })

    for row in db.execute(
        """SELECT fa.id,fa.project_id,fa.filename,fa.file_size_bytes size,
        fa.uploaded_at,p.name project_name,p.project_key
        FROM form_attachments fa JOIN projects p ON p.id=fa.project_id
        WHERE p.tenant_id=? ORDER BY fa.id DESC""",
        (tenant_id,),
    ):
        items.append({
            "id": storage_item_identifier("attachment", tenant_id, row["id"]),
            "name": row["filename"],
            "path": f"database/attachments/{row['id']}",
            "kind": "attachments",
            "source": "database",
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "project_key": row["project_key"],
            "size": row["size"],
            "file_count": 1,
            "created_at": row["uploaded_at"],
            "modified_at": row["uploaded_at"],
            "downloadable": True,
            "deletable": True,
            "_absolute_path": "",
            "_db_table": "form_attachments",
            "_db_id": row["id"],
        })

    for row in db.execute(
        """SELECT ev.id,ev.project_id,ev.filename,ev.size,ev.created_at,
        p.name project_name,p.project_key
        FROM execution_evidence ev JOIN projects p ON p.id=ev.project_id
        WHERE p.tenant_id=? ORDER BY ev.id DESC""",
        (tenant_id,),
    ):
        items.append({
            "id": storage_item_identifier("evidence", tenant_id, row["id"]),
            "name": row["filename"],
            "path": f"database/evidence/{row['id']}",
            "kind": "evidence",
            "source": "database",
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "project_key": row["project_key"],
            "size": row["size"],
            "file_count": 1,
            "created_at": row["created_at"],
            "modified_at": row["created_at"],
            "downloadable": True,
            "deletable": True,
            "_absolute_path": "",
            "_db_table": "execution_evidence",
            "_db_id": row["id"],
        })

    record_groups = database_record_storage_groups(db, tenant_id)
    for group in record_groups:
        items.append({
            "id": storage_item_identifier(
                "records", tenant_id, group["project_id"], group["kind"]
            ),
            "name": f'{group["records"]} {group["kind"]} records',
            "path": f'database/records/{group["project_id"]}/{group["kind"]}',
            "kind": group["kind"],
            "source": "database estimate",
            "project_id": group["project_id"],
            "project_name": group["project_name"],
            "project_key": group["project_key"],
            "size": group["size"],
            "file_count": 0,
            "records_count": group["records"],
            "created_at": group["created_at"],
            "modified_at": group["modified_at"],
            "downloadable": False,
            "deletable": group["kind"] in (
                "requirements", "test-cases", "test-runs", "defects"
            ),
            "_absolute_path": "",
            "_db_table": "",
            "_db_id": None,
        })

    breakdown: dict[str, int] = {}
    for item in items:
        breakdown[item["kind"]] = breakdown.get(item["kind"], 0) + int(item["size"] or 0)
    filesystem_bytes = sum(
        int(item["size"] or 0) for item in items if item["source"] == "filesystem"
    )
    database_blob_bytes = sum(
        int(item["size"] or 0) for item in items if item["source"] == "database"
    )
    database_record_estimate_bytes = sum(
        int(item["size"] or 0) for item in items
        if item["source"] == "database estimate"
    )
    record_count = sum(group["records"] for group in record_groups)
    database_bytes = database_blob_bytes + database_record_estimate_bytes
    return {
        "items": items,
        "summary": {
            "total_bytes": filesystem_bytes + database_bytes,
            "filesystem_bytes": filesystem_bytes,
            "database_bytes": database_bytes,
            "database_blob_bytes": database_blob_bytes,
            "database_record_estimate_bytes": database_record_estimate_bytes,
            "record_count": record_count,
            "database_estimate_method": (
                "Character-count estimate of selected text fields plus 64 bytes "
                "per row. It excludes text encoding differences, indexes, database "
                "pages, and engine-specific overhead. Binary attachments use exact "
                "byte sizes."
            ),
            "item_count": len(items),
            "project_count": len(project_by_id),
            "breakdown": breakdown,
            "computed_at": timestamp(),
        },
        "projects": projects,
    }


def read_workspace_storage_snapshot(
    db, tenant_id: int, max_age_seconds: int = 60
) -> dict | None:
    row = db.execute(
        "SELECT * FROM workspace_storage_snapshots WHERE tenant_id=?",
        (tenant_id,),
    ).fetchone()
    if not row:
        return None
    try:
        computed = datetime.fromisoformat(row["computed_at"])
        age = (datetime.now(timezone.utc) - computed).total_seconds()
    except (TypeError, ValueError):
        return None
    if age < 0 or age > max_age_seconds:
        return None
    return {
        "total_bytes": row["total_bytes"],
        "filesystem_bytes": row["filesystem_bytes"],
        "database_bytes": row["database_bytes"],
        "database_blob_bytes": row["database_blob_bytes"],
        "database_record_estimate_bytes": row["database_record_estimate_bytes"],
        "record_count": row["record_count"],
        "database_estimate_method": row["database_estimate_method"],
        "item_count": row["item_count"],
        "project_count": row["project_count"],
        "breakdown": json.loads(row["breakdown_json"] or "{}"),
        "computed_at": row["computed_at"],
        "cached": True,
    }


def save_workspace_storage_snapshot(db, tenant_id: int, summary: dict) -> None:
    db.execute(
        """INSERT INTO workspace_storage_snapshots
        (tenant_id,total_bytes,filesystem_bytes,database_bytes,database_blob_bytes,
        database_record_estimate_bytes,record_count,database_estimate_method,
        item_count,project_count,breakdown_json,computed_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tenant_id) DO UPDATE SET
        total_bytes=excluded.total_bytes,
        filesystem_bytes=excluded.filesystem_bytes,
        database_bytes=excluded.database_bytes,
        database_blob_bytes=excluded.database_blob_bytes,
        database_record_estimate_bytes=excluded.database_record_estimate_bytes,
        record_count=excluded.record_count,
        database_estimate_method=excluded.database_estimate_method,
        item_count=excluded.item_count,
        project_count=excluded.project_count,
        breakdown_json=excluded.breakdown_json,
        computed_at=excluded.computed_at""",
        (
            tenant_id,
            summary["total_bytes"],
            summary["filesystem_bytes"],
            summary["database_bytes"],
            summary["database_blob_bytes"],
            summary["database_record_estimate_bytes"],
            summary["record_count"],
            summary["database_estimate_method"],
            summary["item_count"],
            summary["project_count"],
            json.dumps(summary["breakdown"], ensure_ascii=True),
            summary["computed_at"],
        ),
    )
    db.execute(
        """INSERT OR IGNORE INTO storage_usage_history
        (tenant_id,total_bytes,filesystem_bytes,database_bytes,calculated_at)
        VALUES(?,?,?,?,?)""",
        (
            tenant_id,
            summary["total_bytes"],
            summary["filesystem_bytes"],
            summary["database_bytes"],
            summary["computed_at"],
        ),
    )


def invalidate_workspace_storage_snapshot(db, tenant_id: int) -> None:
    db.execute(
        "DELETE FROM workspace_storage_snapshots WHERE tenant_id=?",
        (tenant_id,),
    )


def storage_quota_summary(summary: dict) -> dict:
    quota = workspace_storage_quota_bytes()
    percentage = (
        round(summary["total_bytes"] * 100 / quota, 1) if quota else None
    )
    if quota and summary["total_bytes"] >= quota:
        warning = "Workspace storage quota exceeded."
    elif quota and percentage is not None and percentage >= 90:
        warning = "Workspace storage is above 90% of its quota."
    elif quota and percentage is not None and percentage >= 80:
        warning = "Workspace storage is above 80% of its quota."
    else:
        warning = ""
    return {
        **summary,
        "quota_bytes": quota,
        "usage_percent": percentage,
        "warning": warning,
    }


def workspace_storage_payload(
    db,
    storage_root: Path | str,
    tenant_id: int,
    *,
    kind: str = "",
    project_id: int | None = None,
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    sort: str = "size_desc",
    page: int = 1,
    page_size: int = 25,
    force: bool = False,
) -> dict:
    scanned = workspace_storage_scan(db, storage_root, tenant_id)
    summary = storage_quota_summary(scanned["summary"])
    save_workspace_storage_snapshot(db, tenant_id, scanned["summary"])
    items = scanned["items"]
    if kind:
        items = [item for item in items if item["kind"] == kind]
    if project_id is not None:
        items = [item for item in items if item["project_id"] == project_id]
    if search:
        needle = search.casefold()
        items = [
            item for item in items
            if needle in item["name"].casefold()
            or needle in item["path"].casefold()
            or needle in item["project_name"].casefold()
        ]
    if date_from:
        items = [
            item for item in items
            if str(item["modified_at"] or "")[:10] >= date_from
        ]
    if date_to:
        items = [
            item for item in items
            if str(item["modified_at"] or "")[:10] <= date_to
        ]
    sorters = {
        "size_desc": lambda item: (-int(item["size"] or 0), item["name"].casefold()),
        "size_asc": lambda item: (int(item["size"] or 0), item["name"].casefold()),
        "name_asc": lambda item: (item["name"].casefold(), item["path"].casefold()),
        "recent_desc": lambda item: (
            str(item["modified_at"] or ""), item["name"].casefold()
        ),
    }
    if sort not in sorters:
        raise ValueError("Invalid storage sort.")
    items = sorted(items, key=sorters[sort], reverse=sort == "recent_desc")
    total = len(items)
    page_size = max(1, min(page_size, 100))
    pages = max(1, math.ceil(total / page_size))
    page = max(1, min(page, pages))
    start = (page - 1) * page_size
    public_items = [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in items[start:start + page_size]
    ]
    if not force:
        cached = read_workspace_storage_snapshot(db, tenant_id)
        if cached:
            summary = storage_quota_summary(cached)
    return {
        "summary": summary,
        "items": public_items,
        "projects": scanned["projects"],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
        },
        "filters": {
            "kind": kind,
            "project_id": project_id,
            "q": search,
            "date_from": date_from,
            "date_to": date_to,
            "sort": sort,
        },
    }


def safe_storage_item_path(storage_root: Path | str, project_id: int, item: dict) -> Path:
    project_dir = project_storage_dir(storage_root, project_id)
    if project_dir.is_symlink() or not project_dir.is_dir():
        raise ValueError("Project storage is unavailable.")
    resolved_project = project_dir.resolve()
    candidate = Path(item["_absolute_path"])
    if candidate.is_symlink():
        raise ValueError("Symbolic links cannot be deleted through storage management.")
    if any(parent.is_symlink() for parent in (candidate, *candidate.parents)):
        raise ValueError("Symbolic links cannot be deleted through storage management.")
    try:
        candidate.resolve().relative_to(resolved_project)
    except ValueError:
        raise ValueError("Storage item is outside the project directory.")
    return candidate


def delete_workspace_storage_items(
    db,
    storage_root: Path | str,
    tenant_id: int,
    actor_id: int | None,
    item_ids: list[str],
) -> dict:
    scanned = workspace_storage_scan(db, storage_root, tenant_id)
    available = {item["id"]: item for item in scanned["items"]}
    deleted = []
    skipped = []
    freed = 0
    estimated_bytes_removed = 0
    for item_id in list(dict.fromkeys(item_ids)):
        item = available.get(item_id)
        if not item:
            skipped.append({"id": item_id, "reason": "Storage item no longer exists."})
            continue
        if not item.get("deletable"):
            skipped.append({
                "id": item_id,
                "reason": "This protected record group cannot be deleted from storage management.",
            })
            continue
        try:
            if item["source"] == "database estimate":
                project = db.execute(
                    "SELECT id FROM projects WHERE id=? AND tenant_id=?",
                    (item["project_id"], tenant_id),
                ).fetchone()
                if not project:
                    skipped.append({
                        "id": item_id,
                        "reason": "Project is no longer available in this workspace.",
                    })
                    continue
                kind = item["kind"]
                if kind == "requirements":
                    ids = [
                        row["id"] for row in db.execute(
                            "SELECT id FROM requirements WHERE project_id=?",
                            (item["project_id"],),
                        )
                    ]
                    for record_id in ids:
                        cleanup_record_form_data(db, "requirements", record_id)
                    db.execute(
                        "DELETE FROM requirements WHERE project_id=?",
                        (item["project_id"],),
                    )
                elif kind == "test-cases":
                    ids = [
                        row["id"] for row in db.execute(
                            "SELECT id FROM test_cases WHERE project_id=?",
                            (item["project_id"],),
                        )
                    ]
                    for record_id in ids:
                        cleanup_record_form_data(db, "test-cases", record_id)
                    db.execute(
                        "DELETE FROM test_cases WHERE project_id=?",
                        (item["project_id"],),
                    )
                elif kind == "test-runs":
                    db.execute(
                        """DELETE FROM execution_evidence WHERE project_id=?
                        AND execution_id IN (
                            SELECT e.id FROM executions e JOIN test_cases c
                            ON c.id=e.test_case_id WHERE c.project_id=?
                        )""",
                        (item["project_id"], item["project_id"]),
                    )
                    db.execute(
                        """DELETE FROM executions WHERE test_case_id IN
                        (SELECT id FROM test_cases WHERE project_id=?)""",
                        (item["project_id"],),
                    )
                elif kind == "defects":
                    ids = [
                        row["id"] for row in db.execute(
                            "SELECT id FROM bugs WHERE project_id=?",
                            (item["project_id"],),
                        )
                    ]
                    for record_id in ids:
                        cleanup_record_form_data(db, "bugs", record_id)
                    db.execute(
                        "DELETE FROM bugs WHERE project_id=?",
                        (item["project_id"],),
                    )
                else:
                    raise ValueError("Unsupported database record group.")
                deleted_count_records = int(item.get("records_count") or 0)
                estimated_bytes_removed += int(item["size"] or 0)
                audit_event(
                    db,
                    actor_id,
                    "storage.record_group_deleted",
                    "storage_record_group",
                    item_id,
                    {
                        "kind": kind,
                        "records": deleted_count_records,
                        "estimated_bytes": int(item["size"] or 0),
                        "project_id": item["project_id"],
                    },
                    tenant_id=tenant_id,
                )
            elif item["source"] == "database":
                table = item["_db_table"]
                db_id = item["_db_id"]
                if table not in ("form_attachments", "execution_evidence"):
                    raise ValueError("Unsupported database storage item.")
                cursor = db.execute(
                    f"DELETE FROM {table} WHERE id=? AND project_id=?",
                    (db_id, item["project_id"]),
                )
                if cursor.rowcount == 0:
                    skipped.append({
                        "id": item_id,
                        "reason": "Storage record no longer exists.",
                    })
                    continue
            else:
                path = safe_storage_item_path(
                    storage_root, item["project_id"], item
                )
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
                table = item["_db_table"]
                db_id = item["_db_id"]
                if table == "requirement_documents" and db_id is not None:
                    db.execute(
                        "DELETE FROM requirement_documents WHERE id=? AND project_id=?",
                        (db_id, item["project_id"]),
                    )
                elif table == "application_uploads" and db_id is not None:
                    db.execute(
                        "DELETE FROM application_uploads WHERE id=? AND project_id=?",
                        (db_id, item["project_id"]),
                    )
            if item["source"] != "database estimate":
                freed += int(item["size"] or 0)
            deleted.append(item_id)
            if item["source"] != "database estimate":
                audit_event(
                    db,
                    actor_id,
                    "storage.item_deleted",
                    "storage_item",
                    item_id,
                    {
                        "path": item["path"],
                        "kind": item["kind"],
                        "source": item["source"],
                        "bytes": int(item["size"] or 0),
                        "project_id": item["project_id"],
                    },
                    tenant_id=tenant_id,
                )
        except (OSError, ValueError) as exc:
            skipped.append({"id": item_id, "reason": str(exc)})
    invalidate_workspace_storage_snapshot(db, tenant_id)
    return {
        "deleted": deleted,
        "skipped": skipped,
        "freed_bytes": freed,
        "estimated_bytes_removed": estimated_bytes_removed,
        "deleted_count": len(deleted),
    }


def storage_item_download(
    db,
    storage_root: Path | str,
    tenant_id: int,
    item_id: str,
) -> tuple[bytes, str, str] | None:
    scanned = workspace_storage_scan(db, storage_root, tenant_id)
    item = next(
        (candidate for candidate in scanned["items"] if candidate["id"] == item_id),
        None,
    )
    if not item or not item["downloadable"]:
        return None
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", item["name"]) or "download"
    if item["source"] == "database":
        table = item["_db_table"]
        if table == "form_attachments":
            row = db.execute(
                """SELECT filename,content_type,file_data FROM form_attachments
                WHERE id=? AND project_id=?""",
                (item["_db_id"], item["project_id"]),
            ).fetchone()
            if not row:
                return None
            return (
                bytes(row["file_data"]),
                row["content_type"],
                row["filename"],
            )
        if table == "execution_evidence":
            row = db.execute(
                """SELECT filename,media_type,image_data FROM execution_evidence
                WHERE id=? AND project_id=?""",
                (item["_db_id"], item["project_id"]),
            ).fetchone()
            if not row:
                return None
            return (
                bytes(row["image_data"]),
                row["media_type"],
                row["filename"],
            )
        return None
    path = safe_storage_item_path(storage_root, item["project_id"], item)
    if path.is_symlink() or not path.is_file():
        return None
    return (
        path.read_bytes(),
        mimetypes.guess_type(item["name"])[0] or "application/octet-stream",
        safe_name,
    )


def redact_sensitive_text(value, limit: int = 4000) -> str:
    text = "" if value is None else str(value)
    replacements = (
        (r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie)\b\s*[:=]\s*(?:(?:Bearer|Basic)\s+)?(\"[^\"]*\"|'[^']*'|[^\s,;]+)",
         r"\1=[REDACTED]"),
        (r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]"),
        (r"(?i)([?&](?:access[_-]?token|token|key|api[_-]?key|secret|password|code|authorization|cookie|email)=)[^&\s]+", r"\1[REDACTED]"),
        (r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]"),
        (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[REDACTED_IP]"),
        (r"(?i)(?:C:\\Users\\|/home/|/Users/)[^\\/\s]+", "[REDACTED_USER_PATH]"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return text[:limit]


def sanitize_error_metadata(value, limit: int = 8000) -> dict:
    if not isinstance(value, dict):
        return {}
    try:
        json.dumps(value, ensure_ascii=True, default=str)
    except (TypeError, ValueError):
        return {}

    def clean(item, depth=0):
        if isinstance(item, str):
            return redact_sensitive_text(item, 1000)
        if isinstance(item, (int, float, bool)) or item is None:
            return item
        if isinstance(item, dict) and depth < 4:
            cleaned = {}
            for key, nested in list(item.items())[:30]:
                safe_key = str(key)[:100]
                if re.search(
                    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie)",
                    safe_key,
                ):
                    cleaned[safe_key] = "[REDACTED]"
                else:
                    cleaned[safe_key] = clean(nested, depth + 1)
            return cleaned
        if isinstance(item, (list, tuple)) and depth < 4:
            return [clean(nested, depth + 1) for nested in item[:30]]
        return redact_sensitive_text(json.dumps(item, default=str), 1000)

    cleaned = clean(value)
    bounded = {}
    for key, item in cleaned.items():
        candidate = {**bounded, key: item}
        if len(json.dumps(candidate, ensure_ascii=True)) > limit:
            break
        bounded[key] = item
    return bounded


def error_fingerprint(
    tenant_id: int | None,
    source: str,
    error_type: str,
    message: str,
    route: str,
) -> str:
    normalized = redact_sensitive_text(message, 2000).casefold()
    normalized = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", normalized)
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    key = "|".join((
        str(tenant_id or 0), source, error_type.casefold(), normalized, route,
    ))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def record_developer_error(db_path: Path | str, *, source: str, message: str, **context) -> tuple[int, bool]:
    source = source if source in DEVELOPER_ERROR_SOURCES else "backend"
    severity = context.get("severity", "error")
    severity = severity if severity in DEVELOPER_ERROR_SEVERITIES else "error"
    tenant_id = context.get("tenant_id")
    user_id = None
    project_id = context.get("project_id")
    route = redact_sensitive_text(urlparse(
        str(context.get("route", ""))
    ).path, 500)
    error_type = redact_sensitive_text(context.get("error_type", ""), 200)
    clean_message = redact_sensitive_text(message, 4000) or "Unknown error"
    clean_stack = redact_sensitive_text(context.get("stack_trace", ""), 20000)
    metadata = sanitize_error_metadata(context.get("metadata"))
    request_id = redact_sensitive_text(context.get("request_id", ""), 100)
    correlation_id = redact_sensitive_text(context.get("correlation_id", ""), 100)
    user_email = ""
    status_code = context.get("status_code")
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None
    fingerprint = error_fingerprint(
        tenant_id, source, error_type, clean_message, route
    )
    now = timestamp()
    with database(db_path) as db:
        if project_id is not None:
            if tenant_id is None:
                project_id = None
            else:
                project = db.execute(
                    "SELECT id FROM projects WHERE id=? AND tenant_id=?",
                    (project_id, tenant_id),
                ).fetchone()
                project_id = project["id"] if project else None
        existing = db.execute(
            "SELECT id FROM developer_errors WHERE fingerprint=?",
            (fingerprint,),
        ).fetchone()
        if existing:
            db.execute(
                """UPDATE developer_errors SET severity=?,message=?,stack_trace=?,
                route=?,method=?,status_code=?,request_id=?,correlation_id=?,
                user_id=?,user_email=?,project_id=?,metadata_json=?,
                occurrence_count=occurrence_count+1,last_seen_at=? WHERE id=?""",
                (
                    severity, clean_message, clean_stack, route,
                    redact_sensitive_text(context.get("method", ""), 20),
                    status_code, request_id, correlation_id, user_id,
                    "", project_id,
                    json.dumps(metadata, ensure_ascii=True), now,
                    existing["id"],
                ),
            )
            return existing["id"], False
        cursor = db.execute(
            """INSERT INTO developer_errors
            (tenant_id,project_id,fingerprint,source,severity,status,error_type,
            message,stack_trace,route,method,status_code,request_id,correlation_id,
            user_id,user_email,metadata_json,occurrence_count,first_seen_at,last_seen_at)
            VALUES(?,?,?,?,?,'open',?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
            (
                tenant_id, project_id, fingerprint, source, severity, error_type,
                clean_message, clean_stack, route,
                redact_sensitive_text(context.get("method", ""), 20),
                status_code, request_id, correlation_id, user_id, user_email,
                json.dumps(metadata, ensure_ascii=True), now, now,
            ),
        )
        return cursor.lastrowid, True


def developer_error_scope(user: dict | None) -> tuple[str, list]:
    if user and user.get("global_admin"):
        return "1=1", []
    tenant_id = (user or {}).get("tenant_id")
    if tenant_id is None:
        return "tenant_id IS NULL", []
    return "tenant_id=?", [tenant_id]


def developer_api_metrics(server, user: dict, window_seconds: int) -> dict:
    cutoff = time.time() - window_seconds
    global_access = bool(user.get("global_admin"))
    tenant_id = user.get("tenant_id")
    with server.api_metrics_lock:
        recent_metrics = list(server.api_metrics)
    metrics = [
        row for row in recent_metrics
        if row[0] >= cutoff and (global_access or row[1] == tenant_id)
    ]
    failures = [row for row in metrics if row[2] >= 400]
    client_errors = [row for row in failures if 400 <= row[2] < 500]
    server_errors = [row for row in failures if row[2] >= 500]
    endpoints: dict[str, int] = {}
    for _, _, _, route in failures:
        endpoints[route] = endpoints.get(route, 0) + 1
    request_count = len(metrics)
    return {
        "request_count": request_count,
        "failure_count": len(failures),
        "failure_rate_percent": (
            round(len(failures) * 100 / request_count, 1)
            if request_count else 0.0
        ),
        "http_4xx": len(client_errors),
        "http_5xx": len(server_errors),
        "top_failed_endpoints": [
            {"endpoint": route, "failures": count}
            for route, count in sorted(
                endpoints.items(), key=lambda item: (-item[1], item[0])
            )[:8]
        ],
        "window_seconds": window_seconds,
        "retention": (
            "In-memory, per-workspace request metrics; latest 20,000 requests "
            "only and reset when the server restarts."
        ),
    }


def developer_error_dict(row) -> dict:
    item = dict(row)
    for field, limit in (
        ("message", 4000), ("stack_trace", 20000), ("route", 500),
        ("error_type", 200), ("request_id", 100), ("correlation_id", 100),
        ("notes", 4000),
    ):
        item[field] = redact_sensitive_text(item.get(field, ""), limit)
    item["user_email"] = ""
    item["user_id"] = None
    try:
        raw_metadata = json.loads(item.pop("metadata_json") or "{}")
    except json.JSONDecodeError:
        raw_metadata = {}
    item["metadata"] = sanitize_error_metadata(raw_metadata)
    return item


def prune_developer_errors(db, days: int = DEVELOPER_ERROR_RETENTION_DAYS) -> int:
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max(1, days))
    ).isoformat(timespec="seconds")
    cursor = db.execute(
        """DELETE FROM developer_errors WHERE last_seen_at < ?
        AND status IN ('resolved','ignored')""",
        (cutoff,),
    )
    return cursor.rowcount


def developer_health(db, storage_root: Path | str) -> dict:
    started = time.perf_counter()
    try:
        db.execute("SELECT 1").fetchone()
        database_status = "ok"
        database_error = ""
    except Exception as exc:
        database_status = "error"
        database_error = redact_sensitive_text(exc, 500)
    database_latency_ms = round((time.perf_counter() - started) * 1000, 2)
    storage_path = Path(storage_root)
    try:
        usage = shutil.disk_usage(storage_path)
        storage_status = "ok" if os.access(storage_path, os.W_OK) else "read-only"
        storage_payload = {
            "status": storage_status,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
        }
    except OSError as exc:
        storage_payload = {
            "status": "error",
            "error": redact_sensitive_text(exc, 500),
        }
    stale_cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat(timespec="seconds")
    recent_cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=24)
    ).isoformat(timespec="seconds")
    jobs = db.execute(
        """SELECT
        SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) queued,
        SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) running,
        SUM(CASE WHEN status IN ('queued','running') AND created_at < ? THEN 1 ELSE 0 END) stale,
        SUM(CASE WHEN status='failed' AND created_at >= ? THEN 1 ELSE 0 END) failed_24h
        FROM application_analyses""",
        (stale_cutoff, recent_cutoff),
    ).fetchone()
    job_payload = {
        "status": "degraded" if (jobs["stale"] or 0) else "ok",
        "queued": jobs["queued"] or 0,
        "running": jobs["running"] or 0,
        "stale": jobs["stale"] or 0,
        "failed_24h": jobs["failed_24h"] or 0,
    }
    overall = "ok"
    if (
        database_status != "ok"
        or storage_payload["status"] != "ok"
        or job_payload["status"] != "ok"
    ):
        overall = "degraded"
    return {
        "status": overall,
        "checked_at": timestamp(),
        "uptime_seconds": round(time.time() - PROCESS_STARTED_AT),
        "database": {
            "status": database_status,
            "latency_ms": database_latency_ms,
            "error": database_error,
        },
        "storage": storage_payload,
        "jobs": job_payload,
    }


def sync_project_tenant_data(db, project_id: int) -> None:
    for table in (
        "requirements", "requirement_documents", "requirement_analysis_previews",
        "requirement_suggestions", "test_cases", "bugs", "form_attachments",
        "api_docs", "api_endpoints", "execution_evidence",
        "application_analyses", "application_uploads",
    ):
        db.execute(
            f"""UPDATE {table} SET tenant_id=(
                SELECT tenant_id FROM projects WHERE id=?
            ) WHERE project_id=? AND tenant_id<>(
                SELECT tenant_id FROM projects WHERE id=?
            )""",
            (project_id, project_id, project_id),
        )


def connect(db_path: Path | str = DB_PATH):
    return connect_database(db_path)


@contextmanager
def database(db_path: Path | str = DB_PATH):
    db = connect(db_path)
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(db_path: Path | str = DB_PATH) -> None:
    with database(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
                status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active','Inactive')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tenant_subscriptions (
                id INTEGER PRIMARY KEY,
                tenant_id INTEGER NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK(status IN ('trialing','active','expired','canceled')),
                plan TEXT NOT NULL,
                trial_started_at TEXT,
                trial_ends_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY,
                project_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                product_type TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                version TEXT NOT NULL DEFAULT '',
                environment TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Active',
                qa_owner TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requirements (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                requirement_key TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                acceptance_criteria TEXT NOT NULL DEFAULT '',
                business_rules TEXT NOT NULL DEFAULT '',
                validation_rules TEXT NOT NULL DEFAULT '',
                dependencies TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'Medium',
                module_type TEXT NOT NULL DEFAULT 'Functional',
                module_category TEXT NOT NULL DEFAULT 'Functional',
                requirement_type TEXT NOT NULL DEFAULT 'Functional Requirement',
                scope_classification TEXT NOT NULL DEFAULT 'Requires Clarification',
                ai_confidence_status TEXT NOT NULL DEFAULT 'Needs clarification',
                actor TEXT NOT NULL DEFAULT '',
                preconditions TEXT NOT NULL DEFAULT '',
                trigger TEXT NOT NULL DEFAULT '',
                main_flow TEXT NOT NULL DEFAULT '',
                alternative_flow TEXT NOT NULL DEFAULT '',
                exception_flow TEXT NOT NULL DEFAULT '',
                related_requirements TEXT NOT NULL DEFAULT '',
                source_document TEXT NOT NULL DEFAULT '',
                source_section TEXT NOT NULL DEFAULT '',
                source_evidence TEXT NOT NULL DEFAULT '',
                origin TEXT NOT NULL DEFAULT 'manual',
                source_suggestion_id INTEGER,
                review_status TEXT NOT NULL DEFAULT 'Needs review',
                created_at TEXT NOT NULL,
                UNIQUE(project_id, requirement_key)
            );
            CREATE TABLE IF NOT EXISTS requirement_documents (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                content_type TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                requirement_count INTEGER NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                uploaded_at TEXT NOT NULL,
                analysis_json TEXT
            );
            CREATE TABLE IF NOT EXISTS requirement_analysis_previews (
                id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                preview_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requirement_suggestions (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                document_id INTEGER NOT NULL REFERENCES requirement_documents(id) ON DELETE CASCADE,
                related_requirement_id INTEGER REFERENCES requirements(id) ON DELETE SET NULL,
                related_requirement_key TEXT NOT NULL DEFAULT '',
                accepted_requirement_id INTEGER REFERENCES requirements(id) ON DELETE SET NULL,
                original_content_json TEXT NOT NULL,
                content_json TEXT NOT NULL,
                reason TEXT NOT NULL,
                source_reference TEXT NOT NULL DEFAULT '',
                source_evidence TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Pending'
                    CHECK(status IN ('Pending', 'Accepted', 'Rejected')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requirement_suggestion_history (
                id INTEGER PRIMARY KEY,
                suggestion_id INTEGER NOT NULL REFERENCES requirement_suggestions(id) ON DELETE CASCADE,
                action TEXT NOT NULL CHECK(action IN ('Edited', 'Accepted', 'Rejected')),
                content_json TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS test_cases (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                requirement_id INTEGER REFERENCES requirements(id) ON DELETE SET NULL,
                case_key TEXT NOT NULL,
                scenario TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                steps TEXT NOT NULL DEFAULT '',
                expected_result TEXT NOT NULL DEFAULT '',
                preconditions TEXT NOT NULL DEFAULT '',
                test_data TEXT NOT NULL DEFAULT '',
                use_case_id TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'Not specified',
                test_type TEXT NOT NULL DEFAULT 'Functional',
                title TEXT NOT NULL DEFAULT '',
                test_category TEXT NOT NULL DEFAULT 'Functional',
                classification TEXT NOT NULL DEFAULT '',
                assumptions TEXT NOT NULL DEFAULT '',
                source_reference TEXT NOT NULL DEFAULT '',
                generation_key TEXT NOT NULL DEFAULT '',
                requirement_origin TEXT NOT NULL DEFAULT '',
                requirement_approval_status TEXT NOT NULL DEFAULT '',
                source_suggestion_id INTEGER,
                source_requirement_key TEXT NOT NULL DEFAULT '',
                source_requirement_title TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, case_key)
            );
            CREATE TABLE IF NOT EXISTS executions (
                id INTEGER PRIMARY KEY,
                test_case_id INTEGER NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                actual_result TEXT NOT NULL DEFAULT '',
                comments TEXT NOT NULL DEFAULT '',
                executed_by TEXT NOT NULL DEFAULT '',
                executed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bugs (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                test_case_id INTEGER REFERENCES test_cases(id) ON DELETE SET NULL,
                bug_key TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL DEFAULT 'Medium',
                priority TEXT NOT NULL DEFAULT 'P2',
                status TEXT NOT NULL DEFAULT 'Open',
                assigned_to TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id, bug_key)
            );
            CREATE TABLE IF NOT EXISTS custom_fields (
                id INTEGER PRIMARY KEY,
                module_key TEXT NOT NULL,
                name TEXT NOT NULL,
                label TEXT NOT NULL,
                field_type TEXT NOT NULL CHECK(field_type IN ('text','number','dropdown')),
                description TEXT NOT NULL DEFAULT '',
                required INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0,1)),
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                display_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_custom_fields_module_name
                ON custom_fields(module_key, lower(name));
            CREATE TABLE IF NOT EXISTS custom_field_options (
                id INTEGER PRIMARY KEY,
                custom_field_id INTEGER NOT NULL REFERENCES custom_fields(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                value TEXT NOT NULL,
                display_order INTEGER NOT NULL DEFAULT 0,
                UNIQUE(custom_field_id, value)
            );
            CREATE TABLE IF NOT EXISTS custom_field_values (
                id INTEGER PRIMARY KEY,
                custom_field_id INTEGER NOT NULL REFERENCES custom_fields(id) ON DELETE CASCADE,
                module_key TEXT NOT NULL,
                record_id INTEGER NOT NULL,
                value TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                UNIQUE(custom_field_id, module_key, record_id)
            );
            CREATE TABLE IF NOT EXISTS form_attachments (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                module_key TEXT NOT NULL,
                record_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                file_data BLOB NOT NULL,
                uploaded_at TEXT NOT NULL,
                UNIQUE(module_key, record_id, sha256)
            );
            CREATE INDEX IF NOT EXISTS ix_custom_field_values_record
                ON custom_field_values(module_key, record_id);
            CREATE INDEX IF NOT EXISTS ix_form_attachments_record
                ON form_attachments(project_id, module_key, record_id);
            CREATE TRIGGER IF NOT EXISTS clean_requirement_form_data
            AFTER DELETE ON requirements BEGIN
                DELETE FROM custom_field_values WHERE module_key='requirements' AND record_id=OLD.id;
                DELETE FROM form_attachments WHERE module_key='requirements' AND record_id=OLD.id;
            END;
            CREATE TRIGGER IF NOT EXISTS clean_test_case_form_data
            AFTER DELETE ON test_cases BEGIN
                DELETE FROM custom_field_values WHERE module_key='test-cases' AND record_id=OLD.id;
                DELETE FROM form_attachments WHERE module_key='test-cases' AND record_id=OLD.id;
            END;
            CREATE TRIGGER IF NOT EXISTS clean_bug_form_data
            AFTER DELETE ON bugs BEGIN
                DELETE FROM custom_field_values WHERE module_key='bugs' AND record_id=OLD.id;
                DELETE FROM form_attachments WHERE module_key='bugs' AND record_id=OLD.id;
            END;
            CREATE TRIGGER IF NOT EXISTS clean_project_form_data
            AFTER DELETE ON projects BEGIN
                DELETE FROM custom_field_values WHERE module_key='projects' AND record_id=OLD.id;
                DELETE FROM form_attachments WHERE module_key='projects' AND record_id=OLD.id;
            END;
            CREATE TABLE IF NOT EXISTS api_docs (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                title TEXT NOT NULL,
                spec_version TEXT NOT NULL,
                imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS api_endpoints (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                api_doc_id INTEGER NOT NULL REFERENCES api_docs(id) ON DELETE CASCADE,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                operation_id TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS execution_evidence (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                execution_id INTEGER NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                image_data BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS application_analyses (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                input_path TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued', 'running', 'completed', 'failed')),
                summary_json TEXT,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS application_uploads (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                archive_size_bytes INTEGER NOT NULL,
                inventory_json TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                coverage_json TEXT,
                coverage_checked_at TEXT,
                coverage_requirements_signature TEXT,
                uploaded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_settings (
                id INTEGER PRIMARY KEY CHECK(id=1),
                ollama_base_url TEXT NOT NULL,
                ollama_model TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'google'
            );
            CREATE TABLE IF NOT EXISTS ai_provider_configs (
                provider TEXT PRIMARY KEY,
                model TEXT NOT NULL DEFAULT '',
                base_url TEXT NOT NULL DEFAULT '',
                api_key_ciphertext BLOB,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tenant_ai_settings (
                tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
                provider TEXT NOT NULL DEFAULT 'google',
                enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tenant_ai_provider_configs (
                tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                base_url TEXT NOT NULL DEFAULT '',
                api_key_ciphertext BLOB,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(tenant_id, provider)
            );
            CREATE TABLE IF NOT EXISTS workspace_storage_snapshots (
                tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                filesystem_bytes INTEGER NOT NULL DEFAULT 0,
                database_bytes INTEGER NOT NULL DEFAULT 0,
                database_blob_bytes INTEGER NOT NULL DEFAULT 0,
                database_record_estimate_bytes INTEGER NOT NULL DEFAULT 0,
                record_count INTEGER NOT NULL DEFAULT 0,
                database_estimate_method TEXT NOT NULL DEFAULT '',
                item_count INTEGER NOT NULL DEFAULT 0,
                project_count INTEGER NOT NULL DEFAULT 0,
                breakdown_json TEXT NOT NULL DEFAULT '{}',
                computed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                description TEXT NOT NULL DEFAULT '',
                permissions_json TEXT NOT NULL DEFAULT '[]',
                tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
                is_system INTEGER NOT NULL DEFAULT 0 CHECK(is_system IN (0,1)),
                status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active','Inactive')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                role_id INTEGER NOT NULL REFERENCES roles(id),
                phone TEXT NOT NULL DEFAULT '',
                profile_image_type TEXT NOT NULL DEFAULT '',
                profile_image_data BLOB,
                status TEXT NOT NULL DEFAULT 'Active'
                    CHECK(status IN ('Active','Inactive','Invited','Suspended')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            );
            CREATE TABLE IF NOT EXISTS developer_errors (
                id INTEGER PRIMARY KEY,
                tenant_id INTEGER REFERENCES tenants(id) ON DELETE SET NULL,
                project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
                fingerprint TEXT NOT NULL,
                source TEXT NOT NULL
                    CHECK(source IN ('frontend','backend','api','job','database')),
                severity TEXT NOT NULL DEFAULT 'error'
                    CHECK(severity IN ('debug','info','warning','error','critical')),
                status TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','acknowledged','resolved','ignored')),
                error_type TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL,
                stack_trace TEXT NOT NULL DEFAULT '',
                route TEXT NOT NULL DEFAULT '',
                method TEXT NOT NULL DEFAULT '',
                status_code INTEGER,
                request_id TEXT NOT NULL DEFAULT '',
                correlation_id TEXT NOT NULL DEFAULT '',
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                user_email TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                notes TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS tenant_memberships (
                id INTEGER PRIMARY KEY,
                tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role_id INTEGER NOT NULL REFERENCES roles(id),
                status TEXT NOT NULL DEFAULT 'Active'
                    CHECK(status IN ('Active','Inactive','Invited','Suspended')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(tenant_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS permissions (
                key TEXT PRIMARY KEY,
                description TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS role_permissions (
                role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
                permission_key TEXT NOT NULL REFERENCES permissions(key) ON DELETE CASCADE,
                PRIMARY KEY(role_id,permission_key)
            );
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('user','admin')),
                expires_at REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_auth_sessions_user
                ON auth_sessions(user_id, kind);
            CREATE INDEX IF NOT EXISTS ix_auth_sessions_expiry
                ON auth_sessions(expires_at);
            CREATE TABLE IF NOT EXISTS global_admin_profile (
                id INTEGER PRIMARY KEY CHECK(id=1),
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                profile_image_type TEXT NOT NULL DEFAULT '',
                profile_image_data BLOB,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                description TEXT NOT NULL DEFAULT '',
                tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
                owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active','Inactive')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS team_memberships (
                team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                added_at TEXT NOT NULL,
                PRIMARY KEY(team_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY,
                actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                actor_label TEXT,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL DEFAULT '',
                tenant_id INTEGER REFERENCES tenants(id) ON DELETE SET NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_team_memberships_user ON team_memberships(user_id,team_id);
            CREATE INDEX IF NOT EXISTS ix_tenant_memberships_user ON tenant_memberships(user_id,status,tenant_id);
            CREATE INDEX IF NOT EXISTS ix_tenant_memberships_role ON tenant_memberships(role_id,status);
            CREATE INDEX IF NOT EXISTS ix_audit_events_created ON audit_events(created_at DESC);
            CREATE INDEX IF NOT EXISTS ix_requirements_project ON requirements(project_id);
            CREATE INDEX IF NOT EXISTS ix_requirement_documents_project ON requirement_documents(project_id, id DESC);
            CREATE INDEX IF NOT EXISTS ix_requirement_suggestions_project
                ON requirement_suggestions(project_id, status, id DESC);
            CREATE INDEX IF NOT EXISTS ix_cases_project ON test_cases(project_id);
            CREATE INDEX IF NOT EXISTS ix_exec_case_time ON executions(test_case_id, executed_at);
            CREATE INDEX IF NOT EXISTS ix_bugs_project ON bugs(project_id);
            CREATE INDEX IF NOT EXISTS ix_api_docs_project ON api_docs(project_id);
            CREATE INDEX IF NOT EXISTS ix_api_endpoints_project ON api_endpoints(project_id);
            CREATE INDEX IF NOT EXISTS ix_evidence_execution ON execution_evidence(execution_id);
            CREATE INDEX IF NOT EXISTS ix_application_analyses_project ON application_analyses(project_id, id DESC);
            CREATE INDEX IF NOT EXISTS ix_application_uploads_project ON application_uploads(project_id, id DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS ux_roles_name_ci ON roles(lower(name));
            CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_ci ON users(lower(email));
            CREATE UNIQUE INDEX IF NOT EXISTS ux_teams_name_ci ON teams(lower(name));
            CREATE INDEX IF NOT EXISTS ix_workspace_storage_computed
                ON workspace_storage_snapshots(computed_at DESC);
            CREATE INDEX IF NOT EXISTS ix_developer_errors_fingerprint
                ON developer_errors(fingerprint, last_seen_at DESC);
            CREATE INDEX IF NOT EXISTS ix_developer_errors_tenant_status
                ON developer_errors(tenant_id, status, last_seen_at DESC);
            CREATE INDEX IF NOT EXISTS ix_developer_errors_source
                ON developer_errors(source, severity, last_seen_at DESC);
            CREATE INDEX IF NOT EXISTS ix_developer_errors_last_seen
                ON developer_errors(last_seen_at DESC);
            """
        )
        storage_snapshot_columns = {
            row["name"] for row in db.execute(
                "PRAGMA table_info(workspace_storage_snapshots)"
            )
        }
        added_storage_snapshot_columns = False
        for column, declaration in (
            ("database_blob_bytes", "INTEGER NOT NULL DEFAULT 0"),
            ("database_record_estimate_bytes", "INTEGER NOT NULL DEFAULT 0"),
            ("record_count", "INTEGER NOT NULL DEFAULT 0"),
            ("database_estimate_method", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in storage_snapshot_columns:
                db.execute(
                    f"ALTER TABLE workspace_storage_snapshots ADD COLUMN {column} {declaration}"
                )
                added_storage_snapshot_columns = True
        if added_storage_snapshot_columns:
            db.execute("DELETE FROM workspace_storage_snapshots")
        now = timestamp()
        db.execute(
            """INSERT OR IGNORE INTO tenants(id,name,slug,status,created_at,updated_at)
            VALUES(1,'Default Workspace','default','Active',?,?)""",
            (now, now),
        )
        tenant_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(tenants)")
        }
        for column, declaration in (
            ("owner_user_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
            ("suspended_at", "TEXT"),
            ("suspension_reason", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in tenant_columns:
                db.execute(f"ALTER TABLE tenants ADD COLUMN {column} {declaration}")
        db.execute(
            """INSERT OR IGNORE INTO tenant_subscriptions
            (tenant_id,status,plan,trial_started_at,trial_ends_at,created_at,updated_at)
            SELECT id,'active','legacy',NULL,NULL,created_at,updated_at FROM tenants"""
        )
        db.execute(
            """UPDATE tenant_subscriptions SET status='active',plan='legacy',
            trial_started_at=NULL,trial_ends_at=NULL,updated_at=?
            WHERE tenant_id=? AND (
                status!='active' OR plan!='legacy' OR trial_started_at IS NOT NULL
                OR trial_ends_at IS NOT NULL
            )""",
            (now, PERMANENT_WORKSPACE_ID),
        )
        db.execute(
            """UPDATE tenants SET status='Active',suspended_at=NULL,
            suspension_reason=''
            WHERE id=? AND (
                status!='Active' OR suspended_at IS NOT NULL OR suspension_reason!=''
            )""",
            (PERMANENT_WORKSPACE_ID,),
        )
        for table_name in (
            "projects", "requirements", "requirement_documents",
            "requirement_analysis_previews", "requirement_suggestions",
            "test_cases", "bugs", "custom_fields", "form_attachments",
            "custom_field_values",
            "api_docs", "api_endpoints", "execution_evidence",
            "application_analyses", "application_uploads", "teams",
        ):
            table_columns = {
                row["name"] for row in db.execute(f"PRAGMA table_info({table_name})")
            }
            if "tenant_id" not in table_columns:
                db.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1"
                )
        for table_name in (
            "projects", "requirements", "requirement_documents",
            "requirement_analysis_previews", "requirement_suggestions",
            "test_cases", "bugs", "form_attachments", "custom_fields",
            "custom_field_values", "api_docs", "api_endpoints",
            "execution_evidence", "application_analyses", "application_uploads",
            "teams",
        ):
            db.execute(
                f"""CREATE INDEX IF NOT EXISTS ix_{table_name}_tenant
                ON {table_name}(tenant_id)"""
            )
        db.execute("DROP INDEX IF EXISTS ux_custom_fields_module_name")
        db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS ux_custom_fields_tenant_module_name
            ON custom_fields(tenant_id,module_key,lower(name))"""
        )
        audit_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(audit_events)")
        }
        if "tenant_id" not in audit_columns:
            db.execute("ALTER TABLE audit_events ADD COLUMN tenant_id INTEGER")
        if "actor_label" not in audit_columns:
            db.execute("ALTER TABLE audit_events ADD COLUMN actor_label TEXT")
        role_columns = {row["name"] for row in db.execute("PRAGMA table_info(roles)")}
        if "tenant_id" not in role_columns:
            db.execute("ALTER TABLE roles ADD COLUMN tenant_id INTEGER")
        legacy_admin = db.execute(
            "SELECT id FROM roles WHERE name='Admin' COLLATE NOCASE"
        ).fetchone()
        global_admin_role = db.execute(
            "SELECT id FROM roles WHERE name='Global Admin' COLLATE NOCASE"
        ).fetchone()
        if legacy_admin and not global_admin_role:
            db.execute(
                "UPDATE roles SET name='Global Admin',description='System administrator',is_system=1 WHERE id=?",
                (legacy_admin["id"],),
            )
        elif legacy_admin and global_admin_role:
            db.execute(
                "UPDATE users SET role_id=? WHERE role_id=?",
                (global_admin_role["id"], legacy_admin["id"]),
            )
            db.execute(
                "UPDATE tenant_memberships SET role_id=? WHERE role_id=?",
                (global_admin_role["id"], legacy_admin["id"]),
            )
            db.execute(
                "DELETE FROM role_permissions WHERE role_id=?",
                (legacy_admin["id"],),
            )
            db.execute("DELETE FROM roles WHERE id=?", (legacy_admin["id"],))
        db.execute(
            """INSERT OR IGNORE INTO global_admin_profile
            (id,first_name,last_name,email,created_at,updated_at)
            VALUES(1,'Global','Administrator','global-admin@local.qa',?,?)""",
            (now, now),
        )
        for role_name, permissions in DEFAULT_ROLE_PERMISSIONS.items():
            db.execute(
                """INSERT OR IGNORE INTO roles
                (name,description,permissions_json,is_system,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    role_name,
                    (
                        "System administrator"
                        if role_name == "Global Admin"
                        else "Workspace owner with workspace-scoped management permissions"
                        if role_name == "Workspace Owner"
                        else f"Default {role_name} access"
                    ),
                    json.dumps(permissions),
                    int(role_name in ("Global Admin", "Workspace Owner", "Developer")),
                    "Active",
                    timestamp(),
                    timestamp(),
                ),
            )
        db.execute("INSERT OR IGNORE INTO permissions(key,description) VALUES('*','All permissions')")
        for permission in ALL_PERMISSIONS:
            db.execute(
                "INSERT OR IGNORE INTO permissions(key,description) VALUES(?,?)",
                (permission, permission.replace(".", " ").replace("-", " ").capitalize()),
            )
        for role in db.execute("SELECT id,permissions_json FROM roles"):
            for permission in json.loads(role["permissions_json"]):
                db.execute(
                    "INSERT OR IGNORE INTO role_permissions(role_id,permission_key) VALUES(?,?)",
                    (role["id"], permission),
                )
        for user in db.execute("SELECT id,role_id,status,created_at,updated_at FROM users"):
            db.execute(
                """INSERT OR IGNORE INTO tenant_memberships
                (tenant_id,user_id,role_id,status,created_at,updated_at)
                VALUES(1,?,?,?,?,?)""",
                (
                    user["id"], user["role_id"], user["status"],
                    user["created_at"], user["updated_at"],
                ),
            )
        tenant_admin_migration = db.execute(
            "SELECT value FROM system_settings WHERE key='tenant_admin_account_promoted'"
        ).fetchone()
        if not tenant_admin_migration:
            tenant_admin = db.execute(
                """SELECT u.id,m.role_id,r.name role_name
                FROM users u
                JOIN tenant_memberships m ON m.user_id=u.id AND m.tenant_id=1
                JOIN roles r ON r.id=m.role_id
                WHERE lower(u.email)=?""",
                (TENANT_ADMIN_EMAIL,),
            ).fetchone()
            if tenant_admin:
                global_admin_role = db.execute(
                    "SELECT id FROM roles WHERE name='Global Admin' AND status='Active'"
                ).fetchone()
                if not global_admin_role:
                    raise ValueError("The Global Admin role is not available.")
                if tenant_admin["role_id"] != global_admin_role["id"]:
                    db.execute(
                        """UPDATE tenant_memberships SET role_id=?,updated_at=?
                        WHERE user_id=? AND tenant_id=1""",
                        (global_admin_role["id"], now, tenant_admin["id"]),
                    )
                    audit_event(
                        db, None, "user.role_changed", "user", tenant_admin["id"],
                        {
                            "previous": tenant_admin["role_name"],
                            "new": "Global Admin",
                            "source": "tenant_admin_account_migration",
                        },
                        tenant_id=1,
                    )
                db.execute(
                    """INSERT INTO system_settings(key,value)
                    VALUES('tenant_admin_account_promoted','true')"""
                )
        requirement_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(requirements)")
        }
        migrate_requirement_origins = "origin" not in requirement_columns
        ai_settings_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(ai_settings)")
        }
        if "provider" not in ai_settings_columns:
            db.execute(
                "ALTER TABLE ai_settings ADD COLUMN provider TEXT NOT NULL DEFAULT 'google'"
            )
        tenant_ai_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(tenant_ai_settings)")
        }
        if "enabled" not in tenant_ai_columns:
            db.execute(
                "ALTER TABLE tenant_ai_settings ADD COLUMN enabled INTEGER NOT NULL DEFAULT 0"
            )
            db.execute(
                "UPDATE tenant_ai_settings SET enabled=1 WHERE tenant_id=1"
            )
        project_columns = {row["name"] for row in db.execute("PRAGMA table_info(projects)")}
        req_doc_columns = {row["name"] for row in db.execute("PRAGMA table_info(requirement_documents)")}
        if "version" not in req_doc_columns:
            db.execute("ALTER TABLE requirement_documents ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        if "analysis_json" not in req_doc_columns:
            db.execute("ALTER TABLE requirement_documents ADD COLUMN analysis_json TEXT")
        suggestion_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(requirement_suggestions)")
        }
        if "related_requirement_key" not in suggestion_columns:
            db.execute(
                "ALTER TABLE requirement_suggestions ADD COLUMN related_requirement_key TEXT NOT NULL DEFAULT ''"
            )
        if "product_type" not in project_columns:
            db.execute("ALTER TABLE projects ADD COLUMN product_type TEXT NOT NULL DEFAULT ''")
        if "status" not in project_columns:
            db.execute("ALTER TABLE projects ADD COLUMN status TEXT NOT NULL DEFAULT 'Active'")
        for column, declaration in (
            ("business_rules", "TEXT NOT NULL DEFAULT ''"),
            ("validation_rules", "TEXT NOT NULL DEFAULT ''"),
            ("dependencies", "TEXT NOT NULL DEFAULT ''"),
            ("source_section", "TEXT NOT NULL DEFAULT ''"),
            ("source_evidence", "TEXT NOT NULL DEFAULT ''"),
            ("origin", "TEXT NOT NULL DEFAULT 'manual'"),
            ("source_suggestion_id", "INTEGER"),
            ("module_type", "TEXT NOT NULL DEFAULT 'Functional'"),
            ("module_category", "TEXT NOT NULL DEFAULT 'Functional'"),
            ("requirement_type", "TEXT NOT NULL DEFAULT 'Functional Requirement'"),
            ("scope_classification", "TEXT NOT NULL DEFAULT 'Requires Clarification'"),
            ("ai_confidence_status", "TEXT NOT NULL DEFAULT 'Needs clarification'"),
            ("actor", "TEXT NOT NULL DEFAULT ''"),
            ("preconditions", "TEXT NOT NULL DEFAULT ''"),
            ("trigger", "TEXT NOT NULL DEFAULT ''"),
            ("main_flow", "TEXT NOT NULL DEFAULT ''"),
            ("alternative_flow", "TEXT NOT NULL DEFAULT ''"),
            ("exception_flow", "TEXT NOT NULL DEFAULT ''"),
            ("related_requirements", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in requirement_columns:
                db.execute(f"ALTER TABLE requirements ADD COLUMN {column} {declaration}")
        if migrate_requirement_origins:
            db.execute(
                """UPDATE requirements SET origin='legacy_import'
                WHERE source_document!='' AND origin='manual'"""
            )
        case_columns = {row["name"] for row in db.execute("PRAGMA table_info(test_cases)")}
        for column, declaration in (
            ("preconditions", "TEXT NOT NULL DEFAULT ''"),
            ("test_data", "TEXT NOT NULL DEFAULT ''"),
            ("use_case_id", "TEXT NOT NULL DEFAULT ''"),
            ("priority", "TEXT NOT NULL DEFAULT 'Not specified'"),
            ("test_type", "TEXT NOT NULL DEFAULT 'Functional'"),
            ("title", "TEXT NOT NULL DEFAULT ''"),
            ("test_category", "TEXT NOT NULL DEFAULT 'Functional'"),
            ("classification", "TEXT NOT NULL DEFAULT ''"),
            ("assumptions", "TEXT NOT NULL DEFAULT ''"),
            ("source_reference", "TEXT NOT NULL DEFAULT ''"),
            ("generation_key", "TEXT NOT NULL DEFAULT ''"),
            ("requirement_origin", "TEXT NOT NULL DEFAULT ''"),
            ("requirement_approval_status", "TEXT NOT NULL DEFAULT ''"),
            ("source_suggestion_id", "INTEGER"),
            ("source_requirement_key", "TEXT NOT NULL DEFAULT ''"),
            ("source_requirement_title", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in case_columns:
                db.execute(f"ALTER TABLE test_cases ADD COLUMN {column} {declaration}")
        db.execute(
            """UPDATE test_cases SET
            title=CASE WHEN title='' THEN scenario ELSE title END,
            test_category=CASE
                WHEN test_category='Functional' AND test_type IN
                    ('Performance','Security','Usability','Compatibility','Reliability','Non-Functional')
                THEN 'Non-Functional' ELSE test_category END,
            classification=CASE
                WHEN classification='' AND generation_key!='' AND test_type IN ('Negative','Validation')
                THEN 'Negative'
                WHEN classification='' AND generation_key!='' THEN 'Positive'
                ELSE classification END"""
        )
        db.execute(
            """UPDATE test_cases SET
            requirement_origin=CASE WHEN requirement_origin='' THEN COALESCE(
                (SELECT origin FROM requirements WHERE id=test_cases.requirement_id),''
            ) ELSE requirement_origin END,
            requirement_approval_status=CASE WHEN requirement_approval_status='' THEN COALESCE(
                (SELECT review_status FROM requirements WHERE id=test_cases.requirement_id),''
            ) ELSE requirement_approval_status END,
            source_suggestion_id=COALESCE(source_suggestion_id,
                (SELECT source_suggestion_id FROM requirements WHERE id=test_cases.requirement_id)),
            source_requirement_key=CASE WHEN source_requirement_key='' THEN COALESCE(
                (SELECT requirement_key FROM requirements WHERE id=test_cases.requirement_id),''
            ) ELSE source_requirement_key END,
            source_requirement_title=CASE WHEN source_requirement_title='' THEN COALESCE(
                (SELECT title FROM requirements WHERE id=test_cases.requirement_id),''
            ) ELSE source_requirement_title END
            WHERE requirement_id IS NOT NULL"""
        )
        application_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(application_uploads)")
        }
        for column, declaration in (
            ("coverage_json", "TEXT"),
            ("coverage_checked_at", "TEXT"),
            ("coverage_requirements_signature", "TEXT"),
        ):
            if column not in application_columns:
                db.execute(f"ALTER TABLE application_uploads ADD COLUMN {column} {declaration}")
        db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS ux_test_cases_generation_key
            ON test_cases(project_id,generation_key) WHERE generation_key != ''"""
        )
        db.execute(
            """UPDATE application_analyses
            SET status='failed', error='The server restarted before this analysis finished.',
                completed_at=?
            WHERE status IN ('queued', 'running')""",
            (timestamp(),),
        )
        db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS ux_application_analyses_active
            ON application_analyses ((1)) WHERE status IN ('queued', 'running')"""
        )
        db.execute(
            """INSERT OR IGNORE INTO ai_settings(id,ollama_base_url,ollama_model,updated_at)
            VALUES(1,'http://127.0.0.1:11434','',?)""",
            (timestamp(),),
        )
        db.execute(
            """INSERT OR IGNORE INTO ai_provider_configs
            (provider,model,base_url,api_key_ciphertext,updated_at)
            VALUES('google','gemini-2.5-flash','',NULL,?)""",
            (timestamp(),),
        )
        db.execute(
            """INSERT OR IGNORE INTO tenant_ai_settings(tenant_id,provider,enabled,updated_at)
            SELECT 1,provider,1,updated_at FROM ai_settings WHERE id=1"""
        )
        db.execute(
            """INSERT OR IGNORE INTO tenant_ai_provider_configs
            (tenant_id,provider,model,base_url,api_key_ciphertext,updated_at)
            SELECT 1,provider,model,base_url,api_key_ciphertext,updated_at
            FROM ai_provider_configs"""
        )
        db.execute(
            """INSERT OR IGNORE INTO tenant_ai_provider_configs
            (tenant_id,provider,model,base_url,api_key_ciphertext,updated_at)
            VALUES(1,'google','gemini-2.5-flash','',NULL,?)""",
            (timestamp(),),
        )
        db.execute(
            "UPDATE developer_errors SET user_email='' WHERE user_email!=''"
        )
        db.execute(
            "UPDATE developer_errors SET user_id=NULL WHERE user_id IS NOT NULL"
        )
        prune_developer_errors(db)
        upgrade_product_owner_schema(db)


def row_dict(row: sqlite3.Row | None):
    return dict(row) if row else None


def next_key(db: sqlite3.Connection, table: str, col: str, project_id: int, prefix: str) -> str:
    # table/column are constant internal identifiers; all values remain parameterized.
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    values = db.execute(
        f"SELECT {col} FROM {table} WHERE project_id = ?", (project_id,)
    ).fetchall()
    number = max((int(m.group(1)) for row in values if (m := pattern.match(row[0]))), default=0) + 1
    return f"{prefix}-{number:03d}"


def validate_text(data, field: str, required: bool = False, limit: int = 12000) -> str:
    value = data.get(field, "")
    # parse_qs returns lists; unwrap single-element lists transparently
    if isinstance(value, list):
        value = value[0] if value else ""
    if not isinstance(value, str):
        raise ValueError(f"{field.replace('_', ' ').capitalize()} must be text.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field.replace('_', ' ').capitalize()} is required.")
    if len(value) > limit:
        raise ValueError(f"{field.replace('_', ' ').capitalize()} is too long (maximum {limit} characters).")
    return value


def custom_field_options(db: sqlite3.Connection, field_id: int) -> list[dict]:
    return [
        dict(row) for row in db.execute(
            """SELECT id,label,value,display_order FROM custom_field_options
            WHERE custom_field_id=? ORDER BY display_order,id""",
            (field_id,),
        )
    ]


def custom_field_dict(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    field = dict(row)
    field["required"] = bool(field["required"])
    field["active"] = bool(field["active"])
    field["options"] = custom_field_options(db, field["id"])
    return field


def project_tenant_id(db, project_id, default=1):
    row = db.execute(
        "SELECT tenant_id FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    return row["tenant_id"] if row else default


def record_tenant_id(db, module_key, record_id):
    tables = {
        "projects": ("projects", "id"),
        "requirements": ("requirements", "project_id"),
        "test-cases": ("test_cases", "project_id"),
        "bugs": ("bugs", "project_id"),
    }
    if record_id is None or module_key not in tables:
        return None
    table, project_column = tables[module_key]
    if module_key == "projects":
        row = db.execute(
            "SELECT tenant_id FROM projects WHERE id=?", (record_id,)
        ).fetchone()
    else:
        row = db.execute(
            f"""SELECT p.tenant_id FROM {table} r JOIN projects p
            ON p.id=r.{project_column} WHERE r.id=?""",
            (record_id,),
        ).fetchone()
    return row["tenant_id"] if row else None


def validate_custom_field_values(
    db: sqlite3.Connection,
    module_key: str,
    record_id: int,
    values,
    required_on_create: bool = False,
) -> None:
    if not isinstance(values, dict):
        raise ValueError("Custom field values must be an object.")
    tenant_id = record_tenant_id(db, module_key, record_id)
    fields = db.execute(
        """SELECT * FROM custom_fields
        WHERE module_key=? AND active=1 AND (? IS NULL OR tenant_id=?)
        ORDER BY display_order,id""",
        (module_key, tenant_id, tenant_id),
    ).fetchall()
    field_by_id = {str(row["id"]): row for row in fields}
    unknown = set(values) - field_by_id.keys()
    if unknown:
        raise ValueError("One or more custom fields are not configured for this form.")

    normalized = {}
    for field in fields:
        field_id = str(field["id"])
        value = values.get(field_id, "")
        previous = None
        if field_id not in values and not required_on_create:
            previous = db.execute(
                """SELECT value FROM custom_field_values
                WHERE custom_field_id=? AND module_key=? AND record_id=?""",
                (field["id"], module_key, record_id),
            ).fetchone()
            value = previous["value"] if previous else ""
        if value is None:
            value = ""
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ValueError(f"{field['label']} must be a scalar value.")
        value = str(value).strip()
        if (
            field["required"] and not value
            and (
                required_on_create or field_id in values
                or previous is None or not previous["value"]
            )
        ):
            raise ValueError(f"{field['label']} is required.")
        if field["field_type"] == "text" and len(value) > 1000:
            raise ValueError(f"{field['label']} is too long (maximum 1000 characters).")
        if field["field_type"] == "number" and value:
            try:
                valid_number = math.isfinite(float(value))
            except ValueError:
                valid_number = False
            if not valid_number:
                raise ValueError(f"{field['label']} must be a valid number.")
        if field["field_type"] == "dropdown" and value:
            if not db.execute(
                """SELECT 1 FROM custom_field_options
                WHERE custom_field_id=? AND value=?""",
                (field["id"], value),
            ).fetchone():
                raise ValueError(f"Choose a valid option for {field['label']}.")
        normalized[field["id"]] = value

    for field_id, value in normalized.items():
        db.execute(
            """INSERT INTO custom_field_values
            (custom_field_id,module_key,record_id,tenant_id,value,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(custom_field_id,module_key,record_id)
            DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (field_id, module_key, record_id, tenant_id or 1, value, timestamp()),
        )


def custom_fields_for_record(
    db: sqlite3.Connection, module_key: str, record_id: int | None = None,
    include_inactive: bool = False, tenant_id: int | None = None,
) -> list[dict]:
    if module_key not in CUSTOM_FIELD_MODULES:
        raise ValueError("Select a supported form/module.")
    active_filter = "" if include_inactive else " AND active=1"
    tenant_id = record_tenant_id(db, module_key, record_id) or tenant_id
    rows = db.execute(
        f"""SELECT * FROM custom_fields WHERE module_key=?{active_filter}
        AND (? IS NULL OR tenant_id=?)
        ORDER BY display_order,id""",
        (module_key, tenant_id, tenant_id),
    ).fetchall()
    values = {}
    if record_id is not None:
        values = {
            str(row["custom_field_id"]): row["value"]
            for row in db.execute(
                """SELECT custom_field_id,value FROM custom_field_values
                WHERE module_key=? AND record_id=? AND (? IS NULL OR tenant_id=?)""",
                (module_key, record_id, tenant_id, tenant_id),
            )
        }
    result = []
    for row in rows:
        field = custom_field_dict(db, row)
        field["value"] = values.get(str(row["id"]), "")
        result.append(field)
    return result


def normalize_custom_field_options(field_type: str, raw_options) -> list[dict]:
    if field_type != "dropdown":
        if raw_options not in (None, []):
            raise ValueError("Options are only supported for dropdown fields.")
        return []
    if not isinstance(raw_options, list) or not raw_options or len(raw_options) > 100:
        raise ValueError("Dropdown fields need between 1 and 100 options.")
    options = []
    seen = set()
    for index, option in enumerate(raw_options):
        if isinstance(option, str):
            label = option.strip()
            value = label
        elif isinstance(option, dict):
            label = option.get("label", "")
            value = option.get("value", label)
            if not isinstance(label, str) or not isinstance(value, str):
                raise ValueError("Dropdown option labels and values must be text.")
            label, value = label.strip(), value.strip()
        else:
            raise ValueError("Dropdown options must be text or option objects.")
        if not label or not value or len(label) > 200 or len(value) > 200:
            raise ValueError("Dropdown option labels and values must contain 1 to 200 characters.")
        if value.casefold() in seen:
            raise ValueError("Dropdown option values must be unique.")
        seen.add(value.casefold())
        options.append({
            "label": label,
            "value": value,
            "display_order": index,
        })
    return options


def custom_field_configuration(data: dict, existing: sqlite3.Row | None = None) -> dict:
    module_key = data.get(
        "module_key", existing["module_key"] if existing else ""
    )
    if not isinstance(module_key, str) or module_key not in CUSTOM_FIELD_MODULES:
        raise ValueError("Select a supported form/module.")
    name = validate_text(
        data if "name" in data else {"name": existing["name"] if existing else ""},
        "name", True, 80,
    )
    label = validate_text(
        data if "label" in data else {"label": existing["label"] if existing else ""},
        "label", True, 120,
    )
    field_type = data.get(
        "field_type", existing["field_type"] if existing else ""
    )
    if field_type not in CUSTOM_FIELD_TYPES:
        raise ValueError("Select Text, Number, or Dropdown as the field type.")
    description = validate_text(
        data if "description" in data else {
            "description": existing["description"] if existing else "",
        },
        "description", limit=500,
    )
    required = data.get(
        "required", bool(existing["required"]) if existing else False
    )
    active = data.get("active", bool(existing["active"]) if existing else True)
    if not isinstance(required, bool) or not isinstance(active, bool):
        raise ValueError("Required and active must be boolean values.")
    display_order = data.get(
        "display_order", existing["display_order"] if existing else 0
    )
    if (
        not isinstance(display_order, int)
        or isinstance(display_order, bool)
        or display_order < 0
    ):
        raise ValueError("Display order must be a non-negative integer.")
    if "options" in data:
        options = normalize_custom_field_options(field_type, data["options"])
    elif existing:
        options = [] if field_type != existing["field_type"] else None
    elif field_type == "dropdown":
        raise ValueError("Dropdown fields need between 1 and 100 options.")
    else:
        options = []
    return {
        "module_key": module_key,
        "name": name,
        "label": label,
        "field_type": field_type,
        "description": description,
        "required": int(required),
        "active": int(active),
        "display_order": display_order,
        "options": options,
    }


def record_belongs_to_project(
    db: sqlite3.Connection, module_key: str, project_id: int, record_id: int
) -> bool:
    if module_key not in CUSTOM_FIELD_MODULES:
        raise ValueError("Select a supported form/module.")
    table = CUSTOM_FIELD_MODULES[module_key][0]
    return bool(db.execute(
        f"SELECT 1 FROM {table} WHERE id=? AND " +
        ("id=?" if module_key == "projects" else "project_id=?"),
        (record_id, record_id if module_key == "projects" else project_id),
    ).fetchone())


REQUIREMENT_CONTENT_FIELDS = (
    "title", "description", "acceptance_criteria", "business_rules",
    "validation_rules", "dependencies",
)


def requirement_fingerprint(requirement: dict) -> tuple[str, ...]:
    return tuple(
        " ".join(unicodedata.normalize("NFKC", str(requirement.get(field) or "")).casefold().split())
        for field in REQUIREMENT_CONTENT_FIELDS
    )


def find_duplicate_requirement(
    db: sqlite3.Connection,
    project_id: int,
    requirement: dict,
    exclude_id: int | None = None,
) -> sqlite3.Row | None:
    rows = db.execute(
        """SELECT * FROM requirements WHERE project_id=?
        AND (? IS NULL OR id<>?) ORDER BY id""",
        (project_id, exclude_id, exclude_id),
    )
    fingerprint = requirement_fingerprint(requirement)
    return next(
        (row for row in rows if requirement_fingerprint(dict(row)) == fingerprint),
        None,
    )


def insert_requirement_record(
    db: sqlite3.Connection,
    project_id: int,
    record: dict,
    source_document: str,
    source_evidence: str,
    created_at: str,
    *,
    origin: str,
    review_status: str = "Needs review",
    source_suggestion_id: int | None = None,
) -> dict:
    key = record.get("requirement_key") or next_key(
        db, "requirements", "requirement_key", project_id, "REQ"
    )
    module_type, module_category = normalize_requirement_module(
        record.get("module_type"),
        record.get("module_category"),
        record.get("title", ""),
        record.get("description", ""),
        record.get("acceptance_criteria", ""),
        record.get("business_rules", ""),
        record.get("validation_rules", ""),
        record.get("dependencies", ""),
    )
    cursor = db.execute(
        """INSERT INTO requirements
        (project_id,requirement_key,title,description,acceptance_criteria,business_rules,
        validation_rules,dependencies,priority,module_type,module_category,requirement_type,
        scope_classification,ai_confidence_status,actor,preconditions,trigger,main_flow,
        alternative_flow,exception_flow,related_requirements,source_document,source_section,
        source_evidence,origin,source_suggestion_id,review_status,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            project_id, key, record["title"], record.get("description", ""),
            record.get("acceptance_criteria", ""), record.get("business_rules", ""),
            record.get("validation_rules", ""), record.get("dependencies", ""),
            record.get("priority", "Not specified"), module_type, module_category,
            record.get("requirement_type", "Functional Requirement"),
            record.get("scope_classification", "Requires Clarification"),
            record.get("ai_confidence_status", "Needs clarification"),
            record.get("actor", ""), record.get("preconditions", ""),
            record.get("trigger", ""), record.get("main_flow", ""),
            record.get("alternative_flow", ""), record.get("exception_flow", ""),
            record.get("related_requirements", ""), source_document,
            record.get("source_section", record["title"]), source_evidence, origin,
            source_suggestion_id, review_status, created_at,
        ),
    )
    return dict(db.execute(
        "SELECT * FROM requirements WHERE id=?", (cursor.lastrowid,)
    ).fetchone())


def requirement_suggestion_dict(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    suggestion = dict(row)
    suggestion["content"] = json.loads(suggestion.pop("content_json"))
    suggestion["original_content"] = json.loads(suggestion.pop("original_content_json"))
    suggestion["history"] = [
        {
            **dict(entry),
            "content": json.loads(entry["content_json"]),
        }
        for entry in db.execute(
            """SELECT action,content_json,reviewer,created_at
            FROM requirement_suggestion_history WHERE suggestion_id=? ORDER BY id""",
            (suggestion["id"],),
        )
    ]
    for entry in suggestion["history"]:
        del entry["content_json"]
    return suggestion


def build_ai_requirement_suggestions(
    candidates: list[dict],
    source_text: str,
    extracted_records: list[dict],
) -> list[dict]:
    source_folded = source_text.casefold()
    extracted_fingerprints = {
        requirement_fingerprint(record) for record in extracted_records
    }
    suggestions = []
    for candidate in candidates:
        if requirement_fingerprint(candidate) in extracted_fingerprints:
            continue
        candidate = {**candidate, "requirement_key": ""}
        section = candidate.get("source_section", "").strip()
        source_reference = (
            section if section and section.casefold() in source_folded else ""
        )
        related_key = next(
            (
                record.get("requirement_key", "")
                for record in extracted_records
                if candidate.get("title", "").strip().casefold()
                in {
                    record.get("title", "").strip().casefold(),
                    record.get("source_section", "").strip().casefold(),
                }
            ),
            "",
        )
        suggestions.append({
            "content": candidate,
            "reason": (
                "AI interpretation of the BRD; verify each detail against the original source."
                if source_reference else
                "AI-proposed content has no exact source reference; verify or edit before approval."
            ),
            "related_requirement_key": related_key,
            "source_reference": source_reference,
            "source_evidence": source_reference,
        })
    return suggestions


def project_exists(db: sqlite3.Connection, project_id: int) -> bool:
    return db.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is not None








def application_analysis_dict(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["summary"] = json.loads(result.pop("summary_json")) if result.get("summary_json") else None
    return result


def requirements_signature(db: sqlite3.Connection, project_id: int) -> str:
    rows = db.execute(
        """SELECT requirement_key,title,description,acceptance_criteria,business_rules,
        validation_rules,dependencies,priority,module_type,module_category
        FROM requirements WHERE project_id=? ORDER BY id""",
        (project_id,),
    )
    content = json.dumps([list(row) for row in rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def application_upload_dict(
    row: sqlite3.Row,
    current_requirements_signature: str | None = None,
) -> dict:
    result = dict(row)
    result.pop("storage_path", None)
    result["inventory"] = json.loads(result.pop("inventory_json"))
    result["analysis"] = json.loads(result.pop("analysis_json"))
    coverage_json = result.pop("coverage_json", None)
    result["coverage"] = json.loads(coverage_json) if coverage_json else None
    stored_signature = result.pop("coverage_requirements_signature", None)
    result["coverage_is_stale"] = bool(
        coverage_json
        and current_requirements_signature is not None
        and stored_signature != current_requirements_signature
    )
    return result





def openapi_inventory(filename: str, content: str):
    safe_name = filename.replace("\\", "/").split("/")[-1]
    if safe_name != filename or "://" in filename or Path(filename).suffix.lower() != ".json":
        raise ValueError("Only local OpenAPI/Swagger JSON files are supported.")
    if len(content.encode("utf-8")) > MAX_OPENAPI_SIZE:
        raise ValueError("OpenAPI JSON is too large (maximum 1 MB).")
    try:
        spec = json.loads(content)
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("File must contain valid OpenAPI/Swagger JSON.")
    if not isinstance(spec, dict):
        raise ValueError("OpenAPI/Swagger document must be a JSON object.")
    version = spec.get("openapi")
    swagger = spec.get("swagger")
    if isinstance(version, str) and re.fullmatch(r"3\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
        spec_version = version
    elif swagger == "2.0":
        spec_version = swagger
    else:
        raise ValueError("Document must declare OpenAPI 3.x or Swagger 2.0.")
    info = spec.get("info")
    paths = spec.get("paths")
    if not isinstance(info, dict) or not isinstance(info.get("title"), str) or not info["title"].strip():
        raise ValueError("OpenAPI document must include an info.title.")
    if not isinstance(info.get("version"), str) or not info["version"].strip():
        raise ValueError("OpenAPI document must include an info.version.")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI document must include a paths object.")
    endpoints = []
    for endpoint_path, item in paths.items():
        if not isinstance(endpoint_path, str) or not endpoint_path.startswith("/") or not isinstance(item, dict):
            raise ValueError("Each OpenAPI path must start with '/' and have an object value.")
        if len(endpoint_path) > 500:
            raise ValueError("OpenAPI path is too long (maximum 500 characters).")
        for method, operation in item.items():
            if method.lower() not in OPENAPI_METHODS:
                continue
            if not isinstance(operation, dict):
                raise ValueError(f"Operation {method.upper()} {endpoint_path} must be an object.")
            tags = operation.get("tags", [])
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                raise ValueError(f"Operation {method.upper()} {endpoint_path} has invalid tags.")
            summary = operation.get("summary") or operation.get("description") or ""
            operation_id = operation.get("operationId", "")
            endpoints.append({
                "method": method.upper(),
                "path": endpoint_path,
                "summary": summary[:1000] if isinstance(summary, str) else "",
                "operation_id": operation_id[:300] if isinstance(operation_id, str) else "",
                "tags": tags[:30],
            })
            if len(endpoints) > 2000:
                raise ValueError("OpenAPI document has too many operations (maximum 2,000).")
    return spec_version, info["title"].strip()[:200], endpoints


def validate_image(data: bytes, media_type: str):
    if len(data) > MAX_EVIDENCE_SIZE:
        raise ValueError("Evidence image is too large (maximum 5 MB).")
    if not data:
        raise ValueError("Evidence image is empty.")
    if media_type == "image/png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n")
        offset, saw_header, saw_data, saw_end = 8, False, False, False
        width = height = 0
        while valid and offset + 12 <= len(data):
            chunk_size = int.from_bytes(data[offset:offset + 4], "big")
            chunk_type = data[offset + 4:offset + 8]
            chunk_end = offset + 12 + chunk_size
            if chunk_end > len(data):
                valid = False
                break
            chunk = data[offset + 4:offset + 8 + chunk_size]
            crc = int.from_bytes(data[offset + 8 + chunk_size:chunk_end], "big")
            if zlib.crc32(chunk) & 0xffffffff != crc:
                valid = False
                break
            if not saw_header:
                if chunk_type != b"IHDR" or chunk_size != 13:
                    valid = False
                    break
                width, height = struct.unpack(">II", data[offset + 8:offset + 16])
                saw_header = True
            elif chunk_type == b"IHDR":
                valid = False
                break
            if chunk_type == b"IDAT":
                saw_data = True
            if chunk_type == b"IEND":
                saw_end = chunk_size == 0 and chunk_end == len(data)
                break
            offset = chunk_end
        valid = valid and saw_header and saw_data and saw_end and 0 < width <= 10000 and \
            0 < height <= 10000 and width * height <= 40_000_000
    elif media_type == "image/jpeg":
        valid = len(data) >= 4 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
    elif media_type == "image/gif":
        valid = len(data) >= 10 and data[:6] in (b"GIF87a", b"GIF89a") and \
            0 < int.from_bytes(data[6:8], "little") <= 10000 and 0 < int.from_bytes(data[8:10], "little") <= 10000
    elif media_type == "image/webp":
        valid = len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP" and \
            int.from_bytes(data[4:8], "little") + 8 == len(data)
    else:
        raise ValueError("Evidence must be a PNG, JPEG, GIF, or WebP image.")
    if not valid:
        raise ValueError("Image content does not match a supported image format.")


def dashboard(db: sqlite3.Connection, project_id: int) -> dict:
    requirements = db.execute(
        """SELECT COUNT(*) total,
        SUM(CASE WHEN EXISTS (SELECT 1 FROM test_cases tc WHERE tc.requirement_id = r.id) THEN 1 ELSE 0 END) covered
        FROM requirements r WHERE project_id = ?""",
        (project_id,),
    ).fetchone()
    statuses = {status: 0 for status in STATUSES}
    for row in db.execute(
        """SELECT COALESCE(e.status, 'Not Executed') status, COUNT(*) count
        FROM test_cases tc LEFT JOIN executions e ON e.id = (
            SELECT id FROM executions WHERE test_case_id = tc.id ORDER BY id DESC LIMIT 1
        ) WHERE tc.project_id = ? GROUP BY status""",
        (project_id,),
    ):
        statuses[row["status"]] = row["count"]
    bugs = db.execute(
        "SELECT COUNT(*) total, SUM(CASE WHEN status IN ('Open','In Progress') THEN 1 ELSE 0 END) open FROM bugs WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    req_total = requirements["total"] or 0
    covered = requirements["covered"] or 0
    total_cases = sum(statuses.values())
    executed = total_cases - statuses["Not Executed"] - statuses["In Progress"]
    return {
        "requirements": req_total,
        "covered_requirements": covered,
        "coverage_percent": round(covered * 100 / req_total) if req_total else 0,
        "test_cases": total_cases,
        "executed": executed,
        "statuses": statuses,
        "open_bugs": bugs["open"] or 0,
        "total_bugs": bugs["total"] or 0,
        "pass_percent": round(statuses["Pass"] * 100 / executed) if executed else 0,
    }


class QAHandler(BaseHTTPRequestHandler):
    server_version = "QATracker/1.0"

    def log_message(self, fmt, *args):
        if urlparse(self.path).path.rstrip("/") in (
            "/health", "/healthz", "/api/health", "/api/healthz",
        ):
            return
        message = fmt % args
        message = re.sub(
            r'("(?:GET|POST|PATCH|DELETE|PUT|OPTIONS)\s+[^?"\s]+)\?[^"]*(")',
            r"\1?[REDACTED]\2",
            message,
        )
        route = re.sub(r"/\d+(?=/|$)", "/:id", urlparse(self.path).path)
        print(json.dumps({
            "timestamp": timestamp(),
            "severity": "info",
            "service": "http",
            "environment": redact_sensitive_text(
                os.environ.get(
                    "QA_ENVIRONMENT", os.environ.get("ENVIRONMENT", "development")
                ),
                100,
            ),
            "request_id": self.request_identifier(),
            "method": self.command,
            "route": route,
            "message": redact_sensitive_text(message, 1000),
        }, ensure_ascii=True))

    def request_identifier(self):
        value = getattr(self, "_request_id", "")
        if not value:
            incoming = self.headers.get("X-Request-ID", "").strip()
            value = (
                re.sub(r"[^A-Za-z0-9._-]", "", incoming)[:100]
                or uuid.uuid4().hex
            )
            self._request_id = value
        return value

    def record_api_metric(self, status: int) -> None:
        path = urlparse(self.path).path.rstrip("/")
        if (
            not path.startswith("/api/")
            or path.startswith("/api/developer/")
            or path in ("/api/health", "/api/healthz")
        ):
            return
        tenant_id = getattr(self, "_request_tenant_id", None)
        if tenant_id is None:
            return
        route = re.sub(r"(?<=/storage/items/)[^/]+", ":item", path)
        route = re.sub(r"/\d+(?=/|$)", "/:id", route)
        route = re.sub(r"(?<=/)[a-fA-F0-9]{24,}(?=/|$)", ":id", route)
        with self.server.api_metrics_lock:
            self.server.api_metrics.append(
                (time.time(), tenant_id, int(status), route)
            )
        request_db = getattr(self, "_request_db", None)
        if tenant_id and request_db is not None:
            request_db.execute(
                """INSERT INTO usage_events
                (tenant_id,event_type,created_at,metadata_json)
                VALUES(?,'api_request',?,?)""",
                (
                    tenant_id,
                    timestamp(),
                    json.dumps(
                        {"route": route, "status": int(status)},
                        separators=(",", ":"),
                    ),
                ),
            )

    def send_bytes(self, data: bytes, content_type: str, status=200, extra=None):
        self.record_api_metric(status)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Request-ID", self.request_identifier())
        correlation_id = self.headers.get("X-Correlation-ID", "").strip()
        if correlation_id:
            self.send_header(
                "X-Correlation-ID",
                re.sub(r"[^A-Za-z0-9._-]", "", correlation_id)[:100],
            )
        origin = self.headers.get("Origin")
        if origin and origin in self.server.cors_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        for key, value in (extra or {}).items():
            for item in value if isinstance(value, (list, tuple)) else (value,):
                self.send_header(key, item)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, status=200, extra=None):
        self.send_bytes(
            json.dumps(data, ensure_ascii=True).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
            extra,
        )

    def log_exception(self, exc, *, source="backend", severity="error", **context):
        stack_trace = redact_sensitive_text(traceback.format_exc(), 20000)
        message = redact_sensitive_text(exc, 4000)
        try:
            user = self.current_user()
            environment = redact_sensitive_text(
                os.environ.get("QA_ENVIRONMENT", os.environ.get("ENVIRONMENT", "development")),
                100,
            )
            payload = {
                "severity": severity,
                "error_type": type(exc).__name__,
                "stack_trace": stack_trace,
                "route": urlparse(self.path).path,
                "method": self.command,
                "status_code": 500,
                "request_id": self.request_identifier(),
                "correlation_id": self.headers.get("X-Correlation-ID", ""),
                "tenant_id": (user or {}).get("tenant_id"),
                "metadata": {"environment": environment},
                **context,
            }
            record_developer_error(
                self.server.db_path, source=source, message=message, **payload
            )
            print(json.dumps({
                "timestamp": timestamp(),
                "severity": severity,
                "service": source,
                "environment": environment,
                "request_id": payload["request_id"],
                "error_type": type(exc).__name__,
                "message": message,
                "stack_trace": stack_trace,
            }, ensure_ascii=True), file=sys.stderr)
        except Exception:
            print(
                '{"severity":"error","service":"backend",'
                '"message":"Developer error logging failed."}',
                file=sys.stderr,
            )

    def developer_report_allowed(self):
        address = self.client_address[0] if self.client_address else "unknown"
        now = time.time()
        with self.server.developer_report_lock:
            window_start, count = self.server.developer_report_windows.get(
                address, (now, 0)
            )
            if now - window_start >= 60:
                window_start, count = now, 0
            count += 1
            self.server.developer_report_windows[address] = (window_start, count)
            if len(self.server.developer_report_windows) > 2000:
                cutoff = now - 120
                self.server.developer_report_windows = {
                    key: value
                    for key, value in self.server.developer_report_windows.items()
                    if value[0] >= cutoff
                }
            return count <= 30

    def auth_rate_limited(self, keys):
        now = time.time()
        with self.server.auth_rate_lock:
            for key in keys:
                window_start, count = self.server.auth_rate_windows.get(
                    key, (now, 0)
                )
                if (
                    now - window_start < AUTH_RATE_LIMIT_WINDOW_SECONDS
                    and count >= AUTH_RATE_LIMIT_ATTEMPTS
                ):
                    return True
        return False

    def record_auth_failure(self, keys):
        now = time.time()
        with self.server.auth_rate_lock:
            for key in keys:
                window_start, count = self.server.auth_rate_windows.get(
                    key, (now, 0)
                )
                if now - window_start >= AUTH_RATE_LIMIT_WINDOW_SECONDS:
                    window_start, count = now, 0
                self.server.auth_rate_windows[key] = (window_start, count + 1)
            if len(self.server.auth_rate_windows) > AUTH_RATE_LIMIT_MAX_KEYS:
                cutoff = now - AUTH_RATE_LIMIT_WINDOW_SECONDS
                self.server.auth_rate_windows = {
                    key: value
                    for key, value in self.server.auth_rate_windows.items()
                    if value[0] >= cutoff
                }

    def clear_auth_failures(self, key):
        with self.server.auth_rate_lock:
            self.server.auth_rate_windows.pop(key, None)

    def cookie_secure(self):
        configured = os.environ.get("QA_COOKIE_SECURE", "").strip().lower()
        if configured in {"1", "true", "yes", "on"}:
            return True
        if configured in {"0", "false", "no", "off"}:
            return False
        forwarded = self.headers.get("X-Forwarded-Proto", "")
        return forwarded.split(",", 1)[0].strip().lower() == "https"

    def session_cookie_flags(self):
        return "; Secure" if self.cookie_secure() else ""

    def session_cookie(self, name):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        item = cookie.get(name)
        return item.value if item else None

    def current_user(self, db=None):
        admin_token = self.session_cookie("trace_admin_session")
        with self.server.admin_session_lock:
            expiry = self.server.admin_sessions.get(admin_token)
            if expiry is not None and expiry > time.time():
                return {
                    "id": None, "role_name": "Global Admin",
                    "email": "", "global_admin": True, "tenant_id": 1,
                }
            if expiry is not None:
                self.server.admin_sessions.pop(admin_token, None)
        if admin_token:
            persisted = self.load_persisted_session(admin_token, "admin")
            if persisted:
                expiry = persisted[1]
                with self.server.admin_session_lock:
                    self.server.admin_sessions[admin_token] = expiry
                return {
                    "id": None, "role_name": "Global Admin",
                    "email": "", "global_admin": True, "tenant_id": 1,
                }

        token = self.session_cookie("trace_user_session")
        with self.server.admin_session_lock:
            session = self.server.user_sessions.get(token)
            if session is None and token:
                persisted = self.load_persisted_session(token, "user")
                if persisted:
                    session = (persisted[0], persisted[1])
                    self.server.user_sessions[token] = session
            if session is None:
                return None
            user_id, expiry = session
            if expiry <= time.time():
                self.server.user_sessions.pop(token, None)
                self.delete_persisted_session(token)
                return None
        close_db = db is None
        if close_db:
            db = connect(self.server.db_path)
        try:
            requested_tenant = self.headers.get("X-Tenant-ID")
            tenant_id = None
            if requested_tenant:
                try:
                    tenant_id = int(requested_tenant)
                except ValueError:
                    return None
            row = db.execute(
                """SELECT u.id,u.email,u.first_name,u.last_name,u.status,r.name role_name,
                m.tenant_id,m.role_id,t.status workspace_status
                FROM users u
                JOIN tenant_memberships m ON m.user_id=u.id AND m.status='Active'
                JOIN roles r ON r.id=m.role_id AND r.status='Active'
                JOIN tenants t ON t.id=m.tenant_id
                WHERE u.id=? AND u.status='Active'
                AND (? IS NULL OR m.tenant_id=?)
                ORDER BY m.tenant_id LIMIT 1""",
                (user_id, tenant_id, tenant_id),
            ).fetchone()
            if not row:
                active_account = db.execute(
                    """SELECT 1 FROM users u WHERE u.id=? AND u.status='Active'
                    AND EXISTS (
                        SELECT 1 FROM tenant_memberships m
                        JOIN tenants t ON t.id=m.tenant_id
                        JOIN roles r ON r.id=m.role_id AND r.status='Active'
                        WHERE m.user_id=u.id AND m.status='Active'
                    )""",
                    (user_id,),
                ).fetchone()
                if not active_account:
                    with self.server.admin_session_lock:
                        self.server.user_sessions.pop(token, None)
                return None
            if row["status"] != "Active":
                with self.server.admin_session_lock:
                    self.server.user_sessions.pop(token, None)
                return None
            return dict(row)
        finally:
            if close_db:
                db.close()

    def is_authenticated(self):
        return self.current_user() is not None

    def is_admin(self, db=None):
        user = self.current_user(db)
        return bool(user and self.is_global_admin(user))

    @staticmethod
    def is_global_admin(user):
        return bool(user and user.get("global_admin"))

    @staticmethod
    def is_tenant_admin(user):
        return bool(
            user and not user.get("global_admin")
            and user.get("role_name") in ("Global Admin", "Admin", "Workspace Owner")
        )

    def is_workspace_admin(self, db, user):
        if not user:
            return False
        if self.is_global_admin(user) or self.is_tenant_admin(user):
            return True
        if not user.get("id") or user.get("tenant_id") is None:
            return False
        return bool(db.execute(
            "SELECT 1 FROM tenants WHERE id=? AND owner_user_id=?",
            (user["tenant_id"], user["id"]),
        ).fetchone())

    def auth_status(self, db):
        password = os.environ.get("QA_ADMIN_PASSWORD", "")
        user = self.current_user(db)
        workspace_state = (
            workspace_access(db, user["tenant_id"])
            if user and user.get("tenant_id") is not None
            and not self.is_global_admin(user)
            else None
        )
        if user and user.get("global_admin"):
            profile = db.execute(
                "SELECT first_name,last_name,email FROM global_admin_profile WHERE id=1"
            ).fetchone()
            if profile:
                user.update(dict(profile))
        return {
            "authenticated": user is not None,
            "admin_configured": len(password.strip()) >= 12,
            "user": user,
            "permissions": self.user_permissions(db, user) if user else [],
            "workspace": workspace_state["workspace"] if workspace_state else None,
            "subscription": workspace_state["subscription"] if workspace_state else None,
            "workspace_access": (
                workspace_state["access_allowed"] if workspace_state
                else bool(user and self.is_global_admin(user))
            ),
            "workspace_access_code": workspace_state["access_code"] if workspace_state else None,
        }

    @classmethod
    def user_permissions(cls, db, user):
        if not user:
            return []
        if cls.is_global_admin(user):
            return ["*"]
        if not user.get("role_id"):
            return []
        return role_permission_keys(db, user["role_id"])

    def role_in_scope(self, db, role_id, actor):
        role = db.execute(
            "SELECT * FROM roles WHERE id=? AND status='Active'", (role_id,)
        ).fetchone()
        if not role:
            return None
        if self.is_global_admin(actor):
            return role
        tenant_id = (actor or {}).get("tenant_id")
        if role["tenant_id"] is not None and role["tenant_id"] != tenant_id:
            return None
        return role

    def can_assign_role(self, db, actor, role):
        if not self.is_global_admin(actor):
            tenant_id = (actor or {}).get("tenant_id")
            if role["tenant_id"] is not None and role["tenant_id"] != tenant_id:
                return False
        target_permissions = set(role_permission_keys(db, role["id"]))
        developer_permissions = {
            permission for permission in target_permissions
            if permission.startswith("developer.")
        }
        if developer_permissions:
            if not self.is_global_admin(actor):
                return False
            return True
        if self.is_global_admin(actor) or self.is_tenant_admin(actor):
            return True
        if role["name"] in ("Admin", "Global Admin", "Developer"):
            return False
        actor_permissions = set(self.user_permissions(db, actor))
        return "*" not in target_permissions and target_permissions.issubset(actor_permissions)

    def request_permission(self, method, path):
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "users"]:
            if len(parts) == 4 and parts[3] in ("activate", "deactivate"):
                return f"users.{parts[3]}" if method == "POST" else None
            action = {"GET": "view", "POST": "create", "PATCH": "update", "DELETE": "delete"}.get(method)
            return f"users.{action}" if action else None
        if parts[:2] == ["api", "teams"]:
            action = {"GET": "view", "POST": "create", "PATCH": "edit", "DELETE": "delete"}.get(method)
            if len(parts) >= 4 and parts[3] in ("members", "owner"):
                return "teams.manage-members" if method in ("POST", "DELETE", "PATCH") else "teams.view"
            return f"teams.{action}" if action else None
        if parts[:2] == ["api", "roles"]:
            action = {"GET": "view", "POST": "create", "PATCH": "edit", "DELETE": "delete"}.get(method)
            if len(parts) >= 4 and parts[3] == "permissions":
                return "roles.manage-permissions"
            return f"roles.{action}" if action else None
        if parts[:2] == ["api", "profile"]:
            return "profile.edit" if method in ("PATCH", "POST") else "profile.view"
        if parts[:2] == ["api", "auth"]:
            return None
        if parts[:2] == ["api", "ai"]:
            return None
        if parts[:2] == ["api", "workspace"] and parts[2:3] == ["storage"]:
            if method == "DELETE":
                return "storage.delete"
            if method == "POST" and parts[3:4] == ["bulk-delete"]:
                return "storage.delete"
            return "storage.view"
        if parts[:2] == ["api", "developer"]:
            if parts[2:4] == ["errors", "report"]:
                return None
            if method == "PATCH":
                return "developer.manage"
            if method == "DELETE":
                return "developer.delete"
            return "developer.view"
        if parts[:2] == ["api", "custom-fields"]:
            return "admin" if method in ("POST", "PATCH", "DELETE") else None
        if parts[:2] == ["api", "archive"] or parts[:2] == ["api", "audit-events"]:
            return "admin"
        if len(parts) >= 2 and parts[:2] == ["api", "projects"]:
            module = "projects"
            if len(parts) >= 4:
                module = {
                    "requirements": "requirements",
                    "test-cases": "test-cases",
                    "applications": "applications",
                    "api-docs": "api-docs",
                    "analyses": "app-analysis",
                    "bugs": "bugs",
                    "report": "reports",
                    "dashboard": "projects",
                    "attachments": "projects",
                    "files": "projects",
                    "executions": "test-cases",
                    "evidence": "test-cases",
                }.get(parts[3], "projects")
            action = {"GET": "view", "POST": "create", "PATCH": "edit", "DELETE": "delete"}.get(method)
            if method == "POST" and len(parts) >= 5 and parts[4] in ("generate", "analyze", "validate-requirements"):
                action = "create"
            if method == "POST" and len(parts) >= 4 and parts[3] == "executions":
                action = "edit"
            if method == "POST" and len(parts) >= 5 and parts[3:5] == ["requirements", "suggestions"]:
                action = "edit"
            return f"{module}.{action}" if action else None
        return None

    def authorize_request(self, method, path, db=None):
        parsed = urlparse(path)
        path_clean = unquote(parsed.path)
        parts_clean = path_clean.strip("/").split("/")
        if parts_clean == ["health"] or parts_clean == ["healthz"] or parts_clean == ["api", "health"] or parts_clean == ["api", "healthz"]:
            return True
        if parts_clean[:2] == ["api", "auth"]:
            return True
        if parts_clean[:4] == ["api", "developer", "errors", "report"]:
            return True
        user = self.current_user(db)
        if user is None:
            self.send_json({"error": "Sign in is required."}, 401)
            return False
        self._request_tenant_id = user.get("tenant_id")
        if self._request_tenant_id is None and user.get("global_admin"):
            self._request_tenant_id = 0
        if parts_clean[:2] == ["api", "product-owner"]:
            if not self.is_global_admin(user):
                self.send_json({
                    "error": "System Global Admin access is required.",
                    "code": "PRODUCT_OWNER_ACCESS_DENIED",
                }, 403)
                return False
            return True
        if parts_clean[:2] == ["api", "developer"]:
            permission = self.request_permission(method, path_clean)
            permissions = self.user_permissions(db, user)
            if (
                permission is not None
                and "*" not in permissions
                and permission not in permissions
            ):
                self.send_json({
                    "error": f"Your role does not allow this action ({permission}).",
                }, 403)
                return False
            return True
        if (
            len(parts_clean) == 5
            and parts_clean[:2] == ["api", "workspaces"]
            and parts_clean[3:] == ["subscription", "reactivate"]
        ):
            if not self.is_global_admin(user):
                self.send_json({
                    "error": "System Global Admin access is required.",
                    "code": "WORKSPACE_REACTIVATION_DENIED",
                }, 403)
                return False
            return True
        if (
            len(parts_clean) == 4
            and parts_clean[:2] == ["api", "workspaces"]
            and parts_clean[3] == "owner"
            and method == "POST"
        ):
            try:
                target_tenant_id = int(parts_clean[2])
            except ValueError:
                self.send_json({"error": "Invalid workspace ID."}, 400)
                return False
            tenant = db.execute(
                "SELECT owner_user_id FROM tenants WHERE id=?",
                (target_tenant_id,),
            ).fetchone()
            can_transfer = (
                tenant is not None
                and (
                    self.is_global_admin(user)
                    or (
                        user.get("tenant_id") == target_tenant_id
                        and self.is_tenant_admin(user)
                    )
                )
            )
            if not can_transfer:
                self.send_json({
                    "error": "Workspace owner or System Global Admin access is required.",
                    "code": "WORKSPACE_OWNER_TRANSFER_DENIED",
                }, 403)
                return False
            return True
        account_route = parts_clean[:2] == ["api", "profile"]
        access = workspace_access(db, user["tenant_id"])
        if (
            not self.is_global_admin(user)
            and not account_route
            and not access["access_allowed"]
        ):
            code = access["access_code"] or "WORKSPACE_ACCESS_DENIED"
            message = (
                f"{TRIAL_EXPIRY_REASON} Contact support to restore workspace access."
                if code == "TRIAL_EXPIRED"
                else "This workspace is suspended. Contact your workspace administrator."
            )
            self.send_json({"error": message, "code": code}, 403)
            return False
        permission = self.request_permission(method, path_clean)
        parts = path.strip("/").split("/")
        tenant_admin_area = parts[:2] in (
            ["api", "users"], ["api", "teams"], ["api", "roles"],
            ["api", "audit-events"],
        )
        if tenant_admin_area and not (
            self.is_global_admin(user) or self.is_tenant_admin(user)
        ):
            self.send_json({"error": "Global Admin access is required."}, 403)
            return False
        if parts[:2] == ["api", "archive"] and not self.is_global_admin(user):
            self.send_json({"error": "System Global Admin access is required."}, 403)
            return False
        if permission == "admin":
            tenant_audit_access = (
                parts[:2] == ["api", "audit-events"]
                and self.is_tenant_admin(user)
            )
            tenant_custom_field_access = (
                parts[:2] == ["api", "custom-fields"]
                and self.is_tenant_admin(user)
            )
            if not self.is_global_admin(user) and not (
                tenant_audit_access or tenant_custom_field_access
            ):
                self.send_json({"error": "System Global Admin access is required."}, 403)
                return False
            if self.is_global_admin(user):
                return True
            if tenant_audit_access:
                return True
        permissions = self.user_permissions(db, user)
        if permission is not None and "*" not in permissions and permission not in permissions:
            self.send_json({"error": f"Your role does not allow this action ({permission})."}, 403)
            return False
        if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
            try:
                project_id = int(parts[2])
            except ValueError:
                self.send_json({"error": "Invalid project ID."}, 400)
                return False
            project = db.execute(
                "SELECT tenant_id FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if not project or (
                not self.is_global_admin(user)
                and project["tenant_id"] != user.get("tenant_id")
            ):
                self.send_json({"error": "Project not found."}, 404)
                return False
        return True

    def ai_auth_required(self, method: str, path: str) -> bool:
        if method != "POST":
            return False
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "ai"] and parts[2:] in (
            ["settings"], ["test"], ["ollama", "test"]
        ):
            return True
        if len(parts) == 5 and parts[1] == "projects":
            return (
                parts[3:] in (
                    ["requirements", "import"],
                    ["requirements", "analyze"],
                    ["test-cases", "generate"],
                )
            )
        if (
            len(parts) == 6
            and parts[1] == "projects"
            and parts[3] == "applications"
            and parts[5] == "validate-requirements"
        ):
            return True
        return False

    @staticmethod
    def custom_field_admin_required(method: str, path: str) -> bool:
        return method in ("POST", "PATCH", "DELETE") and path.strip("/").split("/")[:2] == [
            "api", "custom-fields",
        ]

    def persist_session(self, token, user_id, kind, expires_at):
        try:
            with database(self.server.db_path) as db:
                db.execute(
                    "DELETE FROM auth_sessions WHERE expires_at<=?",
                    (time.time(),),
                )
                db.execute(
                    """INSERT INTO auth_sessions
                    (token_hash,user_id,kind,expires_at,created_at)
                    VALUES(?,?,?,?,?)""",
                    (
                        session_token_hash(token), user_id, kind, expires_at,
                        timestamp(),
                    ),
                )
        except DATABASE_ERRORS as exc:
            self.log_exception(exc, source="auth_session", action="persist")

    def load_persisted_session(self, token, kind):
        if not token:
            return None
        try:
            with database(self.server.db_path) as db:
                row = db.execute(
                    """SELECT user_id,expires_at FROM auth_sessions
                    WHERE token_hash=? AND kind=?""",
                    (session_token_hash(token), kind),
                ).fetchone()
                if not row:
                    return None
                if float(row["expires_at"]) <= time.time():
                    db.execute(
                        "DELETE FROM auth_sessions WHERE token_hash=?",
                        (session_token_hash(token),),
                    )
                    return None
                return (row["user_id"], float(row["expires_at"]))
        except (DATABASE_ERRORS, TypeError, ValueError) as exc:
            self.log_exception(exc, source="auth_session", action="load")
            return None

    def delete_persisted_session(self, token, db=None):
        if not token:
            return
        try:
            if db is None:
                with database(self.server.db_path) as session_db:
                    session_db.execute(
                        "DELETE FROM auth_sessions WHERE token_hash=?",
                        (session_token_hash(token),),
                    )
            else:
                db.execute(
                    "DELETE FROM auth_sessions WHERE token_hash=?",
                    (session_token_hash(token),),
                )
        except DATABASE_ERRORS as exc:
            self.log_exception(exc, source="auth_session", action="delete")

    def issue_admin_session(self):
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + 12 * 60 * 60
        with self.server.admin_session_lock:
            self.server.admin_sessions[token] = expires_at
        self.persist_session(token, None, "admin", expires_at)
        return token

    def issue_user_session(self, user_id):
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + 12 * 60 * 60
        with self.server.admin_session_lock:
            self.server.user_sessions[token] = (user_id, expires_at)
        self.persist_session(token, user_id, "user", expires_at)
        return token

    def invalidate_user_sessions(self, user_id, keep_current=False, db=None):
        current_token = self.session_cookie("trace_user_session")
        with self.server.admin_session_lock:
            for token, (session_user_id, _) in list(self.server.user_sessions.items()):
                if session_user_id == user_id and (
                    not keep_current or token != current_token
                ):
                    self.server.user_sessions.pop(token, None)
        try:
            if db is None:
                with database(self.server.db_path) as session_db:
                    self.invalidate_persisted_user_sessions(
                        session_db, user_id, keep_current, current_token
                    )
            else:
                self.invalidate_persisted_user_sessions(
                    db, user_id, keep_current, current_token
                )
        except DATABASE_ERRORS as exc:
            self.log_exception(exc, source="auth_session", action="invalidate")

    @staticmethod
    def invalidate_persisted_user_sessions(db, user_id, keep_current, current_token):
        if keep_current and current_token:
            db.execute(
                """DELETE FROM auth_sessions WHERE user_id=? AND kind='user'
                AND token_hash<>?""",
                (user_id, session_token_hash(current_token)),
            )
        else:
            db.execute(
                "DELETE FROM auth_sessions WHERE user_id=? AND kind='user'",
                (user_id,),
            )

    def peer_is_local(self):
        try:
            if os.environ.get("QA_ALLOW_PUBLIC_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}:
                return True
            if os.environ.get("ALLOW_PUBLIC_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}:
                return True
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    @staticmethod
    def validate_password(password):
        if not isinstance(password, str) or len(password) < 12:
            raise ValueError("Password must be at least 12 characters.")
        if not any(char.islower() for char in password) or not any(
            char.isupper() for char in password
        ) or not any(char.isdigit() for char in password):
            raise ValueError("Password must include uppercase and lowercase letters and a number.")

    @staticmethod
    def validated_permissions(value):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError("Permissions must be a list of permission keys.")
        permissions = list(dict.fromkeys(value))
        invalid = set(permissions) - set(ALL_PERMISSIONS)
        if invalid:
            raise ValueError(f"Unknown permission: {sorted(invalid)[0]}")
        return permissions

    def logout_admin(self, db=None):
        admin_token = self.session_cookie("trace_admin_session")
        user_token = self.session_cookie("trace_user_session")
        with self.server.admin_session_lock:
            self.server.admin_sessions.pop(admin_token, None)
            self.server.user_sessions.pop(user_token, None)
        self.delete_persisted_session(admin_token, db=db)
        self.delete_persisted_session(user_token, db=db)
        return {
            "Set-Cookie": [
                "trace_admin_session=; Path=/; HttpOnly; SameSite=Strict; "
                f"Max-Age=0{self.session_cookie_flags()}",
                "trace_user_session=; Path=/; HttpOnly; SameSite=Strict; "
                f"Max-Age=0{self.session_cookie_flags()}",
            ],
        }

    def body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid request size.")
        if size < 0 or size > MAX_BODY:
            raise ValueError("Request is too large (maximum 27 MB).")
        if (
            urlparse(self.path).path.rstrip("/") == "/api/developer/errors/report"
            and size > MAX_DEVELOPER_REPORT_SIZE
        ):
            raise ValueError("Error reports are limited to 64 KB.")
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise ValueError("Request body was interrupted before upload completed.")
        content_type = self.headers.get("Content-Type", "")
        if urlparse(self.path).path.rstrip("/").endswith("/attachments") and \
            content_type.split(";", 1)[0].strip().lower() != "application/json":
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type == "multipart/form-data":
                raise ValueError("Send attachment contents as a raw file upload.")
            filename = parse_qs(urlparse(self.path).query).get("filename", [""])[0]
            return {
                "filename": filename,
                "content_type": media_type,
                "file_bytes": raw,
                "module_key": parse_qs(urlparse(self.path).query).get("module", [""])[0],
                "record_id": parse_qs(urlparse(self.path).query).get("record_id", [""])[0],
            }
        upload_path = urlparse(self.path).path.rstrip("/").endswith("/applications/import")
        is_zip_upload = (
            content_type.split(";", 1)[0].strip().lower() == "application/zip"
            or (upload_path and raw.startswith(b"PK"))
        )
        if is_zip_upload:
            filename = parse_qs(urlparse(self.path).query).get("filename", [""])[0]
            return {"filename": filename, "archive_bytes": raw}
        if "application/json" in content_type:
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("Request body must be valid JSON.")
            if not isinstance(value, dict):
                raise ValueError("Request body must be a JSON object.")
            return value
        return parse_qs(raw.decode("utf-8"), keep_blank_values=True)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path.rstrip("/") in ("/health", "/healthz"):
            self.send_json({"status": "ok"}, 200)
            return
        if path.rstrip("/") == "/ready":
            try:
                with database(self.server.db_path) as db:
                    db.execute("SELECT 1").fetchone()
            except DATABASE_ERRORS + (RuntimeError,) as exc:
                self.log_exception(exc)
                self.send_json({"status": "not_ready"}, 503)
                return
            self.send_json({"status": "ready"}, 200)
            return
        if not path.startswith("/api/"):
            if path.rstrip("/") in ("/health", "/healthz"):
                self.send_json({"status": "ok"}, 200)
                return
            self.static(path)
            return
        if path.rstrip("/") in ("/api/health", "/api/healthz"):
            self.send_json({"status": "ok"}, 200)
            return
        try:
            with database(self.server.db_path) as db:
                self._request_db = db
                try:
                    if not self.authorize_request("GET", path, db):
                        return
                    self.get_api(db, path)
                finally:
                    self._request_db = None
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        except DATABASE_ERRORS as exc:
            self.log_exception(exc)
            self.send_json({"error": "Database error while reading the requested data."}, 500)
        except OSError:
            self.send_json({"error": "Storage error while reading the requested file."}, 500)
        except Exception as exc:
            self.log_exception(exc)
            self.send_json({"error": "Unexpected server error while handling the request. Check the server console."}, 500)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        if not origin or origin not in self.server.cors_origins:
            self.send_json({"error": "Origin is not allowed."}, 403)
            return
        self.send_bytes(
            b"",
            "text/plain; charset=utf-8",
            204,
            {
                "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-Tenant-ID",
                "Access-Control-Max-Age": "600",
            },
        )

    def do_POST(self):
        try:
            request_path = urlparse(self.path).path
            if request_path.rstrip("/") == "/api/developer/errors/report":
                self.post_developer_report(self.body())
                return
            with database(self.server.db_path) as db:
                self._request_db = db
                try:
                    if not self.authorize_request("POST", request_path, db):
                        return
                finally:
                    self._request_db = None
            data = self.body()
            with database(self.server.db_path) as db:
                self._request_db = db
                try:
                    self.post_api(db, request_path, data)
                    parts = request_path.strip("/").split("/")
                    if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
                        try:
                            project_id = int(parts[2])
                        except ValueError:
                            project_id = None
                        if project_id is not None:
                            sync_project_tenant_data(db, project_id)
                finally:
                    self._request_db = None
        except DATABASE_INTEGRITY_ERRORS as exc:
            if integrity_error_is(exc, "users.email", "users_email_key", "ux_users_email_ci"):
                self.send_json({"error": "An account with this email already exists."}, 409)
            elif integrity_error_is(exc, "teams.name", "teams_name_key", "ux_teams_name_ci"):
                self.send_json({"error": "A team with this name already exists."}, 409)
            elif integrity_error_is(exc, "roles.name", "roles_name_key", "ux_roles_name_ci"):
                self.send_json({"error": "A role with this name already exists."}, 409)
            else:
                self.send_json({"error": "The request conflicts with existing data."}, 409)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        except DATABASE_ERRORS as exc:
            self.log_exception(exc)
            self.send_json({"error": "Database error while processing the request."}, 500)
        except OSError:
            self.send_json({"error": "Storage error while processing the request."}, 500)
        except Exception as exc:
            self.log_exception(exc)
            self.send_json({"error": "Unexpected server error while processing the request. Check the server console."}, 500)

    def do_PATCH(self):
        try:
            request_path = urlparse(self.path).path
            with database(self.server.db_path) as db:
                if not self.authorize_request("PATCH", request_path, db):
                    return
            data = self.body()
            with database(self.server.db_path) as db:
                self._request_db = db
                try:
                    self.patch_api(db, request_path, data)
                    parts = request_path.strip("/").split("/")
                    if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
                        try:
                            project_id = int(parts[2])
                        except ValueError:
                            project_id = None
                        if project_id is not None:
                            sync_project_tenant_data(db, project_id)
                finally:
                    self._request_db = None
        except DATABASE_INTEGRITY_ERRORS as exc:
            if integrity_error_is(exc, "users.email", "users_email_key", "ux_users_email_ci"):
                self.send_json({"error": "An account with this email already exists."}, 409)
            elif integrity_error_is(exc, "teams.name", "teams_name_key", "ux_teams_name_ci"):
                self.send_json({"error": "A team with this name already exists."}, 409)
            elif integrity_error_is(exc, "roles.name", "roles_name_key", "ux_roles_name_ci"):
                self.send_json({"error": "A role with this name already exists."}, 409)
            else:
                self.send_json({"error": "The request conflicts with existing data."}, 409)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        except DATABASE_ERRORS as exc:
            self.log_exception(exc)
            self.send_json({"error": "Database error while updating the record."}, 500)
        except Exception as exc:
            self.log_exception(exc)
            self.send_json({"error": "Unexpected server error while updating the record. Check the server console."}, 500)

    def do_DELETE(self):
        path = unquote(urlparse(self.path).path)
        try:
            with database(self.server.db_path) as db:
                if not self.authorize_request("DELETE", path, db):
                    return
        except DATABASE_ERRORS as exc:
            self.log_exception(exc)
            self.send_json({"error": "Database error while deleting the record."}, 500)
            return
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "projects"]:
            project_dir = None
            archived_dir = None
            try:
                with database(self.server.db_path) as db:
                    pid = self.parse_project_id(parts[2])
                    project = db.execute(
                        "SELECT project_key FROM projects WHERE id=?", (pid,)
                    ).fetchone()
                    if not project:
                        response = ({"error": "Project not found"}, 404)
                    else:
                        project_dir = project_storage_dir(self.server.storage_root, pid)
                        if project_dir.is_symlink():
                            raise OSError("Project storage directory cannot be archived because it is a symbolic link.")
                        if project_dir.exists():
                            archive_root = Path(self.server.storage_root) / "archived-projects"
                            archive_root.mkdir(parents=True, exist_ok=True)
                            archived_dir = archive_root / (
                                f"{pid}-{project['project_key']}-{uuid.uuid4().hex}"
                            )
                            project_dir.replace(archived_dir)
                            os.utime(archived_dir, None)
                        cleanup_project_form_data(db, pid)
                        db.execute("DELETE FROM projects WHERE id=?", (pid,))
                        response = ({
                            "deleted": True,
                            "files_archived": archived_dir is not None,
                        }, 200)
            except Exception as exc:
                if archived_dir is not None and archived_dir.exists() and project_dir is not None:
                    try:
                        archived_dir.replace(project_dir)
                    except OSError as rollback_error:
                        rollback_trace = redact_sensitive_text(
                            traceback.format_exc(), 20000
                        )
                        print(json.dumps({
                            "timestamp": timestamp(),
                            "severity": "critical",
                            "service": "backend",
                            "environment": redact_sensitive_text(
                                os.environ.get(
                                    "QA_ENVIRONMENT",
                                    os.environ.get("ENVIRONMENT", "development"),
                                ),
                                100,
                            ),
                            "request_id": self.request_identifier(),
                            "message": redact_sensitive_text(rollback_error, 1000),
                            "stack_trace": rollback_trace,
                        }, ensure_ascii=True), file=sys.stderr)
                self._handle_delete_exception(exc)
                return
            self.send_json(*response)
            return
        try:
            with database(self.server.db_path) as db:
                self.delete_api(db, path)
        except Exception as exc:
            self._handle_delete_exception(exc)

    def _handle_delete_exception(self, exc):
        if isinstance(exc, ValueError):
            self.send_json({"error": str(exc)}, 400)
        elif isinstance(exc, DATABASE_INTEGRITY_ERRORS):
            self.send_json({"error": "The record cannot be deleted because it is still in use."}, 409)
        elif isinstance(exc, DATABASE_ERRORS):
            self.log_exception(exc)
            self.send_json({"error": "Database error while deleting the record."}, 500)
        elif isinstance(exc, OSError):
            self.send_json({"error": "Storage error while deleting the record."}, 500)
        else:
            self.log_exception(exc)
            self.send_json({"error": "Unexpected server error while deleting the record. Check the server console."}, 500)

    def static(self, path):
        if path.rstrip("/") in ("/health", "/healthz"):
            self.send_json({"status": "ok"}, 200)
            return
        clean_path = path.rstrip("/") or "/"
        if clean_path in ("/developer", "/developer.html"):
            path = "/developer.html"
        elif clean_path in ("/product-owner", "/product-owner.html"):
            path = "/product-owner.html"
        elif clean_path in (
            "/", "/index.html", "/about", "/privacy", "/terms",
            "/login", "/signup", "/workspace", "/workspace/storage",
        ):
            path = "/index.html"
        target = (FRONTEND_ROOT / path.lstrip("/")).resolve()
        try:
            target.relative_to(FRONTEND_ROOT.resolve())
        except ValueError:
            self.send_json({"error": "Not found"}, 404)
            return
        if not target.is_file():
            self.send_json({"error": "Not found"}, 404)
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
        }.get(target.suffix.lower(), mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_bytes(target.read_bytes(), content_type)


    def workspace_tenant(self, user):
        return (user or {}).get("tenant_id") or PERMANENT_WORKSPACE_ID

    def get_workspace_storage(self, db, path):
        user = self.current_user(db)
        if not user:
            self.send_json({"error": "Sign in is required."}, 401)
            return
        tenant_id = self.workspace_tenant(user)
        parsed = urlparse(self.path)
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[3] == "summary":
            summary = read_workspace_storage_snapshot(db, tenant_id)
            if not summary:
                scanned = workspace_storage_scan(
                    db, self.server.storage_root, tenant_id
                )
                save_workspace_storage_snapshot(
                    db, tenant_id, scanned["summary"]
                )
                summary = read_workspace_storage_snapshot(db, tenant_id)
            self.send_json({"summary": storage_quota_summary(summary)})
            return
        if (
            len(parts) == 6
            and parts[3] == "items"
            and parts[5] == "download"
        ):
            result = storage_item_download(
                db, self.server.storage_root, tenant_id, parts[4]
            )
            if not result:
                self.send_json({"error": "Storage item not found."}, 404)
                return
            content, content_type, filename = result
            self.send_bytes(
                content,
                content_type,
                200,
                extra={
                    "Content-Disposition": (
                        "attachment; filename*=UTF-8''" + quote(filename)
                    )
                },
            )
            return
        query = parse_qs(parsed.query)
        kind = query.get("kind", [""])[0].strip()
        if kind and kind not in STORAGE_ITEM_KINDS:
            self.send_json({"error": "Invalid storage item type."}, 400)
            return
        project_id = None
        if query.get("project_id", [""])[0].strip():
            try:
                project_id = int(query["project_id"][0])
            except ValueError:
                self.send_json({"error": "Invalid project ID."}, 400)
                return
        sort = query.get("sort", ["size_desc"])[0]
        if sort not in ("size_desc", "size_asc", "name_asc", "recent_desc"):
            self.send_json({"error": "Invalid storage sort."}, 400)
            return
        try:
            page = max(1, int(query.get("page", ["1"])[0]))
            page_size = max(1, min(int(query.get("page_size", ["25"])[0]), 100))
        except ValueError:
            self.send_json({"error": "Invalid pagination."}, 400)
            return
        date_from = parse_date_filter(query, "date_from")
        date_to = parse_date_filter(query, "date_to")
        if date_from and date_to and date_from > date_to:
            self.send_json({"error": "Start date must not be after end date."}, 400)
            return
        payload = workspace_storage_payload(
            db,
            self.server.storage_root,
            tenant_id,
            kind=kind,
            project_id=project_id,
            search=query.get("q", [""])[0].strip(),
            date_from=date_from,
            date_to=date_to,
            sort=sort,
            page=page,
            page_size=page_size,
        )
        self.send_json(payload)

    def post_workspace_storage(self, db, path, data):
        user = self.current_user(db)
        if not user:
            self.send_json({"error": "Sign in is required."}, 401)
            return
        tenant_id = self.workspace_tenant(user)
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[3] == "recalculate":
            payload = workspace_storage_payload(
                db,
                self.server.storage_root,
                tenant_id,
                page=1,
                page_size=25,
                force=True,
            )
            self.send_json(payload)
            return
        if len(parts) >= 4 and parts[3] == "bulk-delete":
            item_ids = data.get("item_ids", data.get("ids"))
            if not isinstance(item_ids, list) or not item_ids:
                raise ValueError("Select at least one storage item to delete.")
            if len(item_ids) > 100:
                raise ValueError("Delete at most 100 storage items at a time.")
            item_ids = [str(item_id) for item_id in item_ids if str(item_id).strip()]
            if not item_ids:
                raise ValueError("Select at least one storage item to delete.")
            result = delete_workspace_storage_items(
                db,
                self.server.storage_root,
                tenant_id,
                user.get("id"),
                item_ids,
            )
            self.send_json(result)
            return
        self.send_json({"error": "Not found."}, 404)

    def get_developer_api(self, db, path):
        user = self.current_user(db)
        if not user:
            self.send_json({"error": "Sign in is required."}, 401)
            return
        parsed = urlparse(self.path)
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[2] == "summary":
            prune_developer_errors(db)
            scope, scope_params = developer_error_scope(user)
            query = parse_qs(parsed.query)
            period = query.get("period", ["24h"])[0].strip()
            period_seconds = {"24h": 24 * 3600, "7d": 7 * 86400, "30d": 30 * 86400}
            if period not in period_seconds:
                self.send_json({"error": "Invalid summary period."}, 400)
                return
            cutoff = (
                datetime.now(timezone.utc) - timedelta(seconds=period_seconds[period])
            ).isoformat(timespec="seconds")
            statuses = {
                row["status"]: row["count"] for row in db.execute(
                    f"""SELECT status,COUNT(*) count FROM developer_errors
                    WHERE {scope} GROUP BY status""",
                    scope_params,
                )
            }
            sources = {
                row["source"]: row["count"] for row in db.execute(
                    f"""SELECT source,COUNT(*) count FROM developer_errors
                    WHERE {scope} GROUP BY source""",
                    scope_params,
                )
            }
            severities = {
                row["severity"]: row["count"] for row in db.execute(
                    f"""SELECT severity,COUNT(*) count FROM developer_errors
                    WHERE {scope} AND status IN ('open','acknowledged')
                    GROUP BY severity""",
                    scope_params,
                )
            }
            recent = db.execute(
                f"""SELECT COUNT(*) errors,COALESCE(SUM(occurrence_count),0) occurrences
                FROM developer_errors WHERE {scope} AND last_seen_at>=?""",
                [*scope_params, cutoff],
            ).fetchone()
            recurring = db.execute(
                f"""SELECT COUNT(*) errors,COALESCE(SUM(occurrence_count),0) occurrences
                FROM developer_errors WHERE {scope} AND last_seen_at>=?
                AND occurrence_count>1""",
                [*scope_params, cutoff],
            ).fetchone()
            totals = db.execute(
                f"""SELECT COUNT(*) errors,COALESCE(SUM(occurrence_count),0) occurrences
                FROM developer_errors WHERE {scope}""",
                scope_params,
            ).fetchone()
            recent_rows = db.execute(
                f"""SELECT id,source,severity,status,error_type,message,route,
                occurrence_count,first_seen_at,last_seen_at
                FROM developer_errors WHERE {scope}
                ORDER BY last_seen_at DESC LIMIT 8""",
                scope_params,
            ).fetchall()
            trend = db.execute(
                f"""SELECT substr(last_seen_at,1,10) day,COUNT(*) errors,
                COALESCE(SUM(occurrence_count),0) occurrences
                FROM developer_errors WHERE {scope} AND last_seen_at>=?
                GROUP BY substr(last_seen_at,1,10) ORDER BY day""",
                [*scope_params, cutoff],
            ).fetchall()
            top_endpoints = db.execute(
                f"""SELECT route endpoint,COUNT(*) errors,
                COALESCE(SUM(occurrence_count),0) occurrences
                FROM developer_errors WHERE {scope} AND last_seen_at>=?
                AND route<>'' GROUP BY route ORDER BY occurrences DESC LIMIT 8""",
                [*scope_params, cutoff],
            ).fetchall()
            top_types = db.execute(
                f"""SELECT error_type,COUNT(*) errors,
                COALESCE(SUM(occurrence_count),0) occurrences
                FROM developer_errors WHERE {scope} AND last_seen_at>=?
                GROUP BY error_type ORDER BY occurrences DESC LIMIT 8""",
                [*scope_params, cutoff],
            ).fetchall()
            api_requests = developer_api_metrics(
                self.server, user, period_seconds[period]
            )
            self.send_json({
                "generated_at": timestamp(),
                "period": period,
                "totals": {
                    "errors": totals["errors"],
                    "occurrences": totals["occurrences"],
                    "last_24h": recent["errors"],
                    "last_24h_occurrences": recent["occurrences"],
                    "period_errors": recent["errors"],
                    "period_occurrences": recent["occurrences"],
                    "recurring_errors": recurring["errors"],
                    "recurring_occurrences": recurring["occurrences"],
                },
                "open": statuses.get("open", 0) + statuses.get("acknowledged", 0),
                "statuses": statuses,
                "sources": sources,
                "unresolved_severities": severities,
                "recent": [dict(row) for row in recent_rows],
                "frequency": [dict(row) for row in trend],
                "top_endpoints": [dict(row) for row in top_endpoints],
                "top_error_types": [dict(row) for row in top_types],
                "api_requests": api_requests,
            })
            return
        if len(parts) >= 3 and parts[2] == "health":
            self.send_json(developer_health(db, self.server.storage_root))
            return
        if len(parts) >= 3 and parts[2] == "errors":
            scope, scope_params = developer_error_scope(user)
            if len(parts) >= 4:
                try:
                    error_id = int(parts[3])
                except ValueError:
                    self.send_json({"error": "Invalid error ID."}, 400)
                    return
                row = db.execute(
                    f"""SELECT * FROM developer_errors
                    WHERE id=? AND {scope}""",
                    [error_id, *scope_params],
                ).fetchone()
                if not row:
                    self.send_json({"error": "Error not found."}, 404)
                    return
                self.send_json({"record": developer_error_dict(row)})
                return
            query = parse_qs(parsed.query)
            clauses = [scope]
            params = list(scope_params)
            date_from = parse_date_filter(query, "date_from")
            date_to = parse_date_filter(query, "date_to")
            if date_from and date_to and date_from > date_to:
                self.send_json({"error": "Start date must not be after end date."}, 400)
                return
            if date_from:
                clauses.append("substr(last_seen_at,1,10)>=?")
                params.append(date_from)
            if date_to:
                clauses.append("substr(last_seen_at,1,10)<=?")
                params.append(date_to)
            for field, column, allowed in (
                ("source", "source", DEVELOPER_ERROR_SOURCES),
                ("severity", "severity", DEVELOPER_ERROR_SEVERITIES),
                ("status", "status", DEVELOPER_ERROR_STATUSES),
            ):
                value = query.get(field, [""])[0].strip()
                if value:
                    if value not in allowed:
                        self.send_json({"error": f"Invalid {field} filter."}, 400)
                        return
                    clauses.append(f"{column}=?")
                    params.append(value)
            search = query.get("q", [""])[0].strip()
            if search:
                if len(search) > 200:
                    self.send_json({"error": "Search terms are limited to 200 characters."}, 400)
                    return
                clauses.append(
                    "(LOWER(message) LIKE ? OR LOWER(route) LIKE ? OR LOWER(error_type) LIKE ?)"
                )
                needle_value = f"%{search.casefold()}%"
                params.extend([needle_value, needle_value, needle_value])
            where = " AND ".join(f"({clause})" for clause in clauses)
            try:
                page = max(1, int(query.get("page", ["1"])[0]))
                page_size = max(1, min(int(query.get("page_size", ["25"])[0]), 100))
            except ValueError:
                self.send_json({"error": "Invalid pagination."}, 400)
                return
            total = db.execute(
                f"SELECT COUNT(*) count FROM developer_errors WHERE {where}",
                params,
            ).fetchone()["count"]
            pages = max(1, math.ceil(total / page_size))
            page = min(page, pages)
            rows = db.execute(
                f"""SELECT * FROM developer_errors WHERE {where}
                ORDER BY last_seen_at DESC,id DESC LIMIT ? OFFSET ?""",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            self.send_json({
                "errors": [developer_error_dict(row) for row in rows],
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "pages": pages,
                },
            })
            return
        self.send_json({"error": "Not found."}, 404)

    def post_developer_report(self, data):
        if not self.developer_report_allowed():
            self.send_json({
                "error": "Too many error reports. Try again shortly.",
            }, 429)
            return
        source = str(data.get("source", "frontend")).strip().lower()
        if source not in ("frontend", "api"):
            raise ValueError("Only frontend and API error reports are accepted.")
        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("An error message is required.")
        message = message.strip()
        if len(message) > 4000:
            raise ValueError("Error messages are limited to 4,000 characters.")
        severity = str(data.get("severity", "error")).strip().lower()
        if severity not in ("info", "warning", "error", "critical"):
            raise ValueError("Invalid error severity.")
        stack_trace = data.get("stack_trace", "")
        error_type = data.get("error_type", "")
        route = data.get("route", "")
        method = data.get("method", "")
        if not all(isinstance(value, str) for value in (
            stack_trace, error_type, route, method
        )):
            raise ValueError("Error details must be text values.")
        if len(stack_trace) > 20000:
            raise ValueError("Stack traces are limited to 20,000 characters.")
        if len(error_type) > 200 or len(route) > 500 or len(method) > 20:
            raise ValueError("Error detail fields exceed their allowed lengths.")
        if "metadata" in data and not isinstance(data["metadata"], dict):
            raise ValueError("Error metadata must be an object.")
        user = self.current_user()
        tenant_id = (user or {}).get("tenant_id")
        project_id = data.get("project_id")
        try:
            if isinstance(project_id, bool):
                raise ValueError
            project_id = int(project_id) if project_id is not None else None
        except (TypeError, ValueError):
            raise ValueError("Project ID must be a positive integer.")
        if project_id is not None and project_id <= 0:
            raise ValueError("Project ID must be a positive integer.")
        status_code = data.get("status_code")
        if status_code is not None:
            try:
                if isinstance(status_code, bool):
                    raise ValueError
                status_code = int(status_code)
            except (TypeError, ValueError):
                raise ValueError("Status code must be an HTTP status integer.")
            if not 100 <= status_code <= 599:
                raise ValueError("Status code must be between 100 and 599.")
        if project_id is not None:
            global_admin = bool((user or {}).get("global_admin"))
            if tenant_id is None and not global_admin:
                raise ValueError("Sign in to associate an error with a project.")
            with database(self.server.db_path) as db:
                project_scope = "" if global_admin else " AND tenant_id=?"
                project_params = [project_id]
                if not global_admin:
                    project_params.append(tenant_id)
                project = db.execute(
                    f"SELECT tenant_id FROM projects WHERE id=?{project_scope}",
                    project_params,
                ).fetchone()
                if not project:
                    raise ValueError("Project not found in this workspace.")
                tenant_id = project["tenant_id"]
        metadata = dict(data.get("metadata") or {})
        metadata.setdefault(
            "environment",
            os.environ.get("QA_ENVIRONMENT", os.environ.get("ENVIRONMENT", "development")),
        )
        found_id, created = record_developer_error(
            self.server.db_path,
            source=source,
            message=message,
            severity=severity,
            error_type=error_type.strip(),
            stack_trace=stack_trace,
            route=route.strip(),
            method=method.strip(),
            status_code=status_code,
            request_id=data.get("request_id") or self.request_identifier(),
            correlation_id=(
                data.get("correlation_id")
                or self.headers.get("X-Correlation-ID", "")
            ),
            tenant_id=tenant_id,
            project_id=project_id,
            metadata=metadata,
        )
        self.send_json({
            "ok": True,
            "id": found_id,
            "duplicate": not created,
        }, 202)

    def patch_developer_error(self, db, path, data):
        user = self.current_user(db)
        if not user:
            self.send_json({"error": "Sign in is required."}, 401)
            return
        parts = path.strip("/").split("/")
        try:
            error_id = int(parts[4])
        except (IndexError, ValueError):
            raise ValueError("Invalid error ID.")
        scope, scope_params = developer_error_scope(user)
        row = db.execute(
            f"SELECT * FROM developer_errors WHERE id=? AND {scope}",
            [error_id, *scope_params],
        ).fetchone()
        if not row:
            self.send_json({"error": "Error not found."}, 404)
            return
        status = row["status"]
        notes = row["notes"]
        if "status" in data:
            status = str(data.get("status", "")).strip().lower()
            if status not in DEVELOPER_ERROR_STATUSES:
                raise ValueError("Invalid developer error status.")
        if "notes" in data:
            notes = redact_sensitive_text(data.get("notes", ""), 4000)
        resolved_at = row["resolved_at"]
        resolved_by = row["resolved_by"]
        if status == "resolved" and row["status"] != "resolved":
            resolved_at = timestamp()
            resolved_by = user.get("id")
        elif status != "resolved":
            resolved_at = None
            resolved_by = None
        db.execute(
            """UPDATE developer_errors SET status=?,notes=?,resolved_at=?,
            resolved_by=? WHERE id=?""",
            (status, notes, resolved_at, resolved_by, error_id),
        )
        audit_event(
            db,
            user.get("id"),
            "developer.error_updated",
            "developer_error",
            error_id,
            {"status": status, "tenant_id": row["tenant_id"]},
            tenant_id=row["tenant_id"] or self.workspace_tenant(user),
        )
        updated = db.execute(
            "SELECT * FROM developer_errors WHERE id=?", (error_id,)
        ).fetchone()
        self.send_json({"record": developer_error_dict(updated)})

    def delete_workspace_storage_item(self, db, path):
        user = self.current_user(db)
        if not user:
            self.send_json({"error": "Sign in is required."}, 401)
            return
        parts = path.strip("/").split("/")
        result = delete_workspace_storage_items(
            db,
            self.server.storage_root,
            self.workspace_tenant(user),
            user.get("id"),
            [parts[4]],
        )
        if not result["deleted"]:
            self.send_json({
                "error": result["skipped"][0]["reason"]
                if result["skipped"] else "Storage item not found.",
            }, 404)
            return
        self.send_json(result)

    def delete_developer_error(self, db, path):
        user = self.current_user(db)
        if not user:
            self.send_json({"error": "Sign in is required."}, 401)
            return
        try:
            error_id = int(path.strip("/").split("/")[4])
        except (IndexError, ValueError):
            raise ValueError("Invalid error ID.")
        scope, scope_params = developer_error_scope(user)
        row = db.execute(
            f"SELECT tenant_id FROM developer_errors WHERE id=? AND {scope}",
            [error_id, *scope_params],
        ).fetchone()
        if not row:
            self.send_json({"error": "Error not found."}, 404)
            return
        db.execute("DELETE FROM developer_errors WHERE id=?", (error_id,))
        audit_event(
            db,
            user.get("id"),
            "developer.error_deleted",
            "developer_error",
            error_id,
            {"tenant_id": row["tenant_id"]},
            tenant_id=row["tenant_id"] or self.workspace_tenant(user),
        )
        self.send_json({"deleted": True})

    def product_owner_audit(
        self, db, user, action, tenant_id, reason, details=None, outcome="success"
    ):
        actor_id = user.get("id") if user else None
        actor_label = (user or {}).get("email") or "Bootstrap Product Owner"
        db.execute(
            """INSERT INTO admin_audit_logs
            (actor_user_id,actor_label,action,target_tenant_id,reason,outcome,
            details_json,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (
                actor_id,
                redact_sensitive_text(actor_label, 254),
                action,
                tenant_id,
                redact_sensitive_text(reason, 500),
                outcome,
                json.dumps(details or {}, ensure_ascii=True),
                timestamp(),
            ),
        )

    @staticmethod
    def product_owner_page(query):
        try:
            page = int(query.get("page", ["1"])[0])
            page_size = int(query.get("page_size", ["25"])[0])
        except ValueError as exc:
            raise ValueError("Page and page size must be integers.") from exc
        if not 1 <= page <= 100000 or not 1 <= page_size <= 100:
            raise ValueError("Page must be positive and page size must be between 1 and 100.")
        return page, page_size

    def product_owner_tenant(self, db, tenant_id):
        try:
            numeric_id = int(tenant_id)
        except ValueError:
            return None
        return db.execute(
            """SELECT t.id,t.public_tenant_id,t.public_workspace_id,t.name,t.slug,
            t.status,t.created_at,t.updated_at,t.owner_user_id,t.suspended_at,
            t.suspension_reason,s.status subscription_status,s.plan,
            s.trial_started_at,s.trial_ends_at,s.created_at subscription_created_at,
            s.updated_at subscription_updated_at,u.public_user_id,u.email,
            u.first_name,u.last_name,u.created_at signup_at,u.last_login_at,
            COALESCE(ss.total_bytes,0) storage_bytes,COALESCE(ss.filesystem_bytes,0)
            filesystem_bytes,COALESCE(ss.database_bytes,0) database_bytes,
            COALESCE(ss.item_count,0) storage_item_count,
            COALESCE(ss.record_count,0) storage_record_count,ss.computed_at
            FROM tenants t LEFT JOIN tenant_subscriptions s ON s.tenant_id=t.id
            LEFT JOIN users u ON u.id=t.owner_user_id
            LEFT JOIN workspace_storage_snapshots ss ON ss.tenant_id=t.id
            WHERE t.id=?""",
            (numeric_id,),
        ).fetchone()

    def get_product_owner_api(self, db, path):
        parts = path.strip("/").split("/")
        query = parse_qs(urlparse(self.path).query)
        if parts == ["api", "product-owner", "session"]:
            self.send_json({"authenticated": True, "role": "Product Owner"})
            return
        if parts == ["api", "product-owner", "dashboard"]:
            totals = {
                row["status"]: row["count"]
                for row in db.execute(
                    "SELECT status,COUNT(*) count FROM tenants GROUP BY status"
                )
            }
            subscriptions = {
                row["status"]: row["count"]
                for row in db.execute(
                    "SELECT status,COUNT(*) count FROM tenant_subscriptions GROUP BY status"
                )
            }
            errors = db.execute(
                """SELECT COUNT(*) count,COALESCE(SUM(occurrence_count),0) occurrences
                FROM developer_errors WHERE status IN ('open','acknowledged')"""
            ).fetchone()
            storage = db.execute(
                """SELECT COALESCE(SUM(total_bytes),0) total_bytes,
                COALESCE(SUM(filesystem_bytes),0) filesystem_bytes,
                COALESCE(SUM(database_bytes),0) database_bytes,
                MAX(computed_at) computed_at FROM workspace_storage_snapshots"""
            ).fetchone()
            users = db.execute("SELECT COUNT(*) count FROM users").fetchone()["count"]
            self.send_json({
                "users": users,
                "tenants": sum(totals.values()),
                "active_tenants": totals.get("Active", 0),
                "suspended_tenants": totals.get("Inactive", 0),
                "trials": subscriptions.get("trialing", 0),
                "trial_duration_days": TRIAL_DURATION_DAYS,
                "expired_trials": subscriptions.get("expired", 0),
                "active_plans": subscriptions.get("active", 0),
                "open_errors": errors["count"],
                "error_occurrences": errors["occurrences"],
                "storage": dict(storage),
                "generated_at": timestamp(),
            })
            return
        if parts == ["api", "product-owner", "tenants"]:
            page, page_size = self.product_owner_page(query)
            q = query.get("q", [""])[0].strip()[:200]
            status = query.get("status", [""])[0].strip()
            plan = query.get("plan", [""])[0].strip()
            if status and status not in ("Active", "Inactive"):
                raise ValueError("Invalid workspace status filter.")
            if plan and len(plan) > 40:
                raise ValueError("Plan filter is too long.")
            sort_columns = {
                "created_at": "t.created_at",
                "last_login_at": "u.last_login_at",
                "email": "u.email",
                "workspace": "t.name",
                "status": "t.status",
                "trial_ends_at": "s.trial_ends_at",
                "storage_bytes": "COALESCE(ss.total_bytes,0)",
            }
            sort = query.get("sort", ["created_at"])[0]
            direction = query.get("direction", ["desc"])[0].lower()
            if sort not in sort_columns or direction not in ("asc", "desc"):
                raise ValueError("Invalid directory sort.")
            clauses = []
            params = []
            if q:
                clauses.append(
                    """(u.email LIKE ? OR t.name LIKE ? OR t.slug LIKE ?
                    OR u.public_user_id LIKE ? OR t.public_tenant_id LIKE ?
                    OR t.public_workspace_id LIKE ?)"""
                )
                wildcard = f"%{q}%"
                params.extend([wildcard] * 6)
            if status:
                clauses.append("t.status=?")
                params.append(status)
            if plan:
                clauses.append("s.plan=?")
                params.append(plan)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            total = db.execute(
                """SELECT COUNT(*) count FROM tenants t
                LEFT JOIN tenant_subscriptions s ON s.tenant_id=t.id
                LEFT JOIN users u ON u.id=t.owner_user_id""" + where,
                params,
            ).fetchone()["count"]
            rows = db.execute(
                """SELECT t.id,t.public_tenant_id,t.public_workspace_id,t.name,
                t.status,t.created_at signup_at,t.suspended_at,t.suspension_reason,
                s.status subscription_status,s.plan,s.trial_started_at,s.trial_ends_at,
                u.public_user_id,u.email,u.last_login_at,
                (SELECT MAX(ue.created_at) FROM usage_events ue WHERE ue.tenant_id=t.id) recent_activity,
                (SELECT COUNT(*) FROM developer_errors de WHERE de.tenant_id=t.id) recent_error_count,
                COALESCE(ss.total_bytes,0) storage_bytes,ss.computed_at storage_calculated_at
                FROM tenants t LEFT JOIN tenant_subscriptions s ON s.tenant_id=t.id
                LEFT JOIN users u ON u.id=t.owner_user_id
                LEFT JOIN workspace_storage_snapshots ss ON ss.tenant_id=t.id"""
                + where
                + f" ORDER BY {sort_columns[sort]} {direction.upper()},t.id LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            self.send_json({
                "items": [dict(row) for row in rows],
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "pages": (total + page_size - 1) // page_size,
                },
            })
            return
        if len(parts) == 4 and parts[:3] == ["api", "product-owner", "tenants"]:
            row = self.product_owner_tenant(db, parts[3])
            if not row:
                self.send_json({"error": "Tenant not found."}, 404)
                return
            tenant_id = row["id"]
            storage = workspace_storage_scan(db, self.server.storage_root, tenant_id)
            save_workspace_storage_snapshot(db, tenant_id, storage["summary"])
            counts = {}
            for key, table in (
                ("projects", "projects"),
                ("requirements", "requirements"),
                ("test_cases", "test_cases"),
                ("defects", "bugs"),
                ("test_runs", "executions"),
            ):
                if key == "test_runs":
                    sql = """SELECT COUNT(*) count FROM executions e
                    JOIN test_cases c ON c.id=e.test_case_id
                    JOIN projects p ON p.id=c.project_id WHERE p.tenant_id=?"""
                elif table == "projects":
                    sql = "SELECT COUNT(*) count FROM projects WHERE tenant_id=?"
                else:
                    sql = f"""SELECT COUNT(*) count FROM {table} r
                    JOIN projects p ON p.id=r.project_id WHERE p.tenant_id=?"""
                counts[key] = db.execute(sql, (tenant_id,)).fetchone()["count"]
            self.send_json({
                "tenant": dict(row),
                "counts": counts,
                "storage": storage["summary"],
                "storage_items": [
                    {key: value for key, value in item.items() if not key.startswith("_")}
                    for item in storage["items"]
                ],
                "storage_history": [
                    dict(snapshot) for snapshot in db.execute(
                        """SELECT total_bytes,filesystem_bytes,database_bytes,
                        calculated_at FROM storage_usage_history
                        WHERE tenant_id=? ORDER BY calculated_at DESC LIMIT 30""",
                        (tenant_id,),
                    )
                ],
                "lifecycle_events": [
                    dict(event) for event in db.execute(
                        """SELECT actor_label,action,target_type,target_id,
                        details_json,created_at FROM audit_events
                        WHERE tenant_id=? ORDER BY created_at DESC,id DESC LIMIT 50""",
                        (tenant_id,),
                    )
                ],
                "recent_errors": [
                    {
                        **dict(error),
                        "message": redact_sensitive_text(error["message"], 4000),
                    }
                    for error in db.execute(
                        """SELECT id,severity,status,error_type,message,route,
                        occurrence_count,last_seen_at FROM developer_errors
                        WHERE tenant_id=? ORDER BY last_seen_at DESC,id DESC LIMIT 10""",
                        (tenant_id,),
                    )
                ],
            })
            return
        if parts == ["api", "product-owner", "plans"]:
            self.send_json([
                dict(row) for row in db.execute(
                    """SELECT code,name,status,storage_limit_bytes,created_at,updated_at
                    FROM plans ORDER BY code"""
                )
            ])
            return
        if parts == ["api", "product-owner", "usage"]:
            date_from = parse_date_filter(query, "date_from")
            date_to = parse_date_filter(query, "date_to")
            if date_from and date_to and date_from > date_to:
                raise ValueError("Start date must not be after end date.")
            tenant_value = query.get("tenant_id", [""])[0].strip()
            clauses = []
            params = []
            if date_from:
                clauses.append("created_at>=?")
                params.append(f"{date_from}T00:00:00")
            if date_to:
                clauses.append("created_at<?")
                params.append(
                    (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
                    .strftime("%Y-%m-%dT00:00:00")
                )
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            if tenant_value:
                try:
                    tenant_id = int(tenant_value)
                except ValueError as exc:
                    raise ValueError("Invalid tenant filter.") from exc
                where += (" AND " if where else " WHERE ") + "tenant_id=?"
                params.append(tenant_id)
            usage = db.execute(
                f"""SELECT event_type,COUNT(*) events,
                COUNT(DISTINCT tenant_id) workspaces FROM usage_events{where}
                GROUP BY event_type ORDER BY event_type""",
                params,
            ).fetchall()
            results = {
                "events": [dict(row) for row in usage],
                "registered_users": db.execute(
                    "SELECT COUNT(*) count FROM users"
                ).fetchone()["count"],
                "registered_tenants": db.execute(
                    "SELECT COUNT(*) count FROM tenants"
                ).fetchone()["count"],
                "api_requests": None,
                "api_requests_note": "Request counts are process-local and reset on restart.",
            }
            today = datetime.now(timezone.utc).date().isoformat()
            activity_end = date_to or today
            month_start = f"{activity_end[:7]}-01"
            activity_start = max(month_start, date_from or month_start)
            activity_scope = "event_type='login'"
            activity_params = []
            if tenant_value:
                activity_scope += " AND tenant_id=?"
                activity_params.append(tenant_id)
            results["daily_active_workspaces"] = db.execute(
                f"""SELECT COUNT(DISTINCT tenant_id) count FROM usage_events
                WHERE {activity_scope} AND substr(created_at,1,10)>=?
                AND substr(created_at,1,10)<=? AND substr(created_at,1,10)=?""",
                [*activity_params, activity_start, activity_end, activity_end],
            ).fetchone()["count"]
            results["monthly_active_workspaces"] = db.execute(
                f"""SELECT COUNT(DISTINCT tenant_id) count FROM usage_events
                WHERE {activity_scope} AND substr(created_at,1,10)>=?
                AND substr(created_at,1,10)<=?""",
                [*activity_params, activity_start, activity_end],
            ).fetchone()["count"]
            case_conditions = []
            case_params = []
            if date_from:
                case_conditions.append("c.created_at>=?")
                case_params.append(f"{date_from}T00:00:00")
            if date_to:
                case_conditions.append("c.created_at<?")
                case_params.append(
                    (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
                    .strftime("%Y-%m-%dT00:00:00")
                )
            if tenant_value:
                case_conditions.append("p.tenant_id=?")
                case_params.append(tenant_id)
            condition = " AND ".join(case_conditions)
            case_where = " WHERE " + condition if condition else ""
            results["test_cases_generated"] = db.execute(
                """SELECT COUNT(*) count FROM test_cases c
                JOIN projects p ON p.id=c.project_id""" + case_where,
                case_params,
            ).fetchone()["count"]
            runs_conditions = [
                condition.replace("c.created_at", "e.executed_at")
                if condition else ""
            ][0]
            runs_where = " WHERE " + runs_conditions if runs_conditions else ""
            results["test_runs"] = db.execute(
                """SELECT COUNT(*) count FROM executions e
                JOIN test_cases c ON c.id=e.test_case_id
                JOIN projects p ON p.id=c.project_id""" + runs_where,
                case_params,
            ).fetchone()["count"]
            storage_clauses = []
            storage_params = []
            if date_from:
                storage_clauses.append("history.calculated_at>=?")
                storage_params.append(f"{date_from}T00:00:00")
            if date_to:
                storage_clauses.append("history.calculated_at<?")
                storage_params.append(
                    (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
                    .strftime("%Y-%m-%dT00:00:00")
                )
            if tenant_value:
                storage_clauses.append("history.tenant_id=?")
                storage_params.append(tenant_id)
            storage_where = (
                " WHERE " + " AND ".join(storage_clauses)
                if storage_clauses else ""
            )
            results["storage_growth"] = [
                dict(snapshot) for snapshot in db.execute(
                    f"""SELECT substr(h.calculated_at,1,10) day,
                    SUM(h.total_bytes) total_bytes,
                    SUM(h.filesystem_bytes) filesystem_bytes,
                    SUM(h.database_bytes) database_bytes
                    FROM storage_usage_history h
                    JOIN (
                        SELECT tenant_id,substr(calculated_at,1,10) day,
                        MAX(calculated_at) calculated_at
                        FROM storage_usage_history history{storage_where}
                        GROUP BY tenant_id,substr(calculated_at,1,10)
                    ) latest ON latest.tenant_id=h.tenant_id
                    AND latest.calculated_at=h.calculated_at
                    GROUP BY substr(h.calculated_at,1,10)
                    ORDER BY day""",
                    storage_params,
                )
            ]
            with self.server.api_metrics_lock:
                metrics = list(self.server.api_metrics)
            if tenant_value:
                metrics = [entry for entry in metrics if entry[1] == tenant_id]
            if date_from:
                lower = datetime.fromisoformat(f"{date_from}T00:00:00").replace(
                    tzinfo=timezone.utc
                ).timestamp()
                metrics = [entry for entry in metrics if entry[0] >= lower]
            if date_to:
                upper = (
                    datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
                ).replace(
                    tzinfo=timezone.utc
                ).timestamp()
                metrics = [entry for entry in metrics if entry[0] < upper]
            results["api_requests"] = len(metrics)
            results["api_failures"] = sum(1 for entry in metrics if entry[2] >= 400)
            results["api_failure_rate_percent"] = (
                round(results["api_failures"] * 100 / results["api_requests"], 2)
                if results["api_requests"] else 0
            )
            audit_filters = []
            audit_params = []
            if date_from:
                audit_filters.append("created_at>=?")
                audit_params.append(f"{date_from}T00:00:00")
            if date_to:
                audit_filters.append("created_at<?")
                audit_params.append(
                    (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
                    .strftime("%Y-%m-%dT00:00:00")
                )
            if tenant_value:
                audit_filters.append("target_tenant_id=?")
                audit_params.append(tenant_id)
            audit_where = " AND ".join(audit_filters)
            plan_actions = db.execute(
                """SELECT details_json FROM admin_audit_logs
                WHERE action='plan.assigned'""" + (
                    " AND " + audit_where if audit_where else ""
                ),
                audit_params,
            ).fetchall()
            results["trial_conversions"] = sum(
                1 for event in plan_actions
                if (
                    json.loads(event["details_json"]).get("previous_plan") == TRIAL_PLAN
                    and json.loads(event["details_json"]).get("plan") != TRIAL_PLAN
                )
            )
            admin_suspensions = db.execute(
                """SELECT COUNT(*) count FROM admin_audit_logs
                WHERE action='workspace.suspended'""" + (
                    " AND " + audit_where if audit_where else ""
                ),
                audit_params,
            ).fetchone()["count"]
            workspace_filters = []
            workspace_params = []
            if date_from:
                workspace_filters.append("created_at>=?")
                workspace_params.append(f"{date_from}T00:00:00")
            if date_to:
                workspace_filters.append("created_at<?")
                workspace_params.append(
                    (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
                    .strftime("%Y-%m-%dT00:00:00")
                )
            if tenant_value:
                workspace_filters.append("tenant_id=?")
                workspace_params.append(tenant_id)
            workspace_where = (
                " AND ".join(workspace_filters) if workspace_filters else ""
            )
            system_suspensions = db.execute(
                """SELECT COUNT(*) count FROM audit_events
                WHERE action='workspace.suspended'""" + (
                    " AND " + workspace_where if workspace_where else ""
                ),
                workspace_params,
            ).fetchone()["count"]
            results["suspensions"] = admin_suspensions + system_suspensions
            self.send_json(results)
            return
        if parts == ["api", "product-owner", "errors"]:
            page, page_size = self.product_owner_page(query)
            clauses = []
            params = []
            severity = query.get("severity", [""])[0].strip()
            status = query.get("status", [""])[0].strip()
            tenant_value = query.get("tenant_id", [""])[0].strip()
            if severity:
                if severity not in DEVELOPER_ERROR_SEVERITIES:
                    raise ValueError("Invalid error severity.")
                clauses.append("severity=?")
                params.append(severity)
            if status:
                if status not in DEVELOPER_ERROR_STATUSES:
                    raise ValueError("Invalid error status.")
                clauses.append("status=?")
                params.append(status)
            if tenant_value:
                try:
                    clauses.append("tenant_id=?")
                    params.append(int(tenant_value))
                except ValueError as exc:
                    raise ValueError("Invalid tenant filter.") from exc
            date_from = parse_date_filter(query, "date_from")
            date_to = parse_date_filter(query, "date_to")
            if date_from:
                clauses.append("last_seen_at>=?")
                params.append(f"{date_from}T00:00:00")
            if date_to:
                clauses.append("last_seen_at<?")
                params.append(
                    (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
                    .strftime("%Y-%m-%dT00:00:00")
                )
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            total = db.execute(
                "SELECT COUNT(*) count FROM developer_errors" + where, params
            ).fetchone()["count"]
            rows = db.execute(
                """SELECT id,tenant_id,severity,status,error_type,message,stack_trace,
                route,method,status_code,request_id,correlation_id,occurrence_count,
                first_seen_at,last_seen_at FROM developer_errors""" + where
                + " ORDER BY last_seen_at DESC,id DESC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["message"] = redact_sensitive_text(item["message"], 4000)
                item["stack_trace"] = redact_sensitive_text(item["stack_trace"], 20000)
                items.append(item)
            self.send_json({
                "items": items,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "pages": (total + page_size - 1) // page_size,
                },
            })
            return
        if parts == ["api", "product-owner", "audit"]:
            page, page_size = self.product_owner_page(query)
            tenant_value = query.get("tenant_id", [""])[0].strip()
            filter_sql = ""
            params = []
            if tenant_value:
                try:
                    params.append(int(tenant_value))
                except ValueError as exc:
                    raise ValueError("Invalid tenant filter.") from exc
                filter_sql = " WHERE target_tenant_id=?"
            union = f"""
                SELECT 'admin:'||CAST(id AS TEXT) id,actor_label,action,
                target_tenant_id,reason,outcome,details_json,created_at
                FROM admin_audit_logs{filter_sql}
                UNION ALL
                SELECT 'workspace:'||CAST(id AS TEXT) id,
                COALESCE(actor_label,'Workspace activity') actor_label,action,
                tenant_id target_tenant_id,'' reason,'success' outcome,
                details_json,created_at FROM audit_events
                {('WHERE tenant_id=?' if tenant_value else '')}
            """
            audit_params = [*params, *params]
            total = db.execute(
                f"SELECT COUNT(*) count FROM ({union}) audit", audit_params
            ).fetchone()["count"]
            rows = db.execute(
                f"""SELECT * FROM ({union}) audit ORDER BY created_at DESC,id DESC
                LIMIT ? OFFSET ?""",
                [*audit_params, page_size, (page - 1) * page_size],
            ).fetchall()
            self.send_json({
                "items": [dict(row) for row in rows],
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "pages": (total + page_size - 1) // page_size,
                },
            })
            return
        self.send_json({"error": "Not found."}, 404)

    def post_product_owner_api(self, db, path, data):
        parts = path.strip("/").split("/")
        user = self.current_user(db) or {}
        if len(parts) == 5 and parts[:3] == ["api", "product-owner", "tenants"]:
            try:
                tenant_id = int(parts[3])
            except ValueError:
                self.send_json({"error": "Invalid tenant ID."}, 400)
                return
            action = parts[4]
            tenant = self.product_owner_tenant(db, str(tenant_id))
            if not tenant:
                self.send_json({"error": "Tenant not found."}, 404)
                return
            if tenant_id == PERMANENT_WORKSPACE_ID and action in (
                "plan", "suspend", "reactivate",
            ):
                raise ValueError("The permanent default workspace cannot be changed here.")
            reason = validate_text(data, "reason", True, 500)
            if len(reason) < 5:
                raise ValueError("Provide an operational reason of at least five characters.")
            if data.get("confirmed") is not True:
                raise ValueError("Explicit confirmation is required.")
            now = timestamp()
            if action == "plan":
                plan = validate_text(data, "plan", True, 40)
                selected = db.execute(
                    "SELECT code FROM plans WHERE code=? AND status='active'", (plan,)
                ).fetchone()
                if not selected:
                    raise ValueError("Select an active configured plan.")
                if plan == TRIAL_PLAN:
                    started_at = utc_timestamp(datetime.now(timezone.utc))
                    ends_at = trial_end(datetime.fromisoformat(started_at))
                    subscription_status = "trialing"
                else:
                    started_at = None
                    ends_at = None
                    subscription_status = "active"
                with db:
                    updated = db.execute(
                        """UPDATE tenant_subscriptions SET status=?,plan=?,
                        trial_started_at=?,trial_ends_at=?,updated_at=? WHERE tenant_id=?""",
                        (subscription_status, plan, started_at, ends_at, now, tenant_id),
                    )
                    if updated.rowcount == 0:
                        db.execute(
                            """INSERT INTO tenant_subscriptions
                            (tenant_id,status,plan,trial_started_at,trial_ends_at,
                            created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
                            (tenant_id, subscription_status, plan, started_at, ends_at, now, now),
                        )
                    db.execute(
                        """UPDATE tenants SET status='Active',suspended_at=NULL,
                        suspension_reason='',updated_at=? WHERE id=?""",
                        (now, tenant_id),
                    )
                    self.product_owner_audit(
                        db, user, "plan.assigned", tenant_id, reason,
                        {
                            "previous_plan": tenant["plan"],
                            "plan": plan,
                            "subscription_status": subscription_status,
                        },
                    )
                self.send_json({"assigned": True, "plan": plan})
                return
            if action == "suspend":
                with db:
                    db.execute(
                        """UPDATE tenants SET status='Inactive',suspended_at=?,
                        suspension_reason=?,updated_at=? WHERE id=?""",
                        (now, reason, now, tenant_id),
                    )
                    self.product_owner_audit(
                        db, user, "workspace.suspended", tenant_id, reason,
                        {"previous_status": tenant["status"]},
                    )
                self.send_json({"suspended": True})
                return
            if action == "reactivate":
                if tenant["plan"] == TRIAL_PLAN:
                    started_at = utc_timestamp(datetime.now(timezone.utc))
                    ends_at = trial_end(datetime.fromisoformat(started_at))
                    subscription_status = "trialing"
                else:
                    started_at = None
                    ends_at = None
                    subscription_status = "active"
                with db:
                    db.execute(
                        """UPDATE tenant_subscriptions SET status=?,trial_started_at=?,
                        trial_ends_at=?,updated_at=? WHERE tenant_id=?""",
                        (subscription_status, started_at, ends_at, now, tenant_id),
                    )
                    db.execute(
                        """UPDATE tenants SET status='Active',suspended_at=NULL,
                        suspension_reason='',updated_at=? WHERE id=?""",
                        (now, tenant_id),
                    )
                    self.product_owner_audit(
                        db, user, "workspace.reactivated", tenant_id, reason,
                        {"plan": tenant["plan"], "subscription_status": subscription_status},
                    )
                self.send_json({"reactivated": True, "trial_ends_at": ends_at})
                return
            self.send_json({"error": "Not found."}, 404)
            return
        if len(parts) == 6 and parts[:3] == ["api", "product-owner", "tenants"] \
            and parts[4:] == ["storage", "delete"]:
            try:
                tenant_id = int(parts[3])
            except ValueError:
                self.send_json({"error": "Invalid tenant ID."}, 400)
                return
            tenant = self.product_owner_tenant(db, str(tenant_id))
            if not tenant:
                self.send_json({"error": "Tenant not found."}, 404)
                return
            reason = validate_text(data, "reason", True, 500)
            item_ids = data.get("item_ids")
            if not isinstance(item_ids, list) or not item_ids or len(item_ids) > 100:
                raise ValueError("Select between one and 100 inventoried storage items.")
            if not all(isinstance(item_id, str) and len(item_id) <= 256 for item_id in item_ids):
                raise ValueError("Invalid storage item selection.")
            if data.get("confirmation") != f"DELETE {tenant['public_tenant_id']}":
                raise ValueError("Enter the tenant-specific deletion confirmation exactly.")
            if len(reason) < 5:
                raise ValueError("Provide an operational reason of at least five characters.")
            with db:
                result = delete_workspace_storage_items(
                    db, self.server.storage_root, tenant_id, user.get("id"), item_ids
                )
                self.product_owner_audit(
                    db, user, "storage.items_deleted", tenant_id, reason,
                    {
                        "selected_count": len(item_ids),
                        "deleted_count": result["deleted_count"],
                        "skipped_count": len(result["skipped"]),
                        "estimated_bytes_removed": result["estimated_bytes_removed"],
                        "freed_bytes": result["freed_bytes"],
                    },
                    outcome="success" if not result["skipped"] else "failure",
                )
            self.send_json(result)
            return
        self.send_json({"error": "Not found."}, 404)

    def get_api(self, db, path):
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "product-owner"]:
            self.get_product_owner_api(db, path)
            return
        if parts[:3] == ["api", "workspace", "storage"]:
            self.get_workspace_storage(db, path)
            return
        if parts[:2] == ["api", "developer"]:
            self.get_developer_api(db, path)
            return
        if parts == ["api", "profile"]:
            user = self.current_user(db)
            if not user or not user.get("id"):
                if not user or not user.get("global_admin"):
                    self.send_json({"error": "A user account is required to view a profile."}, 401)
                    return
                row = db.execute(
                    """SELECT first_name,last_name,email,phone,'Active' status,
                    created_at,NULL last_login_at,profile_image_type,profile_image_data
                    FROM global_admin_profile WHERE id=1"""
                ).fetchone()
                profile = dict(row)
                profile["global_admin"] = True
                profile["role_name"] = "Global Admin"
                if profile["profile_image_data"] is not None:
                    profile["profile_image_data"] = base64.b64encode(
                        profile["profile_image_data"]
                    ).decode("ascii")
                self.send_json(profile)
                return
            row = db.execute(
                """SELECT id,public_user_id,first_name,last_name,email,phone,status,created_at,last_login_at,
                profile_image_type,profile_image_data FROM users WHERE id=?""",
                (user["id"],),
            ).fetchone()
            profile = dict(row)
            if profile["profile_image_data"] is not None:
                profile["profile_image_data"] = base64.b64encode(
                    profile["profile_image_data"]
                ).decode("ascii")
            self.send_json(profile)
            return
        if parts == ["api", "users"]:
            actor = self.current_user(db) or {}
            tenant_id = actor.get("tenant_id", 1)
            users = [
                user_public_dict(db, row["id"], tenant_id)
                for row in db.execute(
                    """SELECT DISTINCT u.id FROM users u
                    JOIN tenant_memberships m ON m.user_id=u.id
                    WHERE m.tenant_id=? ORDER BY u.created_at DESC,u.id DESC""",
                    (tenant_id,),
                )
            ]
            self.send_json(users)
            return
        if parts == ["api", "roles"]:
            actor = self.current_user(db) or {}
            tenant_id = actor.get("tenant_id", 1)
            roles = []
            for row in db.execute(
                """SELECT r.*,COUNT(u.id) user_count FROM roles r
                LEFT JOIN tenant_memberships m ON m.role_id=r.id
                    AND m.tenant_id=?
                LEFT JOIN users u ON u.id=m.user_id
                WHERE r.tenant_id IS NULL OR r.tenant_id=?
                GROUP BY r.id ORDER BY r.name COLLATE NOCASE"""
                , (tenant_id, tenant_id)
            ):
                item = dict(row)
                item["permissions"] = json.loads(item.pop("permissions_json"))
                item["permission_count"] = len(item["permissions"])
                roles.append(item)
            self.send_json({"roles": roles, "permission_modules": PERMISSION_MODULES})
            return
        if parts == ["api", "teams"]:
            actor = self.current_user(db) or {}
            teams = [
                dict(row) for row in db.execute(
                    """SELECT t.*,u.first_name||' '||u.last_name owner_name,
                    (SELECT COUNT(*) FROM team_memberships tm WHERE tm.team_id=t.id) member_count
                    FROM teams t LEFT JOIN users u ON u.id=t.owner_user_id
                    WHERE (?=1 OR t.tenant_id=?)
                    ORDER BY t.created_at DESC,t.id DESC"""
                    , (int(self.is_global_admin(actor)), actor.get("tenant_id", 1))
                )
            ]
            self.send_json(teams)
            return
        if len(parts) == 4 and parts[:2] == ["api", "teams"] and parts[3] == "members":
            team_id = self.parse_project_id(parts[2])
            team = db.execute(
                "SELECT tenant_id FROM teams WHERE id=?", (team_id,)
            ).fetchone()
            actor = self.current_user(db) or {}
            if not team or (
                not self.is_global_admin(actor)
                and team["tenant_id"] != actor.get("tenant_id")
            ):
                self.send_json({"error": "Team not found."}, 404)
                return
            members = [
                user_public_dict(db, row["user_id"], team["tenant_id"])
                for row in db.execute(
                    """SELECT user_id FROM team_memberships WHERE team_id=?
                    ORDER BY added_at,user_id""",
                    (team_id,),
                )
            ]
            self.send_json(members)
            return
        if parts == ["api", "audit-events"]:
            actor = self.current_user(db) or {}
            tenant_filter = "" if self.is_global_admin(actor) else "WHERE a.tenant_id=?"
            parameters = () if self.is_global_admin(actor) else (actor.get("tenant_id", 1),)
            events = []
            for row in db.execute(
                f"""SELECT a.*,u.email actor_email,t.name tenant_name
                FROM audit_events a
                LEFT JOIN users u ON u.id=a.actor_user_id
                LEFT JOIN tenants t ON t.id=a.tenant_id
                {tenant_filter}
                ORDER BY a.created_at DESC,a.id DESC LIMIT 500"""
                , parameters
            ):
                event = dict(row)
                event["details"] = json.loads(event.pop("details_json"))
                events.append(event)
            self.send_json(events)
            return
        if parts == ["api", "archive"]:
            archive_root = Path(self.server.storage_root) / "archived-projects"
            if archive_root.is_symlink():
                raise OSError("Archive storage directory cannot be a symbolic link.")
            archives = []
            if archive_root.is_dir():
                for entry in archive_root.iterdir():
                    if entry.is_symlink() or not entry.is_dir():
                        continue
                    modified = entry.stat().st_mtime
                    archives.append({
                        "archive_id": entry.name,
                        "archived_at": datetime.fromtimestamp(
                            modified, timezone.utc
                        ).isoformat(timespec="seconds"),
                    })
            archives.sort(key=lambda item: item["archived_at"], reverse=True)
            self.send_json({"archives": archives})
            return
        if parts == ["api", "auth", "status"]:
            self.send_json(self.auth_status(db))
            return
        if parts == ["api", "ai", "settings"]:
            user = self.current_user(db) or {}
            tenant_id = user.get("tenant_id", 1)
            settings = provider_settings(
                db, self.is_global_admin(user), tenant_id=tenant_id
            )
            settings["can_manage"] = self.is_workspace_admin(db, user)
            self.send_json(settings)
            return
        if parts == ["api", "custom-fields"]:
            query = parse_qs(urlparse(self.path).query)
            module_key = query.get("module", [""])[0]
            actor = self.current_user(db) or {}
            tenant_id = actor.get("tenant_id", 1)
            record_id = None
            if query.get("record_id", [""])[0]:
                record_id = self.parse_project_id(query["record_id"][0])
                record_tenant = record_tenant_id(db, module_key, record_id)
                if record_tenant is None or (
                    not self.is_global_admin(actor) and record_tenant != tenant_id
                ):
                    self.send_json({"error": "Record not found."}, 404)
                    return
            if module_key:
                include_inactive = query.get("include_inactive", [""])[0] == "true"
                self.send_json({
                    "fields": custom_fields_for_record(
                        db, module_key, record_id, include_inactive, tenant_id
                    ),
                })
            else:
                fields = [
                    custom_field_dict(db, row)
                    for row in db.execute(
                        """SELECT * FROM custom_fields
                        WHERE (?=1 OR tenant_id=?)
                        ORDER BY module_key,display_order,id"""
                        , (int(self.is_global_admin(actor)), tenant_id)
                    )
                ]
                for field in fields:
                    field["module_label"] = CUSTOM_FIELD_MODULES[field["module_key"]][1]
                self.send_json(fields)
            return
        if parts == ["api", "projects"]:
            actor = self.current_user(db) or {}
            rows = db.execute(
                """SELECT * FROM projects WHERE (?=1 OR tenant_id=?)
                ORDER BY id DESC""",
                (int(self.is_global_admin(actor)), actor.get("tenant_id", 1)),
            ).fetchall()
            self.send_json([dict(r) for r in rows])
            return
        if len(parts) >= 3 and parts[1] == "projects":
            try:
                pid = int(parts[2])
            except ValueError:
                raise ValueError("Invalid project ID.")
            if not project_exists(db, pid):
                self.send_json({"error": "Project not found"}, 404)
                return
            if len(parts) == 4 and parts[3] == "dashboard":
                self.send_json(dashboard(db, pid))
                return
            if len(parts) == 4 and parts[3] == "attachments":
                query = parse_qs(urlparse(self.path).query)
                module_key = query.get("module", [""])[0]
                record_id = self.parse_project_id(query.get("record_id", [""])[0])
                if not record_belongs_to_project(db, module_key, pid, record_id):
                    self.send_json({"error": "Record not found in this project."}, 404)
                    return
                self.send_json([
                    {
                        key: row[key] for key in (
                            "id", "module_key", "record_id", "filename",
                            "content_type", "file_size_bytes", "uploaded_at",
                        )
                    }
                    for row in db.execute(
                        """SELECT id,module_key,record_id,filename,content_type,
                        file_size_bytes,uploaded_at FROM form_attachments
                        WHERE project_id=? AND module_key=? AND record_id=?
                        ORDER BY id""",
                        (pid, module_key, record_id),
                    )
                ])
                return
            if (
                len(parts) == 6 and parts[3] == "attachments"
                and parts[5] in ("download", "view")
            ):
                attachment_id = self.parse_project_id(parts[4])
                row = db.execute(
                    """SELECT filename,content_type,file_size_bytes,file_data
                    FROM form_attachments WHERE id=? AND project_id=?""",
                    (attachment_id, pid),
                ).fetchone()
                if not row:
                    self.send_json({"error": "Attachment not found."}, 404)
                    return
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", row["filename"]) or "attachment"
                disposition = "attachment" if parts[5] == "download" else "inline"
                self.send_bytes(
                    bytes(row["file_data"]),
                    row["content_type"],
                    extra={
                        "Content-Disposition": (
                            f'{disposition}; filename="{safe_name}"'
                        )
                    },
                )
                return
            if len(parts) == 4 and parts[3] == "files":
                project_dir = project_storage_dir(self.server.storage_root, pid)
                files = []
                folders = []
                if project_dir.is_dir() and not project_dir.is_symlink():
                    project_dir = project_dir.resolve()
                    folders = sorted(
                        path.name for path in project_dir.iterdir()
                        if path.is_dir() and not path.is_symlink()
                    )
                    for current, directories, filenames in os.walk(project_dir, followlinks=False):
                        current_path = Path(current)
                        directories[:] = sorted(
                            name for name in directories
                            if not (current_path / name).is_symlink()
                        )
                        for filename in sorted(filenames):
                            file_path = current_path / filename
                            if file_path.is_symlink() or not file_path.is_file():
                                continue
                            relative_path = file_path.relative_to(project_dir).as_posix()
                            files.append({
                                "path": relative_path,
                                "size": file_path.stat().st_size,
                            })
                            if len(files) >= 2000:
                                break
                        if len(files) >= 2000:
                            break
                self.send_json({
                    "folders": folders,
                    "files": files,
                    "truncated": len(files) == 2000,
                })
                return
            if len(parts) == 5 and parts[3] == "files" and parts[4] == "download":
                relative_path = parse_qs(urlparse(self.path).query).get("path", [""])[0]
                if not relative_path or Path(relative_path).is_absolute():
                    self.send_json({"error": "Invalid project file path."}, 400)
                    return
                project_dir = project_storage_dir(self.server.storage_root, pid)
                if project_dir.is_symlink():
                    self.send_json({"error": "Project file is unavailable."}, 404)
                    return
                project_dir = project_dir.resolve()
                file_path = project_dir.joinpath(*Path(relative_path).parts)
                if any(parent.is_symlink() for parent in (file_path, *file_path.parents)):
                    self.send_json({"error": "Project file is unavailable."}, 404)
                    return
                resolved_file = file_path.resolve()
                try:
                    resolved_file.relative_to(project_dir)
                except ValueError:
                    self.send_json({"error": "Invalid project file path."}, 400)
                    return
                if not resolved_file.is_file():
                    self.send_json({"error": "Project file not found."}, 404)
                    return
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", resolved_file.name) or "project-file"
                self.send_bytes(
                    resolved_file.read_bytes(),
                    mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
                    extra={"Content-Disposition": f'attachment; filename="{safe_name}"'},
                )
                return
            if len(parts) == 4 and parts[3] == "applications":
                rows = db.execute(
                    """SELECT id,project_id,filename,archive_size_bytes,inventory_json,
                    analysis_json,coverage_json,coverage_checked_at,coverage_requirements_signature,
                    uploaded_at FROM application_uploads
                    WHERE project_id=? ORDER BY id DESC""",
                    (pid,),
                ).fetchall()
                signature = requirements_signature(db, pid)
                self.send_json([application_upload_dict(row, signature) for row in rows])
                return
            if len(parts) == 5 and parts[3] == "applications":
                application_id = self.parse_project_id(parts[4])
                row = db.execute(
                    """SELECT id,project_id,filename,archive_size_bytes,inventory_json,
                    analysis_json,coverage_json,coverage_checked_at,coverage_requirements_signature,
                    uploaded_at FROM application_uploads
                    WHERE id=? AND project_id=?""",
                    (application_id, pid),
                ).fetchone()
                if not row:
                    self.send_json({"error": "Application upload not found"}, 404)
                    return
                self.send_json(application_upload_dict(row, requirements_signature(db, pid)))
                return
            if len(parts) == 4 and parts[3] == "analyses":
                self.send_json([
                    application_analysis_dict(row)
                    for row in db.execute(
                        "SELECT * FROM application_analyses WHERE project_id=? ORDER BY id DESC",
                        (pid,),
                    )
                ])
                return
            if len(parts) == 5 and parts[3] == "analyses":
                analysis_id = self.parse_project_id(parts[4])
                row = db.execute(
                    "SELECT * FROM application_analyses WHERE id=? AND project_id=?",
                    (analysis_id, pid),
                ).fetchone()
                if not row:
                    self.send_json({"error": "Application analysis not found"}, 404)
                    return
                self.send_json(application_analysis_dict(row))
                return
            if len(parts) == 4 and parts[3] == "api-docs":
                documents = [dict(row) for row in db.execute(
                    "SELECT * FROM api_docs WHERE project_id=? ORDER BY id DESC", (pid,)
                )]
                for document in documents:
                    document["endpoints"] = [dict(row) for row in db.execute(
                        """SELECT id,method,path,summary,operation_id,tags FROM api_endpoints
                        WHERE project_id=? AND api_doc_id=? ORDER BY path,method""",
                        (pid, document["id"]),
                    )]
                    for endpoint in document["endpoints"]:
                        endpoint["tags"] = json.loads(endpoint["tags"])
                self.send_json(documents)
                return
            if len(parts) == 6 and parts[3] == "executions" and parts[5] == "evidence":
                execution_id = self.parse_project_id(parts[4])
                if not db.execute(
                    """SELECT 1 FROM executions e JOIN test_cases tc ON tc.id=e.test_case_id
                    WHERE e.id=? AND tc.project_id=?""", (execution_id, pid)
                ).fetchone():
                    self.send_json({"error": "Execution not found"}, 404)
                    return
                self.send_json([dict(row) for row in db.execute(
                    """SELECT id,filename,media_type,size,created_at FROM execution_evidence
                    WHERE project_id=? AND execution_id=? ORDER BY id""", (pid, execution_id)
                )])
                return
            if len(parts) in (5, 6) and parts[3] == "evidence":
                if len(parts) == 6 and parts[5] != "download":
                    self.send_json({"error": "Not found"}, 404)
                    return
                evidence_id = self.parse_project_id(parts[4])
                evidence = db.execute(
                    "SELECT filename,media_type,image_data FROM execution_evidence WHERE id=? AND project_id=?",
                    (evidence_id, pid),
                ).fetchone()
                if not evidence:
                    self.send_json({"error": "Evidence not found"}, 404)
                    return
                download = len(parts) == 6 and parts[5] == "download"
                disposition = "attachment" if download else "inline"
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", evidence["filename"]) or "evidence"
                self.send_bytes(
                    bytes(evidence["image_data"]), evidence["media_type"],
                    extra={"Content-Disposition": f'{disposition}; filename="{safe_name}"'},
                )
                return
            if len(parts) == 4 and parts[3] == "requirements":
                self.send_json([dict(r) for r in db.execute(
                    """SELECT r.*, COUNT(tc.id) test_case_count FROM requirements r
                    LEFT JOIN test_cases tc ON tc.requirement_id = r.id WHERE r.project_id = ?
                    GROUP BY r.id ORDER BY r.id""", (pid,)
                )])
                return
            if len(parts) == 5 and parts[3:5] == ["requirements", "suggestions"]:
                suggestions = [
                    requirement_suggestion_dict(db, row)
                    for row in db.execute(
                        """SELECT * FROM requirement_suggestions
                        WHERE project_id=? ORDER BY id DESC""",
                        (pid,),
                    )
                ]
                self.send_json(suggestions)
                return
            if len(parts) == 5 and parts[3:5] == ["requirements", "documents"]:
                documents = db.execute(
                    """SELECT id,filename,content_type,file_size_bytes,requirement_count,version,
                    uploaded_at,analysis_json IS NOT NULL has_analysis
                    FROM requirement_documents WHERE project_id=? ORDER BY id DESC""",
                    (pid,),
                ).fetchall()
                self.send_json([dict(row) for row in documents])
                return
            if len(parts) == 7 and parts[3:5] == ["requirements", "documents"] and parts[6] == "analysis":
                document_id = self.parse_project_id(parts[5])
                document = db.execute(
                    """SELECT analysis_json FROM requirement_documents
                    WHERE id=? AND project_id=?""",
                    (document_id, pid),
                ).fetchone()
                if not document:
                    self.send_json({"error": "Requirement document not found"}, 404)
                    return
                if not document["analysis_json"]:
                    self.send_json({"error": "BRD analysis is not available for this document."}, 404)
                    return
                self.send_json({"analysis": json.loads(document["analysis_json"])})
                return
            if len(parts) == 7 and parts[3:5] == ["requirements", "documents"] and parts[6] == "analysis.md":
                document_id = self.parse_project_id(parts[5])
                document = db.execute(
                    """SELECT filename,analysis_json FROM requirement_documents
                    WHERE id=? AND project_id=?""",
                    (document_id, pid),
                ).fetchone()
                if not document:
                    self.send_json({"error": "Requirement document not found"}, 404)
                    return
                if not document["analysis_json"]:
                    self.send_json({"error": "BRD analysis is not available for this document."}, 404)
                    return
                analysis = json.loads(document["analysis_json"])
                report = brd_analysis_markdown(analysis).encode("utf-8")
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", document["filename"])
                download_name = f"{Path(safe_name).stem or 'brd'}-analysis.md"
                self.send_bytes(
                    report,
                    "text/markdown; charset=utf-8",
                    extra={"Content-Disposition": f'attachment; filename="{download_name}"'},
                )
                return
            if len(parts) == 6 and parts[3:5] == ["requirements", "documents"]:
                document_id = self.parse_project_id(parts[5])
                document = db.execute(
                    """SELECT filename,storage_path,content_type FROM requirement_documents
                    WHERE id=? AND project_id=?""",
                    (document_id, pid),
                ).fetchone()
                if not document:
                    self.send_json({"error": "Requirement document not found"}, 404)
                    return
                stored_file = Path(document["storage_path"])
                project_dir = project_storage_dir(self.server.storage_root, pid)
                try:
                    if project_dir.is_symlink():
                        raise ValueError
                    resolved_file = stored_file.resolve()
                    resolved_file.relative_to(project_dir.resolve())
                    if stored_file.is_symlink() or not resolved_file.is_file():
                        raise ValueError
                except (OSError, ValueError):
                    self.send_json({"error": "Original requirement document is unavailable."}, 404)
                    return
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", document["filename"]) or "requirements"
                self.send_bytes(
                    resolved_file.read_bytes(),
                    document["content_type"],
                    extra={"Content-Disposition": f'attachment; filename="{safe_name}"'},
                )
                return
            if len(parts) == 4 and parts[3] == "test-cases":
                self.send_json([dict(r) for r in db.execute(
                    """SELECT tc.*,
                    COALESCE(r.requirement_key,NULLIF(tc.source_requirement_key,'')) requirement_key,
                    COALESCE(r.title,NULLIF(tc.source_requirement_title,'')) requirement_title,
                    r.module_type, r.module_category,
                    r.origin requirement_current_origin,
                    r.review_status requirement_current_approval_status,
                    r.source_suggestion_id requirement_current_suggestion_id,
                    COALESCE(e.status, 'Not Executed') status,
                    COALESCE(e.actual_result, '') actual_result, COALESCE(e.comments, '') comments,
                    e.id execution_id, e.executed_at,
                    (SELECT COUNT(*) FROM execution_evidence ev WHERE ev.execution_id=e.id) evidence_count,
                    (SELECT COUNT(*) FROM executions history WHERE history.test_case_id=tc.id) execution_count
                    FROM test_cases tc LEFT JOIN requirements r ON r.id = tc.requirement_id
                    LEFT JOIN executions e ON e.id = (SELECT id FROM executions WHERE test_case_id = tc.id ORDER BY id DESC LIMIT 1)
                    WHERE tc.project_id = ? ORDER BY tc.id DESC""", (pid,)
                )])
                return
            if len(parts) == 6 and parts[3] == "test-cases" and parts[5] == "executions":
                case_id = self.parse_project_id(parts[4])
                if not db.execute(
                    "SELECT 1 FROM test_cases WHERE id=? AND project_id=?", (case_id, pid)
                ).fetchone():
                    self.send_json({"error": "Test case not found"}, 404)
                    return
                history = [dict(row) for row in db.execute(
                    """SELECT id,status,actual_result,comments,executed_by,executed_at FROM executions
                    WHERE test_case_id=? ORDER BY id DESC""", (case_id,)
                )]
                for execution in history:
                    execution["evidence"] = [dict(row) for row in db.execute(
                        """SELECT id,filename,media_type,size,created_at FROM execution_evidence
                        WHERE project_id=? AND execution_id=? ORDER BY id""", (pid, execution["id"])
                    )]
                self.send_json(history)
                return
            if len(parts) == 4 and parts[3] == "bugs":
                self.send_json([dict(r) for r in db.execute(
                    """SELECT b.*, tc.case_key FROM bugs b LEFT JOIN test_cases tc ON tc.id = b.test_case_id
                    WHERE b.project_id = ? ORDER BY b.id DESC""", (pid,)
                )])
                return
            if len(parts) == 5 and parts[3] == "report" and parts[4] in (
                "requirements.csv", "test-cases.csv", "bugs.csv",
            ):
                module = parts[4][:-4]
                query = parse_qs(urlparse(self.path).query)
                self.module_csv_report(db, pid, module, query.get("q", [""])[0])
                return
            if len(parts) == 5 and parts[3] == "report" and parts[4] == "csv":
                self.csv_report(db, pid)
                return
        self.send_json({"error": "Not found"}, 404)

    def post_api(self, db, path, data):
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "product-owner"]:
            self.post_product_owner_api(db, path, data)
            return
        if parts[:3] == ["api", "workspace", "storage"]:
            self.post_workspace_storage(db, path, data)
            return
        now = timestamp()
        if (
            len(parts) == 4
            and parts[:2] == ["api", "workspaces"]
            and parts[3] == "owner"
        ):
            try:
                tenant_id = int(parts[2])
            except ValueError:
                self.send_json({"error": "Invalid workspace ID."}, 400)
                return
            target_user_id = self.parse_project_id(data.get("user_id"))
            with db:
                tenant = db.execute(
                    "SELECT owner_user_id FROM tenants WHERE id=?",
                    (tenant_id,),
                ).fetchone()
                target = db.execute(
                    """SELECT u.id FROM users u
                    JOIN tenant_memberships m ON m.user_id=u.id
                    WHERE m.tenant_id=? AND m.user_id=?
                    AND m.status='Active' AND u.status='Active'""",
                    (tenant_id, target_user_id),
                ).fetchone()
                if not tenant:
                    self.send_json({"error": "Workspace not found."}, 404)
                    return
                if not target:
                    self.send_json({
                        "error": "The new owner must be an active workspace member."
                    }, 400)
                    return
                previous_owner_id = tenant["owner_user_id"]
                if previous_owner_id == target_user_id:
                    self.send_json({
                        "owner": user_public_dict(db, target_user_id, tenant_id),
                    })
                    return
                owner_role = db.execute(
                    "SELECT id FROM roles WHERE name='Workspace Owner' AND status='Active'"
                ).fetchone()
                member_role = db.execute(
                    "SELECT id FROM roles WHERE name='User' AND status='Active'"
                ).fetchone()
                if not owner_role or not member_role:
                    raise ValueError("Workspace owner roles are not available.")
                if previous_owner_id is not None:
                    db.execute(
                        """UPDATE tenant_memberships SET role_id=?,updated_at=?
                        WHERE tenant_id=? AND user_id=?""",
                        (member_role["id"], now, tenant_id, previous_owner_id),
                    )
                db.execute(
                    """UPDATE tenant_memberships SET role_id=?,updated_at=?
                    WHERE tenant_id=? AND user_id=?""",
                    (owner_role["id"], now, tenant_id, target_user_id),
                )
                db.execute(
                    """UPDATE tenants SET owner_user_id=?,updated_at=? WHERE id=?""",
                    (target_user_id, now, tenant_id),
                )
                actor = self.current_user(db) or {}
                audit_event(
                    db, actor.get("id"), "workspace.owner_changed",
                    "workspace", tenant_id,
                    {
                        "previous_owner_user_id": previous_owner_id,
                        "new_owner_user_id": target_user_id,
                    },
                    tenant_id=tenant_id,
                )
                owner = user_public_dict(db, target_user_id, tenant_id)
            self.send_json({"owner": owner})
            return
        if (
            len(parts) == 5
            and parts[:2] == ["api", "workspaces"]
            and parts[3:] == ["subscription", "reactivate"]
        ):
            try:
                tenant_id = int(parts[2])
            except ValueError:
                self.send_json({"error": "Invalid workspace ID."}, 400)
                return
            if not db.execute("SELECT 1 FROM tenants WHERE id=?", (tenant_id,)).fetchone():
                self.send_json({"error": "Workspace not found."}, 404)
                return
            with db:
                subscription = reactivate_trial(db, tenant_id)
            self.send_json({
                "reactivated": True,
                "subscription": subscription,
            })
            return
        if parts == ["api", "auth", "signup"]:
            if not self.peer_is_local():
                self.send_json({"error": "Sign-up is available only over a loopback connection."}, 403)
                return
            client_ip = self.client_address[0] if self.client_address else "unknown"
            signup_key = f"signup-ip:{client_ip}"
            if self.auth_rate_limited((signup_key,)):
                self.send_json(
                    {"error": "Too many sign-up attempts. Try again later."},
                    429,
                    {"Retry-After": str(AUTH_RATE_LIMIT_WINDOW_SECONDS)},
                )
                return
            self.record_auth_failure((signup_key,))
            first_name = validate_text(data, "first_name", True, 80)
            last_name = validate_text(data, "last_name", True, 80)
            email = validate_text(data, "email", True, 254).lower()
            workspace_name = validate_text(data, "workspace_name", limit=80)
            if not workspace_name:
                workspace_name = f"{first_name} {last_name} Workspace"
            workspace_name = normalize_workspace_name(workspace_name)
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                raise ValueError("Enter a valid email address.")
            if email == TENANT_ADMIN_EMAIL:
                self.send_json({
                    "error": "This email is reserved for the existing workspace administrator account."
                }, 409)
                return
            password = data.get("password")
            self.validate_password(password)
            if data.get("confirm_password") != password:
                raise ValueError("Password and confirmation must match.")
            with db:
                if db.execute(
                    "SELECT 1 FROM users WHERE email=? COLLATE NOCASE", (email,)
                ).fetchone():
                    self.send_json({"error": "An account with this email already exists."}, 409)
                    return
                role_name = "Workspace Owner"
                role = db.execute(
                    "SELECT id FROM roles WHERE name=? AND status='Active'", (role_name,)
                ).fetchone()
                if not role:
                    raise ValueError("The workspace owner role is not available.")
                tenant_id, tenant_slug = create_workspace(db, workspace_name, now)
                cursor = db.execute(
                    """INSERT INTO users
                    (first_name,last_name,email,password_hash,role_id,status,created_at,updated_at,public_user_id)
                    VALUES(?,?,?,?,?,'Active',?,?,?)""",
                    (
                        first_name, last_name, email, hash_password(password),
                        role["id"], now, now, str(uuid.uuid4()),
                    ),
                )
                user_id = cursor.lastrowid
                db.execute(
                    """UPDATE tenants SET owner_user_id=?,updated_at=? WHERE id=?""",
                    (user_id, now, tenant_id),
                )
                db.execute(
                    """INSERT INTO tenant_memberships
                    (tenant_id,user_id,role_id,status,created_at,updated_at)
                    VALUES(?,?,?,'Active',?,?)""",
                    (tenant_id, user_id, role["id"], now, now),
                )
                trial_started_at = utc_timestamp(datetime.fromisoformat(now))
                trial_ends_at = trial_end(datetime.fromisoformat(trial_started_at))
                db.execute(
                    """INSERT INTO tenant_subscriptions
                    (tenant_id,status,plan,trial_started_at,trial_ends_at,created_at,updated_at)
                    VALUES(?,'trialing',?,?,?,?,?)""",
                    (tenant_id, TRIAL_PLAN, trial_started_at, trial_ends_at, now, now),
                )
                db.execute(
                    """INSERT INTO usage_events
                    (tenant_id,user_id,event_type,created_at,metadata_json)
                    VALUES(?,?,'signup',?,'{}')""",
                    (tenant_id, user_id, now),
                )
                audit_event(
                    db, user_id, "workspace.created", "workspace", tenant_id,
                    {
                        "name": workspace_name,
                        "slug": tenant_slug,
                        "owner_user_id": user_id,
                        "trial_ends_at": trial_ends_at,
                    },
                    tenant_id=tenant_id,
                )
                user = user_public_dict(db, user_id, tenant_id)
                permissions = self.user_permissions(
                    db,
                    {"id": user_id, "role_name": role_name, "role_id": role["id"]},
                )
                access = workspace_access(db, tenant_id)
            token = self.issue_user_session(user_id)
            self.send_json(
                {
                    "authenticated": True,
                    "user": user,
                    "permissions": permissions,
                    "workspace": access["workspace"],
                    "subscription": access["subscription"],
                    "workspace_access": access["access_allowed"],
                },
                201,
                extra={
                    "Set-Cookie": (
                        f"trace_user_session={token}; Path=/; HttpOnly; SameSite=Strict; "
                        f"Max-Age=43200{self.session_cookie_flags()}"
                    )
                },
            )
            return
        if parts == ["api", "auth", "login"]:
            if not self.peer_is_local():
                self.send_json({"error": "Sign-in is available only over a loopback connection."}, 403)
                return
            email = data.get("email")
            password = data.get("password")
            client_ip = self.client_address[0] if self.client_address else "unknown"
            if isinstance(email, str) and email.strip():
                account_key = f"login:{email.strip().casefold()}"
                rate_keys = (f"auth-ip:{client_ip}", account_key)
                if self.auth_rate_limited(rate_keys):
                    self.send_json(
                        {"error": "Too many sign-in attempts. Try again later."},
                        429,
                        {"Retry-After": str(AUTH_RATE_LIMIT_WINDOW_SECONDS)},
                    )
                    return
                row = db.execute(
                    """SELECT u.id,u.password_hash,u.status FROM users u
                    WHERE u.email=? COLLATE NOCASE""",
                    (email.strip(),),
                ).fetchone()
                if not row or not verify_password(
                    password if isinstance(password, str) else "", row["password_hash"]
                ):
                    self.record_auth_failure(rate_keys)
                    self.send_json({"error": "Email or password is incorrect."}, 401)
                    return
                self.clear_auth_failures(account_key)
                if row["status"] != "Active":
                    self.send_json({
                        "error": "Your account has been deactivated.\nPlease contact your administrator."
                    }, 403)
                    return
                membership = db.execute(
                    """SELECT m.tenant_id FROM tenant_memberships m
                    JOIN tenants t ON t.id=m.tenant_id
                    JOIN roles r ON r.id=m.role_id AND r.status='Active'
                    WHERE m.user_id=? AND m.status='Active'
                    ORDER BY m.tenant_id LIMIT 1""",
                    (row["id"],),
                ).fetchone()
                if not membership:
                    inactive_membership = db.execute(
                        """SELECT 1 FROM tenant_memberships
                        WHERE user_id=? AND status!='Active' LIMIT 1""",
                        (row["id"],),
                    ).fetchone()
                    if inactive_membership:
                        self.send_json({
                            "error": "Your account has been deactivated.\nPlease contact your administrator."
                        }, 403)
                        return
                    self.send_json({"error": "Email or password is incorrect."}, 401)
                    return
                db.execute(
                    "UPDATE users SET last_login_at=?,updated_at=? WHERE id=?",
                    (now, now, row["id"]),
                )
                db.execute(
                    """INSERT INTO usage_events
                    (tenant_id,user_id,event_type,created_at,metadata_json)
                    VALUES(?,?,'login',?,'{}')""",
                    (membership["tenant_id"], row["id"], now),
                )
                user = user_public_dict(db, row["id"], membership["tenant_id"])
                token = self.issue_user_session(row["id"])
                self.send_json(
                    {
                        "authenticated": True,
                        "user": user,
                        "permissions": self.user_permissions(
                            db, {
                                "id": row["id"], "role_name": user["role_name"],
                                "role_id": user["role_id"],
                            }
                        ),
                    },
                    extra={
                        "Set-Cookie": (
                            f"trace_user_session={token}; Path=/; HttpOnly; SameSite=Strict; "
                            f"Max-Age=43200{self.session_cookie_flags()}"
                        )
                    },
                )
                return
            admin_key = f"admin:{client_ip}"
            if self.auth_rate_limited((admin_key,)):
                self.send_json(
                    {"error": "Too many sign-in attempts. Try again later."},
                    429,
                    {"Retry-After": str(AUTH_RATE_LIMIT_WINDOW_SECONDS)},
                )
                return
            expected = os.environ.get("QA_ADMIN_PASSWORD", "")
            if len(expected.strip()) < 12:
                self.send_json({
                    "error": "Set QA_ADMIN_PASSWORD to at least 12 characters in qa-platform/.env, then restart the server."
                }, 503)
                return
            if not isinstance(password, str) or not hmac.compare_digest(password, expected):
                self.record_auth_failure((admin_key,))
                self.send_json({"error": "Admin password is incorrect."}, 401)
                return
            self.clear_auth_failures(admin_key)
            token = self.issue_admin_session()
            self.send_json(
                {"authenticated": True, "role": "Global Admin"},
                extra={
                    "Set-Cookie": (
                        f"trace_admin_session={token}; Path=/; HttpOnly; SameSite=Strict; "
                        f"Max-Age=43200{self.session_cookie_flags()}"
                    )
                },
            )
            return
        if parts == ["api", "auth", "logout"]:
            self.send_json({"authenticated": False}, extra=self.logout_admin(db))
            return
        if len(parts) == 4 and parts[:2] == ["api", "users"] and \
            parts[3] in ("activate", "deactivate"):
            user_id = self.parse_project_id(parts[2])
            actor = self.current_user(db) or {}
            tenant_id = actor.get("tenant_id", 1)
            user = db.execute(
                """SELECT u.id,u.status,m.id membership_id,m.status membership_status,
                r.name role_name,m.tenant_id
                FROM users u JOIN tenant_memberships m ON m.user_id=u.id
                JOIN roles r ON r.id=m.role_id
                WHERE u.id=? AND m.tenant_id=?""",
                (user_id, tenant_id),
            ).fetchone()
            if not user:
                self.send_json({"error": "User not found in this workspace."}, 404)
                return
            new_status = "Active" if parts[3] == "activate" else "Inactive"
            owner = db.execute(
                "SELECT owner_user_id FROM tenants WHERE id=?",
                (tenant_id,),
            ).fetchone()
            if owner and owner["owner_user_id"] == user_id and new_status != "Active":
                self.send_json({
                    "error": "The workspace owner cannot be deactivated before ownership is transferred."
                }, 409)
                return
            tenant_scoped_admin = self.is_tenant_admin(actor)
            if tenant_scoped_admin and new_status == "Active" and user["status"] != "Active":
                self.send_json({
                    "error": "This account is inactive at the system level. Contact the system administrator."
                }, 409)
                return
            if user["membership_status"] == new_status and (
                tenant_scoped_admin or user["status"] == new_status
            ):
                self.send_json({"status": new_status})
                return
            if new_status == "Inactive" and user["role_name"] in ("Admin", "Global Admin"):
                lock_global_admin_role(db)
                active_admins = active_global_admin_count(
                    db, tenant_id if tenant_scoped_admin else None
                )
                if (
                    user["membership_status"] == "Active"
                    and (tenant_scoped_admin or user["status"] == "Active")
                    and active_admins <= 1
                ):
                    self.send_json({
                        "error": "The last active Global Admin cannot be deactivated."
                    }, 409)
                    return
            previous = (
                user["membership_status"] if tenant_scoped_admin else user["status"]
            )
            if not tenant_scoped_admin:
                db.execute(
                    "UPDATE users SET status=?,updated_at=? WHERE id=?",
                    (new_status, now, user_id),
                )
            db.execute(
                """UPDATE tenant_memberships SET status=?,updated_at=? WHERE id=?""",
                (new_status, now, user["membership_id"]),
            )
            if new_status == "Inactive" and not tenant_scoped_admin:
                self.invalidate_user_sessions(user_id, db=db)
            action = "user.activated" if new_status == "Active" else "user.deactivated"
            audit_event(
                db, actor.get("id"), action, "user", user_id,
                {"previous": previous, "new": new_status},
                tenant_id=tenant_id,
            )
            audit_event(
                db, actor.get("id"), "tenant_membership.changed",
                "tenant_membership", user["membership_id"],
                {
                    "previous": user["membership_status"],
                    "new": new_status,
                    "tenant_id": tenant_id,
                },
                tenant_id=tenant_id,
            )
            self.send_json({"status": new_status})
            return
        if parts == ["api", "users"]:
            first_name = validate_text(data, "first_name", True, 80)
            last_name = validate_text(data, "last_name", True, 80)
            email = validate_text(data, "email", True, 254).lower()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                raise ValueError("Enter a valid email address.")
            password = data.get("password")
            self.validate_password(password)
            actor = self.current_user(db)
            tenant_id = (actor or {}).get("tenant_id", 1)
            default_role = db.execute(
                """SELECT id FROM roles WHERE name='User' AND status='Active'
                AND (tenant_id IS NULL OR tenant_id=?)""",
                (tenant_id,),
            ).fetchone()
            role_id = self.parse_project_id(
                data.get("role_id", default_role["id"] if default_role else None)
            )
            role = self.role_in_scope(db, role_id, actor)
            if not role:
                raise ValueError("Choose an active role.")
            if not self.can_assign_role(db, actor, role):
                self.send_json({"error": "You cannot assign a role with permissions beyond your own."}, 403)
                return
            status = self.choice(data, "status", ("Active", "Inactive", "Invited", "Suspended"), "Active")
            cursor = db.execute(
                """INSERT INTO users
                (first_name,last_name,email,password_hash,role_id,phone,status,created_at,updated_at,public_user_id)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    first_name, last_name, email, hash_password(password), role_id,
                    validate_text(data, "phone", False, 40), status, now, now,
                    str(uuid.uuid4()),
                ),
            )
            user_id = cursor.lastrowid
            actor = self.current_user(db)
            tenant_id = actor.get("tenant_id", 1) if actor else 1
            db.execute(
                """INSERT INTO tenant_memberships
                (tenant_id,user_id,role_id,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?)""",
                (tenant_id, user_id, role_id, status, now, now),
            )
            sync_user_teams(db, user_id, tenant_id, data.get("team_ids", []), actor.get("id"))
            audit_event(
                db, actor.get("id"), "user.created",
                "user", user_id,
                {
                    "previous": None,
                    "new": {
                        "email": email, "role": role["name"], "status": status,
                    },
                },
                tenant_id=tenant_id,
            )
            audit_event(
                db, actor.get("id"), "tenant_membership.created",
                "tenant_membership", user_id,
                {"tenant_id": tenant_id, "role": role["name"], "status": status},
                tenant_id=tenant_id,
            )
            self.send_json(user_public_dict(db, user_id, tenant_id), 201)
            return
        if parts == ["api", "teams"]:
            name = validate_text(data, "name", True, 120)
            description = validate_text(data, "description", False, 2000)
            owner_id = self.parse_project_id(data.get("owner_user_id"))
            actor = self.current_user(db) or {}
            tenant_id = actor.get("tenant_id", 1)
            owner = db.execute(
                """SELECT id FROM tenant_memberships
                WHERE user_id=? AND tenant_id=? AND status='Active'""",
                (owner_id, tenant_id),
            ).fetchone()
            if not owner:
                raise ValueError("Choose an active team owner.")
            status = self.choice(data, "status", ("Active", "Inactive"), "Active")
            cursor = db.execute(
                """INSERT INTO teams(name,description,tenant_id,owner_user_id,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)""",
                (name, description, tenant_id, owner_id, status, now, now),
            )
            team_id = cursor.lastrowid
            db.execute(
                "INSERT INTO team_memberships(team_id,user_id,added_at) VALUES(?,?,?)",
                (team_id, owner_id, now),
            )
            actor_id = (self.current_user(db) or {}).get("id")
            if actor_id and actor_id != owner_id:
                db.execute(
                    """INSERT OR IGNORE INTO team_memberships(team_id,user_id,added_at)
                    VALUES(?,?,?)""",
                    (team_id, actor_id, now),
                )
            audit_event(
                db, actor_id,
                "team.created", "team", team_id, {"name": name, "owner_user_id": owner_id},
                tenant_id=tenant_id,
            )
            self.send_json(
                dict(db.execute(
                    """SELECT t.*,u.first_name||' '||u.last_name owner_name,
                    (SELECT COUNT(*) FROM team_memberships tm WHERE tm.team_id=t.id) member_count
                    FROM teams t JOIN users u ON u.id=t.owner_user_id WHERE t.id=?""",
                    (team_id,),
                ).fetchone()),
                201,
            )
            return
        if len(parts) == 4 and parts[:2] == ["api", "teams"] and parts[3] == "members":
            team_id = self.parse_project_id(parts[2])
            user_id = self.parse_project_id(data.get("user_id"))
            actor = self.current_user(db) or {}
            team = db.execute(
                "SELECT id,tenant_id FROM teams WHERE id=? AND status='Active'",
                (team_id,),
            ).fetchone()
            if team and not self.is_global_admin(actor) and \
                team["tenant_id"] != actor.get("tenant_id"):
                self.send_json({"error": "Team not found in this workspace."}, 404)
                return
            user = db.execute(
                """SELECT u.id FROM users u JOIN tenant_memberships m
                ON m.user_id=u.id WHERE u.id=? AND u.status='Active'
                AND m.tenant_id=? AND m.status='Active'""",
                (user_id, team["tenant_id"] if team else None),
            ).fetchone()
            if not team or not user:
                raise ValueError("Choose an active team and active user.")
            inserted = db.execute(
                """INSERT OR IGNORE INTO team_memberships(team_id,user_id,added_at)
                VALUES(?,?,?)""",
                (team_id, user_id, now),
            ).rowcount
            if inserted:
                audit_event(
                    db, (self.current_user(db) or {}).get("id"),
                    "team.member_added", "team", team_id, {"user_id": user_id},
                    tenant_id=team["tenant_id"],
                )
            self.send_json({"added": bool(inserted)})
            return
        if parts == ["api", "roles"]:
            name = validate_text(data, "name", True, 80)
            if name.casefold() in ("admin", "global admin"):
                raise ValueError("The system Global Admin role is protected.")
            permissions = self.validated_permissions(data.get("permissions", []))
            actor = self.current_user(db)
            actor_permissions = set(self.user_permissions(db, actor))
            if not (self.is_global_admin(actor) or self.is_tenant_admin(actor)) and \
                not set(permissions).issubset(actor_permissions):
                self.send_json({"error": "You cannot grant permissions beyond your own."}, 403)
                return
            description = validate_text(data, "description", False, 1000)
            tenant_id = (actor or {}).get("tenant_id", 1)
            cursor = db.execute(
                """INSERT INTO roles
                (name,description,permissions_json,tenant_id,is_system,status,created_at,updated_at)
                VALUES(?,?,?,?,0,'Active',?,?)""",
                (name, description, json.dumps(permissions), tenant_id, now, now),
            )
            role_id = cursor.lastrowid
            sync_role_permissions(db, role_id, permissions)
            audit_event(
                db, (self.current_user(db) or {}).get("id"),
                "role.created", "role", role_id,
                {"name": name, "permission_count": len(permissions)},
                tenant_id=tenant_id,
            )
            self.send_json({
                "id": role_id,
                "name": name,
                "description": description,
                "permissions": permissions,
                "status": "Active",
                "created_at": now,
                "user_count": 0,
                "permission_count": len(permissions),
            }, 201)
            return
        if parts == ["api", "ai", "module"]:
            user = self.current_user(db) or {}
            if not self.is_workspace_admin(db, user):
                self.send_json({
                    "error": "Workspace administrator access is required to enable the AI module."
                }, 403)
                return
            enabled = data.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("Choose whether to enable the AI module.")
            settings = set_ai_module_enabled(
                db, user.get("tenant_id", 1), enabled
            )
            settings["can_manage"] = True
            self.send_json(settings)
            return
        if parts == ["api", "ai", "settings"]:
            user = self.current_user(db) or {}
            tenant_id = user.get("tenant_id", 1)
            settings = save_provider_settings(db, data, tenant_id=tenant_id)
            settings["can_manage"] = self.is_workspace_admin(db, user)
            self.send_json(settings)
            return
        if parts == ["api", "custom-fields"]:
            configuration = custom_field_configuration(data)
            tenant_id = (self.current_user(db) or {}).get("tenant_id", 1)
            duplicate = db.execute(
                """SELECT 1 FROM custom_fields
                WHERE tenant_id=? AND module_key=? AND lower(name)=lower(?)""",
                (tenant_id, configuration["module_key"], configuration["name"]),
            ).fetchone()
            if duplicate:
                self.send_json({"error": "A field with this name already exists for that form."}, 409)
                return
            cursor = db.execute(
                """INSERT INTO custom_fields
                (tenant_id,module_key,name,label,field_type,description,required,active,
                display_order,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    tenant_id, configuration["module_key"], configuration["name"],
                    configuration["label"], configuration["field_type"],
                    configuration["description"], configuration["required"],
                    configuration["active"], configuration["display_order"], now, now,
                ),
            )
            field_id = cursor.lastrowid
            for option in configuration["options"]:
                db.execute(
                    """INSERT INTO custom_field_options
                    (custom_field_id,label,value,display_order) VALUES(?,?,?,?)""",
                    (field_id, option["label"], option["value"], option["display_order"]),
                )
            self.send_json(custom_field_dict(
                db, db.execute("SELECT * FROM custom_fields WHERE id=?", (field_id,)).fetchone()
            ), 201)
            return
        if parts in (["api", "ai", "test"], ["api", "ai", "ollama", "test"]):
            tenant_id = (self.current_user(db) or {}).get("tenant_id", 1)
            settings = active_provider_config(db, tenant_id)
            if settings["provider"] == "local":
                self.send_json({
                    "connected": True,
                    "provider": "local",
                    "model": settings["model"],
                    "message": "Local deterministic mode is ready; no remote connection is required.",
                })
                return
            generate_provider_text(
                settings,
                "You are checking the AI provider connection.",
                "Reply with the single word OK.",
                0,
            )
            self.send_json({
                "connected": True,
                "provider": settings["provider"],
                "model": settings["model"],
            })
            return
        if parts == ["api", "projects"]:
            name = validate_text(data, "name", True, 120)
            key = validate_text(data, "project_key", True, 20).upper()
            if not re.fullmatch(r"[A-Z0-9_-]+", key):
                raise ValueError("Project key may contain only letters, numbers, hyphens, and underscores.")
            owner = validate_text(data, "qa_owner", True, 120)
            status = self.choice(data, "status", ("Active", "Inactive"), "Active")
            tenant_id = (self.current_user(db) or {}).get("tenant_id", 1)
            cur = db.execute(
                "INSERT INTO projects(tenant_id,project_key,name,product_type,description,version,environment,status,qa_owner,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (tenant_id, key, name, validate_text(data, "product_type", limit=80),
                 validate_text(data, "description"), validate_text(data, "version", limit=40),
                 validate_text(data, "environment", limit=80), status, owner, now, now),
            )
            project_id = cur.lastrowid
            validate_custom_field_values(
                db, "projects", project_id, data.get("custom_fields", {}),
                required_on_create=True,
            )
            ensure_project_storage(self.server.storage_root, project_id)
            self.send_json(row_dict(db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()), 201)
            return
        if len(parts) < 4 or parts[:2] != ["api", "projects"]:
            self.send_json({"error": "Not found"}, 404)
            return
        pid = self.parse_project_id(parts[2])
        if not project_exists(db, pid):
            self.send_json({"error": "Project not found"}, 404)
            return
        if parts[3] == "attachments" and len(parts) == 4:
            module_key = data.get("module_key", "")
            record_id = self.parse_project_id(data.get("record_id", ""))
            if not record_belongs_to_project(db, module_key, pid, record_id):
                self.send_json({"error": "Record not found in this project."}, 404)
                return
            filename = data.get("filename")
            file_bytes = data.get("file_bytes")
            if not isinstance(filename, str) or not filename.strip():
                raise ValueError("Choose a file to upload.")
            if not isinstance(file_bytes, bytes) or not file_bytes:
                raise ValueError("Attachment upload is empty.")
            if len(file_bytes) > MAX_ATTACHMENT_SIZE:
                raise ValueError("Attachment is too large (maximum 5 MB).")
            filename = filename.replace("\\", "/").split("/")[-1]
            filename = "".join(
                character for character in filename
                if character.isprintable() and character not in '\"<>|:*?'
            ).strip()
            filename = filename[:180]
            extension = Path(filename).suffix.lower()
            if not filename or extension not in ATTACHMENT_TYPES:
                raise ValueError(
                    "Unsupported attachment type. Choose PDF, DOCX, XLSX, ZIP, CSV, "
                    "JSON, Markdown, TXT, PNG, JPEG, GIF, or WebP."
                )
            media_type = ATTACHMENT_TYPES[extension]
            digest = hashlib.sha256(file_bytes).hexdigest()
            if db.execute(
                """SELECT 1 FROM form_attachments
                WHERE module_key=? AND record_id=? AND sha256=?""",
                (module_key, record_id, digest),
            ).fetchone():
                self.send_json({"error": "This file is already attached to the record."}, 409)
                return
            cursor = db.execute(
                """INSERT INTO form_attachments
                (project_id,module_key,record_id,filename,content_type,file_size_bytes,
                sha256,file_data,uploaded_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    pid, module_key, record_id, filename, media_type, len(file_bytes),
                    digest, file_bytes, now,
                ),
            )
            self.send_json({
                "id": cursor.lastrowid,
                "module_key": module_key,
                "record_id": record_id,
                "filename": filename,
                "content_type": media_type,
                "file_size_bytes": len(file_bytes),
                "uploaded_at": now,
            }, 201)
            return
        if parts[3] == "applications" and len(parts) == 5 and parts[4] == "import":
            filename = data.get("filename")
            if not isinstance(filename, str) or not filename.strip() or len(filename) > 200:
                raise ValueError("Application filename is required and must be at most 200 characters.")
            archive_bytes = data.get("archive_bytes")
            if archive_bytes is None:
                encoded = validate_text(data, "zip_data", True, 35_000_000)
                try:
                    archive_bytes = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("Application ZIP data must be valid base64.") from exc
            if len(archive_bytes) > MAX_ARCHIVE_BYTES:
                raise ValueError("Application ZIP is too large (maximum 25 MB).")
            upload_token = uuid.uuid4().hex
            destination = (
                project_storage_dir(self.server.storage_root, pid) /
                "applications" / upload_token
            )
            result = analyze_application_zip(archive_bytes, filename, destination)
            try:
                cursor = db.execute(
                    """INSERT INTO application_uploads
                    (project_id,filename,storage_path,archive_size_bytes,inventory_json,analysis_json,uploaded_at)
                    VALUES(?,?,?,?,?,?,?)""",
                    (
                        pid,
                        result["filename"],
                        str(destination),
                        result["archive_size_bytes"],
                        json.dumps(result["inventory"], ensure_ascii=False),
                        json.dumps(result["analysis"], ensure_ascii=False),
                        now,
                    ),
                )
                row = db.execute(
                    """SELECT id,project_id,filename,archive_size_bytes,inventory_json,
                    analysis_json,coverage_json,coverage_checked_at,coverage_requirements_signature,
                    uploaded_at FROM application_uploads WHERE id=?""",
                    (cursor.lastrowid,),
                ).fetchone()
            except Exception:
                shutil.rmtree(destination, ignore_errors=True)
                raise
            self.send_json(application_upload_dict(row, requirements_signature(db, pid)), 201)
            return
        if (
            parts[3] == "applications" and len(parts) == 6
            and parts[5] == "validate-requirements"
        ):
            application_id = self.parse_project_id(parts[4])
            application = db.execute(
                """SELECT id,storage_path,inventory_json FROM application_uploads
                WHERE id=? AND project_id=?""",
                (application_id, pid),
            ).fetchone()
            if not application:
                self.send_json({"error": "Application upload not found"}, 404)
                return
            requirements = [
                dict(row) for row in db.execute(
                    "SELECT * FROM requirements WHERE project_id=? ORDER BY id",
                    (pid,),
                )
            ]
            if not requirements:
                self.send_json({"error": "Add or generate project requirements before validating code coverage."}, 400)
                return
            ai_settings = active_provider_config(db, project_tenant_id(db, pid))
            if ai_settings["provider"] == "local":
                raise ValueError(
                    "Static requirement coverage needs a connected AI provider. Select Ollama "
                    "(Default Workspace only), Google Gemini, OpenAI, Anthropic Claude, or an "
                    "OpenAI-compatible provider "
                    "in Settings → AI Module."
                )
            inventory = json.loads(application["inventory_json"])
            excerpts = source_code_excerpts(Path(application["storage_path"]), inventory)
            coverage = []
            for offset in range(0, len(requirements), 100):
                coverage.extend(validate_requirement_coverage(
                    ai_settings["provider"],
                    ai_settings["model"],
                    requirements[offset:offset + 100],
                    excerpts,
                    configuration=ai_settings,
                ))
            checked_at = timestamp()
            signature = requirements_signature(db, pid)
            db.execute(
                """UPDATE application_uploads SET coverage_json=?,coverage_checked_at=?,
                coverage_requirements_signature=?
                WHERE id=? AND project_id=?""",
                (
                    json.dumps(coverage, ensure_ascii=False),
                    checked_at,
                    signature,
                    application_id,
                    pid,
                ),
            )
            self.send_json({
                "application_id": application_id,
                "checked_at": checked_at,
                "coverage": coverage,
                "note": "AI-assisted static evidence estimate only; this does not prove runtime behavior.",
            })
            return
        if parts[3] == "analyses" and len(parts) == 4:
            self.send_json({"error": "App Analysis is coming soon."}, 503)
            return
        if parts[3] == "requirements" and len(parts) == 4:
            title = validate_text(data, "title", True, 200)
            requirement = {
                field: validate_text(data, field)
                for field in REQUIREMENT_CONTENT_FIELDS
                if field != "title"
            }
            requirement["title"] = title
            duplicate = find_duplicate_requirement(db, pid, requirement)
            if duplicate:
                self.send_json(
                    {"error": f"Duplicate requirement: this matches {duplicate['requirement_key']}."},
                    409,
                )
                return
            key = next_key(db, "requirements", "requirement_key", pid, "REQ")
            module_type, module_category = normalize_requirement_module(
                data.get("module_type"),
                data.get("module_category"),
                title,
                requirement["description"],
                requirement["acceptance_criteria"],
                requirement["business_rules"],
                requirement["validation_rules"],
                requirement["dependencies"],
            )
            cur = db.execute(
                """INSERT INTO requirements(project_id,requirement_key,title,description,acceptance_criteria,
                business_rules,validation_rules,dependencies,priority,module_type,module_category,
                requirement_type,scope_classification,ai_confidence_status,actor,preconditions,trigger,
                main_flow,alternative_flow,exception_flow,related_requirements,source_document,
                source_section,review_status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, key, title, requirement["description"], requirement["acceptance_criteria"],
                 requirement["business_rules"], requirement["validation_rules"],
                 requirement["dependencies"],
                 self.choice(data, "priority", ("High", "Medium", "Low", "Not specified"), "Medium"),
                 module_type, module_category,
                 self.choice(data, "requirement_type", (
                     "Use Case", "User Story", "Feature", "Functional Requirement",
                     "Non-Functional Requirement",
                 ), "Functional Requirement"),
                 self.choice(data, "scope_classification", (
                     "In Scope", "Out of Scope", "Requires Clarification",
                 ), "Requires Clarification"),
                 self.choice(data, "ai_confidence_status", (
                     "Supported", "Needs clarification",
                 ), "Needs clarification"),
                 *(validate_text(data, field) for field in (
                     "actor", "preconditions", "trigger", "main_flow",
                     "alternative_flow", "exception_flow", "related_requirements",
                 )),
                 validate_text(data, "source_document", limit=200),
                 validate_text(data, "source_section", limit=200), "Needs review", now),
            )
            validate_custom_field_values(
                db, "requirements", cur.lastrowid,
                data.get("custom_fields", {}), required_on_create=True,
            )
            self.send_json(row_dict(db.execute("SELECT * FROM requirements WHERE id=?", (cur.lastrowid,)).fetchone()), 201)
            return
        if parts[3] == "requirements" and len(parts) == 5 and parts[4] == "import":
            filename = validate_text(data, "filename", True, 200)
            if Path(filename.replace("\\", "/")).name != filename:
                raise ValueError("Requirement filename must be a plain file name.")
            suffix = Path(filename).suffix.lower()
            if suffix not in {".pdf", ".docx", ".csv", ".txt", ".md"}:
                raise ValueError("Only PDF, DOCX, CSV, TXT, and Markdown requirement documents are supported.")
            if suffix in {".docx", ".pdf"}:
                encoded = validate_text(data, "document_data", True, 7_000_000)
                try:
                    original = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError(f"Requirement {suffix[1:].upper()} data must be valid base64.") from exc
                if not original or len(original) > MAX_DOCUMENT_BYTES:
                    raise ValueError(f"Requirement {suffix[1:].upper()} is empty or too large (maximum 5 MB).")
                content_type = (
                    "application/pdf" if suffix == ".pdf"
                    else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
                document_text, records = extract_requirement_document(
                    filename, document_data=original
                )
            else:
                content = data.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("Requirement document content is required.")
                if len(content) > 1_000_000:
                    raise ValueError("Requirement document content is too long (maximum 1000000 characters).")
                document_text, records = extract_requirement_document(filename, content=content)
                original = content.encode("utf-8")
                content_type = (
                    "text/markdown" if suffix == ".md"
                    else "text/csv" if suffix == ".csv"
                    else "text/plain"
                )
            extracted_records = records
            generator = data.get("generator", "auto")
            supported_generators = {
                "auto", "local", "google", "ollama", "openai", "anthropic",
                "openai-compatible", "deterministic",
            }
            if not isinstance(generator, str) or generator not in supported_generators:
                raise ValueError("Choose Local deterministic or a configured AI provider.")
            analysis_warning = ""
            analysis_source = "rule-based"
            ai_suggestions = []
            preview_id = data.get("preview_id")
            if preview_id is None and "confirmed_preview" in data:
                raise ValueError("This BRD preview is no longer valid; analyze the document again.")
            if preview_id is not None:
                if not isinstance(preview_id, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{32,64}", preview_id
                ):
                    raise ValueError("BRD preview ID is invalid; analyze the document again.")
                stored_preview = db.execute(
                    """SELECT filename,source_sha256,preview_json,created_at
                    FROM requirement_analysis_previews WHERE id=? AND project_id=?""",
                    (preview_id, pid),
                ).fetchone()
                if not stored_preview:
                    raise ValueError("BRD preview expired or was already used; analyze again.")
                if (
                    stored_preview["filename"] != filename
                    or not hmac.compare_digest(
                        stored_preview["source_sha256"], hashlib.sha256(original).hexdigest()
                    )
                ):
                    raise ValueError("BRD source changed after preview; analyze the document again.")
                preview_created_at = datetime.fromisoformat(stored_preview["created_at"])
                if datetime.now(timezone.utc) - preview_created_at > timedelta(hours=24):
                    raise ValueError("BRD preview expired; analyze the document again.")
                preview = json.loads(stored_preview["preview_json"])
                records = validate_generated_requirements(
                    preview["requirements"], allow_empty=True
                )
                for record, stored_record in zip(records, preview["requirements"]):
                    record["requirement_key"] = stored_record.get("requirement_key", "")
                    record["source_evidence"] = stored_record.get("source_evidence", "")
                ai_suggestions = preview["suggestions"]
                analysis = validate_brd_analysis(preview["analysis"])
                analysis["analysis_status"] = preview["analysis_status"]
                analysis["requirements"] = [
                    item["content"] for item in ai_suggestions
                ]
                generator = preview["generator"]
                analysis_source = preview["analysis_source"]
                analysis_warning = preview["analysis_warning"]
            else:
                next_requirement_number = int(
                    next_key(db, "requirements", "requirement_key", pid, "REQ").split("-")[1]
                )
                for record in records:
                    duplicate = find_duplicate_requirement(db, pid, record)
                    if duplicate:
                        record["requirement_key"] = duplicate["requirement_key"]
                    elif not record.get("requirement_key"):
                        record["requirement_key"] = f"REQ-{next_requirement_number:03d}"
                        next_requirement_number += 1
                if generator != "deterministic":
                    explicit_ai = generator != "auto"
                    try:
                        ai_settings = active_provider_config(db, project_tenant_id(db, pid))
                        if explicit_ai and generator != ai_settings["provider"]:
                            raise ValueError(
                                "Choose the configured provider in Settings → AI Module before analysis."
                            )
                        if ai_settings["provider"] == "local":
                            generator = "deterministic"
                        else:
                            ai_candidates, analysis = generate_brd_analysis(
                                ai_settings["provider"],
                                ai_settings["model"],
                                document_text,
                                configuration=ai_settings,
                            )
                            generator = ai_settings["provider"]
                            analysis_source = ai_settings["provider"]
                            ai_suggestions = build_ai_requirement_suggestions(
                                ai_candidates, document_text, records
                            )
                    except ValueError as exc:
                        if explicit_ai:
                            raise
                        generator = "deterministic"
                        analysis_warning = (
                            "Local AI analysis was unavailable, so the report requires review "
                            f"and only rule-extracted requirements can be confirmed ({exc})"
                        )
                if generator == "deterministic":
                    analysis = fallback_brd_analysis(document_text)
                    analysis_source = "rule-based"
                source_evidence = [
                    record.get("source_evidence", "") for record in extracted_records
                ]
                records = validate_generated_requirements(records, allow_empty=True)
                for index, record in enumerate(records):
                    record["requirement_key"] = (
                        record.get("requirement_key")
                        or f"REQ-{next_requirement_number + index:03d}"
                    )
                    record["source_evidence"] = source_evidence[index]
                validated_suggestions = validate_generated_requirements(
                    [item["content"] for item in ai_suggestions], allow_empty=True
                )
                ai_suggestions = [
                    {**item, "content": content}
                    for item, content in zip(ai_suggestions, validated_suggestions)
                ]
                analysis = validate_brd_analysis(analysis)
                analysis_status = (
                    "AI SUGGESTION"
                    if analysis_source != "rule-based" else "NEEDS REVIEW"
                )
                analysis["analysis_status"] = analysis_status
                analysis["requirements"] = [
                    item["content"] for item in ai_suggestions
                ]
                preview_id = secrets.token_urlsafe(32)
                expired_before = (
                    datetime.now(timezone.utc) - timedelta(hours=24)
                ).isoformat(timespec="seconds")
                db.execute(
                    "DELETE FROM requirement_analysis_previews WHERE created_at<?",
                    (expired_before,),
                )
                preview = {
                    "requirements": records,
                    "suggestions": ai_suggestions,
                    "analysis": analysis,
                    "analysis_status": analysis_status,
                    "generator": generator,
                    "analysis_source": analysis_source,
                    "analysis_warning": analysis_warning,
                }
                db.execute(
                    """INSERT INTO requirement_analysis_previews
                    (id,project_id,filename,source_sha256,preview_json,created_at)
                    VALUES(?,?,?,?,?,?)""",
                    (
                        preview_id, pid, filename, hashlib.sha256(original).hexdigest(),
                        json.dumps(preview, ensure_ascii=False), timestamp(),
                    ),
                )
                self.send_json({
                    "preview_required": True,
                    "preview": {**preview, "preview_id": preview_id},
                })
                return
            analysis_json = json.dumps(analysis, ensure_ascii=False)
            filename = Path(filename).name
            document_id = uuid.uuid4().hex
            safe_filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:160] or "requirements"
            document_path = (
                project_storage_dir(self.server.storage_root, pid) /
                "requirements" / f"{document_id}-{safe_filename}"
            )
            document_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = document_path.with_name(f".{document_id}.tmp")
            # Determine version number for this filename
            existing_versions = db.execute(
                "SELECT MAX(version) FROM requirement_documents WHERE project_id=? AND filename=?",
                (pid, filename),
            ).fetchone()
            doc_version = (existing_versions[0] or 0) + 1
            try:
                with temp_path.open("xb") as output:
                    output.write(original)
                temp_path.replace(document_path)
                created = []
                skipped_duplicates = 0
                seen_keys: set[str] = set()
                seen_fingerprints = {
                    requirement_fingerprint(dict(row))
                    for row in db.execute("SELECT * FROM requirements WHERE project_id=?", (pid,))
                }
                for record in records:
                    fingerprint = requirement_fingerprint(record)
                    if fingerprint in seen_fingerprints:
                        skipped_duplicates += 1
                        continue
                    key = record.get("requirement_key") or next_key(db, "requirements", "requirement_key", pid, "REQ")
                    if key in seen_keys or db.execute(
                        "SELECT 1 FROM requirements WHERE project_id=? AND requirement_key=?",
                        (pid, key),
                    ).fetchone():
                        raise ValueError(f"Requirement ID {key} already exists in this project.")
                    seen_keys.add(key)
                    seen_fingerprints.add(fingerprint)
                    created.append(insert_requirement_record(
                        db,
                        pid,
                        record,
                        filename,
                        record.get("source_evidence", ""),
                        now,
                        origin="extracted",
                    ))
                cursor = db.execute(
                    """INSERT INTO requirement_documents
                    (project_id,filename,storage_path,content_type,file_size_bytes,requirement_count,
                    version,uploaded_at,analysis_json)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        pid, filename, str(document_path), content_type, len(original),
                        len(created), doc_version, now, analysis_json,
                    ),
                )
                document = db.execute(
                    """SELECT id,filename,content_type,file_size_bytes,requirement_count,version,uploaded_at
                    FROM requirement_documents WHERE id=?""", (cursor.lastrowid,)
                ).fetchone()
                suggestion_count = 0
                for suggestion in ai_suggestions:
                    content = suggestion["content"]
                    related_requirement_id = None
                    related_key = suggestion.get("related_requirement_key", "")
                    if related_key:
                        related = db.execute(
                            """SELECT id FROM requirements
                            WHERE project_id=? AND requirement_key=?""",
                            (pid, related_key),
                        ).fetchone()
                        if not related:
                            related_record = next(
                                (
                                    item for item in records
                                    if item.get("requirement_key") == related_key
                                ),
                                None,
                            )
                            if related_record:
                                related = find_duplicate_requirement(
                                    db, pid, related_record
                                )
                        related_requirement_id = related["id"] if related else None
                    db.execute(
                        """INSERT INTO requirement_suggestions
                        (project_id,document_id,related_requirement_id,related_requirement_key,
                        original_content_json,content_json,reason,source_reference,source_evidence,
                        status,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            pid, cursor.lastrowid, related_requirement_id, related_key,
                            json.dumps(content, ensure_ascii=False),
                            json.dumps(content, ensure_ascii=False), suggestion["reason"],
                            suggestion.get("source_reference", ""),
                            suggestion.get("source_evidence", ""),
                            "Pending", now, now,
                        ),
                    )
                    suggestion_count += 1
                db.execute(
                    "DELETE FROM requirement_analysis_previews WHERE id=? AND project_id=?",
                    (preview_id, pid),
                )
            except Exception:
                temp_path.unlink(missing_ok=True)
                document_path.unlink(missing_ok=True)
                raise
            self.send_json({
                "created": created,
                "count": len(created),
                "functional_count": sum(
                    1 for item in created if item["module_type"] == "Functional"
                ),
                "non_functional_count": sum(
                    1 for item in created if item["module_type"] == "Non-Functional"
                ),
                "skipped_duplicates": skipped_duplicates,
                "total_extracted": len(records),
                "suggestions_added": suggestion_count,
                "generator": generator,
                "analysis_warning": analysis_warning,
                "analysis_source": analysis_source,
                "analysis": analysis,
                "document": dict(document),
            }, 201)
            return
        if parts[3] == "requirements" and len(parts) == 5 and parts[4] == "analyze":
            requirements = [
                dict(row) for row in db.execute(
                    """SELECT * FROM requirements
                    WHERE project_id=? AND origin='manual' ORDER BY id""",
                    (pid,),
                )
            ]
            if not requirements:
                raise ValueError(
                    "AI classification only applies to manually entered requirements; "
                    "extracted and approved suggestion records are protected from AI overwrites."
                )
            ai_settings = active_provider_config(db, project_tenant_id(db, pid))
            if ai_settings["provider"] == "local":
                classifications = classify_requirements_locally(requirements)
            else:
                classifications = classify_requirements(
                    ai_settings["provider"],
                    ai_settings["model"],
                    requirements,
                    configuration=ai_settings,
                )
            for entry in classifications:
                db.execute(
                    """UPDATE requirements SET module_type=?,module_category=?
                    WHERE id=? AND project_id=?""",
                    (
                        entry["module_type"],
                        entry["module_category"],
                        entry["requirement_id"],
                        pid,
                    ),
                )
            updated = [
                dict(row) for row in db.execute(
                    "SELECT * FROM requirements WHERE project_id=? ORDER BY id", (pid,)
                )
            ]
            unchanged_records = db.execute(
                """SELECT COUNT(*) FROM requirements
                WHERE project_id=? AND origin!='manual'""",
                (pid,),
            ).fetchone()[0]
            self.send_json({
                "updated": len(classifications),
                "excluded_from_ai_classification": unchanged_records,
                "functional": sum(1 for item in updated if item["module_type"] == "Functional"),
                "non_functional": sum(
                    1 for item in updated if item["module_type"] == "Non-Functional"
                ),
                "requirements": updated,
            })
            return
        if parts[3] == "api-docs" and len(parts) == 5 and parts[4] == "import":
            filename = validate_text(data, "filename", True, 200)
            content = validate_text(data, "content", True, MAX_OPENAPI_SIZE)
            spec_version, title, endpoints = openapi_inventory(filename, content)
            filename = Path(filename).name[:200]
            cursor = db.execute(
                "INSERT INTO api_docs(project_id,filename,title,spec_version,imported_at) VALUES(?,?,?,?,?)",
                (pid, filename, title, spec_version, now),
            )
            document_id = cursor.lastrowid
            for endpoint in endpoints:
                db.execute(
                    """INSERT INTO api_endpoints(project_id,api_doc_id,method,path,summary,operation_id,tags)
                    VALUES(?,?,?,?,?,?,?)""",
                    (pid, document_id, endpoint["method"], endpoint["path"], endpoint["summary"],
                     endpoint["operation_id"], json.dumps(endpoint["tags"], ensure_ascii=False)),
                )
            document = row_dict(db.execute("SELECT * FROM api_docs WHERE id=?", (document_id,)).fetchone())
            document["endpoint_count"] = len(endpoints)
            self.send_json(document, 201)
            return
        if parts[3] == "test-cases" and len(parts) == 5 and parts[4] == "generate":
            if data.get("mode") == "ai_assisted":
                source_text = validate_text(
                    data, "source_text", True, MAX_AD_HOC_INPUT_LENGTH
                )
                categories = data.get("categories", list(AD_HOC_TEST_CATEGORIES))
                if (
                    not isinstance(categories, list)
                    or not categories
                    or len(categories) > len(AD_HOC_TEST_CATEGORIES)
                    or any(not isinstance(item, str) for item in categories)
                ):
                    raise ValueError("Select one or more valid test categories.")
                generator = data.get("generator", "google")
                if not isinstance(generator, str) or generator not in {
                    "google", "ollama", "openai", "anthropic",
                    "openai-compatible", "local",
                }:
                    raise ValueError("Select a configured AI provider for AI-assisted generation.")
                ai_settings = active_provider_config(db, project_tenant_id(db, pid))
                if generator != ai_settings["provider"]:
                    raise ValueError(
                        "Choose the configured provider in Settings → AI Module before generating cases."
                    )
                if ai_settings["provider"] == "local":
                    raise ValueError(
                        "AI-assisted generation requires a model-based provider. Configure Ollama "
                        "(Default Workspace only), Google Gemini, OpenAI, Anthropic Claude, or an "
                        "OpenAI-compatible provider "
                        "in Settings → AI Module."
                    )
                generated = generate_ad_hoc_test_cases(
                    ai_settings["provider"],
                    ai_settings["model"],
                    source_text,
                    categories,
                    configuration=ai_settings,
                )
                analysis = generated["analysis"]
                generated_cases = generated["test_cases"]
                if not generated_cases:
                    self.send_json({
                        "status": "needs_clarification",
                        "created": [],
                        "count": 0,
                        "skipped_existing": 0,
                        "analysis": analysis,
                        "generator": generator,
                    })
                    return

                created = []
                skipped = 0
                truncated = len(generated_cases) > MAX_GENERATED_CASES
                existing_keys = {
                    row["generation_key"] for row in db.execute(
                        "SELECT generation_key FROM test_cases WHERE project_id=? AND generation_key!=''",
                        (pid,),
                    )
                }
                for item in generated_cases[:MAX_GENERATED_CASES]:
                    if item["generation_key"] in existing_keys:
                        skipped += 1
                        continue
                    key = next_key(db, "test_cases", "case_key", pid, "TC")
                    cursor = db.execute(
                        """INSERT INTO test_cases
                        (project_id,case_key,title,scenario,description,steps,expected_result,
                        preconditions,test_data,priority,test_type,test_category,classification,
                        assumptions,source_reference,generation_key,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            pid, key, item["title"], item["scenario"],
                            "AI-assisted draft from user-provided content.",
                            item["steps"], item["expected_result"], item["preconditions"],
                            item["test_data"], item["priority"], item["test_type"],
                            item["test_category"], item["classification"], item["assumptions"],
                            item["source_reference"], item["generation_key"], now, now,
                        ),
                    )
                    existing_keys.add(item["generation_key"])
                    created.append(row_dict(db.execute(
                        "SELECT * FROM test_cases WHERE id=?", (cursor.lastrowid,)
                    ).fetchone()))
                self.send_json({
                    "status": "generated",
                    "created": created,
                    "count": len(created),
                    "skipped_existing": skipped,
                    "truncated": truncated,
                    "analysis": analysis,
                    "generator": generator,
                })
                return

            requested_ids = data.get("requirement_ids")
            if requested_ids is not None:
                if not isinstance(requested_ids, list) or len(requested_ids) > 100:
                    raise ValueError("Requirement IDs must be a list of at most 100 IDs.")
                if any(not isinstance(value, int) or isinstance(value, bool) for value in requested_ids):
                    raise ValueError("Requirement IDs must be integers.")
                requested_ids = list(dict.fromkeys(requested_ids))
                if not requested_ids:
                    raise ValueError("Select at least one requirement to generate test cases.")
                placeholders = ",".join("?" for _ in requested_ids)
                selected_requirements = db.execute(
                    f"SELECT * FROM requirements WHERE project_id=? AND id IN ({placeholders}) ORDER BY id",
                    (pid, *requested_ids),
                ).fetchall()
                if len(selected_requirements) != len(requested_ids):
                    raise ValueError("One or more requirements do not belong to this project.")
                unreviewed = [
                    item["requirement_key"] for item in selected_requirements
                    if item["review_status"] != "Reviewed"
                ]
                if unreviewed:
                    raise ValueError(
                        "Test cases can only be generated from requirements marked Reviewed. "
                        f"Review first: {', '.join(unreviewed[:10])}."
                    )
                requirements = selected_requirements
            else:
                requirements = db.execute(
                    """SELECT * FROM requirements WHERE project_id=? AND review_status='Reviewed'
                    ORDER BY id""",
                    (pid,),
                ).fetchall()
            if not requirements:
                raise ValueError(
                    "Add or import requirements, then mark at least one as Reviewed before generating test cases."
                )
            excluded_unreviewed = db.execute(
                """SELECT COUNT(*) FROM requirements
                WHERE project_id=? AND review_status!='Reviewed'""",
                (pid,),
            ).fetchone()[0]

            generator = data.get("generator", "deterministic")
            if generator in {
                "google", "ollama", "openai", "anthropic", "openai-compatible", "local",
            }:
                ai_settings = active_provider_config(db, project_tenant_id(db, pid))
                if generator != ai_settings["provider"]:
                    raise ValueError(
                        "Choose the configured provider in Settings → AI Module before generating cases."
                    )
                if ai_settings["provider"] == "local":
                    generated_cases = generate_project_cases(
                        [dict(requirement) for requirement in requirements],
                        [],
                        {},
                    )
                else:
                    generated_cases = generate_test_cases(
                        ai_settings["provider"],
                        ai_settings["model"],
                        [dict(requirement) for requirement in requirements],
                        [],
                        {},
                        configuration=ai_settings,
                    )
            elif generator == "deterministic":
                generated_cases = generate_project_cases(
                    [dict(requirement) for requirement in requirements],
                    [],
                    {},
                )
            else:
                raise ValueError(
                    "Generator must be deterministic or the configured AI provider."
                )
            created = []
            skipped = 0
            truncated = len(generated_cases) > MAX_GENERATED_CASES
            existing_keys = {
                row["generation_key"] for row in db.execute(
                    "SELECT generation_key FROM test_cases WHERE project_id=? AND generation_key!=''",
                    (pid,),
                )
            }
            requirements_by_id = {item["id"]: item for item in requirements}
            for generated in generated_cases[:MAX_GENERATED_CASES]:
                generation_key = generated["generation_key"]
                if generation_key in existing_keys:
                    skipped += 1
                    continue
                key = next_key(db, "test_cases", "case_key", pid, "TC")
                source_requirement = requirements_by_id[generated["requirement_id"]]
                cursor = db.execute(
                    """INSERT INTO test_cases
                    (project_id,requirement_id,case_key,scenario,title,description,steps,expected_result,
                    preconditions,test_data,use_case_id,priority,test_type,test_category,
                    classification,generation_key,
                    requirement_origin,requirement_approval_status,source_suggestion_id,
                    source_requirement_key,source_requirement_title,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        pid, generated["requirement_id"], key, generated["scenario"],
                        generated["scenario"],
                        generated["description"], generated["steps"],
                        generated["expected_result"], generated.get("preconditions", ""),
                        generated.get("test_data", ""), generated.get("use_case_id", ""),
                        generated.get("priority", "Not specified"), generated["test_type"],
                        "Non-Functional" if generated["test_type"] in {
                            "Performance", "Security", "Usability", "Compatibility",
                            "Reliability", "Non-Functional",
                        } else "Functional",
                        "Negative" if generated["test_type"] in {"Negative", "Validation"} else "Positive",
                        generation_key, source_requirement["origin"],
                        source_requirement["review_status"],
                        source_requirement["source_suggestion_id"],
                        source_requirement["requirement_key"], source_requirement["title"],
                        now, now,
                    ),
                )
                existing_keys.add(generation_key)
                created.append(row_dict(db.execute(
                    "SELECT * FROM test_cases WHERE id=?", (cursor.lastrowid,)
                ).fetchone()))
            ai_input_truncated = (
                generator not in {"deterministic", "local"} and len(requirements) > 100
            )
            message = "Generated draft cases from project requirements only."
            if excluded_unreviewed:
                message += f" Excluded {excluded_unreviewed} requirement(s) not marked Reviewed."
            if ai_input_truncated:
                message += " Some input records were omitted by model context limits."
            message += " Review every case before execution."
            covered_requirement_ids = {
                row["requirement_id"] for row in db.execute(
                    """SELECT DISTINCT requirement_id FROM test_cases
                    WHERE project_id=? AND requirement_id IN (
                        SELECT id FROM requirements WHERE project_id=? AND review_status='Reviewed'
                    )""",
                    (pid, pid),
                )
            }
            uncovered_requirements = [
                {
                    "id": item["id"],
                    "requirement_key": item["requirement_key"],
                    "title": item["title"],
                }
                for item in requirements
                if item["id"] not in covered_requirement_ids
            ]
            self.send_json({
                "created": created,
                "count": len(created),
                "skipped_existing": skipped,
                "truncated": truncated,
                "ai_input_truncated": ai_input_truncated,
                "source_counts": {
                    "requirements": len(requirements),
                    "api_endpoints": 0,
                    "application_routes": 0,
                    "source_files": 0,
                },
                "coverage": {
                    "reviewed_requirements": len(requirements),
                    "covered_requirements": len(requirements) - len(uncovered_requirements),
                    "uncovered_count": len(uncovered_requirements),
                    "uncovered_requirements": uncovered_requirements[:100],
                    "uncovered_truncated": len(uncovered_requirements) > 100,
                },
                "generator": generator,
                "message": message,
            }, 200)
            return
        if parts[3] == "test-cases" and len(parts) == 4:
            scenario = validate_text(data, "scenario", True, 200)
            req_id = self.optional_requirement(db, data.get("requirement_id"), pid)
            source_requirement = (
                db.execute(
                    """SELECT origin,review_status,source_suggestion_id,requirement_key,title
                    FROM requirements WHERE id=? AND project_id=?""",
                    (req_id, pid),
                ).fetchone()
                if req_id else None
            )
            key = next_key(db, "test_cases", "case_key", pid, "TC")
            cur = db.execute(
                """INSERT INTO test_cases
                (project_id,requirement_id,case_key,scenario,title,description,steps,expected_result,
                preconditions,test_data,use_case_id,priority,test_type,test_category,classification,
                assumptions,source_reference,requirement_origin,
                requirement_approval_status,source_suggestion_id,source_requirement_key,
                source_requirement_title,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, req_id, key, scenario,
                 validate_text(data, "title", limit=200) or scenario,
                 validate_text(data, "description"), validate_text(data, "steps"),
                 validate_text(data, "expected_result"),
                 validate_text(data, "preconditions"), validate_text(data, "test_data"),
                 validate_text(data, "use_case_id", limit=100),
                 self.choice(data, "priority", ("Critical", "High", "Medium", "Low", "Not specified"), "Not specified"),
                 self.choice(data, "test_type", TEST_TYPES, "Functional"),
                 self.choice(data, "test_category", AD_HOC_TEST_CATEGORIES, "Functional"),
                 self.choice(data, "classification", ("", "Positive", "Negative"), ""),
                 validate_text(data, "assumptions", limit=2000),
                 validate_text(data, "source_reference", limit=500),
                 source_requirement["origin"] if source_requirement else "",
                 source_requirement["review_status"] if source_requirement else "",
                 source_requirement["source_suggestion_id"] if source_requirement else None,
                 source_requirement["requirement_key"] if source_requirement else "",
                 source_requirement["title"] if source_requirement else "",
                 now, now),
            )
            validate_custom_field_values(
                db, "test-cases", cur.lastrowid,
                data.get("custom_fields", {}), required_on_create=True,
            )
            self.send_json(row_dict(db.execute("SELECT * FROM test_cases WHERE id=?", (cur.lastrowid,)).fetchone()), 201)
            return
        if parts[3] == "bugs" and len(parts) == 4:
            title = validate_text(data, "title", True, 200)
            tc_id = self.optional_case(db, data.get("test_case_id"), pid)
            key = next_key(db, "bugs", "bug_key", pid, "BUG")
            cur = db.execute(
                """INSERT INTO bugs(project_id,test_case_id,bug_key,title,description,severity,priority,status,assigned_to,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, tc_id, key, title, validate_text(data, "description"),
                 self.choice(data, "severity", ("Critical", "High", "Medium", "Low"), "Medium"),
                 self.choice(data, "priority", ("P1", "P2", "P3", "P4"), "P2"), "Open",
                 validate_text(data, "assigned_to", limit=120), now, now),
            )
            validate_custom_field_values(
                db, "bugs", cur.lastrowid,
                data.get("custom_fields", {}), required_on_create=True,
            )
            self.send_json(row_dict(db.execute("SELECT * FROM bugs WHERE id=?", (cur.lastrowid,)).fetchone()), 201)
            return
        if parts[3] == "executions" and len(parts) == 5:
            case_id = self.parse_project_id(parts[4])
            if not db.execute("SELECT id FROM test_cases WHERE id=? AND project_id=?", (case_id, pid)).fetchone():
                raise ValueError("Test case does not belong to this project.")
            status = self.choice(data, "status", STATUSES, "Not Executed")
            cur = db.execute(
                "INSERT INTO executions(test_case_id,status,actual_result,comments,executed_by,executed_at) VALUES(?,?,?,?,?,?)",
                (case_id, status, validate_text(data, "actual_result"), validate_text(data, "comments"),
                 validate_text(data, "executed_by", limit=120), now),
            )
            self.send_json(row_dict(db.execute("SELECT * FROM executions WHERE id=?", (cur.lastrowid,)).fetchone()), 201)
            return
        if parts[3] == "executions" and len(parts) == 6 and parts[5] == "evidence":
            execution_id = self.parse_project_id(parts[4])
            if not db.execute(
                """SELECT 1 FROM executions e JOIN test_cases tc ON tc.id=e.test_case_id
                WHERE e.id=? AND tc.project_id=?""", (execution_id, pid)
            ).fetchone():
                raise ValueError("Execution does not belong to this project.")
            filename = validate_text(data, "filename", True, 200)
            media_type = data.get("media_type")
            if not isinstance(media_type, str) or media_type not in IMAGE_TYPES:
                raise ValueError("Evidence must be a PNG, JPEG, GIF, or WebP image.")
            encoded = data.get("image_data")
            if not isinstance(encoded, str) or len(encoded) > ((MAX_EVIDENCE_SIZE + 2) // 3) * 4:
                raise ValueError("Evidence image is missing or too large (maximum 5 MB).")
            try:
                image_data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("Evidence image data must be valid base64.")
            validate_image(image_data, media_type)
            filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename.replace("\\", "/").split("/")[-1])[:160]
            filename = filename or "evidence" + IMAGE_TYPES[media_type]
            cursor = db.execute(
                """INSERT INTO execution_evidence(project_id,execution_id,filename,media_type,size,image_data,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (pid, execution_id, filename, media_type, len(image_data), image_data, now),
            )
            evidence = row_dict(db.execute(
                "SELECT id,filename,media_type,size,created_at FROM execution_evidence WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone())
            self.send_json(evidence, 201)
            return
        self.send_json({"error": "Not found"}, 404)

    def patch_api(self, db, path, data):
        parts = path.strip("/").split("/")
        if parts[:4] == ["api", "developer", "errors"] and len(parts) == 5:
            self.patch_developer_error(db, path, data)
            return
        actor = self.current_user(db) or {}
        actor_id = actor.get("id")
        if parts == ["api", "profile"]:
            global_admin = bool(actor.get("global_admin") and not actor_id)
            if not actor_id and not global_admin:
                self.send_json({"error": "A user account is required to update a profile."}, 401)
                return
            existing = db.execute(
                "SELECT * FROM global_admin_profile WHERE id=1" if global_admin
                else "SELECT * FROM users WHERE id=?",
                () if global_admin else (actor_id,),
            ).fetchone()
            first_name = validate_text(data, "first_name", True, 80)
            last_name = validate_text(data, "last_name", True, 80)
            email = validate_text(data, "email", True, 254).lower()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                raise ValueError("Enter a valid email address.")
            if not global_admin and db.execute(
                "SELECT 1 FROM users WHERE email=? COLLATE NOCASE AND id<>?",
                (email, actor_id),
            ).fetchone():
                self.send_json({"error": "An account with this email already exists."}, 409)
                return
            phone = validate_text(data, "phone", False, 40)
            image_data = existing["profile_image_data"]
            image_type = existing["profile_image_type"]
            if "profile_image_data" in data:
                encoded = data.get("profile_image_data")
                if encoded in (None, ""):
                    image_data, image_type = None, ""
                else:
                    image_type = data.get("profile_image_type", "")
                    if image_type not in IMAGE_TYPES:
                        raise ValueError("Choose a supported profile image (PNG, JPEG, GIF, or WebP).")
                    if not isinstance(encoded, str):
                        raise ValueError("Profile image data is invalid.")
                    try:
                        image_data = base64.b64decode(encoded, validate=True)
                    except (ValueError, binascii.Error):
                        raise ValueError("Profile image data is invalid.")
                    if len(image_data) > 1024 * 1024:
                        raise ValueError("Profile image must be 1 MB or smaller.")
            if global_admin:
                db.execute(
                    """UPDATE global_admin_profile SET first_name=?,last_name=?,email=?,phone=?,
                    profile_image_type=?,profile_image_data=?,updated_at=? WHERE id=1""",
                    (first_name, last_name, email, phone, image_type, image_data, timestamp()),
                )
                audit_event(
                    db, None, "global_admin.profile_updated", "global_admin", 1,
                    {
                        "previous": {
                            "first_name": existing["first_name"],
                            "last_name": existing["last_name"],
                            "email": existing["email"],
                            "phone": existing["phone"],
                            "profile_image_type": existing["profile_image_type"],
                            "has_profile_image": existing["profile_image_data"] is not None,
                        },
                        "new": {
                            "first_name": first_name,
                            "last_name": last_name,
                            "email": email,
                            "phone": phone,
                            "profile_image_type": image_type,
                            "has_profile_image": image_data is not None,
                        },
                    },
                )
                profile = dict(db.execute(
                    """SELECT first_name,last_name,email,phone,'Active' status,
                    created_at,NULL last_login_at,profile_image_type,profile_image_data
                    FROM global_admin_profile WHERE id=1"""
                ).fetchone())
                profile["global_admin"] = True
                profile["role_name"] = "Global Admin"
                if profile["profile_image_data"] is not None:
                    profile["profile_image_data"] = base64.b64encode(
                        profile["profile_image_data"]
                    ).decode("ascii")
                self.send_json(profile)
            else:
                db.execute(
                    """UPDATE users SET first_name=?,last_name=?,email=?,phone=?,
                    profile_image_type=?,profile_image_data=?,updated_at=? WHERE id=?""",
                    (first_name, last_name, email, phone, image_type, image_data, timestamp(), actor_id),
                )
                audit_event(
                    db, actor_id, "user.profile_updated", "user", actor_id,
                    {
                        "previous": {
                            "first_name": existing["first_name"],
                            "last_name": existing["last_name"],
                            "email": existing["email"],
                            "phone": existing["phone"],
                            "profile_image_type": existing["profile_image_type"],
                            "has_profile_image": existing["profile_image_data"] is not None,
                        },
                        "new": {
                            "first_name": first_name,
                            "last_name": last_name,
                            "email": email,
                            "phone": phone,
                            "profile_image_type": image_type,
                            "has_profile_image": image_data is not None,
                        },
                    },
                    tenant_id=actor.get("tenant_id"),
                )
                self.send_json(user_public_dict(db, actor_id))
            return
        if len(parts) == 3 and parts[:2] == ["api", "profile"] and parts[2] == "password":
            if not actor_id:
                self.send_json({"error": "A user account is required to change a password."}, 401)
                return
            row = db.execute(
                "SELECT password_hash FROM users WHERE id=?", (actor_id,)
            ).fetchone()
            current = data.get("current_password", "")
            new_password = data.get("new_password")
            if not row or not verify_password(current, row["password_hash"]):
                self.send_json({"error": "Current password is incorrect."}, 401)
                return
            self.validate_password(new_password)
            if data.get("confirm_password") != new_password:
                raise ValueError("New password and confirmation must match.")
            db.execute(
                "UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
                (hash_password(new_password), timestamp(), actor_id),
            )
            self.invalidate_user_sessions(actor_id, keep_current=True, db=db)
            audit_event(
                db, actor_id, "user.password_changed", "user", actor_id,
                tenant_id=actor.get("tenant_id"),
            )
            self.send_json({"updated": True})
            return
        if len(parts) == 3 and parts[:2] == ["api", "users"]:
            user_id = self.parse_project_id(parts[2])
            existing = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not existing:
                self.send_json({"error": "User not found."}, 404)
                return
            actor = self.current_user(db) or {}
            actor_id = actor.get("id")
            tenant_id = actor.get("tenant_id", 1)
            membership = db.execute(
                """SELECT m.*,r.name role_name FROM tenant_memberships m
                JOIN roles r ON r.id=m.role_id
                WHERE m.user_id=? AND m.tenant_id=?""",
                (user_id, tenant_id),
            ).fetchone()
            if not membership:
                self.send_json({"error": "User not found in this workspace."}, 404)
                return
            tenant_scoped_admin = self.is_tenant_admin(actor)
            other_memberships = db.execute(
                """SELECT COUNT(*) FROM tenant_memberships
                WHERE user_id=? AND tenant_id<>?""",
                (user_id, tenant_id),
            ).fetchone()[0]
            first_name = validate_text(data, "first_name", True, 80)
            last_name = validate_text(data, "last_name", True, 80)
            email = validate_text(data, "email", True, 254).lower()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                raise ValueError("Enter a valid email address.")
            if db.execute(
                "SELECT 1 FROM users WHERE email=? COLLATE NOCASE AND id<>?",
                (email, user_id),
            ).fetchone():
                self.send_json({"error": "An account with this email already exists."}, 409)
                return
            phone = validate_text(data, "phone", False, 40)
            profile_changed = (
                first_name != existing["first_name"]
                or last_name != existing["last_name"]
                or email != existing["email"]
                or phone != existing["phone"]
            )
            if tenant_scoped_admin and other_memberships and (
                profile_changed or bool(data.get("password"))
            ):
                self.send_json({
                    "error": "This account belongs to another workspace. Its shared profile and password can only be changed by the account owner or system administrator."
                }, 409)
                return
            role_id = self.parse_project_id(data.get("role_id", membership["role_id"]))
            role = self.role_in_scope(db, role_id, actor)
            if not role:
                raise ValueError("Choose an active role.")
            role_changed = membership["role_id"] != role_id
            if role_changed and not self.can_assign_role(db, actor, role):
                self.send_json({"error": "You cannot assign a role with permissions beyond your own."}, 403)
                return
            if role_changed and "users.change_role" not in self.user_permissions(db, actor) and \
                "*" not in self.user_permissions(db, actor):
                self.send_json({"error": "Your role does not allow this action (users.change_role)."}, 403)
                return
            status = self.choice(
                data, "status", ("Active", "Inactive", "Invited", "Suspended"),
                membership["status"] if tenant_scoped_admin else existing["status"],
            )
            owner = db.execute(
                "SELECT owner_user_id FROM tenants WHERE id=?",
                (tenant_id,),
            ).fetchone()
            if owner and owner["owner_user_id"] == user_id and (
                role["name"] != "Workspace Owner" or status != "Active"
            ):
                self.send_json({
                    "error": "The workspace owner role and active status must be retained until ownership is transferred."
                }, 409)
                return
            if tenant_scoped_admin and status == "Active" and existing["status"] != "Active":
                self.send_json({
                    "error": "This account is inactive at the system level. Contact the system administrator."
                }, 409)
                return
            if status != existing["status"]:
                status_permission = "users.activate" if status == "Active" else "users.deactivate"
                if status_permission not in self.user_permissions(db, actor) and \
                    "*" not in self.user_permissions(db, actor):
                    self.send_json({"error": f"Your role does not allow this action ({status_permission})."}, 403)
                    return
            is_active_target_admin = (
                membership["role_name"] in ("Admin", "Global Admin")
                and existing["status"] == "Active"
                and membership["status"] == "Active"
            )
            if is_active_target_admin and (
                role["name"] != "Global Admin" or status != "Active"
            ):
                lock_global_admin_role(db)
                active_admins = active_global_admin_count(
                    db, tenant_id if tenant_scoped_admin else None
                )
                if active_admins <= 1:
                    message = (
                        "The last active Global Admin cannot be deactivated."
                        if status != "Active" else
                        "The last active Global Admin cannot be reassigned."
                    )
                    self.send_json({"error": message}, 409)
                    return
            if actor_id == user_id and role["name"] == "Global Admin" and \
                membership["role_name"] != "Global Admin":
                self.send_json({"error": "You cannot assign Global Admin to your own account."}, 403)
                return
            previous = {
                "first_name": existing["first_name"], "last_name": existing["last_name"],
                "email": existing["email"], "phone": existing["phone"],
                "role": membership["role_name"], "status": membership["status"],
            }
            password_hash = existing["password_hash"]
            if data.get("password"):
                self.validate_password(data["password"])
                password_hash = hash_password(data["password"])
            if tenant_scoped_admin:
                db.execute(
                    """UPDATE users SET first_name=?,last_name=?,email=?,phone=?,
                    password_hash=?,updated_at=? WHERE id=?""",
                    (
                        first_name, last_name, email, phone,
                        password_hash, timestamp(), user_id,
                    ),
                )
            else:
                db.execute(
                    """UPDATE users SET first_name=?,last_name=?,email=?,phone=?,role_id=?,
                    status=?,password_hash=?,updated_at=? WHERE id=?""",
                    (
                        first_name, last_name, email, phone, role_id, status,
                        password_hash, timestamp(), user_id,
                    ),
                )
            db.execute(
                """UPDATE tenant_memberships SET role_id=?,status=?,updated_at=?
                WHERE id=?""",
                (role_id, status, timestamp(), membership["id"]),
            )
            sync_user_teams(db, user_id, tenant_id, data.get("team_ids"), actor_id)
            membership_status_changed = status != membership["status"]
            new_team_ids = [
                row["team_id"] for row in db.execute(
                    """SELECT tm.team_id FROM team_memberships tm JOIN teams t
                    ON t.id=tm.team_id WHERE tm.user_id=? AND t.tenant_id=?
                    ORDER BY tm.team_id""",
                    (user_id, tenant_id),
                )
            ]
            new_values = {
                "first_name": first_name, "last_name": last_name, "email": email,
                "phone": validate_text(data, "phone", False, 40),
                "role": role["name"], "status": status, "team_ids": new_team_ids,
            }
            audit_event(
                db, actor_id, "user.updated", "user", user_id,
                {"previous": previous, "new": new_values},
                tenant_id=tenant_id,
            )
            if role_changed:
                audit_event(
                    db, actor_id, "user.role_changed", "user", user_id,
                    {"previous": membership["role_name"], "new": role["name"]},
                    tenant_id=tenant_id,
                )
            if role_changed or membership_status_changed:
                audit_event(
                    db, actor_id, "tenant_membership.changed",
                    "tenant_membership", membership["id"],
                    {
                        "previous": {
                            "role": membership["role_name"],
                            "status": membership["status"],
                        },
                        "new": {"role": role["name"], "status": status},
                        "tenant_id": tenant_id,
                    },
                    tenant_id=tenant_id,
                )
            if membership_status_changed:
                action = "user.activated" if status == "Active" else "user.deactivated"
                audit_event(
                    db, actor_id, action, "user", user_id,
                    {"previous": membership["status"], "new": status},
                    tenant_id=tenant_id,
                )
            if status != "Active" and not tenant_scoped_admin:
                self.invalidate_user_sessions(user_id, db=db)
            if data.get("password"):
                audit_event(
                    db, actor_id, "user.password_reset", "user", user_id,
                    tenant_id=tenant_id,
                )
                self.invalidate_user_sessions(user_id, keep_current=(actor_id == user_id), db=db)
            self.send_json(user_public_dict(db, user_id, tenant_id))
            return
        if len(parts) == 3 and parts[:2] == ["api", "teams"]:
            team_id = self.parse_project_id(parts[2])
            existing = db.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
            actor = self.current_user(db) or {}
            if not existing or (
                not self.is_global_admin(actor)
                and existing["tenant_id"] != actor.get("tenant_id")
            ):
                self.send_json({"error": "Team not found."}, 404)
                return
            name = validate_text(data, "name", True, 120)
            description = validate_text(data, "description", False, 2000)
            owner_id = self.parse_project_id(data.get("owner_user_id", existing["owner_user_id"]))
            owner = db.execute(
                """SELECT id FROM tenant_memberships
                WHERE user_id=? AND tenant_id=? AND status='Active'""",
                (owner_id, existing["tenant_id"]),
            ).fetchone()
            if not owner:
                raise ValueError("Choose an active team owner.")
            status = self.choice(data, "status", ("Active", "Inactive"), existing["status"])
            db.execute(
                """UPDATE teams SET name=?,description=?,owner_user_id=?,status=?,updated_at=?
                WHERE id=?""",
                (name, description, owner_id, status, timestamp(), team_id),
            )
            db.execute(
                """INSERT OR IGNORE INTO team_memberships(team_id,user_id,added_at)
                VALUES(?,?,?)""",
                (team_id, owner_id, timestamp()),
            )
            audit_event(
                db, actor_id, "team.updated", "team", team_id,
                {"name": name, "owner_user_id": owner_id, "status": status},
                tenant_id=existing["tenant_id"],
            )
            self.send_json(dict(db.execute(
                """SELECT t.*,u.first_name||' '||u.last_name owner_name,
                (SELECT COUNT(*) FROM team_memberships tm WHERE tm.team_id=t.id) member_count
                FROM teams t LEFT JOIN users u ON u.id=t.owner_user_id WHERE t.id=?""",
                (team_id,),
            ).fetchone()))
            return
        if len(parts) == 3 and parts[:2] == ["api", "roles"]:
            role_id = self.parse_project_id(parts[2])
            existing = db.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
            if not existing or (
                not self.is_global_admin(actor)
                and existing["tenant_id"] != actor.get("tenant_id")
            ):
                self.send_json({"error": "Role not found."}, 404)
                return
            if existing["is_system"]:
                self.send_json({"error": "System roles are protected from modification."}, 403)
                return
            name = validate_text(data, "name", True, 80)
            description = validate_text(data, "description", False, 1000)
            status = self.choice(data, "status", ("Active", "Inactive"), existing["status"])
            if status == "Inactive" and db.execute(
                "SELECT 1 FROM tenant_memberships WHERE role_id=? LIMIT 1", (role_id,)
            ).fetchone():
                self.send_json(
                    {"error": "Reassign users from this role before deactivating it."}, 409
                )
                return
            permissions = json.loads(existing["permissions_json"])
            if "permissions" in data:
                actor_permissions = self.user_permissions(db, actor)
                if "*" not in actor_permissions and "roles.manage-permissions" not in actor_permissions:
                    self.send_json({"error": "You cannot manage role permissions."}, 403)
                    return
                permissions = self.validated_permissions(data["permissions"])
                if not (self.is_global_admin(actor) or self.is_tenant_admin(actor)) \
                    and not set(permissions).issubset(actor_permissions):
                    self.send_json({"error": "You cannot grant permissions beyond your own."}, 403)
                    return
            previous_permissions = json.loads(existing["permissions_json"])
            db.execute(
                """UPDATE roles SET name=?,description=?,permissions_json=?,status=?,updated_at=?
                WHERE id=?""",
                (name, description, json.dumps(permissions), status, timestamp(), role_id),
            )
            if permissions != previous_permissions:
                sync_role_permissions(db, role_id, permissions)
            audit_event(
                db, actor_id, "role.updated", "role", role_id,
                {
                    "name": name, "status": status,
                    "permission_count": len(permissions),
                },
                tenant_id=(actor or {}).get("tenant_id", 1),
            )
            if permissions != previous_permissions:
                audit_event(
                    db, actor_id, "permission.changed", "role", role_id,
                    {"previous": previous_permissions, "new": permissions},
                    tenant_id=(actor or {}).get("tenant_id", 1),
                )
            self.send_json({
                "id": role_id, "name": name, "description": description,
                "permissions": permissions, "status": status,
                "created_at": existing["created_at"],
            })
            return
        if len(parts) == 3 and parts[:2] == ["api", "custom-fields"]:
            field_id = self.parse_project_id(parts[2])
            actor = self.current_user(db) or {}
            tenant_id = actor.get("tenant_id", 1)
            existing = db.execute(
                """SELECT * FROM custom_fields WHERE id=?
                AND (?=1 OR tenant_id=?)""",
                (field_id, int(self.is_global_admin(actor)), tenant_id),
            ).fetchone()
            if not existing:
                self.send_json({"error": "Custom field not found."}, 404)
                return
            configuration = custom_field_configuration(data, existing)
            duplicate = db.execute(
                """SELECT 1 FROM custom_fields WHERE tenant_id=? AND module_key=?
                AND lower(name)=lower(?) AND id<>?""",
                (
                    existing["tenant_id"], configuration["module_key"],
                    configuration["name"], field_id,
                ),
            ).fetchone()
            if duplicate:
                self.send_json({"error": "A field with this name already exists for that form."}, 409)
                return
            value_count = db.execute(
                "SELECT COUNT(*) FROM custom_field_values WHERE custom_field_id=?",
                (field_id,),
            ).fetchone()[0]
            if value_count and (
                configuration["module_key"] != existing["module_key"]
                or configuration["field_type"] != existing["field_type"]
            ):
                self.send_json(
                    {"error": "A field with saved values cannot change form or field type."},
                    409,
                )
                return
            if configuration["options"] is not None:
                valid_values = {item["value"] for item in configuration["options"]}
                stored_values = {
                    row["value"] for row in db.execute(
                        """SELECT value FROM custom_field_values
                        WHERE custom_field_id=? AND value<>''""",
                        (field_id,),
                    )
                }
                if not stored_values.issubset(valid_values):
                    self.send_json(
                        {"error": "An option with saved record values cannot be removed."},
                        409,
                    )
                    return
            db.execute(
                """UPDATE custom_fields SET module_key=?,name=?,label=?,field_type=?,
                description=?,required=?,active=?,display_order=?,updated_at=? WHERE id=?""",
                (
                    configuration["module_key"], configuration["name"],
                    configuration["label"], configuration["field_type"],
                    configuration["description"], configuration["required"],
                    configuration["active"], configuration["display_order"],
                    timestamp(), field_id,
                ),
            )
            if configuration["options"] is not None:
                db.execute(
                    "DELETE FROM custom_field_options WHERE custom_field_id=?",
                    (field_id,),
                )
                for option in configuration["options"]:
                    db.execute(
                        """INSERT INTO custom_field_options
                        (custom_field_id,label,value,display_order) VALUES(?,?,?,?)""",
                        (field_id, option["label"], option["value"], option["display_order"]),
                    )
            self.send_json(custom_field_dict(
                db, db.execute("SELECT * FROM custom_fields WHERE id=?", (field_id,)).fetchone()
            ))
            return
        if len(parts) == 3 and parts[:2] == ["api", "projects"]:
            pid = self.parse_project_id(parts[2])
            row = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
            if not row:
                self.send_json({"error": "Project not found"}, 404)
                return

            name = validate_text(data, "name", True, 120) if "name" in data else row["name"]
            key = validate_text(data, "project_key", True, 20).upper() if "project_key" in data else row["project_key"]
            if not re.fullmatch(r"[A-Z0-9_-]+", key):
                raise ValueError("Project key may contain only letters, numbers, hyphens, and underscores.")
            duplicate = db.execute(
                "SELECT id FROM projects WHERE project_key=? AND id!=?", (key, pid)
            ).fetchone()
            if duplicate:
                self.send_json({"error": f"Project key {key} is already in use."}, 409)
                return
            product_type = validate_text(data, "product_type", limit=80) if "product_type" in data else row["product_type"]
            description = validate_text(data, "description") if "description" in data else row["description"]
            version = validate_text(data, "version", limit=40) if "version" in data else row["version"]
            environment = validate_text(data, "environment", limit=80) if "environment" in data else row["environment"]
            status = self.choice(data, "status", ("Active", "Inactive"), row["status"])
            owner = validate_text(data, "qa_owner", True, 120) if "qa_owner" in data else row["qa_owner"]
            db.execute(
                """UPDATE projects SET project_key=?,name=?,product_type=?,description=?,
                version=?,environment=?,status=?,qa_owner=?,updated_at=? WHERE id=?""",
                (key, name, product_type, description, version, environment, status, owner, timestamp(), pid),
            )
            if "custom_fields" in data:
                validate_custom_field_values(
                    db, "projects", pid, data["custom_fields"]
                )
            self.send_json(row_dict(db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()))
            return
        if len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3:5] == [
            "requirements", "suggestions",
        ]:
            pid = self.parse_project_id(parts[2])
            suggestion_id = self.parse_project_id(parts[5])
            row = db.execute(
                "SELECT * FROM requirement_suggestions WHERE id=? AND project_id=?",
                (suggestion_id, pid),
            ).fetchone()
            if not row:
                self.send_json({"error": "AI suggestion not found"}, 404)
                return
            if row["status"] != "Pending":
                self.send_json({"error": "This AI suggestion has already been decided."}, 409)
                return
            action = data.get("action")
            actions = {"edit": "Edited", "accept": "Accepted", "reject": "Rejected"}
            if action not in actions:
                raise ValueError("Choose edit, accept, or reject for an AI suggestion.")
            content = json.loads(row["content_json"])
            if action == "reject" and "content" in data:
                raise ValueError("Reject action cannot include edited content.")
            if action != "reject" and "content" in data:
                raw_content = data["content"]
                if not isinstance(raw_content, dict):
                    raise ValueError("Suggestion content must be an object.")
                content = validate_generated_requirements([raw_content])[0]
            elif action == "edit":
                raise ValueError("Edited suggestion content is required.")

            if action == "accept":
                duplicate = find_duplicate_requirement(db, pid, content)
                if duplicate:
                    self.send_json(
                        {"error": f"This suggestion duplicates {duplicate['requirement_key']}."},
                        409,
                    )
                    return
                document = db.execute(
                    "SELECT filename FROM requirement_documents WHERE id=? AND project_id=?",
                    (row["document_id"], pid),
                ).fetchone()
                accepted = insert_requirement_record(
                    db,
                    pid,
                    content,
                    document["filename"],
                    row["source_evidence"],
                    timestamp(),
                    origin="ai_suggestion",
                    review_status="Reviewed",
                    source_suggestion_id=suggestion_id,
                )
                db.execute(
                    """UPDATE requirement_suggestions SET content_json=?,status='Accepted',
                    accepted_requirement_id=?,updated_at=? WHERE id=?""",
                    (
                        json.dumps(content, ensure_ascii=False), accepted["id"],
                        timestamp(), suggestion_id,
                    ),
                )
            elif action == "reject":
                db.execute(
                    """UPDATE requirement_suggestions SET status='Rejected',updated_at=?
                    WHERE id=?""",
                    (timestamp(), suggestion_id),
                )
            else:
                db.execute(
                    """UPDATE requirement_suggestions SET content_json=?,updated_at=?
                    WHERE id=?""",
                    (json.dumps(content, ensure_ascii=False), timestamp(), suggestion_id),
                )
            reviewer = "Admin" if self.is_admin() else "Local reviewer"
            db.execute(
                """INSERT INTO requirement_suggestion_history
                (suggestion_id,action,content_json,reviewer,created_at)
                VALUES(?,?,?,?,?)""",
                (
                    suggestion_id, actions[action],
                    json.dumps(content, ensure_ascii=False), reviewer, timestamp(),
                ),
            )
            updated = db.execute(
                "SELECT * FROM requirement_suggestions WHERE id=?",
                (suggestion_id,),
            ).fetchone()
            self.send_json(requirement_suggestion_dict(db, updated))
            return
        if len(parts) != 5 or parts[:2] != ["api", "projects"]:
            self.send_json({"error": "Not found"}, 404)
            return
        pid = self.parse_project_id(parts[2])
        kind, record = parts[3], self.parse_project_id(parts[4])
        now = timestamp()
        if kind == "requirements":
            row = db.execute("SELECT * FROM requirements WHERE id=? AND project_id=?", (record, pid)).fetchone()
            if not row:
                self.send_json({"error": "Requirement not found"}, 404)
                return
            editable_fields = (
                "title", "description", "acceptance_criteria", "business_rules",
                "validation_rules", "dependencies", "source_section", "actor",
                "preconditions", "trigger", "main_flow", "alternative_flow",
                "exception_flow", "related_requirements",
            )
            vals = [
                validate_text(
                    data, field, field == "title",
                    200 if field in ("title", "source_section") else 12000,
                )
                if field in data else row[field]
                for field in editable_fields
            ]
            updated_content = dict(row)
            updated_content.update(dict(zip(editable_fields, vals)))
            module_type, module_category = normalize_requirement_module(
                data.get("module_type", row["module_type"]),
                data.get("module_category", row["module_category"]),
                updated_content["title"],
                updated_content["description"],
                updated_content["acceptance_criteria"],
                updated_content["business_rules"],
                updated_content["validation_rules"],
                updated_content["dependencies"],
            )
            duplicate = find_duplicate_requirement(db, pid, updated_content, exclude_id=record)
            if duplicate:
                self.send_json(
                    {"error": f"Duplicate requirement: this matches {duplicate['requirement_key']}."},
                    409,
                )
                return
            priority = self.choice(
                data, "priority", ("High", "Medium", "Low", "Not specified"), row["priority"]
            )
            review = self.choice(
                data, "review_status", ("Needs review", "Reviewed", "Rejected"), row["review_status"]
            )
            requirement_type = self.choice(data, "requirement_type", (
                "Use Case", "User Story", "Feature", "Functional Requirement",
                "Non-Functional Requirement",
            ), row["requirement_type"])
            scope_classification = self.choice(data, "scope_classification", (
                "In Scope", "Out of Scope", "Requires Clarification",
            ), row["scope_classification"])
            ai_confidence_status = self.choice(data, "ai_confidence_status", (
                "Supported", "Needs clarification",
            ), row["ai_confidence_status"])
            db.execute(
                """UPDATE requirements SET title=?,description=?,acceptance_criteria=?,business_rules=?,
                validation_rules=?,dependencies=?,source_section=?,actor=?,preconditions=?,trigger=?,
                main_flow=?,alternative_flow=?,exception_flow=?,related_requirements=?,priority=?,
                requirement_type=?,scope_classification=?,ai_confidence_status=?,module_type=?,
                module_category=?,review_status=? WHERE id=? AND project_id=?""",
                (
                    *vals, priority, requirement_type, scope_classification, ai_confidence_status,
                    module_type, module_category, review, record, pid,
                ),
            )
            if row["source_suggestion_id"]:
                decision_content = {
                    **updated_content,
                    "priority": priority,
                    "requirement_type": requirement_type,
                    "scope_classification": scope_classification,
                    "ai_confidence_status": ai_confidence_status,
                    "module_type": module_type,
                    "module_category": module_category,
                    "review_status": review,
                }
                db.execute(
                    """INSERT INTO requirement_suggestion_history
                    (suggestion_id,action,content_json,reviewer,created_at)
                    VALUES(?,?,?,?,?)""",
                    (
                        row["source_suggestion_id"], "Edited",
                        json.dumps(decision_content, ensure_ascii=False),
                        "Admin" if self.is_admin() else "Local reviewer", now,
                    ),
                )
        elif kind == "test-cases":
            row = db.execute("SELECT * FROM test_cases WHERE id=? AND project_id=?", (record, pid)).fetchone()
            if not row:
                self.send_json({"error": "Test case not found"}, 404)
                return
            scenario = validate_text(data, "scenario", True, 200) if "scenario" in data else row["scenario"]
            title = validate_text(data, "title", True, 200) if "title" in data else row["title"]
            desc = validate_text(data, "description") if "description" in data else row["description"]
            steps = validate_text(data, "steps") if "steps" in data else row["steps"]
            expected = validate_text(data, "expected_result") if "expected_result" in data else row["expected_result"]
            preconditions = validate_text(data, "preconditions") if "preconditions" in data else row["preconditions"]
            test_data = validate_text(data, "test_data") if "test_data" in data else row["test_data"]
            use_case_id = validate_text(data, "use_case_id", limit=100) if "use_case_id" in data else row["use_case_id"]
            priority = self.choice(
                data, "priority", ("Critical", "High", "Medium", "Low", "Not specified"), row["priority"]
            )
            test_type = self.choice(
                data, "test_type", TEST_TYPES, row["test_type"]
            )
            test_category = self.choice(
                data, "test_category", AD_HOC_TEST_CATEGORIES, row["test_category"]
            )
            classification = self.choice(
                data, "classification", ("", "Positive", "Negative"), row["classification"]
            )
            assumptions = validate_text(data, "assumptions", limit=2000) if "assumptions" in data else row["assumptions"]
            source_reference = validate_text(data, "source_reference", limit=500) if "source_reference" in data else row["source_reference"]
            req_id = self.optional_requirement(db, data.get("requirement_id"), pid) if "requirement_id" in data else row["requirement_id"]
            source_requirement = (
                db.execute(
                    """SELECT origin,review_status,source_suggestion_id,requirement_key,title
                    FROM requirements WHERE id=? AND project_id=?""",
                    (req_id, pid),
                ).fetchone()
                if req_id else None
            )
            db.execute(
                """UPDATE test_cases SET scenario=?,description=?,steps=?,expected_result=?,
                title=?,preconditions=?,test_data=?,use_case_id=?,priority=?,test_type=?,
                test_category=?,classification=?,assumptions=?,source_reference=?,requirement_id=?,
                requirement_origin=?,requirement_approval_status=?,source_suggestion_id=?,
                source_requirement_key=?,source_requirement_title=?,updated_at=?
                WHERE id=? AND project_id=?""",
                (scenario, desc, steps, expected, title, preconditions, test_data, use_case_id,
                 priority, test_type, test_category, classification, assumptions, source_reference, req_id,
                 source_requirement["origin"] if source_requirement else "",
                 source_requirement["review_status"] if source_requirement else "",
                 source_requirement["source_suggestion_id"] if source_requirement else None,
                 source_requirement["requirement_key"] if source_requirement else "",
                 source_requirement["title"] if source_requirement else "",
                 now, record, pid),
            )
        elif kind == "bugs":
            row = db.execute("SELECT * FROM bugs WHERE id=? AND project_id=?", (record, pid)).fetchone()
            if not row:
                self.send_json({"error": "Bug not found"}, 404)
                return
            title = validate_text(data, "title", True, 200) if "title" in data else row["title"]
            desc = validate_text(data, "description") if "description" in data else row["description"]
            severity = self.choice(data, "severity", ("Critical", "High", "Medium", "Low"), row["severity"])
            priority = self.choice(data, "priority", ("P1", "P2", "P3", "P4"), row["priority"])
            status = self.choice(data, "status", BUG_STATUSES, row["status"])
            assigned = validate_text(data, "assigned_to", limit=120) if "assigned_to" in data else row["assigned_to"]
            db.execute("UPDATE bugs SET title=?,description=?,severity=?,priority=?,status=?,assigned_to=?,updated_at=? WHERE id=? AND project_id=?",
                       (title, desc, severity, priority, status, assigned, now, record, pid))
        else:
            self.send_json({"error": "Not found"}, 404)
            return
        if "custom_fields" in data:
            module_key = {
                "requirements": "requirements",
                "test-cases": "test-cases",
                "bugs": "bugs",
            }[kind]
            validate_custom_field_values(
                db, module_key, record, data["custom_fields"]
            )
        table = {"requirements": "requirements", "test-cases": "test_cases", "bugs": "bugs"}[kind]
        self.send_json(row_dict(db.execute(f"SELECT * FROM {table} WHERE id=?", (record,)).fetchone()))

    def delete_api(self, db, path):
        parts = path.strip("/").split("/")
        if parts[:4] == ["api", "developer", "errors"] and len(parts) == 5:
            self.delete_developer_error(db, path)
            return
        if (
            len(parts) == 5
            and parts[:3] == ["api", "workspace", "storage"]
            and parts[3] == "items"
        ):
            self.delete_workspace_storage_item(db, path)
            return
        actor_id = (self.current_user(db) or {}).get("id")
        if len(parts) == 3 and parts[:2] == ["api", "users"]:
            user_id = self.parse_project_id(parts[2])
            user = db.execute(
                """SELECT u.id,u.first_name,u.last_name,u.email,u.phone,u.status,
                r.name role_name,m.id membership_id,m.tenant_id,m.status membership_status
                FROM users u JOIN tenant_memberships m ON m.user_id=u.id
                JOIN roles r ON r.id=m.role_id
                WHERE u.id=? AND m.tenant_id=?
                ORDER BY CASE m.status WHEN 'Active' THEN 0 ELSE 1 END LIMIT 1""",
                (user_id, (self.current_user(db) or {}).get("tenant_id", 1)),
            ).fetchone()
            if not user:
                self.send_json({"error": "User not found in this workspace."}, 404)
                return
            owner = db.execute(
                "SELECT owner_user_id FROM tenants WHERE id=?",
                (user["tenant_id"],),
            ).fetchone()
            if owner and owner["owner_user_id"] == user_id:
                self.send_json({
                    "error": "The workspace owner cannot be deleted before ownership is transferred."
                }, 409)
                return
            if actor_id == user_id:
                self.send_json({
                    "error": "You cannot delete your own account while signed in."
                }, 409)
                return
            actor = self.current_user(db) or {}
            tenant_scoped_admin = self.is_tenant_admin(actor)
            other_memberships = db.execute(
                """SELECT COUNT(*) FROM tenant_memberships
                WHERE user_id=? AND tenant_id<>?""",
                (user_id, user["tenant_id"]),
            ).fetchone()[0]
            if user["role_name"] in ("Admin", "Global Admin") and \
                user["status"] == "Active" and user["membership_status"] == "Active":
                lock_global_admin_role(db)
                admin_count = active_global_admin_count(
                    db, user["tenant_id"] if tenant_scoped_admin else None
                )
                if admin_count <= 1:
                    self.send_json({
                        "error": "The last active Global Admin cannot be deleted."
                    }, 409)
                    return
            previous = {
                "first_name": user["first_name"], "last_name": user["last_name"],
                "email": user["email"], "phone": user["phone"],
                "role": user["role_name"], "status": user["status"],
            }
            audit_event(
                db, actor_id,
                "tenant_membership.removed"
                if tenant_scoped_admin and other_memberships
                else "user.deleted",
                "tenant_membership"
                if tenant_scoped_admin and other_memberships
                else "user",
                user["membership_id"]
                if tenant_scoped_admin and other_memberships
                else user_id,
                {"previous": previous, "account_deleted": not (
                    tenant_scoped_admin and other_memberships
                )},
                tenant_id=user["tenant_id"],
            )
            if tenant_scoped_admin and other_memberships:
                db.execute(
                    """DELETE FROM team_memberships WHERE user_id=? AND team_id IN
                    (SELECT id FROM teams WHERE tenant_id=?)""",
                    (user_id, user["tenant_id"]),
                )
                db.execute(
                    "DELETE FROM tenant_memberships WHERE id=?",
                    (user["membership_id"],),
                )
            else:
                db.execute("DELETE FROM users WHERE id=?", (user_id,))
                self.invalidate_user_sessions(user_id, db=db)
            self.send_json({
                "deleted": True,
                "account_deleted": not (tenant_scoped_admin and other_memberships),
            })
            return
        if len(parts) == 3 and parts[:2] == ["api", "teams"]:
            team_id = self.parse_project_id(parts[2])
            actor = self.current_user(db) or {}
            row = db.execute(
                "SELECT id,name,tenant_id FROM teams WHERE id=?", (team_id,)
            ).fetchone()
            if not row or (
                not self.is_global_admin(actor)
                and row["tenant_id"] != actor.get("tenant_id")
            ):
                self.send_json({"error": "Team not found."}, 404)
                return
            db.execute(
                "UPDATE teams SET status='Inactive',updated_at=? WHERE id=?",
                (timestamp(), team_id),
            )
            audit_event(
                db, actor_id, "team.archived", "team", team_id,
                {"name": row["name"]}, tenant_id=row["tenant_id"],
            )
            self.send_json({"archived": True})
            return
        if (
            len(parts) == 5
            and parts[:2] == ["api", "teams"]
            and parts[3] == "members"
        ):
            team_id = self.parse_project_id(parts[2])
            user_id = self.parse_project_id(parts[4])
            actor = self.current_user(db) or {}
            team = db.execute(
                "SELECT owner_user_id,tenant_id FROM teams WHERE id=?", (team_id,)
            ).fetchone()
            if team and not self.is_global_admin(actor) and \
                team["tenant_id"] != actor.get("tenant_id"):
                self.send_json({"error": "Team not found in this workspace."}, 404)
                return
            membership = db.execute(
                "SELECT 1 FROM team_memberships WHERE team_id=? AND user_id=?",
                (team_id, user_id),
            ).fetchone()
            if not team or not membership:
                self.send_json({"error": "Team member not found."}, 404)
                return
            if team["owner_user_id"] == user_id:
                self.send_json({"error": "Transfer team ownership before removing its owner."}, 409)
                return
            db.execute(
                "DELETE FROM team_memberships WHERE team_id=? AND user_id=?",
                (team_id, user_id),
            )
            audit_event(
                db, actor_id, "team.member_removed", "team", team_id,
                {"user_id": user_id},
                tenant_id=team["tenant_id"],
            )
            self.send_json({"removed": True})
            return
        if len(parts) == 3 and parts[:2] == ["api", "roles"]:
            role_id = self.parse_project_id(parts[2])
            actor = self.current_user(db) or {}
            role = db.execute(
                "SELECT name,is_system,tenant_id FROM roles WHERE id=?", (role_id,)
            ).fetchone()
            if not role or (
                not self.is_global_admin(actor)
                and role["tenant_id"] != actor.get("tenant_id")
            ):
                self.send_json({"error": "Role not found."}, 404)
                return
            if role["is_system"]:
                self.send_json({"error": "System roles cannot be deleted."}, 403)
                return
            if db.execute(
                """SELECT 1 FROM tenant_memberships WHERE role_id=?
                UNION SELECT 1 FROM users WHERE role_id=? LIMIT 1""",
                (role_id, role_id),
            ).fetchone():
                self.send_json(
                    {"error": "Reassign users from this role before deleting it."}, 409
                )
                return
            db.execute("DELETE FROM roles WHERE id=?", (role_id,))
            audit_event(
                db, actor_id, "role.deleted", "role", role_id,
                {"name": role["name"]},
                tenant_id=(actor or {}).get("tenant_id", 1),
            )
            self.send_json({"deleted": True})
            return
        if len(parts) == 3 and parts[:2] == ["api", "custom-fields"]:
            field_id = self.parse_project_id(parts[2])
            actor = self.current_user(db) or {}
            tenant_id = actor.get("tenant_id", 1)
            if not db.execute(
                """SELECT 1 FROM custom_fields WHERE id=?
                AND (?=1 OR tenant_id=?)""",
                (field_id, int(self.is_global_admin(actor)), tenant_id),
            ).fetchone():
                self.send_json({"error": "Custom field not found."}, 404)
                return
            if db.execute(
                "SELECT 1 FROM custom_field_values WHERE custom_field_id=? LIMIT 1",
                (field_id,),
            ).fetchone():
                self.send_json(
                    {"error": "This field has saved values. Deactivate it instead of deleting it."},
                    409,
                )
                return
            db.execute("DELETE FROM custom_fields WHERE id=?", (field_id,))
            self.send_json({"deleted": True})
            return
        if (
            len(parts) == 5 and parts[:2] == ["api", "projects"]
            and parts[3] == "attachments"
        ):
            project_id = self.parse_project_id(parts[2])
            attachment_id = self.parse_project_id(parts[4])
            cursor = db.execute(
                "DELETE FROM form_attachments WHERE id=? AND project_id=?",
                (attachment_id, project_id),
            )
            if not cursor.rowcount:
                self.send_json({"error": "Attachment not found."}, 404)
                return
            self.send_json({"deleted": True})
            return
        if (
            len(parts) == 6
            and parts[:2] == ["api", "projects"]
            and parts[3:5] == ["requirements", "previews"]
        ):
            pid = self.parse_project_id(parts[2])
            preview_id = parts[5]
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", preview_id):
                self.send_json({"error": "BRD preview ID is invalid."}, 400)
                return
            cursor = db.execute(
                "DELETE FROM requirement_analysis_previews WHERE id=? AND project_id=?",
                (preview_id, pid),
            )
            if not cursor.rowcount:
                self.send_json({"error": "BRD preview not found."}, 404)
                return
            self.send_json({"deleted": True})
            return
        if len(parts) != 5 or parts[:2] != ["api", "projects"] or parts[3] not in ("requirements", "test-cases", "bugs"):
            self.send_json({"error": "Not found"}, 404)
            return
        pid = self.parse_project_id(parts[2])
        record = self.parse_project_id(parts[4])
        if parts[3] == "bugs":
            bug = db.execute(
                "SELECT id FROM bugs WHERE id=? AND project_id=?", (record, pid)
            ).fetchone()
            if not bug:
                self.send_json({"error": "Defect not found"}, 404)
                return
            cleanup_record_form_data(db, "bugs", record)
            db.execute("DELETE FROM bugs WHERE id=? AND project_id=?", (record, pid))
            self.send_json({"deleted": True})
            return
        if parts[3] == "test-cases":
            case = db.execute(
                "SELECT id FROM test_cases WHERE id=? AND project_id=?", (record, pid)
            ).fetchone()
            if not case:
                self.send_json({"error": "Test case not found"}, 404)
                return
            cleanup_record_form_data(db, "test-cases", record)
            db.execute("DELETE FROM test_cases WHERE id=? AND project_id=?", (record, pid))
            self.send_json({"deleted": True})
            return
        requirement = db.execute(
            "SELECT id FROM requirements WHERE id=? AND project_id=?",
            (record, pid),
        ).fetchone()
        if not requirement:
            self.send_json({"error": "Requirement not found"}, 404)
            return
        cleanup_record_form_data(db, "requirements", record)
        linked_cases = db.execute(
            "SELECT COUNT(*) FROM test_cases WHERE project_id=? AND requirement_id=?",
            (pid, record),
        ).fetchone()[0]
        db.execute("DELETE FROM requirements WHERE id=? AND project_id=?", (record, pid))
        self.send_json({"deleted": True, "unlinked_test_cases": linked_cases})

    def csv_report(self, db, pid):
        project = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Project", self.csv_cell(project["name"]), "Key", self.csv_cell(project["project_key"]),
                         "Version", self.csv_cell(project["version"])])
        writer.writerow([])
        writer.writerow(["Case ID", "Requirement ID", "Requirement Title", "Requirement Origin",
                         "Approval Status", "Source Suggestion ID", "Use Case ID",
                         "Module Type", "Module Category",
                         "Test Category", "Test Type", "Classification", "Priority",
                         "Test Case Title", "Scenario", "Preconditions", "Test Data",
                         "Assumptions / Dependencies", "Source Reference",
                         "Description", "Steps", "Expected Result",
                         "Actual Result", "Status", "Comments", "Evidence references"])
        for row in db.execute(
            """SELECT tc.case_key,
            COALESCE(r.requirement_key,NULLIF(tc.source_requirement_key,''),''),
            COALESCE(r.title,NULLIF(tc.source_requirement_title,''),''),
            COALESCE(NULLIF(tc.requirement_origin,''),r.origin,''),
            COALESCE(NULLIF(tc.requirement_approval_status,''),r.review_status,''),
            COALESCE(tc.source_suggestion_id,r.source_suggestion_id,''),
            tc.use_case_id, COALESCE(r.module_type,''), COALESCE(r.module_category,''),
            tc.test_category, tc.test_type, tc.classification, tc.priority,
            tc.title, tc.scenario, tc.preconditions, tc.test_data,
            tc.assumptions, tc.source_reference, tc.description, tc.steps, tc.expected_result,
            COALESCE(e.actual_result,''), COALESCE(e.status,'Not Executed'), COALESCE(e.comments,''),
            (SELECT group_concat(ev.id || ':' || ev.filename, '; ') FROM execution_evidence ev WHERE ev.execution_id=e.id)
            FROM test_cases tc LEFT JOIN requirements r ON r.id=tc.requirement_id
            LEFT JOIN executions e ON e.id=(SELECT id FROM executions WHERE test_case_id=tc.id ORDER BY id DESC LIMIT 1)
            WHERE tc.project_id=? ORDER BY tc.id""", (pid,)
        ):
            writer.writerow([self.csv_cell(value) for value in row])
        writer.writerow([])
        writer.writerow(["Requirement ID", "Module Type", "Module Category", "Requirement",
                         "Mapped Test Cases", "Coverage"])
        for row in db.execute(
            """SELECT r.requirement_key,r.module_type,r.module_category,r.title,COUNT(tc.id),
            CASE WHEN COUNT(tc.id)>0 THEN 'Covered' ELSE 'Not Covered' END
            FROM requirements r LEFT JOIN test_cases tc ON tc.requirement_id=r.id
            WHERE r.project_id=? GROUP BY r.id ORDER BY r.id""", (pid,)
        ):
            writer.writerow([self.csv_cell(value) for value in row])
        writer.writerow([])
        writer.writerow(["Bug ID", "Title", "Test Case ID", "Severity", "Priority", "Status", "Assigned To"])
        for row in db.execute(
            """SELECT b.bug_key,b.title,COALESCE(tc.case_key,''),b.severity,b.priority,b.status,b.assigned_to
            FROM bugs b LEFT JOIN test_cases tc ON tc.id=b.test_case_id
            WHERE b.project_id=? ORDER BY b.id""", (pid,)
        ):
            writer.writerow([self.csv_cell(value) for value in row])
        writer.writerow([])
        writer.writerow(["Evidence ID", "Case ID", "Execution ID", "Filename", "Media type", "Size bytes", "Created at"])
        for row in db.execute(
            """SELECT ev.id,tc.case_key,e.id,ev.filename,ev.media_type,ev.size,ev.created_at
            FROM execution_evidence ev JOIN executions e ON e.id=ev.execution_id
            JOIN test_cases tc ON tc.id=e.test_case_id
            WHERE ev.project_id=? AND tc.project_id=? ORDER BY ev.id""", (pid, pid)
        ):
            writer.writerow([self.csv_cell(value) for value in row])
        content = output.getvalue().encode("utf-8-sig")
        export_dir = project_storage_dir(self.server.storage_root, pid) / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / "project-report.csv").write_bytes(content)
        self.send_bytes(content, "text/csv; charset=utf-8",
                        extra={"Content-Disposition": f'attachment; filename="qa-report-{project["project_key"]}.csv"'})

    def module_csv_report(self, db, pid, module, search=""):
        search = str(search or "").strip().casefold()[:200]
        if module == "bugs":
            rows = [
                dict(row) for row in db.execute(
                    """SELECT b.*,tc.case_key FROM bugs b
                    LEFT JOIN test_cases tc ON tc.id=b.test_case_id
                    WHERE b.project_id=? ORDER BY b.id""",
                    (pid,),
                )
            ]
            custom_fields = custom_fields_for_record(db, "bugs", include_inactive=True)
            headers = [
                "Defect ID", "Title", "Description", "Linked Test Case",
                "Severity", "Priority", "Status", "Assignee", "Created At",
                "Updated At",
            ] + [field["label"] for field in custom_fields] + ["Attachments"]
            output_rows = []
            for row in rows:
                searchable = " ".join(
                    str(row[key] or "") for key in ("bug_key", "title", "case_key")
                ).casefold()
                if search and search not in searchable:
                    continue
                values = [
                    row["bug_key"], row["title"], row["description"],
                    row["case_key"] or "", row["severity"], row["priority"],
                    row["status"], row["assigned_to"], row["created_at"],
                    row["updated_at"],
                ]
                field_values = {
                    str(item["custom_field_id"]): item["value"]
                    for item in db.execute(
                        """SELECT custom_field_id,value FROM custom_field_values
                        WHERE module_key='bugs' AND record_id=?""",
                        (row["id"],),
                    )
                }
                values.extend(field_values.get(str(field["id"]), "") for field in custom_fields)
                attachments = db.execute(
                    """SELECT filename FROM form_attachments
                    WHERE project_id=? AND module_key='bugs' AND record_id=?
                    ORDER BY id""",
                    (pid, row["id"]),
                ).fetchall()
                values.append("; ".join(item["filename"] for item in attachments))
                output_rows.append(values)
            filename = "Defect_Log_Report.csv"
        elif module == "test-cases":
            rows = db.execute(
                """SELECT tc.case_key,tc.title,tc.scenario,tc.description,tc.steps,
                tc.expected_result,tc.preconditions,tc.test_data,tc.assumptions,
                tc.source_reference,tc.priority,tc.test_type,tc.test_category,
                tc.classification,COALESCE(e.status,'Not Executed') status,
                COALESCE(e.actual_result,'') actual_result,
                COALESCE(e.comments,'') comments
                FROM test_cases tc
                LEFT JOIN executions e ON e.id=(
                    SELECT id FROM executions WHERE test_case_id=tc.id ORDER BY id DESC LIMIT 1
                )
                WHERE tc.project_id=? ORDER BY tc.id""",
                (pid,),
            ).fetchall()
            headers = [
                "Test Case ID", "Title", "Scenario", "Description", "Steps",
                "Expected Result", "Preconditions", "Test Data",
                "Assumptions / Dependencies", "Source Reference", "Priority",
                "Type", "Category", "Classification", "Status", "Actual Result",
                "Comments",
            ]
            output_rows = [
                [row[key] for key in row.keys()]
                for row in rows
                if not search or search in " ".join(
                    str(row[key] or "").casefold()
                    for key in ("case_key", "title", "scenario")
                )
            ]
            filename = "Test_Case_Report.csv"
        elif module == "requirements":
            rows = db.execute(
                """SELECT requirement_key,title,description,acceptance_criteria,
                business_rules,validation_rules,dependencies,source_section,priority,
                requirement_type,scope_classification,ai_confidence_status,module_type,
                module_category,review_status
                FROM requirements WHERE project_id=? ORDER BY id""",
                (pid,),
            ).fetchall()
            headers = [
                "Requirement ID", "Title", "Description", "Acceptance Criteria",
                "Business Rules", "Validation Rules", "Dependencies", "Source",
                "Priority", "Type", "Scope", "Confidence", "Module Type",
                "Category", "Review Status",
            ]
            output_rows = [
                [row[key] for key in row.keys()]
                for row in rows
                if not search or search in " ".join(
                    str(row[key] or "").casefold()
                    for key in ("requirement_key", "title")
                )
            ]
            filename = "Requirement_Report.csv"
        else:
            self.send_json({"error": "Unsupported report module."}, 404)
            return

        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(
            [self.csv_cell(value) for value in row] for row in output_rows
        )
        content = output.getvalue().encode("utf-8-sig")
        export_dir = project_storage_dir(self.server.storage_root, pid) / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / filename).write_bytes(content)
        self.send_bytes(
            content,
            "text/csv; charset=utf-8",
            extra={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @staticmethod
    def csv_cell(value):
        value = "" if value is None else str(value)
        if re.match(r"^[\t\r\n ]*[=+\-@]", value):
            return "'" + value
        return value

    @staticmethod
    def parse_project_id(value):
        try:
            return int(value)
        except (ValueError, TypeError):
            raise ValueError("Invalid ID.")

    @staticmethod
    def choice(data, field, choices, default):
        value = data.get(field, default)
        if value not in choices:
            raise ValueError(f"Invalid {field.replace('_', ' ')}. Choose: {', '.join(choices)}.")
        return value

    @staticmethod
    def optional_requirement(db, value, project_id):
        if value in (None, "", 0, "0"):
            return None
        try:
            value = int(value)
        except (ValueError, TypeError):
            raise ValueError("Invalid requirement.")
        if not db.execute("SELECT id FROM requirements WHERE id=? AND project_id=?", (value, project_id)).fetchone():
            raise ValueError("Requirement does not belong to this project.")
        return value

    @staticmethod
    def optional_case(db, value, project_id):
        if value in (None, "", 0, "0"):
            return None
        try:
            value = int(value)
        except (ValueError, TypeError):
            raise ValueError("Invalid test case.")
        if not db.execute("SELECT id FROM test_cases WHERE id=? AND project_id=?", (value, project_id)).fetchone():
            raise ValueError("Test case does not belong to this project.")
        return value


def create_server(host="127.0.0.1", port=8001, db_path: Path | str = DB_PATH):
    validate_encryption_key_configuration()
    initialize(db_path)
    server = ThreadingHTTPServer((host, port), QAHandler)
    server.cors_origins = cors_origins()
    if isinstance(db_path, str) and db_path.startswith(("postgres://", "postgresql://")):
        server.db_path = db_path
        server.storage_root = DATA_ROOT / "storage"
    else:
        server.db_path = Path(db_path)
        server.storage_root = Path(db_path).resolve().parent / "storage"
    server.admin_sessions = {}
    server.user_sessions = {}
    server.admin_session_lock = threading.Lock()
    server.developer_report_windows = {}
    server.developer_report_lock = threading.Lock()
    server.auth_rate_windows = {}
    server.auth_rate_lock = threading.Lock()
    server.api_metrics = deque(maxlen=20000)
    server.api_metrics_lock = threading.Lock()
    with database(server.db_path) as db:
        for row in db.execute("SELECT id FROM projects"):
            ensure_project_storage(server.storage_root, row["id"])
    return server


def create_startup_server(host, port):
    try:
        return create_server(host, port)
    except OSError as exc:
        if (
            exc.errno != errno.EADDRINUSE
            or port != 8001
            or "QA_PORT" in os.environ
        ):
            raise
        print(
            "Port 8001 is already in use; starting QA Platform on an available port.",
            flush=True,
        )
        return create_server(host, 0)


if __name__ == "__main__":
    host, port = server_address()
    server = create_startup_server(host, port)
    port = server.server_port
    browser_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{browser_host}:{port}/"
    print(f"QA Platform running at {url} (Ctrl+C to stop)", flush=True)
    if host in ("127.0.0.1", "localhost", "0.0.0.0"):
        try:
            webbrowser.open(url)
        except (OSError, webbrowser.Error) as exc:
            print(f"Could not open a browser automatically: {exc}", flush=True)
    try:
        try:
            server.serve_forever()
        finally:
            server.server_close()
            close_database_pools()
    except KeyboardInterrupt:
        print("\nStopping QA Platform...")
        server.server_close()
