"""Workspace trial lifecycle and access checks."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone


try:
    TRIAL_DURATION_DAYS = int(os.environ.get("QA_TRIAL_DURATION_DAYS", "14"))
except ValueError as exc:
    raise RuntimeError("QA_TRIAL_DURATION_DAYS must be an integer from 1 to 365.") from exc
if not 1 <= TRIAL_DURATION_DAYS <= 365:
    raise RuntimeError("QA_TRIAL_DURATION_DAYS must be an integer from 1 to 365.")
TRIAL_PLAN = "free_trial"
TRIAL_EXPIRY_REASON = "The 14-day free trial has expired."
PERMANENT_WORKSPACE_ID = 1


def utc_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Trial timestamps must be timezone-aware.")
    return current.astimezone(timezone.utc)


def utc_timestamp(now: datetime | None = None) -> str:
    return utc_now(now).isoformat(timespec="seconds")


def trial_end(started_at: datetime | None = None) -> str:
    return utc_timestamp(utc_now(started_at) + timedelta(days=TRIAL_DURATION_DAYS))


def workspace_access(db, tenant_id: int, now: datetime | None = None) -> dict:
    row = db.execute(
        """SELECT t.id,t.name,t.slug,t.status,t.suspended_at,t.suspension_reason,
        s.status subscription_status,s.plan,s.trial_started_at,s.trial_ends_at
        FROM tenants t LEFT JOIN tenant_subscriptions s ON s.tenant_id=t.id
        WHERE t.id=?""",
        (tenant_id,),
    ).fetchone()
    if row is None:
        return {
            "workspace": None,
            "subscription": None,
            "access_allowed": False,
            "access_code": "WORKSPACE_ACCESS_DENIED",
        }

    workspace = {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "status": row["status"],
        "suspended_at": row["suspended_at"],
        "suspension_reason": row["suspension_reason"],
    }
    if row["id"] == PERMANENT_WORKSPACE_ID:
        workspace.update({
            "status": "Active",
            "suspended_at": None,
            "suspension_reason": "",
        })
        return {
            "workspace": workspace,
            "subscription": {
                "status": "active",
                "plan": "legacy",
                "trial_started_at": None,
                "trial_ends_at": None,
                "days_remaining": None,
                "trial_duration_days": None,
            },
            "access_allowed": True,
            "access_code": None,
        }

    subscription = None
    access_allowed = False
    access_code = "WORKSPACE_SUSPENDED"
    if row["subscription_status"] is not None:
        subscription = {
            "status": row["subscription_status"],
            "plan": row["plan"],
            "trial_started_at": row["trial_started_at"],
            "trial_ends_at": row["trial_ends_at"],
            "days_remaining": None,
            "trial_duration_days": TRIAL_DURATION_DAYS if row["plan"] == TRIAL_PLAN else None,
        }
        if row["plan"] == TRIAL_PLAN and row["trial_ends_at"]:
            ends_at = datetime.fromisoformat(row["trial_ends_at"])
            if ends_at.tzinfo is None:
                raise ValueError("Stored trial expiry timestamps must include a UTC offset.")
            remaining_seconds = (ends_at.astimezone(timezone.utc) - utc_now(now)).total_seconds()
            subscription["days_remaining"] = max(0, math.ceil(remaining_seconds / 86400))
            if row["subscription_status"] == "expired" or remaining_seconds <= 0:
                access_code = "TRIAL_EXPIRED"
            elif row["subscription_status"] == "trialing" and row["status"] == "Active":
                access_allowed = True
                access_code = None
        elif row["subscription_status"] == "active" and row["status"] == "Active":
            access_allowed = True
            access_code = None
        elif row["subscription_status"] == "expired" and row["plan"] == TRIAL_PLAN:
            access_code = "TRIAL_EXPIRED"

    if row["status"] != "Active":
        access_allowed = False
        expired_trial = (
            row["plan"] == TRIAL_PLAN
            and row["trial_ends_at"]
            and (
                row["subscription_status"] == "expired"
                or datetime.fromisoformat(row["trial_ends_at"]).astimezone(timezone.utc)
                <= utc_now(now)
            )
        )
        if not expired_trial:
            access_code = "WORKSPACE_SUSPENDED"

    return {
        "workspace": workspace,
        "subscription": subscription,
        "access_allowed": access_allowed,
        "access_code": access_code,
    }


def expire_trials(db, now: datetime | None = None, batch_size: int = 100) -> int:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 1000:
        raise ValueError("Batch size must be an integer between 1 and 1000.")
    current = utc_timestamp(now)
    candidates = db.execute(
        """SELECT id,tenant_id FROM tenant_subscriptions
        WHERE tenant_id<>? AND plan=? AND status='trialing' AND trial_ends_at<=?
        ORDER BY trial_ends_at,id LIMIT ?""",
        (PERMANENT_WORKSPACE_ID, TRIAL_PLAN, current, batch_size),
    ).fetchall()
    expired_count = 0
    for candidate in candidates:
        updated = db.execute(
            """UPDATE tenant_subscriptions SET status='expired',updated_at=?
            WHERE id=? AND plan=? AND status='trialing' AND trial_ends_at<=?""",
            (current, candidate["id"], TRIAL_PLAN, current),
        )
        if updated.rowcount != 1:
            continue
        db.execute(
            """UPDATE tenants SET status='Inactive',suspended_at=?,suspension_reason=?,updated_at=?
            WHERE id=?""",
            (current, TRIAL_EXPIRY_REASON, current, candidate["tenant_id"]),
        )
        db.execute(
            """INSERT INTO audit_events
            (actor_user_id,actor_label,action,target_type,target_id,tenant_id,details_json,created_at)
            VALUES(NULL,'Trial expiry service','workspace.suspended','workspace',?,?,?,?)""",
            (
                str(candidate["tenant_id"]),
                candidate["tenant_id"],
                json.dumps(
                    {
                        "reason": TRIAL_EXPIRY_REASON,
                        "subscription_id": candidate["id"],
                    },
                    separators=(",", ":"),
                ),
                current,
            ),
        )
        expired_count += 1
    return expired_count


def reactivate_trial(db, tenant_id: int, now: datetime | None = None) -> dict:
    current = utc_now(now)
    started_at = utc_timestamp(current)
    ends_at = trial_end(current)
    subscription = db.execute(
        """SELECT id,status,plan,trial_started_at,trial_ends_at
        FROM tenant_subscriptions WHERE tenant_id=?""",
        (tenant_id,),
    ).fetchone()
    if subscription is None:
        raise ValueError("Workspace subscription was not found.")
    if subscription["plan"] != TRIAL_PLAN:
        raise ValueError("Only free-trial subscriptions can be reactivated with a trial extension.")

    db.execute(
        """UPDATE tenant_subscriptions SET status='trialing',trial_started_at=?,
        trial_ends_at=?,updated_at=? WHERE tenant_id=?""",
        (started_at, ends_at, started_at, tenant_id),
    )
    db.execute(
        """UPDATE tenants SET status='Active',suspended_at=NULL,suspension_reason='',
        updated_at=? WHERE id=?""",
        (started_at, tenant_id),
    )
    db.execute(
        """INSERT INTO audit_events
        (actor_user_id,actor_label,action,target_type,target_id,tenant_id,details_json,created_at)
        VALUES(NULL,'System Global Admin','workspace.trial_reactivated','workspace',?,?,?,?)""",
        (
            str(tenant_id),
            tenant_id,
            json.dumps(
                {"previous_status": subscription["status"], "trial_ends_at": ends_at},
                separators=(",", ":"),
            ),
            started_at,
        ),
    )
    return {
        "status": "trialing",
        "plan": TRIAL_PLAN,
        "trial_started_at": started_at,
        "trial_ends_at": ends_at,
        "days_remaining": TRIAL_DURATION_DAYS,
        "trial_duration_days": TRIAL_DURATION_DAYS,
    }
