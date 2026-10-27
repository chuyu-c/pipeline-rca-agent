"""CLI: `python -m rca.db migrate`."""

import argparse

from rca.config import get_settings
from rca.db import connect
from rca.db.migrate import apply_migrations
from rca.observability.logging import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m rca.db")
    parser.add_argument("command", choices=["migrate"])
    parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    with connect(settings.admin_database_url, autocommit=True) as conn:
        applied = apply_migrations(conn)
    print(f"applied {len(applied)} migration(s): {', '.join(applied) or 'none pending'}")


if __name__ == "__main__":
    main()
