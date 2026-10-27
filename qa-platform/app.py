"""Local-first QA test management MVP. Uses only the Python standard library."""

from __future__ import annotations

import csv
import errno
import hashlib
import ipaddress
import hmac
import io
import json
import mimetypes
import os
import re
import sqlite3
import base64
import binascii
import shutil
import secrets
import struct
import subprocess
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
from qa_platform.core.config import ROOT, database_path, server_address
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


REA_ROOT = ROOT.parent / "rea"
REA_CLI = REA_ROOT / "scripts" / "rea.mjs"
DB_PATH = database_path()
MAX_BODY = 27 * 1024 * 1024
MAX_OPENAPI_SIZE = 1_000_000
MAX_EVIDENCE_SIZE = 5 * 1024 * 1024
REA_ANALYSIS_TIMEOUT_SECONDS = 600
REA_STDOUT_LIMIT = 2 * 1024 * 1024
REA_STDERR_LIMIT = 1024 * 1024
REA_EVIDENCE_LIMIT = 1024 * 1024 * 1024
OPENAPI_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
STATUSES = ("Not Executed", "In Progress", "Pass", "Fail", "Blocked", "Skipped")
BUG_STATUSES = ("Open", "In Progress", "Resolved", "Closed")
REA_REQUIRED_RUNTIME = (
    "dist/cli-runtime/cliBuiltinArguments.js",
    "dist/cli-runtime/cli.js",
    "dist/cli-runtime/cliOutput.js",
    "dist/cli-runtime/cliResultOutput.js",
)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_storage_dir(storage_root: Path | str, project_id: int) -> Path:
    return Path(storage_root) / "projects" / str(project_id)


def ensure_project_storage(storage_root: Path | str, project_id: int) -> Path:
    project_dir = project_storage_dir(storage_root, project_id)
    for name in ("requirements", "applications", "exports"):
        (project_dir / name).mkdir(parents=True, exist_ok=True)
    return project_dir


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


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
            """
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


def _supported_node_version(version: str) -> bool:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return False
    major, minor, patch = (int(part) for part in match.groups())
    return (
        (major == 22 and (minor, patch) >= (23, 4))
        or (major == 24 and (minor, patch) >= (22, 1))
        or (major > 26 or (major == 26 and (minor, patch) >= (12, 1)))
    )


def rea_runtime(server=None) -> dict:
    node = shutil.which("node")
    node_version = None
    reasons = []
    if not node:
        reasons.append("Node.js was not found on the server PATH.")
    else:
        try:
            result = subprocess.run(
                [node, "--version"],
                cwd=str(REA_ROOT),
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
                shell=False,
            )
            node_version = result.stdout.strip()[:80]
            if result.returncode != 0 or not _supported_node_version(node_version):
                reasons.append("REA requires Node.js 22.23.4+, 24.22.1+, or 26.12.1+.")
        except (OSError, subprocess.TimeoutExpired):
            reasons.append("Node.js version could not be read.")

    cli_available = REA_CLI.is_file()
    if not cli_available:
        reasons.append("The sibling rea/scripts/rea.mjs CLI entry was not found.")
    missing_runtime = [
        name for name in REA_REQUIRED_RUNTIME if not (REA_ROOT / name).is_file()
    ]
    if missing_runtime:
        reasons.append("REA's compiled CLI runtime is missing; build REA in the sibling rea/ folder.")
    if not (REA_ROOT / "node_modules").is_dir():
        reasons.append("REA dependencies are not installed in the sibling rea/ folder.")

    loopback = False
    if server is not None:
        try:
            loopback = ipaddress.ip_address(server.server_address[0]).is_loopback
        except ValueError:
            loopback = False
    else:
        loopback = True
    if not loopback:
        reasons.append("REA analysis is disabled unless the QA server is bound to a loopback address.")
    available = not reasons
    return {
        "available": available,
        "analysis_enabled": available,
        "provider": "REA static analysis via CLI",
        "operation": "analyze_javascript_application",
        "node_version": node_version,
        "cli_available": cli_available,
        "compiled_runtime_available": not missing_runtime,
        "dependencies_available": (REA_ROOT / "node_modules").is_dir(),
        "loopback_only": loopback,
        "timeout_seconds": REA_ANALYSIS_TIMEOUT_SECONDS,
        "output_limit_bytes": REA_STDOUT_LIMIT,
        "reason": " ".join(reasons) if reasons else None,
    }


def validate_application_path(value) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Application path is required and must be a valid path string.")
    try:
        target = Path(value)
        if not target.is_absolute():
            raise ValueError("Application path must be absolute.")
        if not target.exists():
            raise ValueError("Application path does not exist on the QA server.")
        if target.is_dir():
            return value
        if target.is_file() and target.suffix.lower() == ".asar":
            return value
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Application path"):
            raise
        raise ValueError("Application path could not be accessed.") from exc
    raise ValueError("Select an application directory or a .asar file; other file types are not accepted.")


def _safe_cli_error(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("error", "message", "user_message", "userMessage"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:1200]
    for key in ("result", "details", "diagnostics"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            message = _safe_cli_error(nested)
            if message:
                return message
    return None


def _number_fields(value, fields):
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in fields if isinstance(value.get(key), int) and value[key] >= 0}


def rea_result_summary(payload, requested_path: str) -> dict:
    if not isinstance(payload, dict):
        raise RuntimeError("REA CLI returned an unexpected JSON result.")
    error = _safe_cli_error(payload)
    if error:
        raise RuntimeError(error)
    view = payload.get("summary") if isinstance(payload.get("summary"), dict) else payload
    if view.get("kind") != "summary" or not isinstance(view.get("summary"), dict):
        # Accept complete Evidence for compatible REA CLI versions, but only
        # retain the same bounded fields exposed by the summary view.
        evidence = payload.get("normalized_result")
        if not isinstance(evidence, dict):
            raise RuntimeError("REA CLI did not return an application summary.")
        analysis = evidence
        fields = {}
    else:
        analysis = view["summary"]
        fields = view.get("fields") if isinstance(view.get("fields"), dict) else {}
    statistics = _number_fields(
        analysis.get("statistics"),
        (
            "relevant_files", "nested_asar_containers", "text_bytes_read",
            "invalid_utf8_files", "parsed_javascript_files", "skipped_javascript_files",
            "visited_ast_nodes", "findings", "modules", "parse_failures",
        ),
    )
    summary_coverage = analysis.get("coverage")
    coverage = {}
    if isinstance(summary_coverage, dict):
        for key in ("static_sources", "application", "semantic"):
            if isinstance(summary_coverage.get(key), dict):
                coverage[key] = summary_coverage[key]
    electron = analysis.get("electron")
    if not isinstance(electron, dict):
        electron = analysis.get("summary")
    electron_summary = {}
    if isinstance(electron, dict):
        allowed_electron = (
            "browser_windows", "explicit_web_preferences", "preload_entrypoints",
            "context_bridge_apis", "exposed_api_members", "sender_validation_observations",
            "utility_processes", "resolved_utility_entrypoints", "native_addon_bindings",
            "resolved_native_addon_bindings",
        )
        electron_summary = _number_fields(electron, allowed_electron)
        if isinstance(electron.get("ipc"), dict):
            electron_summary["ipc"] = _number_fields(
                electron["ipc"],
                (
                    "operations", "literal_channels", "dynamic_channel_operations",
                    "renderer_transmissions", "renderer_listeners", "main_handlers",
                    "paired_renderer_transmissions", "ambiguous_renderer_transmissions",
                    "unpaired_literal_renderer_transmissions",
                ),
            )
    raw_limits = fields.get("limitations", analysis.get("limitations", []))
    limitations = [
        item.strip()[:500] for item in raw_limits
        if isinstance(item, str) and item.strip()
    ][:20] if isinstance(raw_limits, list) else []
    route_inventory = analysis.get("route_inventory")
    routes = None
    if isinstance(route_inventory, dict):
        routes = _number_fields(
            route_inventory.get("statistics"),
            ("sources_seen", "sources_retained", "route_patterns"),
        )
        if isinstance(route_inventory.get("coverage"), dict):
            routes["coverage"] = route_inventory["coverage"]
    artifact = fields.get("artifact") if isinstance(fields.get("artifact"), dict) else {}
    return {
        "provider": "rea-javascript-application",
        "provider_label": "REA static analysis via CLI",
        "operation": "analyze_javascript_application",
        "status": "completed",
        "artifact": {
            "path": requested_path,
            "format": analysis.get("format"),
            "sha256": analysis.get("root_artifact_sha256", artifact.get("sha256")),
        },
        "statistics": statistics,
        "coverage": coverage,
        "electron": electron_summary,
        "routes": routes,
        "limitations": limitations,
    }


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


def run_saved_analysis(server, analysis_id: int):
    try:
        with database(server.db_path) as db:
            row = db.execute(
                "SELECT input_path FROM application_analyses WHERE id=?",
                (analysis_id,),
            ).fetchone()
            if not row:
                return
            db.execute(
                "UPDATE application_analyses SET status='running', started_at=? WHERE id=?",
                (timestamp(), analysis_id),
            )
            input_path = row["input_path"]
        summary = server.rea_runner(input_path, server.rea_runtime())
        with database(server.db_path) as db:
            db.execute(
                """UPDATE application_analyses
                SET status='completed', summary_json=?, completed_at=?, error=''
                WHERE id=?""",
                (json.dumps(summary, ensure_ascii=False), timestamp(), analysis_id),
            )
    except Exception as exc:
        with database(server.db_path) as db:
            db.execute(
                """UPDATE application_analyses
                SET status='failed', error=?, completed_at=?
                WHERE id=?""",
                (str(exc).strip()[:1500] or "REA analysis failed.", timestamp(), analysis_id),
            )


def _drain_process_output(stream, output: bytearray, limit: int, exceeded: threading.Event):
    while True:
        chunk = stream.read(65536)
        if not chunk:
            return
        remaining = limit - len(output)
        if remaining > 0:
            output.extend(chunk[:remaining])
        if len(chunk) > remaining:
            exceeded.set()


def execute_rea_cli(input_path: str, runtime: dict) -> dict:
    if not runtime.get("analysis_enabled"):
        raise RuntimeError(runtime.get("reason") or "REA runtime is unavailable.")
    output_path = ROOT / f".rea-evidence-{uuid.uuid4().hex}.bundle"
    command = [
        shutil.which("node") or "node",
        str(REA_CLI),
        "analyze-javascript-application",
        input_path,
        "--detail", "summary",
        "--evidence_output", str(output_path),
        "--json",
    ]
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            command,
            cwd=str(REA_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise RuntimeError(f"REA CLI could not be started: {exc}") from exc

    stdout = bytearray()
    stderr = bytearray()
    exceeded = threading.Event()
    readers = [
        threading.Thread(target=_drain_process_output, args=(process.stdout, stdout, REA_STDOUT_LIMIT, exceeded), daemon=True),
        threading.Thread(target=_drain_process_output, args=(process.stderr, stderr, REA_STDERR_LIMIT, exceeded), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + REA_ANALYSIS_TIMEOUT_SECONDS
    failure = None
    try:
        while process.poll() is None:
            if time.monotonic() >= deadline:
                failure = f"REA analysis exceeded the {REA_ANALYSIS_TIMEOUT_SECONDS}-second timeout."
                process.kill()
                break
            if exceeded.is_set():
                failure = "REA analysis exceeded the CLI output size limit."
                process.kill()
                break
            try:
                if output_path.exists() and output_path.stat().st_size > REA_EVIDENCE_LIMIT:
                    failure = "REA evidence bundle exceeded the 1 GiB output limit."
                    process.kill()
                    break
            except OSError:
                pass
            time.sleep(0.1)
        process.wait(timeout=5)
        try:
            if output_path.exists() and output_path.stat().st_size > REA_EVIDENCE_LIMIT:
                failure = "REA evidence bundle exceeded the 1 GiB output limit."
        except OSError:
            pass
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        failure = failure or "REA process did not stop after cancellation."
    finally:
        for reader in readers:
            reader.join(timeout=5)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
        try:
            if output_path.is_dir():
                shutil.rmtree(output_path)
            else:
                output_path.unlink(missing_ok=True)
        except OSError:
            pass
    if failure:
        raise RuntimeError(failure)
    if exceeded.is_set():
        raise RuntimeError("REA analysis exceeded the CLI output size limit.")
    try:
        result = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        diagnostic = stderr.decode("utf-8", errors="replace").strip()
        detail = diagnostic[-1000:] if diagnostic else "No diagnostic was returned."
        raise RuntimeError(f"REA CLI returned invalid JSON. {detail}") from exc
    if process.returncode != 0:
        message = _safe_cli_error(result) or stderr.decode("utf-8", errors="replace").strip()[-1000:]
        raise RuntimeError(f"REA CLI exited with status {process.returncode}. {message}".strip())
    return rea_result_summary(result, input_path)


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
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, status=200, extra=None):
        self.send_bytes(
            json.dumps(data, ensure_ascii=True).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
            extra,
        )

    def is_admin(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            session_cookie = cookie.get("trace_admin_session")
        except CookieError:
            return False
        if not session_cookie:
            return False
        token = session_cookie.value
        with self.server.admin_session_lock:
            expiry = self.server.admin_sessions.get(token)
            if expiry is None:
                return False
            if expiry <= time.time():
                self.server.admin_sessions.pop(token, None)
                return False
        return True

    def auth_status(self):
        password = os.environ.get("QA_ADMIN_PASSWORD", "")
        return {
            "authenticated": self.is_admin(),
            "admin_configured": len(password.strip()) >= 12,
        }

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

    def issue_admin_session(self):
        token = secrets.token_urlsafe(32)
        with self.server.admin_session_lock:
            self.server.admin_sessions[token] = time.time() + 12 * 60 * 60
        return token

    def logout_admin(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            cookie = SimpleCookie()
        session_cookie = cookie.get("trace_admin_session")
        if session_cookie:
            with self.server.admin_session_lock:
                self.server.admin_sessions.pop(session_cookie.value, None)
        return {
            "Set-Cookie": (
                "trace_admin_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
            )
        }

    def body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid request size.")
        if size < 0 or size > MAX_BODY:
            raise ValueError("Request is too large (maximum 27 MB).")
        raw = self.rfile.read(size)
        content_type = self.headers.get("Content-Type", "")
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
        if not path.startswith("/api/"):
            self.static(path)
            return
        try:
            with database(self.server.db_path) as db:
                self.get_api(db, path)
        except (ValueError, sqlite3.Error) as exc:
            self.send_json({"error": str(exc)}, 400)
        except OSError:
            self.send_json({"error": "Storage error while reading the requested file."}, 500)
        except Exception:
            traceback.print_exc()
            self.send_json({"error": "Unexpected server error while handling the request. Check the server console."}, 500)

    def do_POST(self):
        analysis_id = None
        try:
            request_path = urlparse(self.path).path
            if self.ai_auth_required("POST", request_path) and not self.is_admin():
                self.send_json({"error": "Admin sign-in is required for AI settings and analysis."}, 401)
                return
            data = self.body()
            with database(self.server.db_path) as db:
                analysis_id = self.post_api(db, request_path, data)
            if analysis_id is not None:
                self.server.schedule_analysis(analysis_id)
        except sqlite3.IntegrityError as exc:
            if "ux_application_analyses_active" in str(exc):
                self.send_json({"error": "Another REA analysis is already queued or running."}, 409)
            else:
                self.send_json({"error": str(exc)}, 400)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        except sqlite3.Error as exc:
            self.send_json({"error": f"Database error: {exc}"}, 400)
        except OSError:
            self.send_json({"error": "Storage error while processing the request."}, 500)
        except Exception:
            traceback.print_exc()
            self.send_json({"error": "Unexpected server error while processing the request. Check the server console."}, 500)

    def do_PATCH(self):
        try:
            data = self.body()
            with database(self.server.db_path) as db:
                self.patch_api(db, urlparse(self.path).path, data)
        except (ValueError, sqlite3.IntegrityError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except sqlite3.Error as exc:
            self.send_json({"error": f"Database error: {exc}"}, 400)
        except Exception:
            traceback.print_exc()
            self.send_json({"error": "Unexpected server error while updating the record. Check the server console."}, 500)

    def do_DELETE(self):
        path = unquote(urlparse(self.path).path)
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
        if isinstance(exc, (ValueError, sqlite3.IntegrityError)):
            self.send_json({"error": str(exc)}, 400)
        elif isinstance(exc, sqlite3.Error):
            self.send_json({"error": f"Database error: {exc}"}, 400)
        elif isinstance(exc, OSError):
            self.send_json({"error": "Storage error while deleting the record."}, 500)
        else:
            traceback.print_exc()
            self.send_json({"error": "Unexpected server error while deleting the record. Check the server console."}, 500)

    def static(self, path):
        if path in ("/", "/index.html"):
            path = "/index.html"
        target = (ROOT / "static" / path.lstrip("/")).resolve()
        try:
            target.relative_to((ROOT / "static").resolve())
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
            self.send_json(self.auth_status())
            return
        if parts == ["api", "rea", "runtime"]:
            self.send_json(self.server.rea_runtime())
            return
        if parts == ["api", "ai", "settings"]:
            self.send_json(provider_settings(db, self.is_admin()))
            return
        if parts == ["api", "projects"]:
            rows = db.execute("SELECT * FROM projects ORDER BY id DESC").fetchall()
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
            if len(parts) == 5 and parts[3] == "report" and parts[4] == "csv":
                self.csv_report(db, pid)
                return
        self.send_json({"error": "Not found"}, 404)

    def post_api(self, db, path, data):
        parts = path.strip("/").split("/")
        now = timestamp()
        if parts == ["api", "auth", "login"]:
            try:
                peer_is_local = ipaddress.ip_address(self.client_address[0]).is_loopback
            except ValueError:
                peer_is_local = False
            expected = os.environ.get("QA_ADMIN_PASSWORD", "")
            if not peer_is_local:
                self.send_json({"error": "Admin sign-in is available only over a loopback connection."}, 403)
                return
            if len(expected.strip()) < 12:
                self.send_json({
                    "error": "Set QA_ADMIN_PASSWORD to at least 12 characters in qa-platform/.env, then restart the server."
                }, 503)
                return
            password = data.get("password")
            if not isinstance(password, str) or not hmac.compare_digest(password, expected):
                self.send_json({"error": "Admin password is incorrect."}, 401)
                return
            token = self.issue_admin_session()
            self.send_json(
                {"authenticated": True, "role": "Admin"},
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
        if parts == ["api", "ai", "settings"]:
            self.send_json(save_provider_settings(db, data))
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
            cur = db.execute(
                "INSERT INTO projects(project_key,name,product_type,description,version,environment,status,qa_owner,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (key, name, validate_text(data, "product_type", limit=80),
                 validate_text(data, "description"), validate_text(data, "version", limit=40),
                 validate_text(data, "environment", limit=80), status, owner, now, now),
            )
            ensure_project_storage(self.server.storage_root, cur.lastrowid)
            self.send_json(row_dict(db.execute("SELECT * FROM projects WHERE id=?", (cur.lastrowid,)).fetchone()), 201)
            return
        if len(parts) < 4 or parts[:2] != ["api", "projects"]:
            self.send_json({"error": "Not found"}, 404)
            return
        pid = self.parse_project_id(parts[2])
        if not project_exists(db, pid):
            self.send_json({"error": "Project not found"}, 404)
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
            input_path = validate_application_path(data.get("input_path"))
            runtime = self.server.rea_runtime()
            if not runtime.get("analysis_enabled"):
                self.send_json(
                    {"error": runtime.get("reason") or "REA runtime is unavailable."},
                    503,
                )
                return
            cursor = db.execute(
                """INSERT INTO application_analyses
                (project_id,input_path,status,created_at) VALUES(?,?,'queued',?)""",
                (pid, input_path, now),
            )
            analysis_id = cursor.lastrowid
            self.send_json(
                {
                    "id": analysis_id,
                    "project_id": pid,
                    "input_path": input_path,
                    "status": "queued",
                    "created_at": now,
                },
                202,
            )
            return analysis_id
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
        table = {"requirements": "requirements", "test-cases": "test_cases", "bugs": "bugs"}[kind]
        self.send_json(row_dict(db.execute(f"SELECT * FROM {table} WHERE id=?", (record,)).fetchone()))

    def delete_api(self, db, path):
        parts = path.strip("/").split("/")
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
        if len(parts) != 5 or parts[:2] != ["api", "projects"] or parts[3] not in ("requirements", "test-cases"):
            self.send_json({"error": "Not found"}, 404)
            return
        pid = self.parse_project_id(parts[2])
        record = self.parse_project_id(parts[4])
        if parts[3] == "test-cases":
            case = db.execute(
                "SELECT id FROM test_cases WHERE id=? AND project_id=?", (record, pid)
            ).fetchone()
            if not case:
                self.send_json({"error": "Test case not found"}, 404)
                return
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
    server.db_path = Path(db_path)
    server.storage_root = Path(db_path).resolve().parent / "storage"
    server.admin_sessions = {}
    server.admin_session_lock = threading.Lock()
    with database(server.db_path) as db:
        for row in db.execute("SELECT id FROM projects"):
            ensure_project_storage(server.storage_root, row["id"])
    server.rea_runtime = lambda: rea_runtime(server)
    server.rea_runner = execute_rea_cli

    def schedule_analysis(analysis_id):
        worker = threading.Thread(
            target=run_saved_analysis,
            args=(server, analysis_id),
            daemon=True,
            name=f"rea-analysis-{analysis_id}",
        )
        worker.start()

    server.schedule_analysis = schedule_analysis
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
