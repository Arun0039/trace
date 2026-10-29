"""Scheduled command for suspending workspaces with expired free trials."""

from __future__ import annotations

import argparse

from app import DB_PATH, database, initialize
from qa_platform.core.trials import expire_trials


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    initialize(DB_PATH)
    with database(DB_PATH) as db:
        expired = expire_trials(db, batch_size=args.batch_size)
    print(f"Suspended {expired} expired trial workspace(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
