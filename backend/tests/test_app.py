import base64
import copy
import errno
import io
import json
import os
import secrets
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
import subprocess
from pathlib import Path
from unittest.mock import patch

import app
from application_zip import MAX_FILE_UNCOMPRESSED, analyze_application_zip, source_code_excerpts
from ollama_integration import (
    analyze_brd as analyze_ai_brd,
    brd_analysis_markdown,
    fallback_brd_analysis,
    generate_brd_analysis as generate_ai_brd_analysis,
    generate_requirements as generate_ai_requirements,
    generate_test_cases as generate_ai_cases,
    validate_base_url,
    validate_brd_analysis,
    validate_requirement_coverage,
)
from requirement_documents import extract_requirement_document
from test_case_generator import generate_requirement_cases
from ollama_integration import generate_ad_hoc_test_cases
from ai_provider import active_provider_config, provider_settings, save_provider_settings
import ai_provider


class ServerStartupTests(unittest.TestCase):
    def test_health_endpoint_is_public_side_effect_free_and_quiet(self):
        with tempfile.TemporaryDirectory() as temporary:
            server = app.create_server(
                host="127.0.0.1",
                port=0,
                db_path=Path(temporary) / "qa.sqlite3",
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                with patch("builtins.print") as print_mock, patch(
                    "app.database"
                ) as database_mock:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{server.server_port}/health"
                    ) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(
                            json.loads(response.read()),
                            {"status": "ok"},
                        )
                print_mock.assert_not_called()
                database_mock.assert_not_called()
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_public_and_auth_routes_serve_the_single_page_application(self):
        with tempfile.TemporaryDirectory() as temporary:
            server = app.create_server(
                host="127.0.0.1",
                port=0,
                db_path=Path(temporary) / "qa.sqlite3",
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                for path in ("/", "/about", "/about/", "/privacy", "/terms", "/login", "/signup", "/workspace"):
                    with self.subTest(path=path):
                        with urllib.request.urlopen(f"{base_url}{path}") as response:
                            self.assertEqual(response.status, 200)
                            self.assertIn(b'id="publicRoot"', response.read())
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_default_port_conflict_uses_an_available_port(self):
        fallback_server = object()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QA_PORT", None)
            with patch.object(
                app,
                "create_server",
                side_effect=[OSError(errno.EADDRINUSE, "address already in use"), fallback_server],
            ) as create_server:
                self.assertIs(app.create_startup_server("127.0.0.1", 8001), fallback_server)

        self.assertEqual(
            create_server.call_args_list,
            [
                unittest.mock.call("127.0.0.1", 8001),
                unittest.mock.call("127.0.0.1", 0),
            ],
        )

    def test_explicit_port_conflict_is_not_silently_changed(self):
        conflict = OSError(errno.EADDRINUSE, "address already in use")
        with patch.dict(os.environ, {"QA_PORT": "8123"}):
            with patch.object(app, "create_server", side_effect=conflict) as create_server:
                with self.assertRaises(OSError):
                    app.create_startup_server("127.0.0.1", 8123)

        create_server.assert_called_once_with("127.0.0.1", 8123)

    def test_separately_served_frontend_cors_preflight_and_auth_status(self):
        origin = "http://127.0.0.1:3000"
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"QA_CORS_ORIGINS": origin}
        ):
            server = app.create_server(
                host="127.0.0.1",
                port=0,
                db_path=Path(temporary) / "qa.sqlite3",
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                preflight = urllib.request.Request(
                    f"{base_url}/api/auth/status",
                    headers={
                        "Origin": origin,
                        "Access-Control-Request-Method": "GET",
                        "Access-Control-Request-Headers": "content-type",
                    },
                    method="OPTIONS",
                )
                with urllib.request.urlopen(preflight) as response:
                    self.assertEqual(response.status, 204)
                    self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
                    self.assertEqual(response.headers["Access-Control-Allow-Credentials"], "true")
                    self.assertIn("GET", response.headers["Access-Control-Allow-Methods"])

                request = urllib.request.Request(
                    f"{base_url}/api/auth/status", headers={"Origin": origin}
                )
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()


def brd_analysis_fixture(**overrides):
    analysis = {
        "summary": "A user signs in to access the account.",
        "brd_summary": {
            "brd_name": "Account BRD",
            "product": "Account Portal",
            "platform": "Web",
            "primary_users": ["User"],
            "use_case_count": 1,
        },
        "use_cases": [{
            "id": "UC-1",
            "name": "Sign in",
            "actor": "User",
            "goal": "Access the account.",
            "steps": [{"step": "Step 1", "summary": "User submits valid credentials."}],
        }],
        "functional_groups": [{
            "name": "Authentication",
            "requirements": [{
                "id": "FR-1.1",
                "title": "Sign in",
                "statement": "The system shall allow users to sign in.",
                "source_use_case": "UC-1",
                "source_step": "Step 1",
                "priority": "Not specified",
            }],
        }],
        "non_functional_requirements": [],
        "scope": {"in_scope": ["User sign in"], "out_of_scope": []},
        "scope_constraints": [],
        "traceability": [{
            "use_case": "UC-1",
            "scenario_step": "Step 1",
            "requirement_ids": ["FR-1.1"],
        }],
        "requirement_gaps": [],
    }
    analysis.update(copy.deepcopy(overrides))
    return analysis


class AiProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.db_path = Path(self.temp.name) / "ai-provider.sqlite3"
        self.env = patch.dict(os.environ, {
            "QA_AI_ENCRYPTION_KEY": "a" * 64,
            "QA_ADMIN_PASSWORD": "test-admin-password-123",
        })
        self.env.start()
        app.initialize(self.db_path)
        self.db = app.connect(self.db_path)

    def tearDown(self):
        self.db.close()
        self.env.stop()
        self.temp.cleanup()

    def test_provider_settings_encrypt_credentials_and_never_return_them(self):
        settings = provider_settings(self.db)
        self.assertEqual(settings["provider"], "google")
        self.assertEqual(settings["model"], "gemini-2.5-flash")
        self.assertFalse(settings["configured"])
        self.assertEqual(
            [provider["id"] for provider in settings["providers"]],
            ["local", "ollama", "google", "openai-compatible"],
        )
        configured = save_provider_settings(self.db, {
            "provider": "google",
            "model": "gemini-2.5-flash",
            "api_key": "test-secret-key",
        })
        self.assertTrue(configured["configured"])
        self.assertNotIn("test-secret-key", json.dumps(configured))
        stored = self.db.execute(
            "SELECT api_key_ciphertext FROM ai_provider_configs WHERE provider='google'"
        ).fetchone()["api_key_ciphertext"]
        self.assertNotIn(b"test-secret-key", stored)
        self.assertEqual(active_provider_config(self.db)["api_key"], "test-secret-key")

    def test_provider_validation_and_encryption_key_rotation_errors(self):
        with self.assertRaisesRegex(ValueError, "Choose Local deterministic"):
            save_provider_settings(self.db, {"provider": "unknown"})
        with self.assertRaisesRegex(ValueError, "Provider URL must use HTTPS"):
            save_provider_settings(self.db, {
                "provider": "openai-compatible",
                "model": "some-model",
                "base_url": "http://models.example/v1",
                "api_key": "key",
            })
        save_provider_settings(self.db, {
            "provider": "google",
            "model": "gemini-2.5-flash",
            "api_key": "test-secret-key",
        })
        with patch.dict(os.environ, {"QA_AI_ENCRYPTION_KEY": "b" * 64}):
            with self.assertRaisesRegex(ValueError, "Could not decrypt stored provider credentials"):
                active_provider_config(self.db)

    def test_text_generation_calls_server_side_adapter_and_returns_text(self):
        completed = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=json.dumps({"text": '{"requirements":[]}'}),
            stderr="",
        )
        configuration = {
            "provider": "google",
            "model": "gemini-2.5-flash",
            "base_url": "",
            "api_key": "secret-test-key",
        }
        with (
            patch("ai_provider.shutil.which", return_value="node"),
            patch("ai_provider.subprocess.run", return_value=completed) as run,
        ):
            result = ai_provider.generate_text(
                configuration, "system text", "BRD text", 0.1
            )
        self.assertEqual(result, '{"requirements":[]}')
        request = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(request["provider"], "google")
        self.assertEqual(request["model"], "gemini-2.5-flash")
        self.assertEqual(request["prompt"], "BRD text")
        self.assertEqual(request["apiKey"], "secret-test-key")

    def test_text_generation_surfaces_provider_errors(self):
        completed = subprocess.CompletedProcess(
            args=["node"],
            returncode=1,
            stdout=json.dumps({"error": "Google Gemini rejected the API key."}),
            stderr="",
        )
        with (
            patch("ai_provider.shutil.which", return_value="node"),
            patch("ai_provider.subprocess.run", return_value=completed),
            self.assertRaisesRegex(ValueError, "rejected the API key"),
        ):
            ai_provider.generate_text(
                {
                    "provider": "google",
                    "model": "gemini-2.5-flash",
                    "base_url": "",
                    "api_key": "secret-test-key",
                },
                "system text",
                "BRD text",
                0.1,
            )


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

    def test_generates_non_functional_case_types_from_module_category(self):
        generated = generate_requirement_cases({
            "id": 2,
            "requirement_key": "REQ-002",
            "title": "Login latency",
            "description": "The login page must load within 2 seconds.",
            "module_type": "Non-Functional",
            "module_category": "Performance",
        })
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0]["test_type"], "Performance")
        self.assertIn("performance", generated[0]["scenario"].lower())
        self.assertIn("performance", generated[0]["expected_result"].lower())


class AdHocTestGenerationTests(unittest.TestCase):
    source_text = "A user can submit a valid registration form."

    def response(self, cases=None, questions=None):
        return {
            "test_cases": cases or [],
            "analysis": {
                "explicit_facts": ["A user can submit a valid registration form."],
                "assumptions": [],
                "coverage_gaps": ["Invalid input behavior is unspecified."],
                "duplicates": [],
                "unclear_or_conflicting": [],
                "clarification_questions": questions or [],
                "additional_recommendations": [],
                "category_assessment": [
                    {"category": category, "applicable": category == "Functional", "reason": "Supported by the supplied workflow."}
                    for category in ("Functional", "Non-Functional", "Sanity", "Smoke")
                ],
            },
        }

    def case(self, **overrides):
        item = {
            "title": "Submit a valid registration",
            "scenario": "A user submits a valid registration form",
            "test_category": "Functional",
            "test_type": "Functional",
            "preconditions": "",
            "test_data": "",
            "steps": "1. Enter valid registration details.\n2. Submit the form.",
            "expected_result": "The form is submitted.",
            "priority": "Not specified",
            "classification": "Positive",
            "assumptions": "",
            "source_reference": self.source_text,
        }
        return {**item, **overrides}

    def generate(self, response):
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(response)}
        }):
            return generate_ad_hoc_test_cases(
                "google", "gemini-2.5-flash", self.source_text,
                ["Functional", "Non-Functional", "Sanity", "Smoke"],
            )

    def test_validates_traceable_cases_and_preserves_analysis(self):
        result = self.generate(self.response([self.case()]))
        self.assertEqual(len(result["test_cases"]), 1)
        self.assertEqual(result["test_cases"][0]["classification"], "Positive")
        self.assertEqual(result["test_cases"][0]["source_reference"], self.source_text)
        self.assertEqual(
            result["analysis"]["category_assessment"][1]["category"],
            "Non-Functional",
        )
        self.assertTrue(result["test_cases"][0]["generation_key"])

    def test_allows_no_cases_when_clarification_is_needed(self):
        result = self.generate(self.response(questions=["What should happen for an invalid email?"]))
        self.assertEqual(result["test_cases"], [])
        self.assertTrue(result["analysis"]["clarification_questions"])

    def test_flags_unverified_source_references_and_rejects_unexplained_empty_results(self):
        result = self.generate(self.response([
            self.case(source_reference="A made-up requirement")
        ]))
        self.assertEqual(result["test_cases"][0]["source_reference"], "")
        self.assertIn(
            "could not be verified",
            result["analysis"]["traceability_warnings"][0],
        )
        self.assertIn(
            "Source traceability needs review",
            result["test_cases"][0]["assumptions"],
        )
        with self.assertRaisesRegex(ValueError, "did not provide clarification"):
            self.generate(self.response())


class AIIntegrationTests(unittest.TestCase):
    def test_combined_brd_analysis_uses_one_model_request(self):
        response = {
            "requirements": [{
                "title": "User login",
                "description": "Users can sign in.",
                "acceptance_criteria": "Valid credentials open the dashboard.",
                "business_rules": "",
                "validation_rules": "",
                "dependencies": "",
                "priority": "High",
                "module_type": "Functional",
                "module_category": "Functional",
            }],
            **brd_analysis_fixture(),
        }
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(response)}
        }) as request:
            requirements, analysis = generate_ai_brd_analysis(
                "http://127.0.0.1:11434", "llama3.1:8b", "Users can sign in."
            )
        self.assertEqual(request.call_count, 1)
        self.assertEqual(requirements[0]["title"], "User login")
        self.assertEqual(analysis["functional_groups"][0]["requirements"][0]["id"], "FR-1.1")

    def test_brd_analysis_uses_the_provider_abstraction(self):
        response = {
            "requirements": [{
                "title": "User login",
                "description": "Users can sign in.",
                "acceptance_criteria": "Valid credentials open the dashboard.",
                "business_rules": "",
                "validation_rules": "",
                "dependencies": "",
                "priority": "High",
                "module_type": "Functional",
                "module_category": "Functional",
            }],
            **brd_analysis_fixture(),
        }
        with patch(
            "ollama_integration.generate_text",
            return_value=json.dumps(response),
        ) as generate:
            requirements, analysis = generate_ai_brd_analysis(
                "google", "gemini-4-argon", "Users can sign in."
            )
        self.assertEqual(requirements[0]["title"], "User login")
        self.assertEqual(analysis["functional_groups"][0]["requirements"][0]["id"], "FR-1.1")
        self.assertEqual(generate.call_args.args[:2], ("google", "gemini-4-argon"))
        self.assertEqual(generate.call_args.kwargs["response_format"], "json")

    def test_brd_analysis_is_structured_validated_and_exportable(self):
        output = brd_analysis_fixture(
            summary="Kanban boards support ticket triage and organization.",
            functional_groups=[{
                "name": "Board Configuration & Settings",
                "requirements": [{
                    "id": "FR-1.1",
                    "title": "Configure columns",
                    "statement": "The system shall let administrators configure board columns.",
                    "source_use_case": "UC-1",
                    "source_step": "Step 1",
                    "priority": "Not specified",
                }],
            }],
            non_functional_requirements=[{
                "id": "NFR-1",
                "category": "Platform & Integration",
                "statement": "The system shall protect OAuth credentials.",
                "source_step": "Security requirements",
            }],
            scope={"in_scope": ["Kanban board workspace"], "out_of_scope": []},
            scope_constraints=[{
                "id": "SC-1",
                "basis": "Explicit",
                "statement": "A board supports no more than 10 columns.",
                "source": "Use Case 1",
            }],
        )
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }) as request:
            analysis = analyze_ai_brd(
                "http://127.0.0.1:11434",
                "llama3.1:8b",
                "The board supports no more than 10 columns.",
            )
        self.assertEqual(analysis["functional_groups"][0]["requirements"][0]["id"], "FR-1.1")
        self.assertEqual(
            analysis["non_functional_requirements"][0]["category"],
            "Platform & Integration",
        )
        prompt = request.call_args.args[2]["messages"][1]["content"]
        self.assertIn("no more than 10 columns", prompt)
        self.assertIn("Do not convert headings", prompt)
        self.assertIn("Functional Requirements (FR)", prompt)
        self.assertIn("Non-Functional Requirements (NFR)", prompt)
        self.assertIn("Keep the Business Rules / Constraints section", prompt)
        self.assertIn("traceability", prompt)
        self.assertIn("requirement_gaps", prompt)
        markdown = brd_analysis_markdown(analysis)
        self.assertIn("## 6. Requirement Traceability Matrix", markdown)
        self.assertIn("## 7. Requirement Gaps", markdown)
        self.assertIn("| SC-1 | A board supports no more than 10 columns. | Use Case 1 |", markdown)
        self.assertIn("## 8. QA Readiness Notes", markdown)

        output["scope_constraints"][0]["id"] = "FR-1.1"
        with self.assertRaisesRegex(ValueError, "invalid or duplicate SC identifier"):
            validate_brd_analysis(output)

    def test_brd_analysis_rejects_heading_only_reports_and_local_fallback_marks_gap(self):
        with self.assertRaisesRegex(ValueError, "must contain a BRD summary"):
            validate_brd_analysis({
                "summary": "A BRD report",
                "functional_groups": [{
                    "name": "Business Requirements Document",
                    "requirements": [{
                        "id": "FR-1.1",
                        "statement": "Business Requirements Document (BRD)",
                    }],
                }],
                "non_functional_requirements": [],
                "scope": {"in_scope": [], "out_of_scope": []},
                "scope_constraints": [],
            })

        fallback = fallback_brd_analysis(
            "# Goals\nUsers need a dashboard.\n# Use Case\nAgent opens dashboard.",
        )
        self.assertEqual(fallback["functional_groups"], [])
        self.assertEqual(fallback["use_cases"], [])
        self.assertEqual(fallback["requirement_gaps"][0]["impact"], "High")
        self.assertIn("## 7. Requirement Gaps", brd_analysis_markdown(fallback))

    def test_feature_table_requirements_do_not_require_a_use_case(self):
        output = brd_analysis_fixture(
            use_cases=[],
            functional_groups=[{
                "name": "Template selection",
                "requirements": [{
                    "id": "FR-1.1",
                    "title": "Preview templates",
                    "statement": "Administrators can preview templates.",
                    "source_use_case": "",
                    "source_step": "",
                    "source_reference": "Feature matrix row A01",
                    "priority": "Not specified",
                }],
            }],
            traceability=[],
            requirement_gaps=[],
        )
        analysis = validate_brd_analysis(output)
        self.assertEqual(
            analysis["functional_groups"][0]["requirements"][0]["source_reference"],
            "Feature matrix row A01",
        )

    def test_generated_requirement_records_include_review_metadata_and_flow(self):
        response = {
            "requirements": [{
                "title": "Build and save a board",
                "description": "Allow an agent to configure a board and save it.",
                "requirement_type": "Use Case",
                "acceptance_criteria": "Source-defined: Given a valid configuration, when it is saved, then it is available to the agent.",
                "business_rules": "",
                "validation_rules": "",
                "dependencies": "Inferred — board configuration; requires confirmation.",
                "priority": "Not specified",
                "module_type": "Functional",
                "module_category": "Functional",
                "source_section": "Use Case 1: Build and save a board",
                "scope_classification": "In Scope",
                "ai_confidence_status": "Supported",
                "actor": "Agent",
                "preconditions": "",
                "trigger": "Agent chooses to create a board.",
                "main_flow": "Configure and save board settings.",
                "alternative_flow": "",
                "exception_flow": "",
                "related_requirements": "",
            }],
            **brd_analysis_fixture(
                use_cases=[{
                    "id": "UC-1",
                    "name": "Build and save a board",
                    "actor": "Agent",
                    "goal": "Save a configured board.",
                    "source_reference": "Use Case 1",
                    "acceptance_criteria": [
                        "A valid board can be saved.",
                        "Invalid board settings show a source-defined validation error.",
                    ],
                    "steps": [
                        {"step": "Step 1", "summary": "Agent configures board settings."},
                        {"step": "Step 2", "summary": "Agent saves the board."},
                    ],
                }],
                scope={
                    "in_scope": ["Board configuration"],
                    "out_of_scope": [],
                    "requires_clarification": [],
                },
                application_flow=[{
                    "step": "Create board",
                    "actor": "Agent",
                    "action": "Configure and save a board.",
                    "system_response": "The board is saved.",
                    "source_status": "Source-defined",
                }],
            ),
        }
        response["functional_groups"][0]["requirements"].append({
            "id": "FR-1.2",
            "title": "Save configured board",
            "statement": "The system shall save the board configuration.",
            "source_use_case": "UC-1",
            "source_step": "Step 2",
            "source_reference": "Use Case 1, Step 2",
            "priority": "Not specified",
        })
        response["non_functional_requirements"] = [{
            "id": "NFR-1",
            "category": "Performance",
            "statement": "The system shall save the board within the source-stated response threshold.",
            "source_use_case": "UC-1",
            "source_step": "Step 2",
            "source_reference": "Use Case 1, Step 2",
        }]
        response["traceability"] = [{
            "use_case": "UC-1",
            "scenario_step": "Step 1",
            "requirement_ids": ["FR-1.1"],
        }, {
            "use_case": "UC-1",
            "scenario_step": "Step 2",
            "requirement_ids": ["FR-1.2", "NFR-1"],
        }]
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(response)}
        }) as request:
            requirements, analysis = generate_ai_brd_analysis(
                "http://127.0.0.1:11434",
                "llama3.1:8b",
                "Use Case 1: Build and save a board. "
                "Step 1: Agent configures the board. "
                "Step 2: Agent saves the board within 2 seconds. "
                "Acceptance criteria: A valid board can be saved.",
            )
        prompt = request.call_args.args[2]["messages"][1]["content"]
        self.assertIn("one record for each distinct use case", prompt)
        self.assertIn("do not make a separate canonical record", prompt.lower())
        self.assertEqual(requirements[0]["requirement_type"], "Use Case")
        self.assertEqual(requirements[0]["priority"], "Not specified")
        self.assertEqual(len(requirements), 1)
        self.assertEqual(len(analysis["functional_groups"][0]["requirements"]), 2)
        self.assertEqual(analysis["non_functional_requirements"][0]["source_use_case"], "UC-1")
        self.assertEqual(len(analysis["use_cases"][0]["acceptance_criteria"]), 2)
        markdown = brd_analysis_markdown(analysis)
        self.assertIn(
            "| ID | Functional Requirement | Source Use Case | Scenario / Acceptance Criterion |",
            markdown,
        )
        self.assertIn(
            "| ID | Non-Functional Requirement | Source Use Case | Scenario / Acceptance Criterion |",
            markdown,
        )
        self.assertIn("Acceptance Criterion 1: A valid board can be saved.", markdown)
        self.assertEqual(analysis["application_flow"][0]["source_status"], "Source-defined")
        self.assertIn("## Requirement Records", markdown)
        self.assertIn("AI confidence / review: Supported", markdown)
        self.assertIn("## Application Flow", markdown)
        self.assertIn("| NFR-1 |", markdown)

    def test_brd_analysis_fills_missing_traceability_rows_for_use_case_steps(self):
        output = brd_analysis_fixture(
            use_cases=[{
                "id": "UC-1",
                "name": "Sign in",
                "actor": "User",
                "goal": "Access the account.",
                "steps": [
                    {"step": "Step 1", "summary": "User submits credentials."},
                    {"step": "Step 2", "summary": "System validates credentials."},
                ],
            }],
            traceability=[{
                "use_case": "UC-1",
                "scenario_step": "Step 1",
                "requirement_ids": ["FR-1.1"],
            }],
        )

        analysis = validate_brd_analysis(output)

        self.assertEqual(
            analysis["traceability"],
            [
                {
                    "use_case": "UC-1",
                    "scenario_step": "Step 1",
                    "requirement_ids": ["FR-1.1"],
                },
                {
                    "use_case": "UC-1",
                    "scenario_step": "Step 2",
                    "requirement_ids": [],
                },
            ],
        )

    def test_connection_is_local_and_generated_cases_are_validated(self):
        self.assertEqual(validate_base_url("http://127.0.0.1:11434/"), "http://127.0.0.1:11434")
        with self.assertRaisesRegex(ValueError, "local URL"):
            validate_base_url("https://example.com")
        output = {
            "test_cases": [{
                "requirement_id": 4,
                "use_case_id": "UC-1",
                "scenario": "Verify valid login",
                "description": "Generated from login acceptance criteria.",
                "preconditions": "The login page is available.",
                "test_data": "<Valid credentials>",
                "steps": "1. Submit valid credentials.",
                "expected_result": "The dashboard is displayed.",
                "priority": "High",
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
            "priority": "High",
            "source_section": "UC-1, Step 2",
        }
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }) as request:
            cases = generate_ai_cases(
                "http://127.0.0.1:11434", "llama3.1:8b", [requirement],
                [{"method": "GET", "path": "/login", "summary": "", "operation_id": "", "tags": '["auth"]'}],
                {"routes": [], "components": [], "api_integrations": []},
            )
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["requirement_id"], 4)
        self.assertEqual(cases[0]["use_case_id"], "UC-1")
        self.assertEqual(cases[0]["priority"], "High")
        self.assertEqual(cases[0]["preconditions"], "The login page is available.")
        self.assertEqual(len(cases[0]["generation_key"]), 64)
        request_payload = request.call_args.args[2]
        self.assertIn('"tags": ["auth"]', request_payload["messages"][1]["content"])
        self.assertIn("must not repeat the scenario", request_payload["messages"][1]["content"])
        self.assertIn("Requirement-to-Test-Case Generation", request_payload["messages"][0]["content"])
        output["test_cases"][0]["expected_result"] = output["test_cases"][0]["scenario"]
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }):
            with self.assertRaisesRegex(ValueError, "scenario and expected result distinct"):
                generate_ai_cases(
                    "http://127.0.0.1:11434", "llama3.1:8b", [requirement], [], {}
                )
        output["test_cases"][0]["expected_result"] = "The dashboard is displayed."
        output["test_cases"][0]["requirement_id"] = 999
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }):
            with self.assertRaisesRegex(ValueError, "unknown requirement"):
                generate_ai_cases(
                    "http://127.0.0.1:11434", "llama3.1:8b", [requirement], [],
                    {"routes": [], "components": [], "api_integrations": []},
                )
        output["test_cases"][0]["requirement_id"] = 4
        output["test_cases"][0]["test_type"] = {}
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }):
            with self.assertRaisesRegex(ValueError, "unsupported test type"):
                generate_ai_cases(
                    "http://127.0.0.1:11434", "llama3.1:8b", [requirement], [],
                    {"routes": [], "components": [], "api_integrations": []},
                )
        output["test_cases"][0]["test_type"] = "Functional"
        output["test_cases"][0]["priority"] = "Critical"
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }):
            with self.assertRaisesRegex(ValueError, "priority not supplied"):
                generate_ai_cases(
                    "http://127.0.0.1:11434", "llama3.1:8b", [requirement], [], {}
                )
        output["test_cases"][0]["priority"] = "High"
        output["test_cases"][0]["use_case_id"] = "UC-99"
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }):
            with self.assertRaisesRegex(ValueError, "use case not present"):
                generate_ai_cases(
                    "http://127.0.0.1:11434", "llama3.1:8b", [requirement], [], {}
                )
        output["test_cases"][0]["use_case_id"] = "UC-1"
        output["test_cases"].append({
            **output["test_cases"][0],
            "test_data": "<Different credentials>",
        })
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }):
            with self.assertRaisesRegex(ValueError, "duplicates a scenario"):
                generate_ai_cases(
                    "http://127.0.0.1:11434", "llama3.1:8b", [requirement], [], {}
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
            requirements = generate_ai_requirements(
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

    def test_requirement_module_classification_is_validated_and_inferred(self):
        output = {"requirements": [{
            "title": "Login page latency",
            "description": "The login page must load within 2 seconds.",
            "acceptance_criteria": "The page loads within 2 seconds.",
            "business_rules": "",
            "validation_rules": "",
            "dependencies": "",
            "priority": "High",
            "module_type": "Non-Functional",
            "module_category": "performance",
        }]}
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }):
            requirements = generate_ai_requirements(
                "http://127.0.0.1:11434", "llama3.1:8b", "Login page must load in 2 seconds."
            )
        self.assertEqual(requirements[0]["module_type"], "Non-Functional")
        self.assertEqual(requirements[0]["module_category"], "Performance")

        output["requirements"][0].pop("module_type")
        output["requirements"][0].pop("module_category")
        output["requirements"][0]["title"] = "Password encryption"
        output["requirements"][0]["description"] = "Passwords must be encrypted at rest."
        with patch("ollama_integration._request", return_value={
            "message": {"content": json.dumps(output)}
        }):
            requirements = generate_ai_requirements(
                "http://127.0.0.1:11434", "llama3.1:8b", "Passwords must be encrypted."
            )
        self.assertEqual(requirements[0]["module_type"], "Non-Functional")
        self.assertEqual(requirements[0]["module_category"], "Security")


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
            <w:tbl><w:tr>
              <w:tc><w:p><w:r><w:t>Feature ID</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Feature</w:t></w:r></w:p></w:tc>
            </w:tr></w:tbl>
            <w:sectPr/>
          </w:body>
        </w:document>"""
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", document)
        return output.getvalue()

    @staticmethod
    def pdf_bytes():
        stream = b"BT /F1 12 Tf 20 200 Td (Users can export reports.) Tj ET"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        ]
        output = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for index, body in enumerate(objects, 1):
            offsets.append(len(output))
            output.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
        xref_offset = len(output)
        output.extend(f"xref\n0 {len(offsets)}\n".encode())
        output.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            output.extend(f"{offset:010} 00000 n \n".encode())
        output.extend(
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n".encode()
        )
        return bytes(output)

    def test_extracts_docx_headings_ids_acceptance_validation_and_source(self):
        text, records = extract_requirement_document(
            "requirements.docx", document_data=self.docx_bytes()
        )
        self.assertIn("Acceptance Criteria", text)
        self.assertIn("Feature ID | Feature", text)
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

    def test_classifies_functional_and_non_functional_requirements(self):
        _, records = extract_requirement_document(
            "requirements.md",
            content=(
                "# REQ-001: User login\nUsers can log in with email and password.\n\n"
                "# REQ-002: Response time\nThe API response time must be under 200 milliseconds.\n\n"
                "# REQ-003: Password storage\nPasswords must be encrypted at rest.\n\n"
                "# REQ-004: Browser support\nThe app must work in Chrome and Firefox.\n"
            ),
        )
        modules = {
            record["requirement_key"]: (record["module_type"], record["module_category"])
            for record in records
        }
        self.assertEqual(modules["REQ-001"], ("Functional", "Functional"))
        self.assertEqual(modules["REQ-002"], ("Non-Functional", "Performance"))
        self.assertEqual(modules["REQ-003"], ("Non-Functional", "Security"))
        self.assertEqual(modules["REQ-004"], ("Non-Functional", "Compatibility"))

    def test_rejects_invalid_or_unsupported_documents(self):
        with self.assertRaisesRegex(ValueError, "valid document"):
            extract_requirement_document("broken.docx", document_data=b"not a document")
        with self.assertRaisesRegex(ValueError, "Only PDF, DOCX, CSV, TXT, and Markdown"):
            extract_requirement_document("requirements.rtf", content="ignored")
        with self.assertRaisesRegex(ValueError, "too large"):
            extract_requirement_document("requirements.txt", content="x" * 1_000_001)
        _, heading_only_records = extract_requirement_document(
            "headings.md",
            content="\n".join(f"# REQ-{number:03d}: Item {number}" for number in range(101)),
        )
        self.assertEqual(heading_only_records, [])
        with self.assertRaisesRegex(ValueError, "too many requirements"):
            extract_requirement_document(
                "many.md",
                content="\n\n".join(
                    f"# REQ-{number:03d}: Item {number}\nThe system shall process item {number}."
                    for number in range(101)
                ),
            )

    def test_extracts_csv_feature_table_rows(self):
        text, records = extract_requirement_document(
            "features.csv",
            content=(
                "Feature ID,Feature,Description,Priority,Version\n"
                "A01,Modern templates,Admins can preview templates before selection.,P0,v0.1\n"
            ),
        )
        self.assertIn("A01 | Modern templates", text)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "Modern templates")
        self.assertIn("preview templates", records[0]["description"])
        self.assertEqual(records[0]["priority"], "High")
        self.assertIn("A01", records[0]["source_evidence"])

    def test_unstructured_background_text_is_not_promoted_to_a_requirement(self):
        _, records = extract_requirement_document(
            "background.txt",
            content="# Product overview\nThe portal supports several account types.",
        )
        self.assertEqual(records, [])

    def test_extracts_text_and_page_traceability_from_pdf(self):
        text, records = extract_requirement_document(
            "reports.pdf", document_data=self.pdf_bytes()
        )
        self.assertIn("[Page 1]", text)
        self.assertIn("Users can export reports", text)
        self.assertEqual(len(records), 1)
        self.assertIn("export reports", records[0]["description"])
        self.assertIn("Page 1", records[0]["source_section"])

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
                db.execute(
                    """INSERT INTO projects
                    (project_key,name,description,version,environment,qa_owner,created_at,updated_at)
                    VALUES('LEGACY','Legacy project','','','','QA','2024-01-01','2024-01-01')"""
                )
                db.execute(
                    """INSERT INTO requirements
                    (project_id,requirement_key,title,source_document,review_status,created_at)
                    VALUES(1,'REQ-001','Imported requirement','old.md','Reviewed','2024-01-01')"""
                )
            app.initialize(db_path)
            with app.database(db_path) as db:
                columns = {row["name"] for row in db.execute("PRAGMA table_info(requirements)")}
                self.assertTrue({
                    "business_rules", "validation_rules", "dependencies", "source_section",
                    "module_type", "module_category", "requirement_type",
                    "scope_classification", "ai_confidence_status", "actor", "main_flow",
                    "source_evidence", "origin", "source_suggestion_id",
                }.issubset(columns))
                case_columns = {row["name"] for row in db.execute("PRAGMA table_info(test_cases)")}
                self.assertTrue({
                    "test_type", "generation_key", "requirement_origin",
                    "requirement_approval_status", "source_suggestion_id",
                    "source_requirement_key", "source_requirement_title",
                    "title", "test_category", "classification", "assumptions",
                    "source_reference",
                }.issubset(case_columns))
                self.assertEqual(
                    tuple(db.execute(
                        "SELECT origin,review_status FROM requirements WHERE requirement_key='REQ-001'"
                    ).fetchone()),
                    ("legacy_import", "Reviewed"),
                )
                self.assertIsNotNone(db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='requirement_documents'"
                ).fetchone())
                for table_name in (
                    "requirement_analysis_previews", "requirement_suggestions",
                    "requirement_suggestion_history",
                ):
                    self.assertIsNotNone(db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                        (table_name,),
                    ).fetchone())
                document_columns = {
                    row["name"] for row in db.execute("PRAGMA table_info(requirement_documents)")
                }
                self.assertIn("analysis_json", document_columns)
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
        self.assertTrue(any(
            "fetch" in item["kind"] for item in result["analysis"]["api_integrations"]
        ))

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

    def request(self, path, method="GET", payload=None, authenticated=True):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if authenticated and getattr(self, "auth_cookie", None):
            headers["Cookie"] = self.auth_cookie
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request) as response:
                content = response.read()
                if "application/json" in response.headers.get("Content-Type", ""):
                    result = (response.status, json.loads(content))
                else:
                    result = (response.status, content)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())
        if (
            method == "POST"
            and path.endswith("/requirements/import")
            and isinstance(payload, dict)
            and "preview_id" not in payload
            and "confirmed_preview" not in payload
            and result[0] == 200
            and isinstance(result[1], dict)
            and result[1].get("preview_required")
        ):
            return self.request(
                path,
                method,
                {**payload, "preview_id": result[1]["preview"]["preview_id"]},
                authenticated=authenticated,
            )
        return result

    def raw_request(self, path, body, content_type, method="POST", authenticated=True):
        headers = {"Content-Type": content_type}
        if authenticated and getattr(self, "auth_cookie", None):
            headers["Cookie"] = self.auth_cookie
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def setUp(self):
        ai_env = patch.dict(os.environ, {
            "QA_ADMIN_PASSWORD": "test-admin-password-123",
            "QA_AI_ENCRYPTION_KEY": "a" * 64,
        })
        ai_env.start()
        self.addCleanup(ai_env.stop)
        token = secrets.token_urlsafe(32)
        with self.server.admin_session_lock:
            self.server.admin_sessions.clear()
            self.server.user_sessions.clear()
            self.server.admin_sessions[token] = time.time() + 3600
        self.admin_cookie = f"trace_admin_session={token}"
        self.auth_cookie = self.admin_cookie
        with app.database(self.db_path) as db:
            db.execute("DELETE FROM projects")
            db.execute("DELETE FROM custom_fields")
            db.execute("DELETE FROM teams")
            db.execute("DELETE FROM users")
            db.execute("DELETE FROM audit_events")
            db.execute(
                """UPDATE global_admin_profile SET first_name='Global',
                last_name='Administrator',email='global-admin@local.qa',phone='',
                profile_image_type='',profile_image_data=NULL,updated_at=? WHERE id=1""",
                (app.timestamp(),),
            )
            db.execute("DELETE FROM ai_provider_configs WHERE provider != 'local'")
            db.execute(
                "UPDATE ai_settings SET provider='local',updated_at=? WHERE id=1",
                (app.timestamp(),),
            )

    def test_custom_fields_are_module_scoped_validated_and_persisted(self):
        status, _ = self.request("/api/custom-fields", "POST", {
            "module_key": "bugs", "name": "environment", "label": "Environment",
            "field_type": "dropdown", "required": True,
            "options": ["Development", "QA", "Production"],
        }, authenticated=False)
        self.assertEqual(status, 401)

        _, project = self.request("/api/projects", "POST", {
            "name": "Custom fields", "project_key": "CFIELD", "qa_owner": "QA",
        })
        _, text_field = self.request("/api/custom-fields", "POST", {
            "module_key": "bugs", "name": "build_number", "label": "Build Number",
            "field_type": "text", "required": True, "display_order": 2,
        })
        _, number_field = self.request("/api/custom-fields", "POST", {
            "module_key": "bugs", "name": "retry_count", "label": "Retry Count",
            "field_type": "number", "required": False, "display_order": 1,
        })
        _, dropdown_field = self.request("/api/custom-fields", "POST", {
            "module_key": "bugs", "name": "environment", "label": "Environment",
            "field_type": "dropdown", "required": True, "display_order": 0,
            "options": ["Development", "QA", "Production"],
        })
        _, requirement_field = self.request("/api/custom-fields", "POST", {
            "module_key": "requirements", "name": "customer_type",
            "label": "Customer Type", "field_type": "text",
        })

        status, error = self.request(
            f"/api/projects/{project['id']}/bugs", "POST", {"title": "Missing values"}
        )
        self.assertEqual(status, 400)
        self.assertIn("is required", error["error"])
        status, error = self.request(
            f"/api/projects/{project['id']}/bugs", "POST", {
                "title": "Bad values",
                "custom_fields": {
                    str(text_field["id"]): "5.4.2",
                    str(number_field["id"]): "3x",
                    str(dropdown_field["id"]): "QA",
                },
            }
        )
        self.assertEqual(status, 400)
        self.assertIn("Retry Count must be a valid number", error["error"])
        status, bug = self.request(
            f"/api/projects/{project['id']}/bugs", "POST", {
                "title": "Valid values",
                "custom_fields": {
                    str(text_field["id"]): "5.4.2",
                    str(number_field["id"]): "3",
                    str(dropdown_field["id"]): "Production",
                },
            }
        )
        self.assertEqual(status, 201)
        fields = self.request(
            f"/api/custom-fields?module=bugs&record_id={bug['id']}"
        )[1]["fields"]
        self.assertEqual(
            {item["id"]: item["value"] for item in fields},
            {
                text_field["id"]: "5.4.2",
                number_field["id"]: "3",
                dropdown_field["id"]: "Production",
            },
        )
        self.assertEqual(
            self.request(f"/api/custom-fields?module=test-cases")[1]["fields"], []
        )
        self.assertEqual(
            self.request(f"/api/custom-fields?module=requirements")[1]["fields"][0]["id"],
            requirement_field["id"],
        )

        _, updated = self.request(
            f"/api/projects/{project['id']}/bugs/{bug['id']}", "PATCH", {
                "custom_fields": {str(text_field["id"]): "5.4.3"},
            }
        )
        self.assertEqual(updated["title"], "Valid values")
        values = self.request(
            f"/api/custom-fields?module=bugs&record_id={bug['id']}"
        )[1]["fields"]
        self.assertEqual(
            {item["id"]: item["value"] for item in values}[number_field["id"]], "3"
        )
        status, error = self.request(
            f"/api/custom-fields/{dropdown_field['id']}", "DELETE"
        )
        self.assertEqual(status, 409)
        self.assertIn("Deactivate it", error["error"])

        _, deactivated = self.request(
            f"/api/custom-fields/{dropdown_field['id']}", "PATCH",
            {"active": False},
        )
        self.assertFalse(deactivated["active"])
        self.assertNotIn(
            dropdown_field["id"],
            {item["id"] for item in self.request(
                f"/api/custom-fields?module=bugs&record_id={bug['id']}"
            )[1]["fields"]},
        )
        inactive_fields = self.request(
            f"/api/custom-fields?module=bugs&record_id={bug['id']}&include_inactive=true"
        )[1]["fields"]
        self.assertEqual(
            {item["id"]: item["value"] for item in inactive_fields}[dropdown_field["id"]],
            "Production",
        )

    def test_custom_fields_are_saved_on_project_requirement_and_test_case_records(self):
        created_fields = {}
        for module, name, label in (
            ("projects", "customer_segment", "Customer Segment"),
            ("requirements", "source_system", "Source System"),
            ("test-cases", "execution_count", "Execution Count"),
        ):
            _, created_fields[module] = self.request("/api/custom-fields", "POST", {
                "module_key": module, "name": name, "label": label,
                "field_type": "text" if module != "test-cases" else "number",
                "required": True,
            })

        project_field = created_fields["projects"]
        status, _ = self.request("/api/projects", "POST", {
            "name": "Project custom field", "project_key": "PROJCF", "qa_owner": "QA",
        })
        self.assertEqual(status, 400)
        status, project = self.request("/api/projects", "POST", {
            "name": "Project custom field", "project_key": "PROJCF", "qa_owner": "QA",
            "custom_fields": {str(project_field["id"]): "Retail"},
        })
        self.assertEqual(status, 201)
        self.assertEqual(
            self.request(f"/api/custom-fields?module=projects&record_id={project['id']}")[1]
            ["fields"][0]["value"],
            "Retail",
        )

        requirement_field = created_fields["requirements"]
        req_path = f"/api/projects/{project['id']}/requirements"
        status, _ = self.request(req_path, "POST", {"title": "Custom requirement"})
        self.assertEqual(status, 400)
        status, requirement = self.request(req_path, "POST", {
            "title": "Custom requirement",
            "custom_fields": {str(requirement_field["id"]): "CRM"},
        })
        self.assertEqual(status, 201)
        self.assertEqual(
            self.request(
                f"/api/custom-fields?module=requirements&record_id={requirement['id']}"
            )[1]["fields"][0]["value"],
            "CRM",
        )

        case_field = created_fields["test-cases"]
        case_path = f"/api/projects/{project['id']}/test-cases"
        status, _ = self.request(case_path, "POST", {"scenario": "Check flow"})
        self.assertEqual(status, 400)
        status, case = self.request(case_path, "POST", {
            "scenario": "Check flow",
            "custom_fields": {str(case_field["id"]): "2"},
        })
        self.assertEqual(status, 201)
        _, edited_case = self.request(
            f"{case_path}/{case['id']}", "PATCH",
            {"custom_fields": {str(case_field["id"]): "3"}},
        )
        self.assertEqual(
            self.request(
                f"/api/custom-fields?module=test-cases&record_id={case['id']}"
            )[1]["fields"][0]["value"],
            "3",
        )
        self.assertEqual(edited_case["id"], case["id"])

    def test_record_attachments_upload_list_view_download_remove_and_validate(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Attachments", "project_key": "ATTACH", "qa_owner": "QA",
        })
        _, bug = self.request(
            f"/api/projects/{project['id']}/bugs", "POST", {"title": "Upload files"}
        )
        base = f"/api/projects/{project['id']}/attachments"
        target = (
            f"{base}?module=bugs&record_id={bug['id']}&filename=notes.txt"
        )
        status, attachment = self.raw_request(
            target, b"Investigation notes", "application/octet-stream"
        )
        self.assertEqual(status, 201)
        self.assertEqual(attachment["filename"], "notes.txt")
        self.assertEqual(attachment["file_size_bytes"], len(b"Investigation notes"))
        status, items = self.request(
            f"{base}?module=bugs&record_id={bug['id']}"
        )
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in items], [attachment["id"]])

        view_request = urllib.request.Request(
            self.base + f"{base}/{attachment['id']}/view",
            headers={"Cookie": self.auth_cookie},
        )
        with urllib.request.urlopen(view_request) as response:
            self.assertEqual(response.read(), b"Investigation notes")
            self.assertIn("inline", response.headers["Content-Disposition"])
        download_request = urllib.request.Request(
            self.base + f"{base}/{attachment['id']}/download",
            headers={"Cookie": self.auth_cookie},
        )
        with urllib.request.urlopen(download_request) as response:
            self.assertEqual(response.read(), b"Investigation notes")
            self.assertIn("attachment", response.headers["Content-Disposition"])

        status, error = self.raw_request(
            target, b"Investigation notes", "application/octet-stream"
        )
        self.assertEqual(status, 409)
        status, error = self.raw_request(
            target.replace("notes.txt", "payload.exe"),
            b"not executable", "application/octet-stream",
        )
        self.assertEqual(status, 400)
        self.assertIn("Unsupported attachment type", error["error"])
        status, error = self.raw_request(
            target.replace("notes.txt", "large.txt"),
            b"x" * (app.MAX_ATTACHMENT_SIZE + 1), "application/octet-stream",
        )
        self.assertEqual(status, 400)
        self.assertIn("maximum 5 MB", error["error"])

        status, result = self.request(
            f"{base}/{attachment['id']}", method="DELETE"
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["deleted"])
        self.assertEqual(self.request(
            f"{base}?module=bugs&record_id={bug['id']}"
        )[1], [])

    def test_ai_settings_show_enabled_provider_without_exposing_credentials(self):
        status, settings = self.request("/api/ai/settings")
        self.assertEqual(status, 200)
        self.assertEqual(settings["provider"], "local")
        self.assertEqual(settings["model"], "deterministic")
        self.assertTrue(settings["configured"])
        self.assertEqual(
            [item["id"] for item in settings["providers"]],
            ["local", "ollama", "google", "openai-compatible"],
        )
        self.assertNotIn("api_key", settings)
        status, error = self.request("/api/ai/settings", "POST", {
            "provider": "google", "model": "gemini-2.5-flash",
            "api_key": "test-key",
        }, authenticated=False)
        self.assertEqual(status, 401)
        status, error = self.request(
            "/api/auth/login", "POST", {"password": "incorrect"}, authenticated=False
        )
        self.assertEqual(status, 401)
        self.assertIn("incorrect", error["error"])

        login_request = urllib.request.Request(
            self.base + "/api/auth/login",
            data=json.dumps({"password": "test-admin-password-123"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(login_request) as response:
            self.assertEqual(response.status, 200)
            login = json.loads(response.read())
            self.auth_cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        self.assertTrue(login["authenticated"])
        self.assertTrue(self.auth_cookie.startswith("trace_admin_session="))

        status, configured = self.request("/api/ai/settings", "POST", {
            "provider": "google",
            "model": "gemini-2.5-flash",
            "api_key": "test-secret-key",
        })
        self.assertEqual(status, 200)
        self.assertTrue(configured["configured"])
        self.assertNotIn("test-secret-key", json.dumps(configured))
        status, configured = self.request("/api/ai/settings")
        self.assertEqual(status, 200)
        self.assertTrue(configured["configured"])
        self.assertNotIn("test-secret-key", json.dumps(configured))

        with patch("app.generate_provider_text", return_value="OK") as generate:
            status, result = self.request("/api/ai/test", "POST", {})
        self.assertEqual(status, 200)
        self.assertTrue(result["connected"])
        self.assertEqual(result["provider"], "google")
        self.assertEqual(result["model"], "gemini-2.5-flash")
        self.assertNotIn("test-key", json.dumps(result))
        self.assertEqual(generate.call_args.args[0]["provider"], "google")
        status, _ = self.request("/api/auth/logout", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/api/auth/status")[1]["authenticated"], False)
        self.assertEqual(self.request("/api/ai/test", "POST", {}, authenticated=False)[0], 401)

    def test_signup_login_password_storage_and_server_side_permissions(self):
        status, error = self.request("/api/projects", authenticated=False)
        self.assertEqual(status, 401)

        _, protected_project = self.request("/api/projects", "POST", {
            "name": "Existing workspace data",
            "project_key": "EXISTING",
            "qa_owner": "QA",
        })
        signup = urllib.request.Request(
            self.base + "/api/auth/signup",
            data=json.dumps({
                "first_name": "First",
                "last_name": "Admin",
                "email": "first.admin@example.test",
                "password": "SecurePassword123",
                "confirm_password": "SecurePassword123",
            }).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(signup) as response:
            self.assertEqual(response.status, 201)
            self.auth_cookie = response.headers["Set-Cookie"].split(";", 1)[0]
            created = json.loads(response.read())
        self.assertEqual(created["user"]["role_name"], "User")
        self.assertNotEqual(created["user"]["role_name"], "Global Admin")
        self.assertEqual(self.request("/api/auth/status")[1]["user"]["email"], "first.admin@example.test")
        self.assertNotEqual(created["user"]["tenant_id"], 1)
        self.assertEqual(self.request("/api/projects")[1], [])
        self.assertEqual(
            self.request(f"/api/projects/{protected_project['id']}")[0],
            404,
        )
        user_cookie = self.auth_cookie
        with app.database(self.db_path) as db:
            stored = db.execute(
                "SELECT password_hash FROM users WHERE email=?",
                ("first.admin@example.test",),
            ).fetchone()["password_hash"]
            membership = db.execute(
                """SELECT tenant_id FROM tenant_memberships
                WHERE user_id=?""",
                (created["user"]["id"],),
            ).fetchone()
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertNotIn("SecurePassword123", stored)
        self.assertEqual(membership["tenant_id"], created["user"]["tenant_id"])
        self.assertEqual(
            self.request("/api/auth/signup", "POST", {
                "first_name": "Other", "last_name": "User",
                "email": "first.admin@example.test",
                "password": "SecurePassword123",
                "confirm_password": "SecurePassword123",
            }, authenticated=False)[0],
            409,
        )

        self.auth_cookie = self.admin_cookie
        _, role = self.request("/api/roles", "POST", {
            "name": "Read only project access",
            "description": "May only view projects.",
            "permissions": ["projects.view"],
        })
        _, user = self.request("/api/users", "POST", {
            "first_name": "Read", "last_name": "Only",
            "email": "reader@example.test", "password": "ReaderPassword123",
            "role_id": role["id"],
        })
        _, manager_role = self.request("/api/roles", "POST", {
            "name": "Limited account manager",
            "permissions": [
                "projects.view", "users.create", "roles.edit",
                "roles.manage-permissions",
            ],
        })
        _, manager = self.request("/api/users", "POST", {
            "first_name": "Limited", "last_name": "Manager",
            "email": "manager@example.test", "password": "ManagerPassword123",
            "role_id": manager_role["id"],
        })
        admin_role_id = next(
            item["id"] for item in self.request("/api/roles")[1]["roles"]
            if item["name"] == "Global Admin"
        )
        login = urllib.request.Request(
            self.base + "/api/auth/login",
            data=json.dumps({
                "email": "reader@example.test",
                "password": "ReaderPassword123",
            }).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(login) as response:
            self.auth_cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(self.request("/api/projects")[0], 200)
        self.assertEqual(
            self.request("/api/projects", "POST", {
                "name": "Forbidden", "project_key": "FORBIDDEN", "qa_owner": "Reader",
            })[0],
            403,
        )
        self.assertEqual(self.request("/api/roles")[0], 403)
        self.assertEqual(self.request("/api/users")[0], 403)
        self.assertEqual(self.request(f"/api/users/{user['id']}", "DELETE")[0], 403)

        manager_login = urllib.request.Request(
            self.base + "/api/auth/login",
            data=json.dumps({
                "email": "manager@example.test",
                "password": "ManagerPassword123",
            }).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(manager_login) as response:
            self.auth_cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(self.request("/api/users", "POST", {
            "first_name": "Escalated", "last_name": "User",
            "email": "escalated@example.test", "password": "EscalatedPassword123",
            "role_id": admin_role_id,
        })[0], 403)
        self.assertEqual(self.request(f"/api/roles/{role['id']}", "PATCH", {
            "name": role["name"], "description": role["description"],
            "status": role["status"], "permissions": ["projects.view", "users.edit"],
        })[0], 403)
        self.auth_cookie = user_cookie
        self.assertEqual(self.request("/api/users", "POST", {
            "first_name": "Self", "last_name": "Promoted",
            "email": "self.promoted@example.test", "password": "SelfPromoted123",
        })[0], 403)

    def test_reserved_tenant_admin_account_is_promoted_and_scoped(self):
        self.assertEqual(self.request("/api/auth/signup", "POST", {
            "first_name": "Reserved", "last_name": "Admin",
            "email": app.TENANT_ADMIN_EMAIL,
            "password": "ReservedAdmin123",
            "confirm_password": "ReservedAdmin123",
        }, authenticated=False)[0], 409)

        _, tenant_admin = self.request("/api/users", "POST", {
            "first_name": "Workspace", "last_name": "Administrator",
            "email": app.TENANT_ADMIN_EMAIL,
            "password": "TenantAdminPassword123",
        })
        self.assertEqual(tenant_admin["role_name"], "User")
        _, removed_user = self.request("/api/users", "POST", {
            "first_name": "Tenant", "last_name": "Member",
            "email": "tenant.member@example.test",
            "password": "TenantMemberPassword123",
        })
        with app.database(self.db_path) as db:
            now = app.timestamp()
            db.execute(
                """INSERT INTO tenants(name,slug,status,created_at,updated_at)
                VALUES('Second Workspace','second-workspace','Active',?,?)""",
                (now, now),
            )
            tenant_two = db.execute(
                "SELECT id FROM tenants WHERE slug='second-workspace'"
            ).fetchone()["id"]
            user_role = db.execute(
                "SELECT id FROM roles WHERE name='User'"
            ).fetchone()["id"]
            for user_id in (tenant_admin["id"], removed_user["id"]):
                db.execute(
                    """INSERT INTO tenant_memberships
                    (tenant_id,user_id,role_id,status,created_at,updated_at)
                    VALUES(?,?,?,'Active',?,?)""",
                    (tenant_two, user_id, user_role, now, now),
                )
            project_cursor = db.execute(
                """INSERT INTO projects
                (tenant_id,project_key,name,qa_owner,created_at,updated_at)
                VALUES(?,?,?,?,?,?)""",
                (tenant_two, "TENANT-TWO", "Second tenant project", "QA", now, now),
            )
            tenant_two_project_id = project_cursor.lastrowid

        app.initialize(self.db_path)
        with app.database(self.db_path) as db:
            memberships = {
                row["tenant_id"]: row["role_name"]
                for row in db.execute(
                    """SELECT m.tenant_id,r.name role_name
                    FROM tenant_memberships m JOIN roles r ON r.id=m.role_id
                    WHERE m.user_id=?""",
                    (tenant_admin["id"],),
                )
            }
        self.assertEqual(memberships[1], "Global Admin")
        self.assertEqual(memberships[tenant_two], "User")

        login = urllib.request.Request(
            self.base + "/api/auth/login",
            data=json.dumps({
                "email": app.TENANT_ADMIN_EMAIL,
                "password": "TenantAdminPassword123",
            }).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(login) as response:
            self.auth_cookie = response.headers["Set-Cookie"].split(";", 1)[0]
            authenticated = json.loads(response.read())
        self.assertEqual(authenticated["user"]["role_name"], "Global Admin")
        self.assertIn("*", authenticated["permissions"])
        self.assertEqual(self.request("/api/users")[0], 200)
        self.assertNotIn(
            tenant_two_project_id,
            {item["id"] for item in self.request("/api/projects")[1]},
        )
        self.assertEqual(self.request(f"/api/projects/{tenant_two_project_id}")[0], 404)

        self.assertEqual(
            self.request(f"/api/users/{removed_user['id']}/deactivate", "POST", {})[0],
            200,
        )
        member_login = urllib.request.Request(
            self.base + "/api/auth/login",
            data=json.dumps({
                "email": "tenant.member@example.test",
                "password": "TenantMemberPassword123",
            }).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(member_login) as response:
            member_cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        for selected_tenant, expected_status in ((1, 401), (tenant_two, 200)):
            scoped_projects = urllib.request.Request(
                self.base + "/api/projects",
                headers={
                    "Cookie": member_cookie,
                    "X-Tenant-ID": str(selected_tenant),
                },
            )
            try:
                with urllib.request.urlopen(scoped_projects) as response:
                    scoped_status = response.status
                    projects = json.loads(response.read())
            except urllib.error.HTTPError as error:
                scoped_status = error.code
                projects = []
            self.assertEqual(scoped_status, expected_status)
            if selected_tenant == tenant_two:
                self.assertIn(tenant_two_project_id, {item["id"] for item in projects})

        self.assertEqual(
            self.request(f"/api/users/{removed_user['id']}/activate", "POST", {})[0],
            200,
        )
        status, removal = self.request(
            f"/api/users/{removed_user['id']}", method="DELETE"
        )
        self.assertEqual(status, 200)
        self.assertFalse(removal["account_deleted"])
        with app.database(self.db_path) as db:
            self.assertIsNotNone(db.execute(
                "SELECT 1 FROM users WHERE id=?", (removed_user["id"],)
            ).fetchone())
            self.assertIsNone(db.execute(
                """SELECT 1 FROM tenant_memberships
                WHERE user_id=? AND tenant_id=1""",
                (removed_user["id"],),
            ).fetchone())
            self.assertIsNotNone(db.execute(
                """SELECT 1 FROM tenant_memberships
                WHERE user_id=? AND tenant_id=?""",
                (removed_user["id"], tenant_two),
            ).fetchone())
            event = db.execute(
                """SELECT action,tenant_id FROM audit_events
                WHERE target_id=? ORDER BY id DESC LIMIT 1""",
                (str(removed_user["id"]),),
            ).fetchone()
        self.assertEqual(event["action"], "tenant_membership.removed")
        self.assertEqual(event["tenant_id"], 1)

        other_tenant_request = urllib.request.Request(
            self.base + "/api/users",
            headers={
                "Cookie": self.auth_cookie,
                "X-Tenant-ID": str(tenant_two),
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(other_tenant_request)
        self.assertEqual(error.exception.code, 403)

    def test_profile_password_teams_memberships_and_audit(self):
        signup = urllib.request.Request(
            self.base + "/api/auth/signup",
            data=json.dumps({
                "first_name": "Workspace", "last_name": "Admin",
                "email": "workspace.admin@example.test",
                "password": "SecurePassword123",
                "confirm_password": "SecurePassword123",
            }).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(signup) as response:
            self.auth_cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        status, profile = self.request("/api/profile")
        self.assertEqual(status, 200)
        self.assertNotIn("password_hash", profile)
        _, updated = self.request("/api/profile", "PATCH", {
            "first_name": "Updated", "last_name": "Admin",
            "email": "updated.admin@example.test", "phone": "+1 555 0100",
        })
        self.assertEqual(updated["first_name"], "Updated")
        self.assertEqual(updated["email"], "updated.admin@example.test")
        with app.database(self.db_path) as db:
            profile_event = db.execute(
                """SELECT details_json FROM audit_events
                WHERE action='user.profile_updated' ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        profile_details = json.loads(profile_event["details_json"])
        self.assertEqual(profile_details["previous"]["email"], "workspace.admin@example.test")
        self.assertEqual(profile_details["new"]["email"], "updated.admin@example.test")
        self.assertNotIn("password_hash", profile_details["new"])
        self.assertEqual(self.request("/api/profile/password", "PATCH", {
            "current_password": "SecurePassword123",
            "new_password": "ChangedPassword456",
            "confirm_password": "ChangedPassword456",
        })[0], 200)
        bad_login = urllib.request.Request(
            self.base + "/api/auth/login",
            data=json.dumps({
                "email": "updated.admin@example.test",
                "password": "SecurePassword123",
            }).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(bad_login)
        self.assertEqual(error.exception.code, 401)

        self.auth_cookie = self.admin_cookie
        qa_role = next(
            item for item in self.request("/api/roles")[1]["roles"]
            if item["name"] == "QA Engineer"
        )
        _, member = self.request("/api/users", "POST", {
            "first_name": "Team", "last_name": "Member",
            "email": "team.member@example.test",
            "password": "TeamMemberPassword123",
            "role_id": qa_role["id"],
        })
        _, owner = self.request("/api/users", "POST", {
            "first_name": "Team", "last_name": "Owner",
            "email": "team.owner@example.test",
            "password": "TeamOwnerPassword123",
        })
        _, team = self.request("/api/teams", "POST", {
            "name": "Regression QA", "description": "Release regression testing",
            "owner_user_id": member["id"],
        })
        self.assertEqual(team["member_count"], 1)
        _, members = self.request(f"/api/teams/{team['id']}/members")
        self.assertEqual({item["id"] for item in members}, {member["id"]})
        self.assertEqual(
            self.request(f"/api/teams/{team['id']}/members/{member['id']}", "DELETE")[0],
            409,
        )
        self.assertEqual(self.request(f"/api/teams/{team['id']}", "PATCH", {
            "name": team["name"], "description": team["description"],
            "owner_user_id": owner["id"], "status": team["status"],
        })[0], 200)
        self.assertEqual(
            self.request(f"/api/teams/{team['id']}/members/{member['id']}", "DELETE")[0],
            200,
        )
        self.assertEqual(self.request(f"/api/teams/{team['id']}", "DELETE")[0], 200)
        _, events = self.request("/api/audit-events")
        self.assertTrue(any(event["action"] == "user.password_changed" for event in events))
        self.assertTrue(any(event["action"] == "team.member_removed" for event in events))

    def test_tenant_rbac_user_lifecycle_and_audit(self):
        _, user = self.request("/api/users", "POST", {
            "first_name": "Normal", "last_name": "User",
            "email": "lifecycle.user@example.test",
            "password": "LifecyclePassword123",
        })
        self.assertEqual(user["role_name"], "User")
        self.assertEqual(user["tenant_id"], 1)
        with app.database(self.db_path) as db:
            membership = db.execute(
                """SELECT tenant_id,status FROM tenant_memberships
                WHERE user_id=?""",
                (user["id"],),
            ).fetchone()
            user_permissions = app.QAHandler.user_permissions(
                db, {"id": user["id"], "role_name": "User", "role_id": user["role_id"]}
            )
            db.execute(
                """INSERT INTO tenants(name,slug,status,created_at,updated_at)
                VALUES('Other Workspace','other-workspace','Active',?,?)""",
                (app.timestamp(), app.timestamp()),
            )
            other_tenant_id = db.execute(
                "SELECT id FROM tenants WHERE slug='other-workspace'"
            ).fetchone()["id"]
            now = app.timestamp()
            cursor = db.execute(
                """INSERT INTO projects
                (tenant_id,project_key,name,qa_owner,created_at,updated_at)
                VALUES(?,?,?,?,?,?)""",
                (other_tenant_id, "TENANT-B", "Other tenant project", "QA", now, now),
            )
            other_project_id = cursor.lastrowid
        self.assertEqual(membership["tenant_id"], 1)
        self.assertEqual(membership["status"], "Active")
        self.assertNotIn("users.create", user_permissions)

        user_login = urllib.request.Request(
            self.base + "/api/auth/login",
            data=json.dumps({
                "email": "lifecycle.user@example.test",
                "password": "LifecyclePassword123",
            }).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(user_login) as response:
            self.auth_cookie = response.headers["Set-Cookie"].split(";", 1)[0]
            authenticated_user = json.loads(response.read())["user"]
        self.assertEqual(authenticated_user["role_name"], "User")
        self.assertEqual(self.request("/api/users")[0], 403)
        self.assertEqual(self.request(f"/api/users/{user['id']}", "PATCH", {
            "first_name": "Promoted", "last_name": "User",
            "email": "lifecycle.user@example.test", "role_id": user["role_id"],
        })[0], 403)
        project_ids = {row["id"] for row in self.request("/api/projects")[1]}
        self.assertNotIn(other_project_id, project_ids)
        self.assertEqual(self.request(f"/api/projects/{other_project_id}")[0], 404)

        self.auth_cookie = self.admin_cookie
        _, team_owner = self.request("/api/users", "POST", {
            "first_name": "Team", "last_name": "Owner",
            "email": "lifecycle.team.owner@example.test",
            "password": "LifecycleOwnerPassword123",
        })
        team_status, team = self.request("/api/teams", "POST", {
            "name": "Lifecycle Team", "owner_user_id": team_owner["id"],
        })
        self.assertEqual(team_status, 201)
        qa_role = next(
            item for item in self.request("/api/roles")[1]["roles"]
            if item["name"] == "QA Engineer"
        )
        _, edited = self.request(f"/api/users/{user['id']}", "PATCH", {
            "first_name": "Edited", "last_name": "User",
            "email": "lifecycle.user@example.test", "phone": "",
            "role_id": qa_role["id"], "status": "Active",
            "team_ids": [team["id"]], "password": "",
        })
        self.assertEqual(edited["role_name"], "QA Engineer")
        self.assertEqual(edited["team_ids"], [team["id"]])
        self.assertEqual(
            self.request(f"/api/users/{user['id']}/deactivate", "POST", {})[0], 200
        )
        inactive_login = self.request("/api/auth/login", "POST", {
            "email": "lifecycle.user@example.test",
            "password": "LifecyclePassword123",
        }, authenticated=False)
        self.assertEqual(inactive_login[0], 403)
        self.assertEqual(
            inactive_login[1]["error"],
            "Your account has been deactivated.\nPlease contact your administrator.",
        )
        self.assertEqual(
            self.request(f"/api/users/{user['id']}/activate", "POST", {})[0], 200
        )

        global_admin_role = next(
            item for item in self.request("/api/roles")[1]["roles"]
            if item["name"] == "Global Admin"
        )
        _, first_admin = self.request("/api/users", "POST", {
            "first_name": "First", "last_name": "Global Admin",
            "email": "first.global@example.test",
            "password": "FirstGlobalPassword123",
            "role_id": global_admin_role["id"],
        })
        with app.database(self.db_path) as db:
            now = app.timestamp()
            db.execute(
                """INSERT INTO tenant_memberships
                (tenant_id,user_id,role_id,status,created_at,updated_at)
                VALUES(?,?,?,'Active',?,?)""",
                (
                    other_tenant_id, first_admin["id"],
                    global_admin_role["id"], now, now,
                ),
            )
        self.assertEqual(
            self.request(f"/api/users/{first_admin['id']}/deactivate", "POST", {})[0],
            409,
        )
        self.assertEqual(
            self.request(f"/api/users/{first_admin['id']}", "DELETE")[1]["error"],
            "The last active Global Admin cannot be deleted.",
        )
        _, second_admin = self.request("/api/users", "POST", {
            "first_name": "Second", "last_name": "Global Admin",
            "email": "second.global@example.test",
            "password": "SecondGlobalPassword123",
            "role_id": global_admin_role["id"],
        })
        self.assertEqual(
            self.request(f"/api/users/{first_admin['id']}/deactivate", "POST", {})[0],
            200,
        )
        self.assertEqual(
            self.request(f"/api/users/{first_admin['id']}", "DELETE")[0], 200
        )

        _, project = self.request("/api/projects", "POST", {
            "name": "Preserved History", "project_key": "USER-HISTORY",
            "qa_owner": "QA",
        })
        now = app.timestamp()
        with app.database(self.db_path) as db:
            db.execute(
                """INSERT INTO requirements(project_id,requirement_key,title,created_at)
                VALUES(?,?,?,?)""",
                (project["id"], "REQ-USER-HISTORY", "Historical requirement", now),
            )
            db.execute(
                """INSERT INTO test_cases(project_id,case_key,scenario,created_at,updated_at)
                VALUES(?,?,?,?,?)""",
                (project["id"], "TC-USER-HISTORY", "Historical test case", now, now),
            )
            db.execute(
                """INSERT INTO bugs(project_id,bug_key,title,created_at,updated_at)
                VALUES(?,?,?,?,?)""",
                (project["id"], "BUG-USER-HISTORY", "Historical defect", now, now),
            )
        self.assertEqual(self.request(f"/api/users/{user['id']}", "DELETE")[0], 200)
        self.assertEqual(
            self.request(f"/api/users/{second_admin['id']}/deactivate", "POST", {})[0],
            409,
        )
        self.assertEqual(
            self.request(f"/api/users/{second_admin['id']}", "DELETE")[1]["error"],
            "The last active Global Admin cannot be deleted.",
        )
        with app.database(self.db_path) as db:
            self.assertIsNone(db.execute(
                "SELECT 1 FROM users WHERE id=?", (user["id"],)
            ).fetchone())
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM requirements WHERE project_id=?",
                (project["id"],),
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM test_cases WHERE project_id=?",
                (project["id"],),
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM bugs WHERE project_id=?",
                (project["id"],),
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='user.deleted' AND target_id=?",
                (str(user["id"]),),
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='user.role_changed' AND target_id=?",
                (str(user["id"]),),
            ).fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='user.team_changed' AND target_id=?",
                (str(user["id"]),),
            ).fetchone()[0], 1)

    def test_global_admin_profile_is_available_without_signing_out(self):
        self.assertEqual(self.request("/api/profile", authenticated=False)[0], 401)
        status, profile = self.request("/api/profile")
        self.assertEqual(status, 200)
        self.assertTrue(profile["global_admin"])
        self.assertEqual(profile["role_name"], "Global Admin")
        self.assertEqual(profile["first_name"], "Global")

        status, updated = self.request("/api/profile", "PATCH", {
            "first_name": "Workspace", "last_name": "Administrator",
            "email": "workspace-admin@local.qa", "phone": "+1 555 0100",
        })
        self.assertEqual(status, 200)
        self.assertTrue(updated["global_admin"])
        self.assertEqual(updated["email"], "workspace-admin@local.qa")

        status, profile = self.request("/api/profile")
        self.assertEqual(status, 200)
        self.assertEqual(profile["first_name"], "Workspace")
        status, auth = self.request("/api/auth/status")
        self.assertEqual(status, 200)
        self.assertTrue(auth["authenticated"])
        self.assertEqual(auth["user"]["role_name"], "Global Admin")
        self.assertTrue(auth["user"]["global_admin"])
        self.assertEqual(auth["user"]["email"], "workspace-admin@local.qa")
        self.assertEqual(self.request("/api/profile/password", "PATCH", {
            "current_password": "test-admin-password-123",
            "new_password": "ChangedPassword456",
            "confirm_password": "ChangedPassword456",
        })[0], 401)

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
            "use_case_id": "UC-1", "preconditions": "Login page is available.",
            "test_data": "<Valid credentials>", "priority": "Medium",
        })
        self.assertEqual(status, 201)
        _, edited = self.request(
            f"/api/projects/{project['id']}/test-cases/{case['id']}", "PATCH", {
                "scenario": "Check successful sign in", "description": "Updated description",
                "steps": "Enter user credentials\nSubmit form",
                "expected_result": "Dashboard appears", "test_type": "Negative",
                "requirement_id": requirement["id"],
                "use_case_id": "UC-2", "preconditions": "User is logged out.",
                "test_data": "<Invalid credentials>", "priority": "High",
            }
        )
        self.assertEqual(edited["scenario"], "Check successful sign in")
        self.assertEqual(edited["description"], "Updated description")
        self.assertEqual(edited["steps"], "Enter user credentials\nSubmit form")
        self.assertEqual(edited["test_type"], "Negative")
        self.assertEqual(edited["use_case_id"], "UC-2")
        self.assertEqual(edited["preconditions"], "User is logged out.")
        self.assertEqual(edited["test_data"], "<Invalid credentials>")
        self.assertEqual(edited["priority"], "High")
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
            "use_case_id": "UC-1", "preconditions": "Login page is open.",
            "test_data": "<Valid credentials>", "priority": "High",
        })
        self.assertEqual(case["use_case_id"], "UC-1")
        self.assertEqual(case["priority"], "High")
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
        self.assertIn(b"Use Case ID", csv_bytes)
        self.assertIn(b"Preconditions", csv_bytes)
        self.assertIn(b"<Valid credentials>", csv_bytes)
        self.assertIn(b"Not Covered", csv_bytes)
        self.assertIn(b"Sign in fails", csv_bytes)
        _, defect_report = self.request(
            f"/api/projects/{project['id']}/report/bugs.csv?q=Sign"
        )
        self.assertIn(b"Defect ID,Title,Description,Linked Test Case", defect_report)
        self.assertIn(b"Sign in fails", defect_report)
        self.assertNotIn(b"Requirement ID", defect_report)
        _, testcase_report = self.request(
            f"/api/projects/{project['id']}/report/test-cases.csv?q=Valid"
        )
        self.assertIn(b"Test Case ID,Title,Scenario", testcase_report)
        self.assertIn(b"Valid sign in", testcase_report)
        self.assertNotIn(b"Sign in fails", testcase_report)
        self.assertNotIn(b"Requirement ID", testcase_report)
        _, requirement_report = self.request(
            f"/api/projects/{project['id']}/report/requirements.csv"
        )
        self.assertIn(b"Requirement ID,Title,Description", requirement_report)
        self.assertNotIn(b"Test Case ID", requirement_report)
        self.assertNotIn(b"Defect ID", requirement_report)
        saved_report = (
            self.server.storage_root / "projects" / str(project["id"]) /
            "exports" / "project-report.csv"
        )
        self.assertEqual(saved_report.read_bytes(), csv_bytes)
        self.assertTrue(
            (
                self.server.storage_root / "projects" / str(project["id"]) /
                "exports" / "Defect_Log_Report.csv"
            ).is_file()
        )

    def test_defect_delete_requires_configured_permission(self):
        project_status, project = self.request("/api/projects", "POST", {
            "name": "Permission project", "project_key": "PERMBUG", "qa_owner": "QA",
        })
        self.assertEqual(project_status, 201)
        _, defect = self.request(
            f"/api/projects/{project['id']}/bugs", "POST",
            {"title": "Protected defect"},
        )
        _, role = self.request("/api/roles", "POST", {
            "name": "Defect viewer",
            "permissions": ["projects.view", "bugs.view"],
        })
        _, user = self.request("/api/users", "POST", {
            "first_name": "Defect", "last_name": "Viewer",
            "email": "defect-viewer@example.test",
            "password": "DefectViewer123",
            "role_id": role["id"],
        })
        admin_cookie = self.auth_cookie
        login = urllib.request.Request(
            self.base + "/api/auth/login",
            data=json.dumps({
                "email": "defect-viewer@example.test",
                "password": "DefectViewer123",
            }).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(login) as response:
            viewer_cookie = response.headers["Set-Cookie"].split(";", 1)[0]

        self.auth_cookie = viewer_cookie
        self.assertEqual(
            self.request(f"/api/projects/{project['id']}/bugs/{defect['id']}", "DELETE")[0],
            403,
        )
        self.assertEqual(len(self.request(f"/api/projects/{project['id']}/bugs")[1]), 1)

        self.auth_cookie = admin_cookie
        self.request(f"/api/roles/{role['id']}", "PATCH", {
            "name": role["name"], "description": role["description"],
            "status": role["status"],
            "permissions": ["projects.view", "bugs.view", "bugs.delete"],
        })
        self.auth_cookie = viewer_cookie
        status, deleted = self.request(
            f"/api/projects/{project['id']}/bugs/{defect['id']}", "DELETE"
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(self.request(f"/api/projects/{project['id']}/bugs")[1], [])

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
            (404, {"error": "Project not found."}),
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

    def test_requirement_import_rejects_non_string_generator(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Invalid generator", "project_key": "INVALIDGEN", "qa_owner": "QA"
        })
        status, error = self.request(
            f"/api/projects/{project['id']}/requirements/import",
            "POST",
            {
                "filename": "spec.txt",
                "content": "Users can sign in.",
                "generator": {},
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            error["error"],
            "Choose Local deterministic or a configured AI provider.",
        )

    def test_requirement_import_waits_for_explicit_preview_confirmation(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Preview before save", "project_key": "PREVIEW", "qa_owner": "QA"
        })
        path = f"/api/projects/{project['id']}/requirements/import"
        payload = {
            "filename": "spec.md",
            "content": "# Sign in\nUsers can sign in with an email address.",
            "generator": "deterministic",
        }
        status, preview_response = self.raw_request(
            path,
            json.dumps(payload).encode("utf-8"),
            "application/json",
        )
        self.assertEqual(status, 200)
        self.assertTrue(preview_response["preview_required"])
        preview = preview_response["preview"]
        self.assertEqual(len(preview["requirements"]), 1)
        self.assertEqual(preview["requirements"][0]["title"], "Sign in")
        with app.database(self.db_path) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM requirements WHERE project_id=?", (project["id"],)).fetchone()[0],
                0,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM requirement_documents WHERE project_id=?",
                    (project["id"],),
                ).fetchone()[0],
                0,
            )

        status, imported = self.request(
            path,
            "POST",
            {**payload, "preview_id": preview["preview_id"]},
        )
        self.assertEqual(status, 201)
        self.assertEqual(imported["count"], 1)
        self.assertEqual(imported["created"][0]["title"], "Sign in")

    def test_requirement_import_preview_is_server_issued_and_source_bound(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Bound preview", "project_key": "BOUND", "qa_owner": "QA"
        })
        path = f"/api/projects/{project['id']}/requirements/import"
        payload = {
            "filename": "spec.md",
            "content": "# Sign in\nUsers can sign in.",
            "generator": "deterministic",
        }
        status, result = self.raw_request(
            path, json.dumps(payload).encode("utf-8"), "application/json"
        )
        self.assertEqual(status, 200)
        preview_id = result["preview"]["preview_id"]
        status, changed_source = self.request(
            path,
            "POST",
            {
                **payload,
                "content": "# Sign in\nUsers can sign in using a different flow.",
                "preview_id": preview_id,
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("source changed after preview", changed_source["error"])
        status, forged_preview = self.request(
            path,
            "POST",
            {**payload, "confirmed_preview": result["preview"]},
        )
        self.assertEqual(status, 400)
        self.assertIn("preview is no longer valid", forged_preview["error"])
        with app.database(self.db_path) as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM requirements WHERE project_id=?", (project["id"],)).fetchone()[0],
                0,
            )
        status, imported = self.request(
            path, "POST", {**payload, "preview_id": preview_id}
        )
        self.assertEqual(status, 201)
        self.assertEqual(imported["created"][0]["origin"], "extracted")
        status, cancelled_preview = self.raw_request(
            path, json.dumps(payload).encode("utf-8"), "application/json"
        )
        self.assertEqual(status, 200)
        cancelled_id = cancelled_preview["preview"]["preview_id"]
        status, deleted = self.request(
            f"/api/projects/{project['id']}/requirements/previews/{cancelled_id}",
            "DELETE",
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])

    def test_requirement_import_never_falls_back_to_the_whole_document(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "No fallback requirement", "project_key": "NOFALLBACK", "qa_owner": "QA"
        })
        status, imported = self.request(
            f"/api/projects/{project['id']}/requirements/import", "POST",
            {
                "filename": "overview.txt",
                "content": "# Product overview\nThe portal supports several account types.",
                "generator": "deterministic",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(imported["count"], 0)
        self.assertEqual(imported["total_extracted"], 0)
        status, requirements = self.request(
            f"/api/projects/{project['id']}/requirements"
        )
        self.assertEqual(status, 200)
        self.assertEqual(requirements, [])

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
            "source_section": "Users need a way to reset their password.",
        }
        generated_analysis = brd_analysis_fixture(
            summary="A password reset flow with security and validation requirements.",
            functional_groups=[{
                "name": "Account Management",
                "requirements": [{
                    "id": "FR-1.1",
                    "title": "Request password reset",
                    "statement": "The system shall allow users to request a password reset.",
                    "source_use_case": "UC-1",
                    "source_step": "Step 1",
                    "priority": "High",
                }],
            }],
            non_functional_requirements=[{
                "id": "NFR-1",
                "category": "Security",
                "statement": "The system shall validate the reset request.",
                "source_use_case": "UC-1",
                "source_step": "Step 1",
            }],
            scope={"in_scope": ["Password reset"], "out_of_scope": []},
            scope_constraints=[{
                "id": "SC-1",
                "basis": "Explicit",
                "statement": "The user must provide a registered email address.",
                "source": "UC-1, Step 1",
            }],
        )
        with app.database(self.db_path) as db:
            save_provider_settings(db, {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "api_key": "test-key",
            })
        with patch(
            "app.generate_brd_analysis",
            return_value=([generated_requirement], generated_analysis),
        ) as generate:
            status, imported = self.request(
                f"/api/projects/{project['id']}/requirements/import", "POST",
                {
                    "filename": "raw.txt",
                    "content": "Users need a way to reset their password.",
                    "generator": "google",
                },
            )
        self.assertEqual(status, 201)
        self.assertEqual(imported["generator"], "google")
        self.assertEqual(imported["count"], 1)
        self.assertEqual(imported["analysis_source"], "google")
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(imported["analysis"]["summary"], generated_analysis["summary"])
        self.assertEqual(imported["analysis"]["requirements"][0]["title"], "Reset password")
        self.assertEqual(imported["analysis"]["requirements"][0]["requirement_key"], "")
        self.assertEqual(imported["suggestions_added"], 1)
        document_id = imported["document"]["id"]
        status, saved_analysis = self.request(
            f"/api/projects/{project['id']}/requirements/documents/{document_id}/analysis"
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved_analysis["analysis"], imported["analysis"])
        status, markdown = self.request(
            f"/api/projects/{project['id']}/requirements/documents/{document_id}/analysis.md"
        )
        self.assertEqual(status, 200)
        self.assertIn(b"FR-1.1", markdown)
        self.assertIn(b"## 5. Business Rules / Constraints", markdown)
        requirement = imported["created"][0]
        self.assertEqual(requirement["origin"], "extracted")
        self.assertEqual(requirement["review_status"], "Needs review")
        self.assertTrue(requirement["source_evidence"])
        status, suggestions = self.request(
            f"/api/projects/{project['id']}/requirements/suggestions"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["status"], "Pending")
        self.assertEqual(suggestions[0]["original_content"]["title"], "Reset password")
        self.assertEqual(
            suggestions[0]["source_reference"],
            "Users need a way to reset their password.",
        )
        self.assertTrue(suggestions[0]["source_evidence"])
        status, blocked = self.request(
            f"/api/projects/{project['id']}/test-cases/generate", "POST",
            {"generator": "deterministic"},
        )
        self.assertEqual(status, 400)
        self.assertIn("mark at least one as Reviewed", blocked["error"])
        status, accepted = self.request(
            f"/api/projects/{project['id']}/requirements/suggestions/{suggestions[0]['id']}",
            "PATCH",
            {"action": "accept"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(accepted["status"], "Accepted")
        self.assertEqual(len(accepted["history"]), 1)
        accepted_requirement_id = accepted["accepted_requirement_id"]
        status, protected = self.request(
            f"/api/projects/{project['id']}/requirements/analyze", "POST", {}
        )
        self.assertEqual(status, 400)
        self.assertIn("are protected from AI overwrites", protected["error"])
        status, edited_requirement = self.request(
            f"/api/projects/{project['id']}/requirements/{accepted_requirement_id}",
            "PATCH",
            {"title": "Approved password reset", "review_status": "Reviewed"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(edited_requirement["origin"], "ai_suggestion")
        with patch(
            "app.generate_brd_analysis",
            return_value=([generated_requirement], generated_analysis),
        ):
            status, rerun = self.request(
                f"/api/projects/{project['id']}/requirements/import", "POST",
                {
                    "filename": "raw.txt",
                    "content": "Users need a way to reset their password.",
                    "generator": "google",
                },
            )
        self.assertEqual(status, 201)
        self.assertEqual(rerun["count"], 0)
        status, current_requirements = self.request(
            f"/api/projects/{project['id']}/requirements"
        )
        self.assertEqual(status, 200)
        unchanged = next(item for item in current_requirements if item["id"] == accepted_requirement_id)
        self.assertEqual(unchanged["title"], "Approved password reset")
        self.assertEqual(unchanged["origin"], "ai_suggestion")
        status, all_suggestions = self.request(
            f"/api/projects/{project['id']}/requirements/suggestions"
        )
        accepted_saved = next(item for item in all_suggestions if item["status"] == "Accepted")
        self.assertEqual(len(accepted_saved["history"]), 2)
        pending = next(item for item in all_suggestions if item["status"] == "Pending")
        status, edited_suggestion = self.request(
            f"/api/projects/{project['id']}/requirements/suggestions/{pending['id']}",
            "PATCH",
            {
                "action": "edit",
                "content": {**pending["content"], "title": "Edited proposal"},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(edited_suggestion["content"]["title"], "Edited proposal")
        self.assertEqual(len(edited_suggestion["history"]), 1)
        status, rejected = self.request(
            f"/api/projects/{project['id']}/requirements/suggestions/{pending['id']}",
            "PATCH",
            {"action": "reject"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(rejected["status"], "Rejected")
        self.assertEqual(len(rejected["history"]), 2)
        status, generated = self.request(
            f"/api/projects/{project['id']}/test-cases/generate", "POST",
            {"generator": "deterministic"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(generated["count"], 2)
        self.assertTrue(all(item["requirement_id"] == accepted_requirement_id for item in generated["created"]))
        status, cases = self.request(f"/api/projects/{project['id']}/test-cases")
        self.assertEqual(status, 200)
        self.assertTrue(all(item["requirement_origin"] == "ai_suggestion" for item in cases))
        self.assertTrue(all(item["requirement_approval_status"] == "Reviewed" for item in cases))
        self.assertTrue(all(item["source_suggestion_id"] == accepted["id"] for item in cases))
        self.assertTrue(all(item["source_requirement_key"] for item in cases))
        self.assertTrue(all(
            item["source_requirement_title"] == "Approved password reset"
            for item in cases
        ))
        self.assertIn(requirement["module_type"], {"Functional", "Non-Functional"})
        self.assertTrue(requirement["module_category"])

    def test_file_import_falls_back_to_rule_classification_when_gemini_is_unavailable(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Rule fallback", "project_key": "RULEFALL", "qa_owner": "QA"
        })
        with app.database(self.db_path) as db:
            save_provider_settings(db, {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "api_key": "test-key",
            })
        with patch(
            "app.generate_brd_analysis",
            side_effect=ValueError("Google Gemini request failed."),
        ):
            status, imported = self.request(
                f"/api/projects/{project['id']}/requirements/import", "POST",
                {
                    "filename": "security.txt",
                    "content": "Passwords must be encrypted at rest.",
                },
            )
        self.assertEqual(status, 201)
        self.assertEqual(imported["generator"], "deterministic")
        self.assertIn("Local AI analysis was unavailable", imported["analysis_warning"])
        self.assertEqual(imported["analysis_source"], "rule-based")
        self.assertIn("Use-case analysis was unavailable", imported["analysis"]["summary"])
        requirement = imported["created"][0]
        self.assertEqual(requirement["module_type"], "Non-Functional")
        self.assertEqual(requirement["module_category"], "Security")

    def test_ai_requirement_classification_updates_existing_requirements(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "AI classify", "project_key": "AICLASS", "qa_owner": "QA"
        })
        _, functional = self.request(
            f"/api/projects/{project['id']}/requirements", "POST",
            {"title": "Checkout", "description": "A user can place an order."},
        )
        _, non_functional = self.request(
            f"/api/projects/{project['id']}/requirements", "POST",
            {"title": "Checkout latency", "description": "Checkout responds quickly."},
        )
        with app.database(self.db_path) as db:
            save_provider_settings(db, {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "api_key": "test-key",
            })
        with patch("app.classify_requirements", return_value=[
            {
                "requirement_id": functional["id"],
                "module_type": "Functional",
                "module_category": "Functional",
            },
            {
                "requirement_id": non_functional["id"],
                "module_type": "Non-Functional",
                "module_category": "Performance",
            },
        ]) as classify:
            status, result = self.request(
                f"/api/projects/{project['id']}/requirements/analyze", "POST", {}
            )
        self.assertEqual(status, 200)
        self.assertEqual(result["updated"], 2)
        self.assertEqual(result["functional"], 1)
        self.assertEqual(result["non_functional"], 1)
        self.assertEqual(classify.call_args.args[2][0]["id"], functional["id"])
        _, requirements = self.request(f"/api/projects/{project['id']}/requirements")
        by_id = {item["id"]: item for item in requirements}
        self.assertEqual(by_id[non_functional["id"]]["module_category"], "Performance")

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
        self.assertEqual(remaining_case["requirement_key"], requirement["requirement_key"])
        self.assertEqual(remaining_case["source_requirement_title"], requirement["title"])

    def test_only_text_import_and_execution_status_validation(self):
        _, project = self.request("/api/projects", "POST", {"name": "Only text", "project_key": "TEXT", "qa_owner": "QA"})
        status, error = self.request(f"/api/projects/{project['id']}/requirements/import", "POST", {
            "filename": "application.zip", "content": "not actually a zip"
        })
        self.assertEqual(status, 400)
        self.assertIn("Only PDF, DOCX, CSV, TXT, and Markdown", error["error"])
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
        stored_path = Path(self.server.storage_root) / "projects" / str(project["id"]) / "requirements"
        existing_files = set(stored_path.iterdir()) if stored_path.exists() else set()
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
        self.assertEqual(len(list(stored_path.iterdir())), len(existing_files) + 1)

        _, documents = self.request(
            f"/api/projects/{project['id']}/requirements/documents"
        )
        self.assertEqual(documents[0]["filename"], "login.docx")
        self.assertTrue(documents[0]["has_analysis"])
        status, saved_analysis = self.request(
            f"/api/projects/{project['id']}/requirements/documents/{documents[0]['id']}/analysis"
        )
        self.assertEqual(status, 200)
        self.assertIn("Use-case analysis was unavailable", saved_analysis["analysis"]["summary"])
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
        self.assertEqual(len(list(stored_path.iterdir())), len(existing_files) + 1)

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
        with app.database(self.db_path) as db:
            save_provider_settings(db, {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "api_key": "test-key",
            })
        with patch("app.validate_requirement_coverage", return_value=report) as validate:
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
        self.assertEqual(status, 400)
        self.assertIn("mark at least one as Reviewed", error["error"])
        status, error = self.request(
            f"/api/projects/{project['id']}/test-cases/generate",
            "POST",
            {"requirement_ids": [requirement["id"]]},
        )
        self.assertEqual(status, 400)
        self.assertIn("marked Reviewed", error["error"])
        status, error = self.request(
            f"/api/projects/{project['id']}/requirements/{requirement['id']}",
            "PATCH",
            {"review_status": "Reviewed"},
        )
        self.assertEqual(status, 200)
        status, error = self.request(
            f"/api/projects/{project['id']}/test-cases/generate", "POST", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(error["count"], 4)
        self.assertEqual(error["coverage"]["reviewed_requirements"], 1)
        self.assertEqual(error["coverage"]["covered_requirements"], 1)
        self.assertEqual(error["coverage"]["uncovered_requirements"], [])
        self.assertTrue(all(case["requirement_id"] == requirement["id"] for case in error["created"]))
        self.assertTrue(all(case["priority"] == "Medium" for case in error["created"]))
        self.assertTrue(all("preconditions" in case and "test_data" in case for case in error["created"]))
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
        _, uncovered_requirement = self.request(
            f"/api/projects/{project['id']}/requirements",
            "POST",
            {"title": "Account recovery"},
        )
        self.request(
            f"/api/projects/{project['id']}/requirements/{uncovered_requirement['id']}",
            "PATCH",
            {"review_status": "Reviewed"},
        )
        with app.database(self.db_path) as db:
            save_provider_settings(db, {
                "provider": "google",
                "model": "gemini-2.5-flash",
            })
        status, result = self.request(
            f"/api/projects/{project['id']}/test-cases/generate",
            "POST",
            {"generator": "google"},
        )
        self.assertEqual(status, 400)
        self.assertIn("Add the API key", result["error"])
        with app.database(self.db_path) as db:
            save_provider_settings(db, {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "api_key": "test-key",
            })
        with patch(
            "app.generate_test_cases",
            side_effect=ValueError("Google Gemini request failed."),
        ):
            status, result = self.request(
                f"/api/projects/{project['id']}/test-cases/generate",
                "POST",
                {"generator": "google"},
            )
        self.assertEqual(status, 400)
        self.assertIn("Google Gemini request failed", result["error"])
        with patch("app.generate_test_cases", return_value=[{
            "requirement_id": requirement["id"],
            "scenario": "AI draft case",
            "description": "Generated from the requirement.",
            "steps": "1. Follow the login workflow.",
            "expected_result": "The documented result is shown.",
            "test_type": "Functional",
            "generation_key": "google-test-key",
        }]):
            status, result = self.request(
                f"/api/projects/{project['id']}/test-cases/generate",
                "POST",
                {"generator": "google"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(result["generator"], "google")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["created"][0]["generation_key"], "google-test-key")
        self.assertEqual(result["coverage"]["reviewed_requirements"], 2)
        self.assertEqual(result["coverage"]["covered_requirements"], 1)
        self.assertEqual(
            result["coverage"]["uncovered_requirements"][0]["requirement_key"],
            uncovered_requirement["requirement_key"],
        )
        _, isolated = self.request(f"/api/projects/{other['id']}/test-cases")
        self.assertEqual(isolated, [])
        status, error = self.request(
            f"/api/projects/{other['id']}/test-cases/generate",
            "POST",
            {"requirement_ids": [requirement["id"]]},
        )
        self.assertEqual(status, 400)
        self.assertIn("do not belong", error["error"])

    def test_ai_assisted_generation_works_without_requirements_and_preserves_case_metadata(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Ad hoc generation", "project_key": "ADHOC", "qa_owner": "QA"
        })
        path = f"/api/projects/{project['id']}/test-cases/generate"
        payload = {
            "mode": "ai_assisted",
            "source_text": "A user can submit a valid registration form.",
            "categories": ["Functional", "Non-Functional", "Sanity", "Smoke"],
            "generator": "local",
        }
        status, error = self.request(path, "POST", payload)
        self.assertEqual(status, 400)
        self.assertIn("model-based provider", error["error"])
        with app.database(self.db_path) as db:
            save_provider_settings(db, {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "api_key": "test-key",
            })
        case = {
            "title": "Submit a valid registration",
            "scenario": "A user submits a valid registration form",
            "description": "AI-assisted draft from user-provided content.",
            "steps": "1. Enter valid details.\n2. Submit the form.",
            "expected_result": "The form is submitted.",
            "preconditions": "",
            "test_data": "",
            "priority": "Not specified",
            "test_type": "Functional",
            "test_category": "Functional",
            "classification": "Positive",
            "assumptions": "Assumption: the registration page is available.",
            "source_reference": "A user can submit a valid registration form.",
            "generation_key": "ad-hoc-generation-key",
        }
        analysis = {
            "explicit_facts": ["A user can submit a valid registration form."],
            "assumptions": ["The registration page is available."],
            "coverage_gaps": ["Invalid input behavior is unspecified."],
            "duplicates": [],
            "unclear_or_conflicting": [],
            "clarification_questions": [],
            "additional_recommendations": [],
            "category_assessment": [
                {"category": category, "applicable": category == "Functional", "reason": "Not supported by input."}
                for category in payload["categories"]
            ],
        }
        with patch("app.generate_ad_hoc_test_cases", return_value={
            "test_cases": [case], "analysis": analysis,
        }) as generate:
            status, result = self.request(path, "POST", {**payload, "generator": "google"})
        self.assertEqual(status, 200)
        generate.assert_called_once()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["analysis"]["coverage_gaps"], analysis["coverage_gaps"])
        created = result["created"][0]
        self.assertIsNone(created["requirement_id"])
        self.assertEqual(created["title"], case["title"])
        self.assertEqual(created["classification"], "Positive")
        self.assertEqual(created["source_reference"], case["source_reference"])

        status, updated = self.request(
            f"/api/projects/{project['id']}/test-cases/{created['id']}",
            "PATCH",
            {
                "title": "Updated registration test",
                "test_category": "Sanity",
                "classification": "Negative",
                "assumptions": "Assumption: an invalid email is rejected.",
            },
        )
        self.assertEqual(status, 200)
        _, listed = self.request(f"/api/projects/{project['id']}/test-cases")
        self.assertEqual(listed[0]["title"], "Updated registration test")
        self.assertEqual(listed[0]["test_category"], "Sanity")
        self.assertEqual(listed[0]["classification"], "Negative")
        _, report = self.request(f"/api/projects/{project['id']}/report/csv")
        self.assertIn(b"Test Category", report)
        self.assertIn(b"Source Reference", report)
        self.assertIn(b"Updated registration test", report)

    def test_ai_assisted_generation_requires_input_and_returns_clarification_without_cases(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Clarification", "project_key": "CLARIFY", "qa_owner": "QA"
        })
        path = f"/api/projects/{project['id']}/test-cases/generate"
        status, error = self.request(path, "POST", {
            "mode": "ai_assisted", "source_text": "   ", "generator": "local",
        })
        self.assertEqual(status, 400)
        self.assertIn("required", error["error"])
        with app.database(self.db_path) as db:
            save_provider_settings(db, {
                "provider": "google",
                "model": "gemini-2.5-flash",
                "api_key": "test-key",
            })
        clarification = {
            "explicit_facts": [],
            "assumptions": [],
            "coverage_gaps": [],
            "duplicates": [],
            "unclear_or_conflicting": [],
            "clarification_questions": ["What outcome should follow submission?"],
            "additional_recommendations": [],
            "category_assessment": [{
                "category": "Functional",
                "applicable": False,
                "reason": "No workflow was supplied.",
            }],
        }
        with patch("app.generate_ad_hoc_test_cases", return_value={
            "test_cases": [], "analysis": clarification,
        }):
            status, result = self.request(path, "POST", {
                "mode": "ai_assisted",
                "source_text": "Something should happen after submission.",
                "categories": ["Functional"],
                "generator": "google",
            })
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["count"], 0)
        self.assertTrue(result["analysis"]["clarification_questions"])
        _, listed = self.request(f"/api/projects/{project['id']}/test-cases")
        self.assertEqual(listed, [])

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

    def test_app_analysis_is_coming_soon_and_does_not_create_jobs(self):
        _, project = self.request("/api/projects", "POST", {
            "name": "Analysis project", "project_key": "ANALYSIS", "qa_owner": "QA"
        })
        status, error = self.request(
            f"/api/projects/{project['id']}/analyses",
            "POST",
            {"input_path": "unused"},
        )
        self.assertEqual(status, 503)
        self.assertEqual(error["error"], "App Analysis is coming soon.")
        with app.database(self.server.db_path) as db:
            cursor = db.execute(
                """INSERT INTO application_analyses
                (project_id,input_path,status,summary_json,created_at,completed_at)
                VALUES(?,?,'completed',?,?,?)""",
                (
                    project["id"],
                    "C:/legacy-app",
                    json.dumps({"statistics": {"modules": 2}}),
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:01:00+00:00",
                ),
            )
            legacy_analysis_id = cursor.lastrowid
        _, history = self.request(f"/api/projects/{project['id']}/analyses")
        self.assertEqual([item["id"] for item in history], [legacy_analysis_id])
        self.assertEqual(history[0]["summary"]["statistics"]["modules"], 2)

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
