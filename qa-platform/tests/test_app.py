import base64
import io
import json
import stat
import struct
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

import app
from application_zip import MAX_FILE_UNCOMPRESSED, analyze_application_zip, source_code_excerpts
from ollama_integration import (
    generate_requirements as generate_ollama_requirements,
    generate_test_cases as generate_ollama_cases,
    validate_base_url,
    validate_requirement_coverage,
)
from requirement_documents import extract_requirement_document
from test_case_generator import generate_requirement_cases


class TestCaseGeneratorTests(unittest.TestCase):
    def test_generates_linkable_drafts_from_acceptance_rules_and_validation(self):
        requirement = {
            "id": 7,
            "requirement_key": "REQ-007",
            "title": "Account registration",
            "description": "Users can create an account.",
            "acceptance_criteria": (
                "A valid user can create an account.\nAn invalid email is rejected."
            ),
            "business_rules": "Account is locked after 5 failed attempts.",
            "validation_rules": "Email is required.",
        }
        generated = generate_requirement_cases(requirement)
        self.assertEqual(len(generated), 4)
        self.assertEqual(
            [case["test_type"] for case in generated],
            ["Functional", "Negative", "Boundary", "Validation"],
        )
        self.assertTrue(all(case["scenario"] != case["expected_result"] for case in generated))
        self.assertTrue(all(case["generation_key"] for case in generated))
        self.assertIn("REQ-007", generated[0]["description"])

    def test_falls_back_to_requirement_description_without_inventing_rules(self):
        generated = generate_requirement_cases({
            "id": 1,
            "requirement_key": "REQ-001",
            "title": "Export report",
            "description": "A QA lead can export a report.",
        })
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0]["test_type"], "Functional")
        self.assertIn("export a report", generated[0]["expected_result"])
        self.assertNotIn("permission", generated[0]["scenario"].lower())


class OllamaIntegrationTests(unittest.TestCase):
    def test_connection_is_local_and_generated_cases_are_validated(self):
        self.assertEqual(validate_base_url("http://127.0.0.1:11434/"), "http://127.0.0.1:11434")
        with self.assertRaisesRegex(ValueError, "local URL"):
            validate_base_url("https://example.com")
        output = {
            "test_cases": [{
                "requirement_id": 4,
                "scenario": "Verify valid login",
                "description": "Generated from login acceptance criteria.",
                "steps": "1. Submit valid credentials.",
                "expected_result": "The dashboard is displayed.",
                "test_type": "Functional",
            }]
        }
        requirement = {
            "id": 4,
            "requirement_key": "REQ-004",
            "title": "Login",
            "description": "",
            "acceptance_criteria": "Valid credentials open the dashboard.",
            "business_rules": "",
            "validation_rules": "",
        }
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }) as request:
            cases = generate_ollama_cases(
                "http://127.0.0.1:11434", "llama3.1:8b", [requirement],
                [{"method": "GET", "path": "/login", "summary": "", "operation_id": "", "tags": '["auth"]'}],
                {"routes": [], "components": [], "api_integrations": []},
            )
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["requirement_id"], 4)
        self.assertEqual(len(cases[0]["generation_key"]), 64)
        request_payload = request.call_args.args[2]
        self.assertIn('"tags": ["auth"]', request_payload["messages"][1]["content"])
        self.assertIn("must not repeat the scenario", request_payload["messages"][1]["content"])
        output["test_cases"][0]["expected_result"] = output["test_cases"][0]["scenario"]
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }):
            with self.assertRaisesRegex(ValueError, "scenario and expected result distinct"):
                generate_ollama_cases(
                    "http://127.0.0.1:11434", "llama3.1:8b", [requirement], [], {}
                )
        output["test_cases"][0]["expected_result"] = "The dashboard is displayed."
        output["test_cases"][0]["requirement_id"] = 999
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }):
            with self.assertRaisesRegex(ValueError, "unknown requirement"):
                generate_ollama_cases(
                    "http://127.0.0.1:11434", "llama3.1:8b", [requirement], [],
                    {"routes": [], "components": [], "api_integrations": []},
                )
        output["test_cases"][0]["requirement_id"] = 4
        output["test_cases"][0]["test_type"] = {}
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }):
            with self.assertRaisesRegex(ValueError, "unsupported test type"):
                generate_ollama_cases(
                    "http://127.0.0.1:11434", "llama3.1:8b", [requirement], [],
                    {"routes": [], "components": [], "api_integrations": []},
                )

    def test_ai_requirement_extraction_and_evidence_cited_coverage_are_validated(self):
        requirement_output = {"requirements": [{
            "title": "Account login",
            "description": "Users can sign in.",
            "acceptance_criteria": "Valid credentials open the account page.",
            "business_rules": "",
            "validation_rules": "",
            "dependencies": "",
            "priority": "High",
        }]}
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(requirement_output)}
        }) as request:
            requirements = generate_ollama_requirements(
                "http://127.0.0.1:11434", "llama3.1:8b", "Users need account login."
            )
        self.assertEqual(requirements[0]["title"], "Account login")
        self.assertIn("Users need account login.", request.call_args.args[2]["messages"][1]["content"])

        requirement = {
            "id": 5,
            "requirement_key": "REQ-005",
            "title": "Account login",
            "description": "Users can sign in.",
            "acceptance_criteria": "Valid credentials open the account page.",
            "business_rules": "",
            "validation_rules": "",
        }
        source_files = [{"path": "src/login.js", "content": "function login() { return '/account'; }"}]
        report = {"coverage": [{
            "requirement_id": 5,
            "status": "Covered",
            "rationale": "The source defines the login handler.",
            "evidence": [{"file": "src/login.js", "snippet": "function login()"}],
        }]}
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(report)}
        }):
            coverage = validate_requirement_coverage(
                "http://127.0.0.1:11434", "llama3.1:8b", [requirement], source_files
            )
        self.assertEqual(coverage[0]["status"], "Covered")

        report["coverage"][0]["evidence"][0]["snippet"] = "function register()"
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(report)}
        }):
            with self.assertRaisesRegex(ValueError, "text not found"):
                validate_requirement_coverage(
                    "http://127.0.0.1:11434", "llama3.1:8b", [requirement], source_files
                )


class RequirementDocumentTests(unittest.TestCase):
    @staticmethod
    def docx_bytes():
        document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>REQ-105: User login</w:t></w:r></w:p>
            <w:p><w:r><w:t>Users can log in with an email and password.</w:t></w:r></w:p>
            <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Acceptance Criteria</w:t></w:r></w:p>
            <w:p><w:r><w:t>Valid credentials open the dashboard.</w:t></w:r></w:p>
            <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Validation Rules</w:t></w:r></w:p>
            <w:p><w:r><w:t>Email is required.</w:t></w:r></w:p>
            <w:sectPr/>
          </w:body>
        </w:document>"""
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", document)
        return output.getvalue()

    def test_extracts_docx_headings_ids_acceptance_validation_and_source(self):
        text, records = extract_requirement_document(
            "requirements.docx", document_data=self.docx_bytes()
        )
        self.assertIn("Acceptance Criteria", text)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["requirement_key"], "REQ-105")
        self.assertEqual(records[0]["title"], "User login")
        self.assertIn("email and password", records[0]["description"])
        self.assertIn("dashboard", records[0]["acceptance_criteria"])
        self.assertIn("required", records[0]["validation_rules"])
        self.assertEqual(records[0]["source_section"], "User login")

    def test_extracts_markdown_structured_sections_and_priorities(self):
        _, records = extract_requirement_document(
            "requirements.md",
            content=(
                "# REQ-012: Password reset\n"
                "Users can request a reset link.\n"
                "Priority: High\n"
                "## Business Rules\n- Link expires after 30 minutes.\n"
                "## Acceptance Criteria\n- A reset email is sent.\n"
                "## Dependencies\n- Email service\n"
            ),
        )
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["requirement_key"], "REQ-012")
        self.assertEqual(record["priority"], "High")
        self.assertIn("30 minutes", record["business_rules"])
        self.assertIn("reset email", record["acceptance_criteria"])
        self.assertEqual(record["dependencies"], "Email service")

    def test_rejects_invalid_or_unsupported_documents(self):
        with self.assertRaisesRegex(ValueError, "valid document"):
            extract_requirement_document("broken.docx", document_data=b"not a document")
        with self.assertRaisesRegex(ValueError, "Only DOCX, TXT, and Markdown"):
            extract_requirement_document("requirements.pdf", content="ignored")
        with self.assertRaisesRegex(ValueError, "too large"):
            extract_requirement_document("requirements.txt", content="x" * 1_000_001)
        with self.assertRaisesRegex(ValueError, "too many requirements"):
            extract_requirement_document(
                "many.md",
                content="\n".join(f"# REQ-{number:03d}: Item {number}" for number in range(101)),
            )

    def test_initialize_migrates_existing_requirement_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "legacy.sqlite3"
            with app.database(db_path) as db:
                db.executescript(
                    """CREATE TABLE projects (
                        id INTEGER PRIMARY KEY, project_key TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                        version TEXT NOT NULL DEFAULT '', environment TEXT NOT NULL DEFAULT '',
                        qa_owner TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE requirements (
                        id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
                        requirement_key TEXT NOT NULL, title TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '', acceptance_criteria TEXT NOT NULL DEFAULT '',
                        priority TEXT NOT NULL DEFAULT 'Medium', source_document TEXT NOT NULL DEFAULT '',
                        review_status TEXT NOT NULL DEFAULT 'Needs review', created_at TEXT NOT NULL,
                        UNIQUE(project_id, requirement_key)
                    );
                    CREATE TABLE test_cases (
                        id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
                        requirement_id INTEGER, case_key TEXT NOT NULL, scenario TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '', steps TEXT NOT NULL DEFAULT '',
                        expected_result TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL, UNIQUE(project_id, case_key)
                    );"""
                )
            app.initialize(db_path)
            with app.database(db_path) as db:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(requirements)")}
                self.assertTrue({
                    "business_rules", "validation_rules", "dependencies", "source_section"
                }.issubset(columns))
                case_columns = {row["name"] for row in db.execute("PRAGMA table_info(test_cases)")}
                self.assertTrue({"test_type", "generation_key"}.issubset(case_columns))
                self.assertIsNotNone(db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='requirement_documents'"
                ).fetchone())
                self.assertIsNotNone(db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='ai_settings'"
                ).fetchone())


class ApplicationZipAnalysisTests(unittest.TestCase):
    @staticmethod
    def zip_bytes(entries):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries:
                archive.writestr(name, content)
        return output.getvalue()

    def analyze(self, archive, parent, name="application.zip"):
        destination = parent / "extracted"
        return analyze_application_zip(archive, name, destination), destination

    def test_static_inventory_detects_framework_routes_components_and_api_calls(self):
        archive = self.zip_bytes([
            ("client/package.json", b'{"dependencies":{"react":"18","express":"4"}}'),
            ("client/src/App.jsx", b"export function HomePage() { return fetch('/api/items'); }"),
            ("src/server.js", b"app.get('/health', handler);"),
            ("server/src/main/java/Main.java", b"class Main {}"),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            result, destination = self.analyze(archive, Path(temporary))
            analysis = result["analysis"]
            self.assertEqual(result["filename"], "application.zip")
            self.assertTrue((destination / "client" / "src" / "App.jsx").is_file())
            self.assertEqual(analysis["total_files"], 4)
            self.assertIn("React", analysis["frameworks"])
            self.assertIn("Express", analysis["frameworks"])
            self.assertIn("Java", analysis["frameworks"])
            self.assertTrue(any(item["route"] == "/health" for item in analysis["routes"]))
            self.assertTrue(any(item["name"] == "HomePage" for item in analysis["components"]))
            self.assertTrue(any("fetch" in item["kind"] for item in analysis["api_integrations"]))

    def test_static_analysis_replaces_non_utf8_source_bytes_instead_of_failing(self):
        package = b'{"name":"caf\xe9","dependencies":{"react":"18"}}'
        source = b"export function AccountPage() { return fetch('/api/account'); }\n// caf\xe9"
        archive = self.zip_bytes([
            ("package.json", package),
            ("src/AccountPage.tsx", source),
        ])
        with tempfile.TemporaryDirectory() as temporary:
            result, _ = self.analyze(archive, Path(temporary))
        self.assertIn("React", result["analysis"]["frameworks"])
        self.assertEqual(result["analysis"]["scanned_source_files"], 1)
        self.assertTrue(any(item["name"] == "AccountPage" for item in result["analysis"]["components"]))
        self.assertTrue(any(item["route"] == "/api/account" for item in result["analysis"]["routes"]))

    def test_rejects_traversal_absolute_and_windows_paths_without_writing_outside(self):
        malicious_paths = (
            "../escape.txt",
            "nested/../../escape.txt",
            "/rooted.txt",
            "C:\\outside.txt",
            "\\\\server\\share\\outside.txt",
            "nested\\..\\escape.txt",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, path in enumerate(malicious_paths):
                with self.subTest(path=path):
                    archive = self.zip_bytes([(path, b"untrusted")])
                    destination = root / f"extracted-{index}"
                    with self.assertRaises(ValueError):
                        analyze_application_zip(archive, "application.zip", destination)
                    self.assertFalse(destination.exists())
            self.assertFalse((root / "escape.txt").exists())
            self.assertFalse(list(root.glob(".*.tmp")))

    def test_rejects_symlink_entries(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            entry = zipfile.ZipInfo("link")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "target")
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "extracted"
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                analyze_application_zip(output.getvalue(), "application.zip", destination)
            self.assertFalse(destination.exists())
            self.assertFalse(list(Path(temporary).glob(".*.tmp")))

    def test_rejects_archive_bombs_and_cleans_staging_after_corrupt_member(self):
        oversized = self.zip_bytes([("large.txt", b"\0" * (MAX_FILE_UNCOMPRESSED + 1))])
        high_ratio = self.zip_bytes([("repetitive.txt", b"\0" * 1_000_000)])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "larger than 20 MB"):
                analyze_application_zip(oversized, "application.zip", root / "oversized")
            with self.assertRaisesRegex(ValueError, "excessive compression ratio"):
                analyze_application_zip(high_ratio, "application.zip", root / "high-ratio")

            stored = io.BytesIO()
            with zipfile.ZipFile(stored, "w", zipfile.ZIP_STORED) as archive:
                archive.writestr("src/app.js", b"export const App = () => null;")
            valid = stored.getvalue()
            corrupt = bytearray(valid)
            marker = corrupt.find(b"export const App")
            self.assertGreaterEqual(marker, 0)
            corrupt[marker] ^= 1
            with self.assertRaisesRegex(ValueError, "corrupt or unreadable"):
                analyze_application_zip(bytes(corrupt), "application.zip", root / "corrupt")
            self.assertFalse((root / "corrupt").exists())
            self.assertFalse(list(root.glob(".*.tmp")))

    def test_rejects_unsafe_upload_names_and_non_zip_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "plain file name"):
                analyze_application_zip(self.zip_bytes([]), "..\\outside.zip", root / "bad-name")
            with self.assertRaisesRegex(ValueError, "valid ZIP"):
                analyze_application_zip(b"not a zip", "application.zip", root / "not-zip")


class PlatformApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        cls.db_path = Path(cls.temp.name) / "tests.sqlite3"
        cls.server = app.create_server("127.0.0.1", 0, cls.db_path)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def request(self, path, method="GET", payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request) as response:
                content = response.read()
                if "application/json" in response.headers.get("Content-Type", ""):
                    return response.status, json.loads(content)
                return response.status, content
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def raw_request(self, path, body, content_type, method="POST"):
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers={"Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def setUp(self):
        with app.database(self.db_path) as db:
            db.execute("DELETE FROM projects")
            db.execute(
                """UPDATE ai_settings SET ollama_base_url='http://127.0.0.1:11434',
                ollama_model='',updated_at=? WHERE id=1""",
                (app.timestamp(),),
            )

    def test_ollama_settings_are_local_and_persisted(self):
        status, settings = self.request("/api/ai/settings")
        self.assertEqual(status, 200)
        self.assertEqual(settings["ollama_base_url"], "http://127.0.0.1:11434")
        status, error = self.request("/api/ai/settings", "POST", {
            "ollama_base_url": "http://192.168.1.10:11434",
            "ollama_model": "model:tag",
        })
        self.assertEqual(status, 400)
        self.assertIn("local URL", error["error"])
        status, saved = self.request("/api/ai/settings", "POST", {
            "ollama_base_url": "http://127.0.0.1:11434/",
            "ollama_model": "llama3.1:8b",
        })
        self.assertEqual(status, 200)
        self.assertEqual(saved["ollama_base_url"], "http://127.0.0.1:11434")
        self.assertEqual(saved["ollama_model"], "llama3.1:8b")

    def test_project_isolation_and_required_validation(self):
        status, one = self.request("/api/projects", "POST", {
            "name": "Portal", "product_type": "Web application", "project_key": "port-1", "qa_owner": "QA"
        })
        self.assertEqual(status, 201)
        self.assertEqual(one["product_type"], "Web application")
        self.assertEqual(one["status"], "Active")
        _, two = self.request("/api/projects", "POST", {"name": "Store", "project_key": "STORE", "qa_owner": "QA"})
        one_dir = self.server.storage_root / "projects" / str(one["id"])
        two_dir = self.server.storage_root / "projects" / str(two["id"])
        self.assertTrue(one_dir.is_dir())
        self.assertTrue((one_dir / "requirements").is_dir())
        self.assertTrue((one_dir / "applications").is_dir())
        self.assertTrue((one_dir / "exports").is_dir())
        self.assertNotEqual(one_dir, two_dir)
        _, req = self.request(f"/api/projects/{one['id']}/requirements", "POST", {"title": "Login"})
        self.assertEqual(req["requirement_key"], "REQ-001")
        status, error = self.request(f"/api/projects/{two['id']}/test-cases", "POST", {
            "scenario": "Foreign link", "requirement_id": req["id"]
        })
        self.assertEqual(status, 400)
        self.assertIn("does not belong", error["error"])
        status, error = self.request("/api/projects", "POST", {"project_key": "NO-NAME", "qa_owner": "QA"})
        self.assertEqual(status, 400)
        self.assertIn("Name is required", error["error"])
        _, isolated = self.request(f"/api/projects/{two['id']}/requirements")
        self.assertEqual(isolated, [])

    def test_project_update_validates_and_updates_fields_without_creating_another_project(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Edit me", "project_key": "EDIT-ME", "qa_owner": "QA",
            "status": "Inactive",
        })
        self.assertEqual(project["status"], "Inactive")
        with patch("app.timestamp", return_value="2099-01-01T00:00:00+00:00"):
            status, updated = self.request(f"/api/projects/{project['id']}", "PATCH", {
                "name": "Updated project",
                "product_type": "Web application",
                "project_key": "EDITED",
                "description": "Project details updated.",
                "version": "2.0",
                "environment": "Staging",
                "qa_owner": "QA lead",
                "status": "Active",
            })
        self.assertEqual(status, 200)
        self.assertEqual(updated["name"], "Updated project")
        self.assertEqual(updated["project_key"], "EDITED")
        self.assertEqual(updated["product_type"], "Web application")
        self.assertEqual(updated["status"], "Active")
        self.assertEqual(updated["environment"], "Staging")
        self.assertEqual(updated["updated_at"], "2099-01-01T00:00:00+00:00")
        _, projects = self.request("/api/projects")
        self.assertEqual(len(projects), 1)

        status, error = self.request(f"/api/projects/{project['id']}", "PATCH", {
            "name": " ", "project_key": "EDITED",
        })
        self.assertEqual(status, 400)
        self.assertIn("Name is required", error["error"])

        _, other = self.request("/api/projects", "POST", {
            "name": "Other project", "project_key": "OTHER", "qa_owner": "QA",
        })
        status, error = self.request(f"/api/projects/{project['id']}", "PATCH", {
            "project_key": other["project_key"],
        })
        self.assertEqual(status, 409)
        self.assertIn("already in use", error["error"])

        status, error = self.request(f"/api/projects/{project['id']}", "PATCH", {
            "status": "Archived",
        })
        self.assertEqual(status, 400)
        self.assertIn("Invalid status", error["error"])

    def test_project_files_are_listed_and_downloaded_only_from_project_folder(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Files", "project_key": "FILES", "qa_owner": "QA"
        })
        file_path = self.server.storage_root / "projects" / str(project["id"]) / "exports" / "report.csv"
        file_path.write_bytes(b"project report")

        status, result = self.request(f"/api/projects/{project['id']}/files")
        self.assertEqual(status, 200)
        self.assertEqual(result["folders"], ["applications", "exports", "requirements"])
        self.assertIn({"path": "exports/report.csv", "size": 14}, result["files"])
        status, content = self.request(
            f"/api/projects/{project['id']}/files/download?path=exports%2Freport.csv"
        )
        self.assertEqual(status, 200)
        self.assertEqual(content, b"project report")
        status, error = self.request(
            f"/api/projects/{project['id']}/files/download?path=..%2F..%2Foutside.txt"
        )
        self.assertEqual(status, 400)
        self.assertIn("Invalid project file path", error["error"])

    def test_test_case_edit_execution_status_and_delete(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Case edits", "project_key": "CASEEDIT", "qa_owner": "QA"
        })
        _, requirement = self.request(
            f"/api/projects/{project['id']}/requirements", "POST", {"title": "Requirement"}
        )
        status, case = self.request(f"/api/projects/{project['id']}/test-cases", "POST", {
            "requirement_id": requirement["id"], "scenario": "Check sign in",
            "test_type": "Functional", "description": "Verify sign in",
            "steps": "Enter valid credentials", "expected_result": "Dashboard appears",
        })
        self.assertEqual(status, 201)
        _, edited = self.request(
            f"/api/projects/{project['id']}/test-cases/{case['id']}", "PATCH", {
                "scenario": "Check successful sign in", "description": "Updated description",
                "steps": "Enter user credentials\nSubmit form",
                "expected_result": "Dashboard appears", "test_type": "Negative",
                "requirement_id": requirement["id"],
            }
        )
        self.assertEqual(edited["scenario"], "Check successful sign in")
        self.assertEqual(edited["description"], "Updated description")
        self.assertEqual(edited["steps"], "Enter user credentials\nSubmit form")
        self.assertEqual(edited["test_type"], "Negative")
        _, execution = self.request(f"/api/projects/{project['id']}/executions/{case['id']}", "POST", {
            "status": "Pass", "actual_result": "Dashboard appeared", "executed_by": "QA",
        })
        self.assertEqual(execution["status"], "Pass")
        _, cases = self.request(f"/api/projects/{project['id']}/test-cases")
        self.assertEqual(cases[0]["status"], "Pass")
        self.assertEqual(cases[0]["actual_result"], "Dashboard appeared")
        status, deleted = self.request(
            f"/api/projects/{project['id']}/test-cases/{case['id']}", "DELETE"
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(self.request(f"/api/projects/{project['id']}/test-cases")[1], [])

    def test_import_execution_dashboard_bug_and_csv(self):
        _, project = self.request("/api/projects", "POST", {"name": "QA Project", "project_key": "QA", "qa_owner": "Alex"})
        status, imported = self.request(f"/api/projects/{project['id']}/requirements/import", "POST", {
            "filename": "spec.md",
            "content": "# Sign in\nUsers can sign in.\n\n## Password reset\nA reset link can be requested.",
        })
        self.assertEqual(status, 201)
        self.assertEqual(imported["count"], 2)
        self.assertEqual(imported["created"][0]["source_document"], "spec.md")
        _, reviewed = self.request(
            f"/api/projects/{project['id']}/requirements/{imported['created'][0]['id']}",
            "PATCH", {"review_status": "Reviewed", "priority": "High"},
        )
        self.assertEqual(reviewed["review_status"], "Reviewed")
        _, case = self.request(f"/api/projects/{project['id']}/test-cases", "POST", {
            "scenario": "Valid sign in", "requirement_id": imported["created"][0]["id"],
            "steps": "Enter credentials", "expected_result": "Dashboard opens",
        })
        _, edited_case = self.request(
            f"/api/projects/{project['id']}/test-cases/{case['id']}",
            "PATCH", {"description": "Positive login path"},
        )
        self.assertEqual(edited_case["description"], "Positive login path")
        _, execution = self.request(f"/api/projects/{project['id']}/executions/{case['id']}", "POST", {
            "status": "Fail", "actual_result": "Error shown", "comments": "Reproduced",
        })
        self.assertEqual(execution["status"], "Fail")
        _, dashboard = self.request(f"/api/projects/{project['id']}/dashboard")
        self.assertEqual(dashboard["requirements"], 2)
        self.assertEqual(dashboard["covered_requirements"], 1)
        self.assertEqual(dashboard["statuses"]["Fail"], 1)
        _, bug = self.request(f"/api/projects/{project['id']}/bugs", "POST", {
            "title": "Sign in fails", "test_case_id": case["id"], "severity": "High",
        })
        self.assertEqual(bug["bug_key"], "BUG-001")
        _, updated = self.request(f"/api/projects/{project['id']}/bugs/{bug['id']}", "PATCH", {"status": "Resolved"})
        self.assertEqual(updated["status"], "Resolved")
        status, csv_bytes = self.request(f"/api/projects/{project['id']}/report/csv")
        self.assertEqual(status, 200)
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"Valid sign in", csv_bytes)
        self.assertIn(b"Not Covered", csv_bytes)
        self.assertIn(b"Sign in fails", csv_bytes)
        saved_report = (
            self.server.storage_root / "projects" / str(project["id"]) /
            "exports" / "project-report.csv"
        )
        self.assertEqual(saved_report.read_bytes(), csv_bytes)

    def test_project_delete_removes_records_and_archives_project_files(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Archive project", "project_key": "ARCHIVE", "qa_owner": "QA"
        })
        _, requirement = self.request(
            f"/api/projects/{project['id']}/requirements", "POST",
            {"title": "Keep files on delete"},
        )
        project_dir = self.server.storage_root / "projects" / str(project["id"])
        retained_file = project_dir / "requirements" / "retained.txt"
        retained_file.write_text("Retained project file", encoding="utf-8")

        status, deleted = self.request(f"/api/projects/{project['id']}", "DELETE")

        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])
        self.assertTrue(deleted["files_archived"])
        self.assertFalse(project_dir.exists())
        archived_dirs = list(
            (self.server.storage_root / "archived-projects").glob(f"{project['id']}-ARCHIVE-*")
        )
        self.assertEqual(len(archived_dirs), 1)
        _, archive_response = self.request("/api/archive")
        self.assertEqual(len(archive_response["archives"]), 1)
        self.assertEqual(archive_response["archives"][0]["archive_id"], archived_dirs[0].name)
        self.assertTrue(archive_response["archives"][0]["archived_at"])
        self.assertEqual(
            archive_response["archives"][0]["archived_at"],
            app.datetime.fromtimestamp(
                archived_dirs[0].stat().st_mtime, app.timezone.utc
            ).isoformat(timespec="seconds"),
        )
        self.assertEqual(
            (archived_dirs[0] / "requirements" / "retained.txt").read_text(encoding="utf-8"),
            "Retained project file",
        )
        self.assertEqual(self.request("/api/projects")[1], [])
        self.assertEqual(
            self.request(f"/api/projects/{project['id']}/requirements"),
            (404, {"error": "Project not found"}),
        )
        with app.database(self.db_path) as db:
            self.assertIsNone(db.execute(
                "SELECT id FROM requirements WHERE id=?", (requirement["id"],)
            ).fetchone())

    def test_requirement_import_and_manual_creation_skip_duplicate_content(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Duplicate requirements", "project_key": "DEDUP", "qa_owner": "QA"
        })
        import_path = f"/api/projects/{project['id']}/requirements/import"
        source = "# Sign in\nUsers can sign in.\n\n# Password reset\nUsers can request a reset link."
        status, first = self.request(import_path, "POST", {"filename": "spec.md", "content": source})
        self.assertEqual(status, 201)
        self.assertEqual(first["count"], 2)
        self.assertEqual(first["skipped_duplicates"], 0)

        repeated = "# SIGN IN\n  USERS   CAN SIGN IN. \n\n# PASSWORD RESET\nUsers can request a reset link."
        status, second = self.request(import_path, "POST", {"filename": "repeat.md", "content": repeated})
        self.assertEqual(status, 201)
        self.assertEqual(second["count"], 0)
        self.assertEqual(second["skipped_duplicates"], 2)
        self.assertEqual(second["total_extracted"], 2)

        status, duplicate = self.request(
            f"/api/projects/{project['id']}/requirements", "POST",
            {"title": " sign in ", "description": "USERS can   sign in."},
        )
        self.assertEqual(status, 409)
        self.assertIn("Duplicate requirement", duplicate["error"])
        status, distinct = self.request(
            f"/api/projects/{project['id']}/requirements", "POST",
            {"title": "Sign in", "description": "Users can sign in with a passkey."},
        )
        self.assertEqual(status, 201)
        self.assertEqual(distinct["requirement_key"], "REQ-003")

    def test_ai_requirement_import_and_requirement_only_case_generation(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "AI requirement flow", "project_key": "AIREQ", "qa_owner": "QA"
        })
        generated_requirement = {
            "title": "Reset password",
            "description": "A user can request a password reset.",
            "acceptance_criteria": "A reset link is sent to the user's email.",
            "business_rules": "",
            "validation_rules": "Email address is required.",
            "dependencies": "",
            "priority": "High",
        }
        with patch("app.generate_ollama_requirements", return_value=[generated_requirement]):
            status, imported = self.request(
                f"/api/projects/{project['id']}/requirements/import", "POST",
                {
                    "filename": "raw.txt",
                    "content": "Users need a way to reset their password.",
                    "generator": "ollama",
                },
            )
        self.assertEqual(status, 201)
        self.assertEqual(imported["generator"], "ollama")
        self.assertEqual(imported["count"], 1)
        requirement = imported["created"][0]
        self.assertEqual(requirement["priority"], "High")
        status, generated = self.request(
            f"/api/projects/{project['id']}/test-cases/generate", "POST",
            {"generator": "deterministic"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(generated["count"], 2)
        self.assertTrue(all(item["requirement_id"] == requirement["id"] for item in generated["created"]))

    def test_requirement_delete_is_project_scoped_and_unlinks_cases(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Delete requirement", "project_key": "DELREQ", "qa_owner": "QA"
        })
        _, other = self.request("/api/projects", "POST", {
            "name": "Other project", "project_key": "DELREQOTHER", "qa_owner": "QA"
        })
        _, requirement = self.request(
            f"/api/projects/{project['id']}/requirements", "POST", {"title": "Keep test case"}
        )
        _, case = self.request(
            f"/api/projects/{project['id']}/test-cases", "POST",
            {"scenario": "Case remains after requirement deletion", "requirement_id": requirement["id"]},
        )
        status, error = self.request(
            f"/api/projects/{other['id']}/requirements/{requirement['id']}", "DELETE"
        )
        self.assertEqual(status, 404)
        self.assertIn("Requirement not found", error["error"])

        status, deleted = self.request(
            f"/api/projects/{project['id']}/requirements/{requirement['id']}", "DELETE"
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["unlinked_test_cases"], 1)
        _, requirements = self.request(f"/api/projects/{project['id']}/requirements")
        self.assertEqual(requirements, [])
        _, cases = self.request(f"/api/projects/{project['id']}/test-cases")
        remaining_case = next(item for item in cases if item["id"] == case["id"])
        self.assertIsNone(remaining_case["requirement_id"])

    def test_only_text_import_and_execution_status_validation(self):
        _, project = self.request("/api/projects", "POST", {"name": "Only text", "project_key": "TEXT", "qa_owner": "QA"})
        status, error = self.request(f"/api/projects/{project['id']}/requirements/import", "POST", {
            "filename": "application.zip", "content": "not actually a zip"
        })
        self.assertEqual(status, 400)
        self.assertIn("Only DOCX, TXT, and Markdown", error["error"])
        _, case = self.request(f"/api/projects/{project['id']}/test-cases", "POST", {"scenario": "Case"})
        status, error = self.request(f"/api/projects/{project['id']}/executions/{case['id']}", "POST", {
            "status": "Maybe"
        })
        self.assertEqual(status, 400)
        self.assertIn("Invalid status", error["error"])

    def test_openapi_import_inventory_and_project_isolation(self):
        _, project = self.request("/api/projects", "POST", {"name": "API project", "project_key": "API", "qa_owner": "QA"})
        _, other = self.request("/api/projects", "POST", {"name": "Other project", "project_key": "OTHER", "qa_owner": "QA"})
        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Accounts", "version": "v1"},
            "paths": {
                "/accounts/{accountId}": {
                    "get": {"summary": "Read account", "operationId": "getAccount", "tags": ["Accounts"]},
                    "delete": {"operationId": "deleteAccount"},
                },
                "/accounts": {"post": {"summary": "Create account"}},
            },
        }
        status, imported = self.request(
            f"/api/projects/{project['id']}/api-docs/import",
            "POST", {"filename": "accounts.json", "content": json.dumps(spec)},
        )
        self.assertEqual(status, 201)
        self.assertEqual(imported["endpoint_count"], 3)
        _, docs = self.request(f"/api/projects/{project['id']}/api-docs")
        self.assertEqual(len(docs), 1)
        self.assertEqual({item["method"] for item in docs[0]["endpoints"]}, {"GET", "DELETE", "POST"})
        get_endpoint = next(item for item in docs[0]["endpoints"] if item["method"] == "GET")
        self.assertEqual(get_endpoint["tags"], ["Accounts"])
        _, isolated = self.request(f"/api/projects/{other['id']}/api-docs")
        self.assertEqual(isolated, [])
        status, error = self.request(
            f"/api/projects/{project['id']}/api-docs/import",
            "POST", {"filename": "https://example.test/spec.json", "content": json.dumps(spec)},
        )
        self.assertEqual(status, 400)
        self.assertIn("local OpenAPI", error["error"])
        status, error = self.request(
            f"/api/projects/{project['id']}/api-docs/import",
            "POST", {"filename": "bad.json", "content": '{"swagger":"3.0","info":{},"paths":{}}'},
        )
        self.assertEqual(status, 400)
        self.assertIn("declare OpenAPI", error["error"])

    def test_requirement_docx_import_stores_original_and_structured_fields_by_project(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Requirements", "project_key": "REQDOC", "qa_owner": "QA"
        })
        _, other = self.request("/api/projects", "POST", {
            "name": "Other requirements", "project_key": "REQOTHER", "qa_owner": "QA"
        })
        docx = RequirementDocumentTests.docx_bytes()
        status, imported = self.request(
            f"/api/projects/{project['id']}/requirements/import",
            "POST",
            {
                "filename": "login.docx",
                "document_data": base64.b64encode(docx).decode("ascii"),
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(imported["count"], 1)
        requirement = imported["created"][0]
        self.assertEqual(requirement["requirement_key"], "REQ-105")
        self.assertIn("dashboard", requirement["acceptance_criteria"])
        self.assertIn("required", requirement["validation_rules"])
        self.assertEqual(requirement["source_document"], "login.docx")
        self.assertEqual(imported["document"]["file_size_bytes"], len(docx))
        stored_path = Path(self.server.storage_root) / "projects" / str(project["id"]) / "requirements"
        self.assertEqual(len(list(stored_path.iterdir())), 1)

        _, documents = self.request(
            f"/api/projects/{project['id']}/requirements/documents"
        )
        self.assertEqual(documents[0]["filename"], "login.docx")
        status, original = self.request(
            f"/api/projects/{project['id']}/requirements/documents/{documents[0]['id']}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(original, docx)
        status, _ = self.request(
            f"/api/projects/{other['id']}/requirements/documents/{documents[0]['id']}"
        )
        self.assertEqual(status, 404)
        _, isolated_documents = self.request(
            f"/api/projects/{other['id']}/requirements/documents"
        )
        self.assertEqual(isolated_documents, [])
        _, edited = self.request(
            f"/api/projects/{project['id']}/requirements/{requirement['id']}",
            "PATCH",
            {"business_rules": "Accounts lock after repeated failures.", "review_status": "Reviewed"},
        )
        self.assertEqual(edited["business_rules"], "Accounts lock after repeated failures.")
        self.assertEqual(edited["review_status"], "Reviewed")

        status, error = self.request(
            f"/api/projects/{project['id']}/requirements/import",
            "POST",
            {
                "filename": "duplicate.docx",
                "document_data": base64.b64encode(docx).decode("ascii"),
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("already exists", error["error"])
        self.assertEqual(len(list(stored_path.iterdir())), 1)

    def test_application_zip_upload_persists_analysis_and_is_project_scoped(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Application upload", "project_key": "APPZIP", "qa_owner": "QA"
        })
        _, other = self.request("/api/projects", "POST", {
            "name": "Other application", "project_key": "OTHERAPP", "qa_owner": "QA"
        })
        archive = ApplicationZipAnalysisTests.zip_bytes([
            ("package.json", b'{"dependencies":{"react":"18"}}'),
            ("src/App.jsx", b"export function Dashboard() { return fetch('/api/status'); }"),
        ])
        payload = {"filename": "client.zip", "zip_data": base64.b64encode(archive).decode("ascii")}
        status, uploaded = self.request(
            f"/api/projects/{project['id']}/applications/import", "POST", payload
        )
        self.assertEqual(status, 201)
        self.assertNotIn("storage_path", uploaded)
        self.assertEqual(uploaded["analysis"]["total_files"], 2)
        self.assertIn("React", uploaded["analysis"]["frameworks"])
        self.assertEqual(uploaded["analysis"]["routes"], [])
        self.assertEqual(uploaded["inventory"][1]["path"], "src/App.jsx")

        _, uploads = self.request(f"/api/projects/{project['id']}/applications")
        self.assertEqual([item["id"] for item in uploads], [uploaded["id"]])
        _, isolated = self.request(f"/api/projects/{other['id']}/applications")
        self.assertEqual(isolated, [])
        status, _ = self.request(
            f"/api/projects/{other['id']}/applications/{uploaded['id']}"
        )
        self.assertEqual(status, 404)
        status, error = self.request(
            f"/api/projects/{project['id']}/applications/import",
            "POST",
            {
                "filename": "malicious.zip",
                "zip_data": base64.b64encode(
                    ApplicationZipAnalysisTests.zip_bytes([("../outside.txt", b"no")])
                ).decode("ascii"),
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("unsafe path", error["error"])

    def test_uploaded_source_validates_and_persists_requirement_coverage(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Coverage review", "project_key": "COVERAGE", "qa_owner": "QA"
        })
        _, requirement = self.request(
            f"/api/projects/{project['id']}/requirements", "POST",
            {"title": "Account sign in", "description": "Users can sign in."},
        )
        archive = ApplicationZipAnalysisTests.zip_bytes([
            ("src/login.js", b"export function login() { return '/account'; }"),
        ])
        _, uploaded = self.request(
            f"/api/projects/{project['id']}/applications/import", "POST",
            {"filename": "final.zip", "zip_data": base64.b64encode(archive).decode("ascii")},
        )
        report = [{
            "requirement_id": requirement["id"],
            "status": "Partial",
            "rationale": "A login function is present; behavior needs runtime verification.",
            "evidence": [{
                "file": "src/login.js",
                "snippet": "export function login()",
            }],
        }]
        with patch("app.validate_ollama_requirement_coverage", return_value=report) as validate:
            status, result = self.request(
                f"/api/projects/{project['id']}/applications/{uploaded['id']}/validate-requirements",
                "POST", {},
            )
        self.assertEqual(status, 200)
        self.assertEqual(result["coverage"], report)
        self.assertIn("not prove runtime behavior", result["note"])
        sent_excerpts = validate.call_args.args[3]
        self.assertEqual(sent_excerpts[0]["path"], "src/login.js")
        self.assertIn("function login", sent_excerpts[0]["content"])
        _, persisted = self.request(f"/api/projects/{project['id']}/applications/{uploaded['id']}")
        self.assertEqual(persisted["coverage"], report)
        self.assertTrue(persisted["coverage_checked_at"])
        self.assertFalse(persisted["coverage_is_stale"])
        self.request(
            f"/api/projects/{project['id']}/requirements/{requirement['id']}",
            "PATCH", {"title": "Account authentication"},
        )
        _, stale = self.request(f"/api/projects/{project['id']}/applications/{uploaded['id']}")
        self.assertTrue(stale["coverage_is_stale"])

    def test_application_zip_raw_upload_accepts_over_five_megabytes(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Large ZIP", "project_key": "LARGEZIP", "qa_owner": "QA"
        })
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("large.txt", b"x" * (6 * 1024 * 1024))
        archive_bytes = output.getvalue()
        self.assertGreater(len(archive_bytes), 5 * 1024 * 1024)
        status, uploaded = self.raw_request(
            f"/api/projects/{project['id']}/applications/import?filename=large.zip",
            archive_bytes,
            "application/zip",
        )
        self.assertEqual(status, 201)
        self.assertEqual(uploaded["archive_size_bytes"], len(archive_bytes))
        self.assertEqual(uploaded["analysis"]["total_files"], 1)

    def test_application_zip_raw_bytes_with_misreported_content_type_and_legacy_source_encoding(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Encoded source", "project_key": "ENCODED", "qa_owner": "QA"
        })
        archive = ApplicationZipAnalysisTests.zip_bytes([
            ("package.json", b'{"name":"caf\xe9","dependencies":{"react":"18"}}'),
            ("src/App.tsx", b"export function App() { return null; } // caf\xe9"),
        ])
        status, uploaded = self.raw_request(
            f"/api/projects/{project['id']}/applications/import?filename=react.zip",
            archive,
            "application/json",
        )
        self.assertEqual(status, 201)
        self.assertIn("React", uploaded["analysis"]["frameworks"])
        self.assertEqual(uploaded["analysis"]["scanned_source_files"], 1)

    def test_unexpected_zip_analysis_error_returns_json_and_keeps_http_server_alive(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Upload failure", "project_key": "UPFAIL", "qa_owner": "QA"
        })
        archive = ApplicationZipAnalysisTests.zip_bytes([("src/app.js", b"const app = true;")])
        with patch("app.analyze_application_zip", side_effect=RuntimeError("unexpected analyzer failure")):
            status, error = self.raw_request(
                f"/api/projects/{project['id']}/applications/import?filename=app.zip",
                archive,
                "application/zip",
            )
        self.assertEqual(status, 500)
        self.assertIn("Unexpected server error", error["error"])
        status, projects = self.request("/api/projects")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["id"] == project["id"] for item in projects))

    def test_generate_test_cases_from_project_requirements_is_idempotent_and_scoped(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Generated cases", "project_key": "GENCASE", "qa_owner": "QA"
        })
        _, other = self.request("/api/projects", "POST", {
            "name": "Other project", "project_key": "OTHGEN", "qa_owner": "QA"
        })
        _, requirement = self.request(
            f"/api/projects/{project['id']}/requirements",
            "POST",
            {
                "title": "Account registration",
                "description": "A user can create an account.",
                "acceptance_criteria": (
                    "A valid user can create an account.\nAn invalid email is rejected."
                ),
                "business_rules": "Account is locked after 5 failed attempts.",
                "validation_rules": "Email is required.",
            },
        )
        status, error = self.request(
            f"/api/projects/{project['id']}/test-cases/generate", "POST", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(error["count"], 4)
        self.assertTrue(all(case["requirement_id"] == requirement["id"] for case in error["created"]))
        api_spec = {
            "openapi": "3.0.3",
            "info": {"title": "Accounts API", "version": "1.0"},
            "paths": {
                "/accounts": {
                    "post": {
                        "summary": "Create account",
                        "operationId": "createAccount",
                    }
                }
            },
        }
        status, _ = self.request(
            f"/api/projects/{project['id']}/api-docs/import",
            "POST",
            {"filename": "accounts.json", "content": json.dumps(api_spec)},
        )
        self.assertEqual(status, 201)
        application_zip = ApplicationZipAnalysisTests.zip_bytes([
            ("src/registration.js", b"app.post('/accounts', createAccount);"),
            ("src/App.jsx", b"export function AccountForm() { return null; }"),
        ])
        status, _ = self.request(
            f"/api/projects/{project['id']}/applications/import",
            "POST",
            {
                "filename": "app.zip",
                "zip_data": base64.b64encode(application_zip).decode("ascii"),
            },
        )
        self.assertEqual(status, 201)
        status, result = self.request(
            f"/api/projects/{project['id']}/test-cases/generate", "POST", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["skipped_existing"], 4)
        self.assertFalse(result["truncated"])
        self.assertIn("requirements only", result["message"])
        all_created = error["created"] + result["created"]
        linked_cases = [case for case in all_created if case["requirement_id"]]
        unlinked_source_cases = [case for case in all_created if not case["requirement_id"]]
        self.assertTrue(linked_cases)
        self.assertTrue(all(case["requirement_id"] == requirement["id"] for case in linked_cases))
        self.assertEqual(len(unlinked_source_cases), 0)
        self.assertEqual(
            {case["test_type"] for case in all_created},
            {"Functional", "Negative", "Boundary", "Validation"},
        )
        self.assertTrue(all(case["generation_key"] for case in result["created"]))

        _, repeated = self.request(
            f"/api/projects/{project['id']}/test-cases/generate", "POST", {}
        )
        self.assertEqual(repeated["count"], 0)
        self.assertEqual(repeated["skipped_existing"], 4)
        _, cases = self.request(f"/api/projects/{project['id']}/test-cases")
        self.assertEqual(len(cases), 4)
        _, settings = self.request("/api/ai/settings", "POST", {
            "ollama_base_url": "http://127.0.0.1:11434",
            "ollama_model": "llama3.1:8b",
        })
        self.assertEqual(settings["ollama_model"], "llama3.1:8b")
        with patch(
            "app.generate_ollama_test_cases",
            side_effect=ValueError("Could not connect to Ollama at http://127.0.0.1:11434."),
        ):
            status, result = self.request(
                f"/api/projects/{project['id']}/test-cases/generate",
                "POST",
                {"generator": "ollama"},
            )
        self.assertEqual(status, 400)
        self.assertIn("Could not connect to Ollama", result["error"])
        with patch("app.generate_ollama_test_cases", return_value=[{
            "requirement_id": requirement["id"],
            "scenario": "AI draft case",
            "description": "Generated from the requirement.",
            "steps": "1. Follow the login workflow.",
            "expected_result": "The documented result is shown.",
            "test_type": "Functional",
            "generation_key": "ollama-test-key",
        }]):
            status, result = self.request(
                f"/api/projects/{project['id']}/test-cases/generate",
                "POST",
                {"generator": "ollama"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(result["generator"], "ollama")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["created"][0]["generation_key"], "ollama-test-key")
        _, isolated = self.request(f"/api/projects/{other['id']}/test-cases")
        self.assertEqual(isolated, [])
        status, error = self.request(
            f"/api/projects/{other['id']}/test-cases/generate",
            "POST",
            {"requirement_ids": [requirement["id"]]},
        )
        self.assertEqual(status, 400)
        self.assertIn("do not belong", error["error"])

    def test_execution_evidence_storage_access_and_report_reference(self):
        _, project = self.request("/api/projects", "POST", {"name": "Evidence", "project_key": "EVID", "qa_owner": "QA"})
        _, foreign = self.request("/api/projects", "POST", {"name": "Foreign", "project_key": "FOREIGN", "qa_owner": "QA"})
        _, case = self.request(f"/api/projects/{project['id']}/test-cases", "POST", {"scenario": "Screenshot case"})
        _, execution = self.request(f"/api/projects/{project['id']}/executions/{case['id']}", "POST", {"status": "Fail"})
        image = self.png_image()
        status, evidence = self.request(
            f"/api/projects/{project['id']}/executions/{execution['id']}/evidence",
            "POST", {"filename": "result.png", "media_type": "image/png",
                     "image_data": base64.b64encode(image).decode("ascii")},
        )
        self.assertEqual(status, 201)
        self.assertEqual(evidence["size"], len(image))
        _, listed = self.request(f"/api/projects/{project['id']}/executions/{execution['id']}/evidence")
        self.assertEqual([item["id"] for item in listed], [evidence["id"]])
        status, downloaded = self.request(f"/api/projects/{project['id']}/evidence/{evidence['id']}/download")
        self.assertEqual(status, 200)
        self.assertEqual(downloaded, image)
        status, error = self.request(f"/api/projects/{foreign['id']}/evidence/{evidence['id']}")
        self.assertEqual(status, 404)
        status, error = self.request(
            f"/api/projects/{project['id']}/executions/{execution['id']}/evidence",
            "POST", {"filename": "spoof.png", "media_type": "image/png",
                     "image_data": base64.b64encode(b"not an image").decode("ascii")},
        )
        self.assertEqual(status, 400)
        self.assertIn("does not match", error["error"])
        _, cases = self.request(f"/api/projects/{project['id']}/test-cases")
        self.assertEqual(cases[0]["evidence_count"], 1)
        _, history = self.request(f"/api/projects/{project['id']}/test-cases/{case['id']}/executions")
        self.assertEqual(history[0]["evidence"][0]["id"], evidence["id"])
        _, report = self.request(f"/api/projects/{project['id']}/report/csv")
        self.assertIn(b"Evidence references", report)
        self.assertIn(b"Evidence ID", report)
        self.assertIn(f"{evidence['id']}:result.png".encode(), report)

    def test_rea_application_analysis_summary_history_and_project_isolation(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Analysis project", "project_key": "ANALYSIS", "qa_owner": "QA"
        })
        _, other = self.request("/api/projects", "POST", {
            "name": "Other analysis project", "project_key": "ANALYSIS2", "qa_owner": "QA"
        })
        source = Path(self.temp.name) / "source-app"
        source.mkdir()
        self.server.rea_runtime = lambda: {
            "analysis_enabled": True, "available": True,
            "provider": "REA static analysis via CLI",
        }

        def fake_runner(input_path, runtime):
            return app.rea_result_summary({
                "summary": {
                    "kind": "summary",
                    "fields": {
                        "parent_operation": "analyze_javascript_application",
                        "artifact": {"sha256": "a" * 64},
                        "limitations": ["Some source files were outside analysis scope."],
                    },
                    "summary": {
                        "format": "directory",
                        "root_artifact_sha256": "a" * 64,
                        "statistics": {
                            "modules": 3, "relevant_files": 5, "text_bytes_read": 1200,
                            "visited_ast_nodes": 25,
                        },
                        "electron": {"browser_windows": 1, "ipc": {"operations": 2}},
                        "coverage": {"application": {"status": "partial"}},
                    },
                },
                "evidence_bundle": {"path": "temporary", "bytes": 100},
            }, input_path)

        self.server.rea_runner = fake_runner
        status, queued = self.request(
            f"/api/projects/{project['id']}/analyses", "POST", {"input_path": str(source)}
        )
        self.assertEqual(status, 202)
        deadline = time.time() + 3
        while time.time() < deadline:
            _, result = self.request(f"/api/projects/{project['id']}/analyses/{queued['id']}")
            if result["status"] in ("completed", "failed"):
                break
            time.sleep(0.02)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["input_path"], str(source))
        self.assertEqual(result["summary"]["provider_label"], "REA static analysis via CLI")
        self.assertEqual(result["summary"]["statistics"]["modules"], 3)
        self.assertEqual(result["summary"]["electron"]["ipc"]["operations"], 2)
        self.assertEqual(len(result["summary"]["limitations"]), 1)
        _, history = self.request(f"/api/projects/{project['id']}/analyses")
        self.assertEqual([item["id"] for item in history], [queued["id"]])
        _, isolated = self.request(f"/api/projects/{other['id']}/analyses")
        self.assertEqual(isolated, [])
        status, error = self.request(f"/api/projects/{other['id']}/analyses/{queued['id']}")
        self.assertEqual(status, 404)

    def test_rea_analysis_rejects_invalid_inputs_and_reports_unavailable_runtime(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Analysis validation", "project_key": "ANALYSIS-VALIDATION", "qa_owner": "QA"
        })
        source = Path(self.temp.name) / "local-app"
        source.mkdir()
        archive = Path(self.temp.name) / "not-an-app.zip"
        archive.write_bytes(b"not accepted")
        self.server.rea_runtime = lambda: {
            "analysis_enabled": True, "available": True, "provider": "REA static analysis via CLI"
        }
        for path in ("relative/path", str(archive), str(Path(self.temp.name) / "missing")):
            status, error = self.request(
                f"/api/projects/{project['id']}/analyses", "POST", {"input_path": path}
            )
            self.assertEqual(status, 400)
            self.assertTrue(error["error"])
        self.server.rea_runtime = lambda: {
            "analysis_enabled": False, "available": False,
            "reason": "REA's compiled CLI runtime is missing.",
        }
        status, error = self.request(
            f"/api/projects/{project['id']}/analyses", "POST", {"input_path": str(source)}
        )
        self.assertEqual(status, 503)
        self.assertIn("compiled CLI runtime is missing", error["error"])
        _, history = self.request(f"/api/projects/{project['id']}/analyses")
        self.assertEqual(history, [])

    def test_rea_analysis_process_failure_is_saved_in_history(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Analysis failure", "project_key": "ANALYSIS-FAIL", "qa_owner": "QA"
        })
        source = Path(self.temp.name) / "failure-app"
        source.mkdir()
        self.server.rea_runtime = lambda: {"analysis_enabled": True, "available": True}

        def fail_runner(input_path, runtime):
            raise RuntimeError("REA CLI exited with status 1. Runtime build is missing.")

        self.server.rea_runner = fail_runner
        status, queued = self.request(
            f"/api/projects/{project['id']}/analyses", "POST", {"input_path": str(source)}
        )
        self.assertEqual(status, 202)
        deadline = time.time() + 3
        while time.time() < deadline:
            _, result = self.request(f"/api/projects/{project['id']}/analyses/{queued['id']}")
            if result["status"] == "failed":
                break
            time.sleep(0.02)
        self.assertEqual(result["status"], "failed")
        self.assertIn("Runtime build is missing", result["error"])

    @staticmethod
    def png_image():
        def chunk(kind, content):
            body = kind + content
            return struct.pack(">I", len(content)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)
        header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        pixels = zlib.compress(b"\x00\xff\x00\x00")
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


if __name__ == "__main__":
    unittest.main()
