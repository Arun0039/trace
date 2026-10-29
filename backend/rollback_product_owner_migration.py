"""Explicit rollback for Product Owner migration 0001."""

from __future__ import annotations

import argparse

from app import DB_PATH, database
from qa_platform.core.product_owner import MIGRATION_ID, downgrade


CONFIRMATION = "remove-product-owner-dashboard-data"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm",
        required=True,
        help=f"Must be exactly {CONFIRMATION!r}; back up the database first.",
    )
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        parser.error(f"Confirmation must be exactly {CONFIRMATION!r}.")
    with database(DB_PATH) as db:
        applied = db.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if not applied:
            parser.error(f"Migration {MIGRATION_ID} is not applied.")
        downgrade(db)
    print(
        "Product Owner schema removed. Existing users, tenants, workspaces, "
        "and customer records were preserved; Product Owner usage and audit "
        "records were removed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
