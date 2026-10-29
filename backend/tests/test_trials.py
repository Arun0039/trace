import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from qa_platform.core.trials import (
    PERMANENT_WORKSPACE_ID,
    TRIAL_EXPIRY_REASON,
    TRIAL_PLAN,
    expire_trials,
    reactivate_trial,
    workspace_access,
)


class TrialLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE tenants (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT NOT NULL,
                status TEXT NOT NULL,
                suspended_at TEXT,
                suspension_reason TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE tenant_subscriptions (
                id INTEGER PRIMARY KEY,
                tenant_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL,
                plan TEXT NOT NULL,
                trial_started_at TEXT,
                trial_ends_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE audit_events (
                id INTEGER PRIMARY KEY,
                actor_user_id INTEGER,
                actor_label TEXT,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                tenant_id INTEGER,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE workspace_data (value TEXT NOT NULL);
            """
        )
        self.started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.started = self.started_at.isoformat(timespec="seconds")
        self.ends = (self.started_at + timedelta(days=14)).isoformat(timespec="seconds")
        self.db.execute(
            """INSERT INTO tenants(id,name,slug,status,updated_at)
            VALUES(1,'Workspace 001','workspace-001','Active',?)""",
            (self.started,),
        )
        self.db.execute(
            """INSERT INTO tenant_subscriptions
            (id,tenant_id,status,plan,trial_started_at,trial_ends_at,created_at,updated_at)
            VALUES(1,1,'active','legacy',NULL,NULL,?,?)""",
            (self.started, self.started),
        )
        self.db.execute(
            """INSERT INTO tenants(id,name,slug,status,updated_at)
            VALUES(2,'Trial Workspace','trial-workspace','Active',?)""",
            (self.started,),
        )
        self.db.execute(
            """INSERT INTO tenant_subscriptions
            (id,tenant_id,status,plan,trial_started_at,trial_ends_at,created_at,updated_at)
            VALUES(2,2,'trialing',?,?,?,?,?)""",
            (TRIAL_PLAN, self.started, self.ends, self.started, self.started),
        )
        self.db.execute("INSERT INTO workspace_data(value) VALUES('preserve me')")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_expiry_is_an_exclusive_boundary(self):
        just_before = datetime.fromisoformat(self.ends) - timedelta(microseconds=1)
        self.assertTrue(workspace_access(self.db, 2, just_before)["access_allowed"])

        at_expiry = workspace_access(self.db, 2, datetime.fromisoformat(self.ends))
        self.assertFalse(at_expiry["access_allowed"])
        self.assertEqual(at_expiry["access_code"], "TRIAL_EXPIRED")
        self.assertEqual(at_expiry["subscription"]["days_remaining"], 0)

        after_expiry = datetime.fromisoformat(self.ends) + timedelta(seconds=1)
        self.assertEqual(workspace_access(self.db, 2, after_expiry)["access_code"], "TRIAL_EXPIRED")

    def test_expiry_job_suspends_once_and_preserves_workspace_data(self):
        expired_at = datetime.fromisoformat(self.ends)
        with self.db:
            self.assertEqual(expire_trials(self.db, expired_at), 1)
        with self.db:
            self.assertEqual(expire_trials(self.db, expired_at), 0)

        tenant = self.db.execute(
            "SELECT status,suspended_at,suspension_reason FROM tenants WHERE id=2"
        ).fetchone()
        subscription = self.db.execute(
            "SELECT status FROM tenant_subscriptions WHERE tenant_id=2"
        ).fetchone()
        self.assertEqual(tenant["status"], "Inactive")
        self.assertEqual(tenant["suspension_reason"], TRIAL_EXPIRY_REASON)
        self.assertEqual(subscription["status"], "expired")
        self.assertEqual(
            self.db.execute("SELECT value FROM workspace_data").fetchone()["value"],
            "preserve me",
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='workspace.suspended'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            workspace_access(self.db, 2, expired_at)["access_code"], "TRIAL_EXPIRED"
        )

    def test_workspace_001_is_always_active_and_never_trial_expired(self):
        expired_at = datetime.fromisoformat(self.ends)
        with self.db:
            self.db.execute(
                """UPDATE tenants SET status='Inactive',suspended_at=?,
                suspension_reason=? WHERE id=?""",
                (self.started, TRIAL_EXPIRY_REASON, PERMANENT_WORKSPACE_ID),
            )
            self.db.execute(
                """UPDATE tenant_subscriptions SET status='trialing',plan=?,
                trial_started_at=?,trial_ends_at=? WHERE tenant_id=?""",
                (TRIAL_PLAN, self.started, self.ends, PERMANENT_WORKSPACE_ID),
            )
            access = workspace_access(self.db, PERMANENT_WORKSPACE_ID, expired_at)
            self.assertTrue(access["access_allowed"])
            self.assertIsNone(access["access_code"])
            self.assertEqual(access["workspace"]["status"], "Active")
            self.assertEqual(access["subscription"]["plan"], "legacy")
            self.assertEqual(expire_trials(self.db, expired_at), 1)

        permanent_subscription = self.db.execute(
            "SELECT status,plan FROM tenant_subscriptions WHERE tenant_id=?",
            (PERMANENT_WORKSPACE_ID,),
        ).fetchone()
        self.assertEqual(permanent_subscription["status"], "trialing")
        self.assertEqual(permanent_subscription["plan"], TRIAL_PLAN)

    def test_reactivated_trial_is_not_expired_by_stale_expiry_run(self):
        extended_at = datetime(2026, 1, 20, tzinfo=timezone.utc)
        with self.db:
            subscription = reactivate_trial(self.db, 2, extended_at)
        self.assertEqual(subscription["status"], "trialing")
        self.assertEqual(subscription["trial_started_at"], extended_at.isoformat(timespec="seconds"))
        self.assertEqual(
            expire_trials(self.db, datetime.fromisoformat(self.ends) + timedelta(days=1)),
            0,
        )
        self.assertTrue(
            workspace_access(self.db, 2, extended_at + timedelta(days=1))["access_allowed"]
        )
        self.assertEqual(
            self.db.execute("SELECT status FROM tenants WHERE id=2").fetchone()["status"],
            "Active",
        )

    def test_active_paid_subscription_is_not_expired_as_a_trial(self):
        with self.db:
            self.db.execute(
                """UPDATE tenant_subscriptions SET status='active',plan='paid',
                trial_ends_at=?,updated_at=? WHERE tenant_id=2""",
                (self.started, self.started),
            )
        expired_at = self.started_at + timedelta(days=14)
        self.assertTrue(workspace_access(self.db, 2, expired_at)["access_allowed"])
        with self.db:
            self.assertEqual(expire_trials(self.db, expired_at), 0)


if __name__ == "__main__":
    unittest.main()
