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
import shutil
import secrets
import struct
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
from urllib.parse import parse_qs, unquote, urlparse
from qa_platform.core.config import (
    DATA_ROOT,
    FRONTEND_ROOT,
    cors_origins,
    database_connection,
    server_address,
)
from qa_platform.core.database import (
    DATABASE_ERRORS,
    DATABASE_INTEGRITY_ERRORS,
    connect_database,
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
}
ALL_PERMISSIONS = [
    f"{module}.{action}"
    for module, actions in PERMISSION_MODULES.items()
    for action in actions
]
DEFAULT_ROLE_PERMISSIONS = {
    "Global Admin": ["*"],
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
        """SELECT         u.id,u.first_name,u.last_name,u.email,u.phone,
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
            """
        )
        now = timestamp()
        db.execute(
            """INSERT OR IGNORE INTO tenants(id,name,slug,status,created_at,updated_at)
            VALUES(1,'Default Workspace','default','Active',?,?)""",
            (now, now),
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
                    "System administrator" if role_name == "Global Admin" else f"Default {role_name} access",
                    json.dumps(permissions),
                    int(role_name == "Global Admin"),
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
        print("%s - %s" % (self.log_date_time_string(), fmt % args))

    def send_bytes(self, data: bytes, content_type: str, status=200, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
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

        token = self.session_cookie("trace_user_session")
        with self.server.admin_session_lock:
            session = self.server.user_sessions.get(token)
            if session is None:
                return None
            user_id, expiry = session
            if expiry <= time.time():
                self.server.user_sessions.pop(token, None)
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
                m.tenant_id,m.role_id
                FROM users u
                JOIN tenant_memberships m ON m.user_id=u.id AND m.status='Active'
                JOIN roles r ON r.id=m.role_id AND r.status='Active'
                JOIN tenants t ON t.id=m.tenant_id AND t.status='Active'
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
                        JOIN tenants t ON t.id=m.tenant_id AND t.status='Active'
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
            and user.get("role_name") in ("Global Admin", "Admin")
        )

    def auth_status(self, db):
        password = os.environ.get("QA_ADMIN_PASSWORD", "")
        user = self.current_user(db)
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

    def can_assign_role(self, db, actor, role):
        if self.is_global_admin(actor) or self.is_tenant_admin(actor):
            return True
        if role["name"] in ("Admin", "Global Admin"):
            return False
        actor_permissions = set(self.user_permissions(db, actor))
        target_permissions = set(role_permission_keys(db, role["id"]))
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
            return "admin" if method != "GET" else None
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
        if path.strip("/").split("/")[:2] == ["api", "auth"]:
            return True
        user = self.current_user(db)
        if user is None:
            self.send_json({"error": "Sign in is required."}, 401)
            return False
        permission = self.request_permission(method, path)
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

    def issue_admin_session(self):
        token = secrets.token_urlsafe(32)
        with self.server.admin_session_lock:
            self.server.admin_sessions[token] = time.time() + 12 * 60 * 60
        return token

    def issue_user_session(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.server.admin_session_lock:
            self.server.user_sessions[token] = (user_id, time.time() + 12 * 60 * 60)
        return token

    def invalidate_user_sessions(self, user_id, keep_current=False):
        current_token = self.session_cookie("trace_user_session")
        with self.server.admin_session_lock:
            for token, (session_user_id, _) in list(self.server.user_sessions.items()):
                if session_user_id == user_id and (
                    not keep_current or token != current_token
                ):
                    self.server.user_sessions.pop(token, None)

    def peer_is_local(self):
        try:
            if os.environ.get("QA_ALLOW_PUBLIC_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}:
                return True
            if os.environ.get("ALLOW_PUBLIC_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}:
                return True
            # Allow if running in cloud/container mode
            host, _ = server_address()
            if host == "0.0.0.0":
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

    def logout_admin(self):
        admin_token = self.session_cookie("trace_admin_session")
        user_token = self.session_cookie("trace_user_session")
        with self.server.admin_session_lock:
            self.server.admin_sessions.pop(admin_token, None)
            self.server.user_sessions.pop(user_token, None)
        return {
            "Set-Cookie": [
                "trace_admin_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0",
                "trace_user_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0",
            ],
        }

    def body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid request size.")
        if size < 0 or size > MAX_BODY:
            raise ValueError("Request is too large (maximum 27 MB).")
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
        if path in ("/health", "/healthz"):
            self.send_json({"status": "ok"}, 200)
            return
        if not path.startswith("/api/"):
            self.static(path)
            return
        if path in ("/api/health", "/api/healthz"):
            self.send_json({"status": "ok"}, 200)
            return
        try:
            with database(self.server.db_path) as db:
                if not self.authorize_request("GET", path, db):
                    return
                self.get_api(db, path)
        except (ValueError, *DATABASE_ERRORS) as exc:
            self.send_json({"error": str(exc)}, 400)
        except OSError:
            self.send_json({"error": "Storage error while reading the requested file."}, 500)
        except Exception:
            traceback.print_exc()
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
            with database(self.server.db_path) as db:
                if not self.authorize_request("POST", request_path, db):
                    return
            data = self.body()
            with database(self.server.db_path) as db:
                self.post_api(db, request_path, data)
                parts = request_path.strip("/").split("/")
                if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
                    try:
                        project_id = int(parts[2])
                    except ValueError:
                        project_id = None
                    if project_id is not None:
                        sync_project_tenant_data(db, project_id)
        except DATABASE_INTEGRITY_ERRORS as exc:
            if integrity_error_is(exc, "users.email", "users_email_key", "ux_users_email_ci"):
                self.send_json({"error": "An account with this email already exists."}, 409)
            elif integrity_error_is(exc, "teams.name", "teams_name_key", "ux_teams_name_ci"):
                self.send_json({"error": "A team with this name already exists."}, 409)
            elif integrity_error_is(exc, "roles.name", "roles_name_key", "ux_roles_name_ci"):
                self.send_json({"error": "A role with this name already exists."}, 409)
            else:
                self.send_json({"error": str(exc)}, 400)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        except DATABASE_ERRORS as exc:
            self.send_json({"error": f"Database error: {exc}"}, 400)
        except OSError:
            self.send_json({"error": "Storage error while processing the request."}, 500)
        except Exception:
            traceback.print_exc()
            self.send_json({"error": "Unexpected server error while processing the request. Check the server console."}, 500)

    def do_PATCH(self):
        try:
            request_path = urlparse(self.path).path
            with database(self.server.db_path) as db:
                if not self.authorize_request("PATCH", request_path, db):
                    return
            data = self.body()
            with database(self.server.db_path) as db:
                self.patch_api(db, request_path, data)
                parts = request_path.strip("/").split("/")
                if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
                    try:
                        project_id = int(parts[2])
                    except ValueError:
                        project_id = None
                    if project_id is not None:
                        sync_project_tenant_data(db, project_id)
        except DATABASE_INTEGRITY_ERRORS as exc:
            if integrity_error_is(exc, "users.email", "users_email_key", "ux_users_email_ci"):
                self.send_json({"error": "An account with this email already exists."}, 409)
            elif integrity_error_is(exc, "teams.name", "teams_name_key", "ux_teams_name_ci"):
                self.send_json({"error": "A team with this name already exists."}, 409)
            elif integrity_error_is(exc, "roles.name", "roles_name_key", "ux_roles_name_ci"):
                self.send_json({"error": "A role with this name already exists."}, 409)
            else:
                self.send_json({"error": str(exc)}, 400)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        except DATABASE_ERRORS as exc:
            self.send_json({"error": f"Database error: {exc}"}, 400)
        except Exception:
            traceback.print_exc()
            self.send_json({"error": "Unexpected server error while updating the record. Check the server console."}, 500)

    def do_DELETE(self):
        path = unquote(urlparse(self.path).path)
        try:
            with database(self.server.db_path) as db:
                if not self.authorize_request("DELETE", path, db):
                    return
        except DATABASE_ERRORS as exc:
            self.send_json({"error": f"Database error: {exc}"}, 400)
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
                    except OSError:
                        traceback.print_exc()
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
        if isinstance(exc, (ValueError, *DATABASE_INTEGRITY_ERRORS)):
            self.send_json({"error": str(exc)}, 400)
        elif isinstance(exc, DATABASE_ERRORS):
            self.send_json({"error": f"Database error: {exc}"}, 400)
        elif isinstance(exc, OSError):
            self.send_json({"error": "Storage error while deleting the record."}, 500)
        else:
            traceback.print_exc()
            self.send_json({"error": "Unexpected server error while deleting the record. Check the server console."}, 500)

    def static(self, path):
        if (path.rstrip("/") or "/") in (
            "/", "/index.html", "/about", "/privacy", "/terms",
            "/login", "/signup", "/workspace",
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

    def get_api(self, db, path):
        parts = path.strip("/").split("/")
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
                """SELECT id,first_name,last_name,email,phone,status,created_at,last_login_at,
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
            self.send_json(provider_settings(db, self.is_admin()))
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
                if not stored_file.is_file():
                    self.send_json({"error": "Original requirement document is unavailable."}, 404)
                    return
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", document["filename"]) or "requirements"
                self.send_bytes(
                    stored_file.read_bytes(),
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
        now = timestamp()
        if parts == ["api", "auth", "signup"]:
            if not self.peer_is_local():
                self.send_json({"error": "Sign-up is available only over a loopback connection."}, 403)
                return
            first_name = validate_text(data, "first_name", True, 80)
            last_name = validate_text(data, "last_name", True, 80)
            email = validate_text(data, "email", True, 254).lower()
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
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone():
                self.send_json({"error": "An account with this email already exists."}, 409)
                return
            role_name = "User"
            role = db.execute(
                "SELECT id FROM roles WHERE name=? AND status='Active'", (role_name,)
            ).fetchone()
            if not role:
                raise ValueError("The default account role is not available.")
            cursor = db.execute(
                """INSERT INTO users
                (first_name,last_name,email,password_hash,role_id,status,created_at,updated_at)
                VALUES(?,?,?,?,?,'Active',?,?)""",
                (first_name, last_name, email, hash_password(password), role["id"], now, now),
            )
            user_id = cursor.lastrowid
            db.execute(
                """INSERT INTO tenant_memberships
                (tenant_id,user_id,role_id,status,created_at,updated_at)
                VALUES(1,?,?,'Active',?,?)""",
                (user_id, role["id"], now, now),
            )
            audit_event(
                db, user_id, "user.created", "user", user_id,
                {
                    "previous": None,
                    "new": {
                        "email": email, "role": role_name, "status": "Active",
                    },
                    "source": "signup",
                },
                tenant_id=1,
            )
            token = self.issue_user_session(user_id)
            user = user_public_dict(db, user_id, 1)
            self.send_json(
                {
                    "authenticated": True,
                    "user": user,
                    "permissions": self.user_permissions(
                        db, {"id": user_id, "role_name": role_name, "role_id": role["id"]}
                    ),
                },
                201,
                extra={
                    "Set-Cookie": (
                        f"trace_user_session={token}; Path=/; HttpOnly; SameSite=Strict; "
                        "Max-Age=43200"
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
            if isinstance(email, str) and email.strip():
                row = db.execute(
                    """SELECT u.id,u.password_hash,u.status FROM users u
                    WHERE u.email=? COLLATE NOCASE""",
                    (email.strip(),),
                ).fetchone()
                if not row or not verify_password(
                    password if isinstance(password, str) else "", row["password_hash"]
                ):
                    self.send_json({"error": "Email or password is incorrect."}, 401)
                    return
                if row["status"] != "Active":
                    self.send_json({
                        "error": "Your account has been deactivated.\nPlease contact your administrator."
                    }, 403)
                    return
                membership = db.execute(
                    """SELECT m.tenant_id FROM tenant_memberships m
                    JOIN tenants t ON t.id=m.tenant_id
                    JOIN roles r ON r.id=m.role_id AND r.status='Active'
                    WHERE m.user_id=? AND m.status='Active' AND t.status='Active'
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
                            "Max-Age=43200"
                        )
                    },
                )
                return
            expected = os.environ.get("QA_ADMIN_PASSWORD", "")
            if len(expected.strip()) < 12:
                self.send_json({
                    "error": "Set QA_ADMIN_PASSWORD to at least 12 characters in qa-platform/.env, then restart the server."
                }, 503)
                return
            if not isinstance(password, str) or not hmac.compare_digest(password, expected):
                self.send_json({"error": "Admin password is incorrect."}, 401)
                return
            token = self.issue_admin_session()
            self.send_json(
                {"authenticated": True, "role": "Global Admin"},
                extra={
                    "Set-Cookie": (
                        f"trace_admin_session={token}; Path=/; HttpOnly; SameSite=Strict; "
                        "Max-Age=43200"
                    )
                },
            )
            return
        if parts == ["api", "auth", "logout"]:
            self.send_json({"authenticated": False}, extra=self.logout_admin())
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
                self.invalidate_user_sessions(user_id)
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
            default_role = db.execute(
                "SELECT id FROM roles WHERE name='User' AND status='Active'"
            ).fetchone()
            role_id = self.parse_project_id(
                data.get("role_id", default_role["id"] if default_role else None)
            )
            role = db.execute(
                "SELECT * FROM roles WHERE id=? AND status='Active'", (role_id,)
            ).fetchone()
            if not role:
                raise ValueError("Choose an active role.")
            if not self.can_assign_role(db, self.current_user(db), role):
                self.send_json({"error": "You cannot assign a role with permissions beyond your own."}, 403)
                return
            status = self.choice(data, "status", ("Active", "Inactive", "Invited", "Suspended"), "Active")
            cursor = db.execute(
                """INSERT INTO users
                (first_name,last_name,email,password_hash,role_id,phone,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    first_name, last_name, email, hash_password(password), role_id,
                    validate_text(data, "phone", False, 40), status, now, now,
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
        if parts == ["api", "ai", "settings"]:
            self.send_json(save_provider_settings(db, data))
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
            settings = active_provider_config(db)
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
            ai_settings = active_provider_config(db)
            if ai_settings["provider"] == "local":
                raise ValueError(
                    "Static requirement coverage needs a connected AI provider. Select Ollama, "
                    "Google Gemini, or an OpenAI-compatible provider in Settings → AI Module."
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
                "auto", "local", "google", "ollama", "openai-compatible",
                "deterministic",
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
                        ai_settings = active_provider_config(db)
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
            ai_settings = active_provider_config(db)
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
                    "google", "ollama", "openai-compatible", "local",
                }:
                    raise ValueError("Select a configured AI provider for AI-assisted generation.")
                ai_settings = active_provider_config(db)
                if generator != ai_settings["provider"]:
                    raise ValueError(
                        "Choose the configured provider in Settings → AI Module before generating cases."
                    )
                if ai_settings["provider"] == "local":
                    raise ValueError(
                        "AI-assisted generation requires a model-based provider. Configure Ollama, "
                        "Google Gemini, or an OpenAI-compatible provider in Settings → AI Module."
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
            if generator in {"google", "ollama", "openai-compatible", "local"}:
                ai_settings = active_provider_config(db)
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
            self.invalidate_user_sessions(actor_id, keep_current=True)
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
            role = db.execute(
                "SELECT * FROM roles WHERE id=? AND status='Active'", (role_id,)
            ).fetchone()
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
                self.invalidate_user_sessions(user_id)
            if data.get("password"):
                audit_event(
                    db, actor_id, "user.password_reset", "user", user_id,
                    tenant_id=tenant_id,
                )
                self.invalidate_user_sessions(user_id, keep_current=(actor_id == user_id))
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
                self.invalidate_user_sessions(user_id)
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
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping QA Platform...")
        server.server_close()
