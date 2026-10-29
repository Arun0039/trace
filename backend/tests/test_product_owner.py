import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import app
from qa_platform.core.product_owner import downgrade, upgrade


class ProductOwnerMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "product-owner.sqlite3"
        app.initialize(self.db_path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_existing_tenant_receives_distinct_stable_public_identifiers(self):
        with app.database(self.db_path) as db:
            row = db.execute(
                """SELECT public_tenant_id,public_workspace_id FROM tenants
                WHERE id=1"""
            ).fetchone()
            migration = db.execute(
                "SELECT migration_id FROM schema_migrations"
            ).fetchone()
            self.assertTrue(row["public_tenant_id"])
            self.assertTrue(row["public_workspace_id"])
            self.assertNotEqual(row["public_tenant_id"], row["public_workspace_id"])
            self.assertEqual(migration["migration_id"], "0001_product_owner_dashboard")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "UPDATE tenants SET public_tenant_id='changed' WHERE id=1"
                )
        app.initialize(self.db_path)
        with app.database(self.db_path) as db:
            repeated = db.execute(
                """SELECT public_tenant_id,public_workspace_id FROM tenants
                WHERE id=1"""
            ).fetchone()
        self.assertEqual(repeated["public_tenant_id"], row["public_tenant_id"])
        self.assertEqual(repeated["public_workspace_id"], row["public_workspace_id"])

    def test_downgrade_preserves_customer_tenant_and_upgrade_is_repeatable(self):
        with app.database(self.db_path) as db:
            db.execute(
                """INSERT INTO tenants
                (name,slug,status,created_at,updated_at,public_tenant_id,public_workspace_id)
                VALUES('Preserved customer','preserved-customer','Active','now','now',?,?)""",
                ("tenant-public-id", "workspace-public-id"),
            )
        with app.database(self.db_path) as db:
            downgrade(db)
            self.assertEqual(
                db.execute(
                    "SELECT name FROM tenants WHERE slug='preserved-customer'"
                ).fetchone()["name"],
                "Preserved customer",
            )
            upgrade(db)
            upgrade(db)
            self.assertEqual(
                db.execute("SELECT COUNT(*) count FROM plans").fetchone()["count"],
                4,
            )


class ProductOwnerApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "api.sqlite3"
        self.environment = patch.dict(
            os.environ,
            {"QA_ADMIN_PASSWORD": "LocalAdminPassword2026", "QA_ALLOW_PUBLIC_AUTH": "true"},
        )
        self.environment.start()
        self.server = app.create_server(
            host="127.0.0.1", port=0, db_path=self.db_path
        )
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.environment.stop()
        self.temporary.cleanup()

    def request(self, path, method="GET", body=None, cookie=None):
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(
            self.base_url + path, data=payload, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read()), response.headers
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read()), error.headers

    def test_dashboard_requires_system_global_admin_not_workspace_user(self):
        status, _, _ = self.request("/api/product-owner/dashboard")
        self.assertEqual(status, 401)

        signup_status, signup, signup_headers = self.request(
            "/api/auth/signup",
            "POST",
            {
                "first_name": "Workspace",
                "last_name": "Owner",
                "email": "owner@example.test",
                "workspace_name": "Owner Workspace",
                "password": "WorkspacePassword2026",
                "confirm_password": "WorkspacePassword2026",
            },
        )
        self.assertEqual(signup_status, 201)
        user_cookie = signup_headers["Set-Cookie"].split(";", 1)[0]
        status, _, _ = self.request(
            "/api/product-owner/dashboard", cookie=user_cookie
        )
        self.assertEqual(status, 403)
        self.assertEqual(signup["user"]["public_user_id"], self.read_user_public_id())

    def test_bootstrap_admin_can_assign_plan_and_action_is_audited(self):
        _, signup, _ = self.request(
            "/api/auth/signup",
            "POST",
            {
                "first_name": "Plan",
                "last_name": "Owner",
                "email": "plan-owner@example.test",
                "workspace_name": "Plan Workspace",
                "password": "WorkspacePassword2026",
                "confirm_password": "WorkspacePassword2026",
            },
        )
        tenant_id = signup["workspace"]["id"]
        with app.database(self.db_path) as db:
            ids = db.execute(
                """SELECT u.public_user_id,t.public_tenant_id,t.public_workspace_id
                FROM users u JOIN tenants t ON t.owner_user_id=u.id WHERE u.email=?""",
                ("plan-owner@example.test",),
            ).fetchone()
        self.assertTrue(ids["public_user_id"])
        self.assertEqual(len({
            ids["public_user_id"], ids["public_tenant_id"], ids["public_workspace_id"],
        }), 3)
        login_status, _, headers = self.request(
            "/api/auth/login",
            "POST",
            {"password": "LocalAdminPassword2026"},
        )
        self.assertEqual(login_status, 200)
        admin_cookie = headers["Set-Cookie"].split(";", 1)[0]
        dashboard_status, dashboard, _ = self.request(
            "/api/product-owner/dashboard", cookie=admin_cookie
        )
        self.assertEqual(dashboard_status, 200)
        self.assertGreaterEqual(dashboard["tenants"], 2)

        action_status, result, _ = self.request(
            f"/api/product-owner/tenants/{tenant_id}/plan",
            "POST",
            {
                "confirmed": True,
                "reason": "Manual plan assignment for acceptance test",
                "plan": "starter",
            },
            admin_cookie,
        )
        self.assertEqual(action_status, 200)
        self.assertTrue(result["assigned"])
        with app.database(self.db_path) as db:
            subscription = db.execute(
                "SELECT status,plan FROM tenant_subscriptions WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
            audit = db.execute(
                """SELECT action,reason,outcome FROM admin_audit_logs
                WHERE target_tenant_id=? ORDER BY id DESC LIMIT 1""",
                (tenant_id,),
            ).fetchone()
        self.assertEqual(subscription["status"], "active")
        self.assertEqual(subscription["plan"], "starter")
        self.assertEqual(audit["action"], "plan.assigned")
        self.assertIn("acceptance test", audit["reason"])
        self.assertEqual(audit["outcome"], "success")

        for path in (
            "/api/product-owner/tenants?page=1&page_size=10&sort=email&direction=asc",
            f"/api/product-owner/tenants/{tenant_id}",
            f"/api/product-owner/usage?date_from=2026-10-01&date_to=2026-10-31&tenant_id={tenant_id}",
            "/api/product-owner/errors?page=1&page_size=10",
            "/api/product-owner/audit?page=1&page_size=10",
        ):
            status, _, _ = self.request(path, cookie=admin_cookie)
            self.assertEqual(status, 200, path)

        rejected_status, _, _ = self.request(
            f"/api/product-owner/tenants/{tenant_id}/suspend",
            "POST",
            {"reason": "Missing explicit confirmation"},
            admin_cookie,
        )
        self.assertEqual(rejected_status, 400)
        suspend_status, _, _ = self.request(
            f"/api/product-owner/tenants/{tenant_id}/suspend",
            "POST",
            {
                "confirmed": True,
                "reason": "Manual suspension acceptance test",
            },
            admin_cookie,
        )
        self.assertEqual(suspend_status, 200)
        with app.database(self.db_path) as db:
            self.assertEqual(
                db.execute(
                    "SELECT status FROM tenants WHERE id=?", (tenant_id,)
                ).fetchone()["status"],
                "Inactive",
            )
        reactivate_status, _, _ = self.request(
            f"/api/product-owner/tenants/{tenant_id}/reactivate",
            "POST",
            {
                "confirmed": True,
                "reason": "Restore approved starter workspace",
            },
            admin_cookie,
        )
        self.assertEqual(reactivate_status, 200)
        delete_status, deletion, _ = self.request(
            f"/api/product-owner/tenants/{tenant_id}/storage/delete",
            "POST",
            {
                "item_ids": ["not-an-inventoried-item"],
                "confirmation": f"DELETE {ids['public_tenant_id']}",
                "reason": "Test cross-tenant cleanup rejection",
            },
            admin_cookie,
        )
        self.assertEqual(delete_status, 200)
        self.assertEqual(deletion["deleted_count"], 0)
        self.assertEqual(len(deletion["skipped"]), 1)

    def test_usage_active_workspace_metrics_follow_date_range(self):
        _, signup, _ = self.request(
            "/api/auth/signup",
            "POST",
            {
                "first_name": "Usage",
                "last_name": "Owner",
                "email": "usage-owner@example.test",
                "workspace_name": "Usage Workspace",
                "password": "WorkspacePassword2026",
                "confirm_password": "WorkspacePassword2026",
            },
        )
        tenant_id = signup["workspace"]["id"]
        with app.database(self.db_path) as db:
            db.executemany(
                """INSERT INTO usage_events
                (tenant_id,event_type,created_at,metadata_json)
                VALUES(?,'login',?,'{}')""",
                [
                    (1, "2025-03-15T12:00:00+00:00"),
                    (tenant_id, "2025-03-13T12:00:00+00:00"),
                    (1, "2025-03-09T12:00:00+00:00"),
                    (tenant_id, "2025-03-16T12:00:00+00:00"),
                ],
            )
        _, _, headers = self.request(
            "/api/auth/login",
            "POST",
            {"password": "LocalAdminPassword2026"},
        )
        admin_cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, usage, _ = self.request(
            "/api/product-owner/usage?date_from=2025-03-10&date_to=2025-03-15",
            cookie=admin_cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(usage["daily_active_workspaces"], 1)
        self.assertEqual(usage["monthly_active_workspaces"], 2)

        invalid_status, _, _ = self.request(
            "/api/product-owner/usage?date_from=2025-03-16&date_to=2025-03-15",
            cookie=admin_cookie,
        )
        self.assertEqual(invalid_status, 400)

    def read_user_public_id(self):
        with app.database(self.db_path) as db:
            return db.execute(
                "SELECT public_user_id FROM users WHERE email='owner@example.test'"
            ).fetchone()["public_user_id"]


if __name__ == "__main__":
    unittest.main()
