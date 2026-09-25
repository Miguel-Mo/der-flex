"""Apply the idempotent PostgreSQL schema migrations for DER Flex."""

from __future__ import annotations

import argparse
import os

from der_flex.reservations import PostgresReservationBackend


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url",
        default=os.getenv("DER_FLEX_DATABASE_URL"),
        help="PostgreSQL DSN (defaults to DER_FLEX_DATABASE_URL)",
    )
    arguments = parser.parse_args()
    if not arguments.database_url:
        parser.error("--database-url or DER_FLEX_DATABASE_URL is required")
    PostgresReservationBackend(arguments.database_url).initialize()
    print("PostgreSQL schema is at version 1")


if __name__ == "__main__":
    main()
